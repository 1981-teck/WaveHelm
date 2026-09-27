from __future__ import annotations

import logging
import time
from typing import Any

from src.audio.audio_event_models import AudioEventType
from src.ui_wx.progress_gesture_wx import ProgressGestureMixin
from src.ui_wx.progress_motion import FRAME_INTERVAL_MS
from src.controller.playback_view import PlaybackView
from src.ui_wx.playback_presentation import DisplayReading, PlaybackPresentation
from src.ui_wx.common import (
    WX_CALLBACK_EXCEPTIONS,
    apply_colors,
    get_theme_colors,
    prepare_progress_slider,
    register_callback,
    set_label_text,
    set_progress_slider_value,
    unregister_callback,
)

logger = logging.getLogger(__name__)

_INTERRUPT_ACTION_EVENTS = {
    'stop': AudioEventType.STOP_REQUESTED,
    'previous': AudioEventType.PREVIOUS_REQUESTED,
    'next': AudioEventType.NEXT_REQUESTED,
}

from src.ui_wx.video_overlay_native import VideoOverlayNative
from src.ui_wx.video_overlay_layout import VideoOverlayLayout

class ExternalVideoControlOverlay(ProgressGestureMixin, VideoOverlayNative, VideoOverlayLayout):
    """Auto-hiding control strip rendered above the external video host.

    Edge cases handled deterministically:
    1. Pointer activity can arrive while the hide timer is being replaced, so timer restarts always stop the previous handle first.
    2. Native video playback can swallow wx motion events, so cursor polling falls back to Win32 APIs instead of relying only on wx events.
    3. Overlay child windows can be repainted behind the native video surface, so every show/layout pass forces the control strip back to the top sibling z-order.
    """

    _HIDE_DELAY_MS = 4200
    _POLL_INTERVAL_MS = FRAME_INTERVAL_MS
    _DEFAULT_HEIGHT = 84

    def __init__(
        self,
        wx_module: Any,
        parent: Any,
        *,
        player_controller: Any = None,
        event_bus: Any = None,
        localization_manager: Any = None,
        theme_manager: Any = None,
        fullscreen_callback: Any = None,
    ) -> None:
        self._wx = wx_module
        self._player_controller = player_controller
        self._event_bus = event_bus
        self._localization_manager = localization_manager
        self._theme_manager = theme_manager
        self._fullscreen_callback = fullscreen_callback
        self._subscriptions: list[tuple[Any, Any]] = []
        self._hide_timer: Any = None
        self._poll_timer: Any = None
        self._poll_scheduled = False
        self._closed = False
        self._dragging = False
        self._updating_progress_slider = False
        self._current_duration = 0.0
        self._current_position = 0.0
        self._last_pointer_signature: tuple[int, int] | None = None
        self._pointer_inside_host = False
        self._last_pointer_poll = 0.0
        self._anchor_window = parent
        self._host_window = self._resolve_overlay_owner(parent)
        self._root_window = self._create_overlay_root_window(self._host_window)
        self.panel = wx_module.Panel(self._root_window)
        root_owner_setter = getattr(self._root_window, '__setattr__', None)
        if callable(root_owner_setter):
            self._root_window.owner = self
        self.panel.owner = self
        self.progress_slider: Any = None
        self.prev_button: Any = None
        self.play_button: Any = None
        self.pause_button: Any = None
        self.next_button: Any = None
        self.stop_button: Any = None
        self.fullscreen_button: Any = None
        self._display_reading = DisplayReading()
        self._current_media_path = ''
        self._presentation = PlaybackPresentation(
            lambda: self._player_controller, self._render_playback,
            getattr(self._wx, 'CallAfter', None), surface='overlay')
        self._build_ui()
        prepare_progress_slider(self.progress_slider)
        self._overlay_visible = False
        self._bind_controls()
        self._register_callbacks()
        self._subscribe_events()
        self.update_localization()
        self.update_theme_colors()
        self._poll_progress()
        self._schedule_poll()


    def _bind_controls(self) -> None:
        wx = self._wx
        self.prev_button.Bind(wx.EVT_BUTTON, lambda _event: self._invoke_player_action('previous'))
        self.play_button.Bind(wx.EVT_BUTTON, lambda _event: self._invoke_player_action('play_action'))
        self.pause_button.Bind(wx.EVT_BUTTON, lambda _event: self._invoke_player_action('pause'))
        self.next_button.Bind(wx.EVT_BUTTON, lambda _event: self._invoke_player_action('next'))
        self.stop_button.Bind(wx.EVT_BUTTON, lambda _event: self._invoke_player_action('stop'))
        self.fullscreen_button.Bind(wx.EVT_BUTTON, lambda _event: self._invoke_fullscreen_toggle())
        self._bind_progress_gesture()
        self._bind_pointer_activity(self._root_window)
        self._bind_pointer_activity(self.panel)

    def _bind_pointer_activity(self, widget: Any | None) -> None:
        if widget is None:
            return
        binder = getattr(widget, 'Bind', None)
        if not callable(binder):
            return
        for event_name in ('EVT_MOTION', 'EVT_ENTER_WINDOW'):
            event_type = getattr(self._wx, event_name, None)
            if event_type is not None:
                binder(event_type, self.on_pointer_activity)

    def _register_callbacks(self) -> None:
        register_callback(
            self._theme_manager,
            'register_theme_change_callback',
            self.update_theme_colors,
            logger=logger,
            message='Unable to register external video overlay theme callback.',
        )
        register_callback(
            self._localization_manager,
            'register_language_change_callback',
            self.update_localization,
            logger=logger,
            message='Unable to register external video overlay language callback.',
        )

    def _subscribe_events(self) -> None:
        subscribe = getattr(self._event_bus, 'subscribe', None)
        if not callable(subscribe):
            return
        bindings = {
            AudioEventType.PLAYBACK_PROGRESS: self._handle_progress_event,
            AudioEventType.PLAYBACK_PRESENTATION_FINISHED: self._handle_terminal_event,
            AudioEventType.PLAYER_STATE_CHANGED: self._handle_state_event,
            AudioEventType.VIDEO_DURATION_UPDATE: self._handle_video_duration_event,
        }
        for event_type, callback in bindings.items():
            try:
                subscription = subscribe(event_type, callback)
            except WX_CALLBACK_EXCEPTIONS:
                logger.debug('External video overlay subscription failed for %s.', event_type, exc_info=True)
                continue
            self._subscriptions.append((event_type, subscription if subscription is not None else callback))

    def update_localization(self, *_: Any) -> None:
        if not self._is_widget_alive(self.panel):
            return
        set_label_text(self.prev_button, '⏮')
        set_label_text(self.play_button, '▶')
        set_label_text(self.pause_button, '⏸')
        set_label_text(self.next_button, '⏭')
        set_label_text(self.stop_button, '⏹')
        set_label_text(self.fullscreen_button, '⛶')

    def update_theme_colors(self, *_: Any) -> None:
        if not self._is_widget_alive(self.panel):
            return
        colors = get_theme_colors(self._theme_manager)
        background = colors.get('footer_bg') or colors.get('panel_bg') or '#101010'
        foreground = colors.get('text_color') or '#f5f5f5'
        accent = colors.get('button_color') or '#1e1e1e'
        apply_colors(self.panel, background=background, foreground=foreground)
        apply_colors(self.progress_slider, background=background, foreground=foreground)
        for button in (
            self.prev_button,
            self.play_button,
            self.pause_button,
            self.next_button,
            self.stop_button,
            self.fullscreen_button,
        ):
            apply_colors(button, background=accent, foreground=foreground)


    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._cancel_progress_gesture(repaint=False)
        self._presentation.close()
        self._stop_hide_timer()
        self._stop_poll_timer()
        unsubscribe = getattr(self._event_bus, 'unsubscribe', None)
        if callable(unsubscribe):
            for event_type, subscription in self._subscriptions:
                try:
                    unsubscribe(event_type, subscription=subscription)
                except WX_CALLBACK_EXCEPTIONS:
                    try:
                        unsubscribe(event_type, callback=subscription)
                    except WX_CALLBACK_EXCEPTIONS:
                        logger.debug('External video overlay unsubscribe failed for %s.', event_type, exc_info=True)
        unregister_callback(
            self._theme_manager,
            'unregister_theme_change_callback',
            self.update_theme_colors,
            logger=logger,
            message='Unable to unregister external video overlay theme callback.',
        )
        unregister_callback(
            self._localization_manager,
            'unregister_language_change_callback',
            self.update_localization,
            logger=logger,
            message='Unable to unregister external video overlay language callback.',
        )
        self._subscriptions.clear()
        self._set_overlay_visibility(False)
        destroy = getattr(self._root_window, 'Destroy', None)
        if callable(destroy):
            try:
                destroy()
            except WX_CALLBACK_EXCEPTIONS:
                logger.debug('External video overlay root destroy failed.', exc_info=True)


    def _schedule_poll(self) -> None:
        if self._closed or self._poll_scheduled:
            return
        self._poll_scheduled = True
        try:
            self._poll_timer = self._wx.CallLater(self._POLL_INTERVAL_MS, self._on_poll_timer)
        except WX_CALLBACK_EXCEPTIONS:
            logger.debug('Overlay timer unavailable; closing view.', exc_info=True)
            self._poll_scheduled = False
            self.close()


    def _on_poll_timer(self) -> None:
        self._poll_scheduled = False
        self._poll_timer = None
        if self._closed:
            return
        try:
            now = time.monotonic()
            if now - self._last_pointer_poll >= 0.1:
                self._last_pointer_poll = now
                self._monitor_pointer_activity()
            self._poll_progress()
        finally:
            self._schedule_poll()





    def _poll_progress(self) -> None:
        """Cache-only clock refresh; pointer/window maintenance remains separate."""
        if not self._closed:
            self._presentation.refresh()


    def _handle_progress_event(self, payload: object) -> None:
        # Do not retain numeric payloads: delayed events only request a current read.
        self._presentation.request()


    def _handle_state_event(self, payload: object) -> None:
        # Do not retain numeric payloads: delayed events only request a current read.
        self._presentation.request()


    def _handle_video_duration_event(self, payload: object) -> None:
        # Do not retain numeric payloads: delayed events only request a current read.
        self._presentation.request()




    def _apply_progress_payload(self, payload: object) -> None:
        """Compatibility entry point; only the bound current view may change the bar."""
        self._poll_progress()


    def _apply_state_payload(self, payload: object) -> None:
        """Compatibility entry point; only the bound current view may change the bar."""
        self._poll_progress()


    def _apply_video_duration_payload(self, payload: object) -> None:
        """Compatibility entry point; only the bound current view may change the bar."""
        self._poll_progress()


    def _render_playback(self, view: PlaybackView | None, reading: DisplayReading) -> bool:
        """Reject dead widgets and use one pair rather than independent scalar reads."""
        if self._closed or not self._progress_allows_render(view, reading):
            return False
        if not self._can_touch_progress_slider():
            self.close()
            return False
        try:
            if view is None or not view.is_video:
                reading = DisplayReading()
            self._display_reading = reading
            self._current_media_path = view.path or '' if view and view.is_video else ''
            self._current_position = reading.position if reading.position is not None else 0.0
            self._current_duration = reading.duration if reading.duration is not None else 0.0
            self._sync_progress_slider()
            return not self._closed
        except WX_CALLBACK_EXCEPTIONS:
            logger.debug('Overlay presentation failed; closing view.', exc_info=True)
            self.close()
            return False


    def _handle_terminal_event(self, payload: object = None) -> None:
        self._presentation.refresh_terminal()

    def _sync_progress_slider(self) -> None:
        if self._dragging or not self._can_touch_progress_slider():
            return
        ratio = self._display_reading.visual_ratio
        self._updating_progress_slider = True
        try:
            set_progress_slider_value(self.progress_slider, int(round(ratio * 1000.0)))
            if self._display_reading.terminal:
                # Final UI handoff only, never a per-tick forced paint or delay.
                self.progress_slider.Refresh()
                if not self._closed:
                    self.progress_slider.Update()
        except WX_CALLBACK_EXCEPTIONS:
            self.close()
            logger.debug('External video overlay progress sync ignored after widget teardown.', exc_info=True)
        finally:
            self._updating_progress_slider = False

    def _set_progress_slider_value(self, ratio: float) -> None:
        if not self._can_touch_progress_slider():
            return
        self._updating_progress_slider = True
        try:
            set_progress_slider_value(
                self.progress_slider, int(round(max(0.0, min(1.0, ratio)) * 1000.0))
            )
        except WX_CALLBACK_EXCEPTIONS:
            self.close()
            logger.debug('External video overlay slider update ignored after widget teardown.', exc_info=True)
        finally:
            self._updating_progress_slider = False

    def _invoke_player_action(self, action_name: str) -> None:
        """Invoke overlay playback commands through a single transport path.

        Edge cases handled deterministically:
        1. Stop/next/previous publish only one interruption request when the event bus is available, preventing double queue advances.
        2. Missing event buses or publish helpers degrade to the direct controller action without leaving transport buttons inert.
        3. Unknown or non-transport action names stay controller-local and never emit false interruption signals.
        """
        self.show_temporarily()
        normalized_action = str(action_name or '').strip().lower()
        if self._publish_interruption_request(normalized_action):
            return
        player_controller = self._player_controller
        action = getattr(player_controller, action_name, None)
        if callable(action):
            action()

    def _publish_interruption_request(self, action_name: str) -> bool:
        event_type = _INTERRUPT_ACTION_EVENTS.get(str(action_name or '').strip().lower())
        if event_type is None:
            return False
        publish = getattr(self._event_bus, 'publish', None)
        if not callable(publish):
            return False
        try:
            publish(event_type, {'source': 'external_video_overlay'})
            return True
        except WX_CALLBACK_EXCEPTIONS:
            logger.debug('External video overlay interruption publish failed for %s.', event_type, exc_info=True)
            return False

    def _invoke_fullscreen_toggle(self) -> None:
        self.show_temporarily()
        callback = self._fullscreen_callback
        if callable(callback):
            callback()
