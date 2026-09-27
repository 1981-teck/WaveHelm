"""Video transport and completion, separate from source/track preparation.

The old module re-exports these methods for existing integrations. Only seek
admission changes in the new step; the other methods are preserved extractions.
"""
from __future__ import annotations
from src.video.seek_receipt import SeekReceipt, seek_seconds
from src.audio.audio_events import AudioEventType
from .video_controller_state import VideoState
from .seek_observation import read_seek_observation
from .video_terminal import capture_video_end, finalize_video_end, same_video_end
import logging
logger = logging.getLogger("src.controller.video_controller_playback")
ADAPTER_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError, OSError)
FORMAT_EXCEPTIONS = (TypeError, ValueError)
_MISSING_SEEK = object()


def pause(self) -> None:
    """Mette in pausa il video corrente, se possibile."""
    if self._adapter and self._state == VideoState.PLAYING:
        try:
            logger.debug('[VideoController] pause() requested')
            self._adapter.pause()
            self._update_state(VideoState.PAUSED)
            self._publish_event(AudioEventType.VIDEO_PLAYBACK_PAUSED, {})
        except ADAPTER_EXCEPTIONS as exc:
            logger.error('[VideoController] Failed to pause video: %s', exc, exc_info=True)


def resume(self) -> None:
    """Riprende la riproduzione del video corrente, se possibile."""
    if self._adapter and self._state == VideoState.PAUSED:
        try:
            logger.debug('[VideoController] resume() requested')
            self._adapter.resume()
            self._update_state(VideoState.PLAYING)
            self._publish_event(AudioEventType.VIDEO_PLAYBACK_RESUMED, {})
        except ADAPTER_EXCEPTIONS as exc:
            logger.error('[VideoController] Failed to resume video: %s', exc, exc_info=True)


def stop(self, close_adapter: bool = False) -> None:
    """
    Ferma la riproduzione attuale.
    Se close_adapter=True chiude anche l'adapter video.
    """
    if self._shutting_down:
        if close_adapter:
            self._close_adapter_internal()
        return

    if close_adapter:
        self._close_adapter_internal()
        return

    if self._adapter and self._state in (VideoState.PLAYING, VideoState.PAUSED):
        try:
            logger.debug('[VideoController] stop(close_adapter=%s) requested', close_adapter)
            self._adapter.stop()
            self._update_state(VideoState.STOPPED)
        except ADAPTER_EXCEPTIONS as exc:
            logger.warning('[VideoController] stop() failed: %s', exc, exc_info=True)

    self._publish_event(
        AudioEventType.VIDEO_PLAYBACK_STOPPED,
        {},
        require_ui_thread=True,
    )


def set_volume(self, volume: float) -> None:
    """Imposta il volume del video."""
    try:
        normalized_volume = max(0.0, min(1.0, float(volume)))
    except FORMAT_EXCEPTIONS:
        normalized_volume = 1.0
    self._volume = normalized_volume

    if self._adapter:
        try:
            logger.debug('[VideoController] set_volume(%.3f)', normalized_volume)
            self._adapter.set_volume(normalized_volume)
        except ADAPTER_EXCEPTIONS as exc:
            logger.error('[VideoController] Failed to set volume: %s', exc, exc_info=True)


def get_duration(self) -> float:
    """Ritorna la durata del media corrente."""
    duration = 0.0
    if self._adapter:
        try:
            duration = float(self._adapter.get_duration())
        except ADAPTER_EXCEPTIONS:
            duration = 0.0

    if duration > 0.0:
        return duration
    return max(0.0, float(self._current_duration_hint or 0.0))


def get_position(self) -> float:
    """Ritorna la posizione corrente del media."""
    if not self._adapter:
        return 0.0
    try:
        return float(self._adapter.get_position())
    except ADAPTER_EXCEPTIONS:
        return 0.0


def seek(self, position_sec: float) -> bool:
    """Return only actual adapter admission, not command execution or seek completion."""
    target = seek_seconds(position_sec)
    adapter = self._adapter
    if target is None or adapter is None or self._shutting_down:
        return False
    try:
        return adapter.seek(target) is True
    except ADAPTER_EXCEPTIONS as exc:
        logger.debug('Video seek admission failed: %s', exc, exc_info=True)
        return False


def get_seek_receipt(self) -> SeekReceipt | None:
    """Expose current adapter command facts; never perform a synchronous COM read."""
    adapter = self._adapter
    if adapter is None or self._shutting_down:
        return None
    # An advertised but failing receipt must not look like no pending seek.
    getter = getattr(adapter, 'get_seek_receipt', _MISSING_SEEK)
    if getter is _MISSING_SEEK:
        return None  # Legacy adapters have no tracked seek API.
    if not callable(getter):
        raise TypeError('Seek receipt accessor is not callable')
    result = getter()
    if self._adapter is not adapter:
        raise RuntimeError('Video adapter changed during receipt observation')
    if result is not None and type(result) is not SeekReceipt:
        raise TypeError('Video adapter returned malformed seek receipt')
    return result


def pump_pending_events(self) -> bool:
    """Pump normal error/lifecycle events even while a seek blocks EOS.

    An absent adapter, a pump error or reentrant replacement returns False. Never
    claim that a failed pump completed or let a pending seek hide native errors.
    """
    adapter = self._adapter
    if adapter is None:
        return False
    try:
        adapter.pump_events()
    except ADAPTER_EXCEPTIONS as exc:
        logger.warning('[VideoController] pump_events() failed: %s', exc, exc_info=True)
        return False
    return self._adapter is adapter and not self._shutting_down


def observe_end(self) -> bool:
    """Observe native EOS without publishing close or destroying its final clock.

    The tracker retains an identity-bound terminal record before UI handoff. The
    canonical facade emits the end notification on the GUI thread before next.
    Legacy poll_end callers retain their explicit observation-and-close behavior.
    """
    self._video_end_ticket = None
    if not pump_pending_events(self):
        return False
    adapter = self._adapter
    path = getattr(self, '_current_path', None)
    before_seek = read_seek_observation(self, path)
    if before_seek.blocks_end:
        return False

    try:
        owner = capture_video_end(self, before_seek)
        ended = self._adapter.has_ended()
        after_owner = capture_video_end(self, read_seek_observation(self, path))
    except ADAPTER_EXCEPTIONS as exc:
        logger.debug('[VideoController] has_ended() check failed: %s', exc, exc_info=True)
        return False

    after_seek = read_seek_observation(self, path)
    if (ended is not True or not same_video_end(owner, after_owner) or self._adapter is not adapter or getattr(self, '_current_path', None) != path
            or after_seek.blocks_end or after_seek != before_seek):
        return False

    if self._loop_enabled:
        return False
    self._video_end_ticket = after_owner
    return True


def poll_end(self) -> bool:
    """Legacy query-and-close contract; the shipped tracker uses observe_end instead."""
    if not observe_end(self):
        return False
    logger.info('[VideoController] Video ended (loop=%s)', self._loop_enabled)
    self._publish_event(
        AudioEventType.VIDEO_PLAYBACK_ENDED,
        {
            'path': self._current_path,
            'position': self.get_position(),
            'duration': self.get_duration(),
        },
        require_ui_thread=True,
    )

    self._close_adapter_internal()
    return True
