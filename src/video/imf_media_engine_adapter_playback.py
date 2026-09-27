from __future__ import annotations

from .seek_receipt import SeekReceipt, seek_seconds
from .imf_media_engine_adapter_tracks import (
    get_selected_audio_streams, get_number_of_streams, select_audio_stream, prime_audio_stream_candidates, get_text_track_descriptors, get_active_text_track_ids, select_text_track, disable_text_tracks,
)


from typing import Callable, Tuple

from .mf_base import logger

STATE_EXCEPTIONS = (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError)
CORE_EXCEPTIONS = (AttributeError, OSError, ReferenceError, RuntimeError, TypeError, ValueError)
COERCE_EXCEPTIONS = (TypeError, ValueError, OverflowError)


def _is_engine_initialized(self) -> bool:
    """Ritorna True se il core ha un MediaEngine COM valido (best effort)."""
    try:
        ptr = getattr(self._core, '_media_engine', None) or getattr(self._core, '_engine', None)
        return bool(ptr)
    except STATE_EXCEPTIONS:
        return False


def _can_issue_playback_commands(self) -> bool:
    """True se e sicuro inviare comandi playback (non in shutdown, engine pronto)."""
    try:
        with self._lock:
            if self._shutdown_requested or self._closed:
                return False
        return bool(self._is_engine_initialized())
    except STATE_EXCEPTIONS:
        return False


def _require_active_engine(self, operation: str) -> None:
    """Validate adapter preconditions before accepting playback commands."""
    with self._lock:
        if self._shutdown_requested or self._closed:
            raise RuntimeError(f'Cannot execute {operation}: adapter is shutting down or already closed.')

    if not self._is_engine_initialized():
        raise RuntimeError(f'Cannot execute {operation}: MediaEngine is not initialized.')


def startup(self) -> None:
    """Garantisce che il thread COM dedicato sia avviato (lazy) prima dell'uso."""
    logger.debug('[IMFAdapter] startup() called (ensure COM thread)')
    try:
        self._get_com_thread_manager(ensure_started=True)
    except CORE_EXCEPTIONS:
        logger.debug('[IMFAdapter] startup failed', exc_info=True)


def shutdown(self) -> None:
    """Shutdown completo: rilascia engine (idempotente)."""
    with self._lock:
        if self._closed or self._shutdown_requested:
            return
        self._shutdown_requested = True
        self._pending_play_epoch = None
        self._pending_source_epoch = None
        self._pending_source_path = None
        self._detaching_source_epoch = None
        self._playing_epoch = None
        logger.info('[IMFAdapter] shutdown()')

    try:
        self._core.shutdown()
    except CORE_EXCEPTIONS:
        logger.debug('[IMFAdapter] core.shutdown failed (best effort)', exc_info=True)
    finally:
        with self._lock:
            self._closed = True


def close(self) -> None:
    """Compat: alias per shutdown()."""
    self.shutdown()


def stop(self) -> None:
    """Compat: stop playback."""
    if not self._can_issue_playback_commands():
        logger.debug('[IMFAdapter] stop ignored (shutdown or engine not initialized)')
        return

    self._core.stop()


def set_hwnd(self, hwnd: int) -> None:
    """Binda l'HWND di output all'engine."""
    if not isinstance(hwnd, int) or hwnd <= 0:
        raise ValueError(f'HWND non valido: {hwnd!r}')

    with self._lock:
        if self._shutdown_requested:
            raise RuntimeError('Shutdown gia richiesto: impossibile set_hwnd()')
        self._hwnd = hwnd

    logger.info('[IMFAdapter] set_hwnd(HWND=%s)', hwnd)

    try:
        self._core.ensure_engine(hwnd)
    except CORE_EXCEPTIONS:
        logger.error('[IMFAdapter] ensure_engine failed for HWND=%s', hwnd, exc_info=True)
        raise

    self._apply_settings_core()


def bind_hwnd(self, hwnd: int) -> None:
    return self.set_hwnd(hwnd)


def set_target_hwnd(self, hwnd: int) -> None:
    return self.set_hwnd(hwnd)


def set_video_window(self, hwnd: int) -> None:
    return self.set_hwnd(hwnd)


def set_loop(self, enabled: bool) -> None:
    with self._lock:
        self._loop_enabled = bool(enabled)

    if self._is_engine_initialized():
        self._core.set_loop(self._loop_enabled)


def set_loop_enabled(self, enabled: bool) -> None:
    self.set_loop(enabled)


def refresh_video_window(self, hwnd: int, width: int, height: int) -> bool:
    if not self._is_engine_initialized():
        return False

    with self._lock:
        if self._shutdown_requested or self._closed:
            return False
        if hwnd and int(hwnd) > 0:
            self._hwnd = int(hwnd)

    return bool(self._core.update_video_stream(int(width), int(height)))


