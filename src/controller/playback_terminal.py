"""Final presentation is handed off on the UI thread before next/teardown.

The native result decides completion. This layer reads an already-bound cache;
it does not synthesize clock values, sleep for paint or wait on a native clock.
"""
from __future__ import annotations
import logging
import threading
from typing import TYPE_CHECKING
from src.audio.audio_events import AudioEventType

if TYPE_CHECKING:
    from src.controller.player_controller import PlayerController

logger = logging.getLogger(__name__)


def present_terminal(player: PlayerController) -> bool:
    """Wake both bars before the end event destroys the external window.

    The default synchronous bus preserves this order on the GUI thread. A delayed
    or asynchronous subscriber must reread current ownership; no old 100% crosses
    into another playback. Paint availability is not a frame-duration guarantee.
    """
    if threading.current_thread() is not threading.main_thread():
        return True
    sample = None
    try:
        tracker = getattr(player, 'progress_tracker', None)
        getter = getattr(tracker, 'get_progress_snapshot', None)
        sample = getter() if callable(getter) else None
        view = player.get_playback_view()
        if view is None and sample is not None and sample.terminal:
            retry = tracker.request_terminal_retry(sample)
            logger.warning('Terminal view unavailable; bounded UI retry admitted=%s.', retry)
            return False
        if view is None or view.sample is None or not view.sample.terminal:
            return True
        if view.seek.blocks_end:
            return False
        player._publish_event(AudioEventType.PLAYBACK_PRESENTATION_FINISHED, {})
        current = player.get_playback_view()
        if current != view or player._is_shutdown:
            if not player._is_shutdown and sample is not None:
                tracker.request_terminal_retry(sample)
            return False
        backend = getattr(player.engine_controller, 'video_controller', None)
        if view.is_video and callable(getattr(backend, 'observe_end', None)):
            if backend.finalize_video_end() is not True:
                return False
            player._publish_event(AudioEventType.VIDEO_PLAYBACK_ENDED, {
                'path': view.path, 'playback_revision': view.revision,
                'position': view.sample.clock.position.seconds,
                'duration': view.sample.clock.duration.seconds,
            })
        return True
    except (AttributeError, RuntimeError, TypeError, ValueError, OSError):
        logger.warning('Terminal presentation could not be delivered.', exc_info=True)
        return False


def run_track_end_transition(self: PlayerController, expected_signature: tuple[str | None, int, str, int | None] | None = None) -> None:
    """Esegue la logica di fine traccia sul thread UI."""
    try:
        if getattr(self, '_is_shutdown', False):
            logger.debug('PlayerController: _run_track_end_transition ignored during shutdown')
            return

        if expected_signature is not None and not self._is_track_end_signature_current(expected_signature):
            logger.debug('PlayerController: stale track-end transition ignored for %r', expected_signature)
            return

        if not present_terminal(self):
            return
        if expected_signature is not None and not self._is_track_end_signature_current(expected_signature):
            return
        logger.debug('PlayerController: Gestione fine traccia.')
        should_repeat_current = bool(self.state_manager._loop)
        repeat_getter = getattr(self.engine_controller, 'should_repeat_current_track', None)
        if callable(repeat_getter):
            try:
                should_repeat_current = bool(repeat_getter())
            except (AttributeError, RuntimeError, TypeError, ValueError):
                logger.debug('PlayerController: should_repeat_current_track fallback failed.', exc_info=True)

        if should_repeat_current:
            logger.info('Looping traccia corrente.')
            if self.queue_manager.current_track:
                self.play_action(self.queue_manager.current_track)
        else:
            logger.info('Passaggio alla traccia successiva.')
            self.next()
    finally:
        self._track_end_dispatch_pending = False
