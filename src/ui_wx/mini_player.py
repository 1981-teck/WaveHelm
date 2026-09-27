from __future__ import annotations

import importlib
import logging
from typing import Any, Callable

from src.audio.audio_event_models import AudioEventType
from src.ui_wx.progress_gesture_wx import ProgressGestureMixin
from src.ui_wx.progress_motion import FRAME_INTERVAL_MS
from src.controller.playback_view import PlaybackView
from src.controller.component_player.playback_state_manager import PlayerState
from src.playback_observation import ReadingStatus
from src.ui_wx.playback_presentation import DisplayReading, PlaybackPresentation
from src.ui_wx.common import (
    WX_CALLBACK_EXCEPTIONS,
    prepare_progress_slider,
    set_label_text,
    set_progress_slider_value,
    unregister_callback,
)
from src.ui_wx.mini_player_shared import format_time

logger = logging.getLogger(__name__)

_INTERRUPT_ACTION_EVENTS = {
    'stop': AudioEventType.STOP_REQUESTED,
    'previous': AudioEventType.PREVIOUS_REQUESTED,
    'next': AudioEventType.NEXT_REQUESTED,
}


from src.ui_wx.mini_player_chrome import MiniPlayerChrome

class MiniPlayer(ProgressGestureMixin, MiniPlayerChrome):
    """wx mini-player rendering current cache-only playback views.

    Delayed events only request a fresh view; no scalar clock polling occurs on
    the GUI thread. Closed widgets, unknown samples and failed timers are guarded.
    Pointer/key gestures use the shared preview/commit contract; native UI validation remains open.
    """

    def __init__(
        self,
        parent: Any,
        player_controller: Any = None,
        event_bus: Any = None,
        audio_engine: Any = None,
        localization_manager: Any = None,
        theme_manager: Any = None,
        **_: Any,
    ) -> None:
        self.parent = parent
        self.player_controller = player_controller
        self.event_bus = event_bus
        self.audio_engine = audio_engine
        self.localization_manager = localization_manager
        self.theme_manager = theme_manager
        self._wx = importlib.import_module('wx')
        self.panel = self._wx.Panel(parent)
        self.panel.owner = self
        self._subscriptions: list[tuple[Any, Any]] = []
        self._progress_timer: Any = None
        self._timer_running = False
        self._closed = False
        self._dragging = False
        self._updating_progress_slider = False
        self._volume_sync = False
        self._current_duration = 0.0
        self._current_position = 0.0
        self._current_media_path = ''
        self._current_track_title = ''
        self._last_nonzero_volume = 1.0
        self._muted = False
        self._is_playing = False
        self._shuffle_enabled = False
        self._loop_enabled = False
        self._display_reading = DisplayReading()
        self._presentation = PlaybackPresentation(
            lambda: self.player_controller, self._render_playback,
            getattr(self._wx, 'CallAfter', None), surface='mini')
        self._build_ui()
        prepare_progress_slider(self.progress_slider)
        self._bind_controls()
        self._register_callbacks()
        self._subscribe_events()
        self._sync_initial_state()
        self.update_localization()
        self.update_theme_colors()
        self._schedule_progress_timer()


    def _subscribe_events(self) -> None:
        subscribe = getattr(self.event_bus, 'subscribe', None)
        if not callable(subscribe):
            return
        bindings = {
            AudioEventType.PLAYBACK_PROGRESS: self._handle_progress_event,
            AudioEventType.PLAYBACK_PRESENTATION_FINISHED: self._handle_terminal_event,
            AudioEventType.PLAYER_STATE_CHANGED: self._handle_state_event,
            AudioEventType.VOLUME_CHANGED: self._handle_volume_event,
            AudioEventType.MUTE_CHANGED: self._handle_mute_event,
            AudioEventType.SHUFFLE_CHANGED: self._handle_shuffle_event,
            AudioEventType.LOOP_CHANGED: self._handle_loop_event,
            AudioEventType.VIDEO_DURATION_UPDATE: self._handle_video_duration_event,
        }
        for event_type, callback in bindings.items():
            try:
                subscription = subscribe(event_type, callback)
            except WX_CALLBACK_EXCEPTIONS:
                logger.debug('wx MiniPlayer subscription failed for %s.', event_type, exc_info=True)
                continue
            self._subscriptions.append((event_type, subscription))

    def _sync_initial_state(self) -> None:
        volume = self._read_initial_volume()
        self._set_volume_slider(volume)
        muted_getter = getattr(self.audio_engine, 'is_muted', None)
        if callable(muted_getter):
            try:
                self._muted = bool(muted_getter())
            except WX_CALLBACK_EXCEPTIONS:
                logger.debug('wx MiniPlayer mute bootstrap failed.', exc_info=True)
        self._poll_progress()
        self._update_time_labels()


    def _schedule_progress_timer(self) -> None:
        if self._closed or self._timer_running:
            return
        call_later = getattr(self._wx, 'CallLater', None)
        if not callable(call_later):
            return
        self._timer_running = True
        try:
            self._progress_timer = call_later(FRAME_INTERVAL_MS, self._on_progress_timer_tick)
        except WX_CALLBACK_EXCEPTIONS:
            logger.debug('Mini-player timer unavailable; closing view.', exc_info=True)
            self._timer_running = False
            self.close()


    def _on_progress_timer_tick(self) -> None:
        self._timer_running = False
        self._progress_timer = None
        if self._closed:
            return
        try:
            self._poll_progress()
        finally:
            self._schedule_progress_timer()


    def _poll_progress(self) -> None:
        """Read only cached state; no COM/mixer call from the GUI timer."""
        if not self._closed:
            self._presentation.refresh()


    def _defer_ui(self, callback: Callable[..., None], *args: Any) -> None:
        """wx callback boundary: late UI work cannot touch a closed mini-player."""
        def deliver() -> None:
            if not self._closed:
                callback(*args)
        if self._closed:
            return
        call_after = getattr(self._wx, 'CallAfter', None)
        if callable(call_after):
            call_after(deliver)
        else:
            deliver()


    def _defer_player_action(self, method_name: str) -> None:
        """Dispatch mini-player actions through a single transport path.

        Edge cases handled deterministically:
        1. Stop/next/previous publish only one interruption request when the event bus is available, preventing double queue advances.
        2. Missing event buses or publish helpers fall back to the direct controller method without leaving the transport button inert.
        3. Non-transport actions stay controller-local and never emit false interruption signals.
        """
        normalized_method = str(method_name or '').strip().lower()
        if self._publish_interruption_request(normalized_method):
            return
        method = getattr(self.player_controller, method_name, None)
        if callable(method):
            self._defer_ui(method)

    def _publish_interruption_request(self, method_name: str) -> bool:
        event_type = _INTERRUPT_ACTION_EVENTS.get(str(method_name or '').strip().lower())
        if event_type is None:
            return False
        publish = getattr(self.event_bus, 'publish', None)
        if not callable(publish):
            return False
        try:
            publish(event_type, {'source': 'mini_player'})
            return True
        except WX_CALLBACK_EXCEPTIONS:
            logger.debug('wx MiniPlayer interruption publish failed for %s.', event_type, exc_info=True)
            return False

    def _handle_progress_event(self, payload: object) -> None:
        # Events only wake the current cache reader; never retain old numbers/state.
        self._presentation.request()


    def _handle_state_event(self, payload: object) -> None:
        # Events only wake the current cache reader; never retain old numbers/state.
        self._presentation.request()


    def _handle_volume_event(self, payload: Any) -> None:
        self._defer_ui(self._apply_volume_payload, payload if isinstance(payload, dict) else {})

    def _handle_mute_event(self, payload: Any) -> None:
        self._defer_ui(self._apply_mute_payload, payload if isinstance(payload, dict) else {})

    def _handle_shuffle_event(self, payload: Any) -> None:
        self._defer_ui(self._apply_shuffle_payload, payload if isinstance(payload, dict) else {})

    def _handle_loop_event(self, payload: Any) -> None:
        self._defer_ui(self._apply_loop_payload, payload if isinstance(payload, dict) else {})

    def _handle_video_duration_event(self, payload: object) -> None:
        # Events only wake the current cache reader; never retain old numbers/state.
        self._presentation.request()


    def _apply_player_state(self, payload: object) -> None:
        """Compatibility entry point; payload is not an authority for current state."""
        self._poll_progress()



    def _apply_progress_payload(self, payload: object) -> None:
        """Compatibility entry point; payload is not an authority for current state."""
        self._poll_progress()


    def _apply_video_duration_payload(self, payload: object) -> None:
        """Compatibility entry point; payload is not an authority for current state."""
        self._poll_progress()


    def _render_playback(self, view: PlaybackView | None, reading: DisplayReading) -> bool:
        """Apply one coherent pair; widget destruction fails closed at this boundary."""
        if self._closed or not self._progress_allows_render(view, reading):
            return False
        try:
            old = self._display_reading
            cursor_only = (view is not None and view == getattr(self, '_paint_view', None)
                           and (reading.position, reading.duration, reading.status, reading.seek_phase)
                           == (old.position, old.duration, old.status, old.seek_phase))
            self._display_reading = reading
            if cursor_only:
                self._sync_progress_slider()
                return not self._closed
            self._paint_view = view
            self._current_position = reading.position if reading.position is not None else 0.0
            self._current_duration = reading.duration if reading.duration is not None else 0.0
            self._current_media_path = view.path or '' if view else ''
            self._current_track_title = view.title if view else ''
            self._is_playing = view is not None and view.state in (
                PlayerState.PLAYING_AUDIO, PlayerState.PLAYING_VIDEO)
            if view is not None:
                self._shuffle_enabled, self._loop_enabled = view.shuffle, view.loop
            self._refresh_track_and_state_text()
            self._refresh_toggle_labels()
            has_media = view is not None and view.path is not None
            loading = view is not None and view.state is PlayerState.LOADING
            self._set_enabled(self.play_button, has_media and not loading and not self._is_playing)
            self._set_enabled(self.pause_button, self._is_playing)
            self._set_enabled(self.stop_button, has_media)
            self._set_enabled(self.prev_button, has_media)
            self._set_enabled(self.next_button, has_media)
            self._sync_progress_slider()
            self._update_time_labels()
            return True
        except WX_CALLBACK_EXCEPTIONS:
            logger.debug('Mini-player presentation failed; closing view.', exc_info=True)
            self.close()
            return False



    def _reset_progress_state(self, *, clear_path: bool) -> None:
        self._display_reading = DisplayReading()
        self._paint_view = None
        self._presentation.invalidate_paint()
        self._current_position = 0.0
        self._current_duration = 0.0
        if clear_path:
            self._current_media_path = ''
        self._sync_progress_slider()
        self._update_time_labels()

    def _handle_terminal_event(self, payload: object = None) -> None:
        self._presentation.refresh_terminal()

    def _sync_progress_slider(self) -> None:
        if self._closed or self._dragging:
            return
        value = int(round(self._display_reading.visual_ratio * 1000.0))
        self._updating_progress_slider = True
        try:
            set_progress_slider_value(self.progress_slider, value)
            if self._display_reading.terminal:
                # Final UI handoff only, never a per-tick forced paint or delay.
                self.progress_slider.Refresh()
                if not self._closed:
                    self.progress_slider.Update()
        finally:
            self._updating_progress_slider = False


    def _update_time_labels(self) -> None:
        known = self._display_reading.status is ReadingStatus.KNOWN
        set_label_text(self.time_left_label, format_time(self._current_position) if known else '--:--')
        set_label_text(self.time_right_label, format_time(self._current_duration) if known else '--:--')


    def _set_progress_slider_value(self, ratio: float) -> None:
        if self._closed:
            return
        slider_value = int(round(max(0.0, min(1.0, ratio)) * 1000.0))
        self._updating_progress_slider = True
        try:
            set_progress_slider_value(self.progress_slider, slider_value)
        finally:
            self._updating_progress_slider = False


    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._cancel_progress_gesture(repaint=False)
        self._presentation.close()
        self._stop_timer()
        unsubscribe = getattr(self.event_bus, 'unsubscribe', None)
        if callable(unsubscribe):
            for event_type, subscription in self._subscriptions:
                try:
                    unsubscribe(event_type, subscription=subscription)
                except WX_CALLBACK_EXCEPTIONS:
                    logger.debug('wx MiniPlayer unsubscribe failed for %s.', event_type, exc_info=True)
        unregister_callback(
            self.localization_manager,
            'unregister_language_change_callback',
            self.update_localization,
            logger=logger,
            message='Unable to unregister wx MiniPlayer language callback.',
        )
        unregister_callback(
            self.theme_manager,
            'unregister_theme_change_callback',
            self.update_theme_colors,
            logger=logger,
            message='Unable to unregister wx MiniPlayer theme callback.',
        )
        self._subscriptions.clear()

    def _stop_timer(self) -> None:
        timer = self._progress_timer
        self._progress_timer = None
        self._timer_running = False
        stopper = getattr(timer, 'Stop', None)
        if callable(stopper):
            try:
                stopper()
            except WX_CALLBACK_EXCEPTIONS:
                logger.debug('Mini-player timer cancellation failed after invalidation.', exc_info=True)