def set_volume(self, volume: float) -> None:
    with self._lock:
        try:
            v = float(volume)
        except COERCE_EXCEPTIONS:
            v = 1.0
        self._volume = max(0.0, min(1.0, v))

    if self._can_issue_playback_commands():
        try:
            self._core.set_volume(self._volume)
        except CORE_EXCEPTIONS:
            logger.debug('[IMFAdapter] set_volume failed', exc_info=True)


def set_muted(self, muted: bool) -> None:
    with self._lock:
        self._muted = bool(muted)

    if self._can_issue_playback_commands():
        try:
            self._core.set_muted(self._muted)
        except CORE_EXCEPTIONS:
            logger.debug('[IMFAdapter] set_muted failed', exc_info=True)


def resume(self) -> None:
    self.play()


def _stage_source_request(self, src: str) -> tuple[int, bool, bool, bool]:
    """Stage one source request and return epoch/replacement state."""
    with self._lock:
        if self._shutdown_requested or self._closed:
            raise RuntimeError('Cannot load video source while shutdown is in progress.')
        current_pending_epoch = self._pending_source_epoch
        if current_pending_epoch is not None:
            self._pending_source_path = src
            pending_epoch = current_pending_epoch
            replacing_existing = True
            replace_pending = True
            active_playing = False
        else:
            active_epoch = self._source_epoch
            replacing_existing = bool(self._source and self._source != src)
            replace_pending = False
            active_playing = replacing_existing and self._playing_epoch == active_epoch
            if replacing_existing:
                pending_epoch = active_epoch + 1
                self._pending_source_epoch = pending_epoch
                self._pending_source_path = src
            else:
                self._source = src
                self._source_epoch = active_epoch + 1
                pending_epoch = self._source_epoch
        self._loadstart_epoch = None
        self._ready_epoch = None
        self._pending_play_epoch = None
        return pending_epoch, replacing_existing, replace_pending, active_playing


def load_source(self, path: str) -> None:
    """Select a source after detaching any existing native source.

    Edge cases: a playing source waits for PAUSE; rapid requests coalesce on the
    pending epoch; Pause/detach failure keeps the committed source fail-closed.
    """
    if not path:
        raise ValueError('Video source path must not be empty.')
    self._require_active_engine('load_source')
    pipeline = getattr(self._core, '_wic_pipeline', None)
    if pipeline is not None:
        pipeline.invalidate()
    src = str(path)
    pending_epoch, replacing_existing, replace_pending, active_playing = _stage_source_request(self, src)

    logger.info('[IMFAdapter] load_source(%s)', src)
    if replace_pending:
        logger.info('[IMFAdapter] Pending source replaced before detach epoch=%d', pending_epoch)
        return
    if not replacing_existing:
        self._core.load_source(src)
        return

    try:
        if active_playing:
            self._core.pause_for_source_switch()
            logger.info('[IMFAdapter] Source switch deferred until PAUSE epoch=%d', pending_epoch)
        else:
            self._begin_source_detach(self._source_epoch)
            logger.info('[IMFAdapter] Source switch detach admitted epoch=%d', pending_epoch)
    except CORE_EXCEPTIONS:
        with self._lock:
            if self._pending_source_epoch == pending_epoch:
                self._pending_source_epoch = None
                self._pending_source_path = None
                self._detaching_source_epoch = None
        raise


def load_video(self, path: str, loop: bool = False) -> bool:
    try:
        self.set_loop_enabled(loop)
        self.load_source(path)
        self.play()
        return True
    except CORE_EXCEPTIONS:
        logger.error('[IMFAdapter] load_video failed', exc_info=True)
        return False


def play(self) -> None:
    """Issue Play only after the active source reaches CANPLAY.

    Edge cases:
        - Play requested during asynchronous loading is deferred, never duplicated.
        - Resume on an already-ready source is issued immediately.
        - Source replacement invalidates a deferred Play through its epoch token.
    """
    self._require_active_engine('play')
    with self._lock:
        source_epoch = getattr(self, '_pending_source_epoch', None) or self._source_epoch
        if source_epoch > 0 and self._ready_epoch != source_epoch:
            self._pending_play_epoch = source_epoch
            logger.info('[IMFAdapter] Play deferred until CANPLAY epoch=%d', source_epoch)
            return

    self._core.play()


def pause(self) -> None:
    self._require_active_engine('pause')
    self._core.pause()


def seek(self, seconds: float) -> bool:
    """Admit a seek without mistaking an absent/void backend result for success."""
    target = seek_seconds(seconds)
    if target is None:
        return False
    self._require_active_engine('seek')
    core = self._core
    return core.seek(target) is True


def get_seek_receipt(self) -> SeekReceipt | None:
    """Cached command evidence only; a closed/replaced adapter cannot expose it."""
    if self._closed or self._shutdown_requested or self._core is None:
        return None
    core = self._core
    result = core.get_seek_receipt()
    if self._core is not core or self._closed or self._shutdown_requested:
        raise RuntimeError('Adapter ownership changed during seek receipt read')
    return result


def get_position(self) -> float:
    try:
        return float(self._core.get_position())
    except CORE_EXCEPTIONS:
        return 0.0


def get_duration(self) -> float:
    try:
        return float(self._core.get_duration())
    except CORE_EXCEPTIONS:
        return 0.0


def has_ended(self) -> bool:
    try:
        return bool(self._core.is_ended())
    except CORE_EXCEPTIONS:
        return False


def get_video_size(self) -> Tuple[int, int]:
    try:
        size = self._core.get_video_size()
        if isinstance(size, tuple) and len(size) == 2:
            return (int(size[0]), int(size[1]))
        return (0, 0)
    except CORE_EXCEPTIONS:
        return (0, 0)


def has_video(self) -> bool:
    try:
        return bool(self._core.has_video())
    except CORE_EXCEPTIONS:
        return False


def _apply_settings_core(self) -> None:
    """Applica impostazioni correnti su MediaEngineCore (best effort)."""
    if not self._is_engine_initialized():
        return

    with self._lock:
        loop_enabled = self._loop_enabled
        volume = self._volume
        muted = self._muted
        shutdown = self._shutdown_requested or self._closed

    if shutdown:
        return

    try:
        self._core.set_loop(loop_enabled)
    except CORE_EXCEPTIONS:
        logger.debug('[IMFAdapter] apply set_loop failed', exc_info=True)

    try:
        if volume != 1.0:
            self._core.set_volume(volume)
    except CORE_EXCEPTIONS:
        logger.debug('[IMFAdapter] apply set_volume failed', exc_info=True)

    try:
        if muted:
            self._core.set_muted(True)
    except CORE_EXCEPTIONS:
        logger.debug('[IMFAdapter] apply set_muted failed', exc_info=True)


_IMF_MEDIA_ENGINE_ADAPTER_PLAYBACK_METHODS: tuple[tuple[str, Callable[..., object]], ...] = (
    ("_is_engine_initialized", _is_engine_initialized),
    ("_can_issue_playback_commands", _can_issue_playback_commands),
    ("_require_active_engine", _require_active_engine),
    ("startup", startup),
    ("shutdown", shutdown),
    ("close", close),
    ("stop", stop),
    ("set_hwnd", set_hwnd),
    ("bind_hwnd", bind_hwnd),
    ("set_target_hwnd", set_target_hwnd),
    ("set_video_window", set_video_window),
    ("set_loop", set_loop),
    ("set_loop_enabled", set_loop_enabled),
    ("refresh_video_window", refresh_video_window),
    ("set_volume", set_volume),
    ("set_muted", set_muted),
    ("resume", resume),
    ("load_source", load_source),
    ("load_video", load_video),
    ("get_selected_audio_streams", get_selected_audio_streams),
    ("get_number_of_streams", get_number_of_streams),
    ("select_audio_stream", select_audio_stream),
    ("prime_audio_stream_candidates", prime_audio_stream_candidates),
    ("get_text_track_descriptors", get_text_track_descriptors),
    ("get_active_text_track_ids", get_active_text_track_ids),
    ("select_text_track", select_text_track),
    ("disable_text_tracks", disable_text_tracks),
    ("play", play),
    ("pause", pause),
    ("seek", seek),
    ("get_seek_receipt", get_seek_receipt),
    ("get_position", get_position),
    ("get_duration", get_duration),
    ("has_ended", has_ended),
    ("get_video_size", get_video_size),
    ("has_video", has_video),
    ("_apply_settings_core", _apply_settings_core),
)


def install_imf_media_engine_adapter_playback_behavior(cls) -> None:
    """Install playback behavior on the adapter leaf module.

    Edge cases:
        - Duplicate installs during reloads can silently rebind methods.
        - Partial imports can leave playback helpers attached only in part.
        - Legacy callers can still import the old attach_* entrypoint directly.
    """
    if getattr(cls, "_imf_media_engine_adapter_playback_behavior_attached", False):
        return

    for name, method in _IMF_MEDIA_ENGINE_ADAPTER_PLAYBACK_METHODS:
        setattr(cls, name, method)

    setattr(cls, "_imf_media_engine_adapter_playback_behavior_attached", True)


def attach_imf_media_engine_adapter_playback_behavior(cls) -> None:
    """Backward-compatible shim for legacy attach_* imports."""
    install_imf_media_engine_adapter_playback_behavior(cls)
