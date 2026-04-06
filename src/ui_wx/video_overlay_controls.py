from __future__ import annotations

import ctypes
import logging
from typing import Any

from src.audio.audio_event_models import AudioEventType
from src.controller.playback_status import get_duration, get_position, is_video_current, seek_to
from src.ui_wx.common import (
    WX_CALLBACK_EXCEPTIONS,
    apply_colors,
    get_theme_colors,
    register_callback,
    set_label_text,
    unregister_callback,
)

logger = logging.getLogger(__name__)

_INTERRUPT_ACTION_EVENTS = {
    'stop': AudioEventType.STOP_REQUESTED,
    'previous': AudioEventType.PREVIOUS_REQUESTED,
    'next': AudioEventType.NEXT_REQUESTED,
}


class ExternalVideoControlOverlay:
    """Auto-hiding control strip rendered above the external video host.

    Edge cases handled deterministically:
    1. Pointer activity can arrive while the hide timer is being replaced, so timer restarts always stop the previous handle first.
    2. Native video playback can swallow wx motion events, so cursor polling falls back to Win32 APIs instead of relying only on wx events.
    3. Overlay child windows can be repainted behind the native video surface, so every show/layout pass forces the control strip back to the top sibling z-order.
    """

    _HIDE_DELAY_MS = 4200
    _POLL_INTERVAL_MS = 100
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
        self._build_ui()
        self._overlay_visible = False
        self._bind_controls()
        self._register_callbacks()
        self._subscribe_events()
        self.update_localization()
        self.update_theme_colors()
        self._schedule_poll()

    def _build_ui(self) -> None:
        wx = self._wx
        root = wx.BoxSizer(wx.VERTICAL)
        self.progress_slider = wx.Slider(self.panel, value=0, minValue=0, maxValue=1000)
        controls_row = wx.BoxSizer(wx.HORIZONTAL)
        self.prev_button = wx.Button(self.panel, label='')
        self.play_button = wx.Button(self.panel, label='')
        self.pause_button = wx.Button(self.panel, label='')
        self.next_button = wx.Button(self.panel, label='')
        self.stop_button = wx.Button(self.panel, label='')
        self.fullscreen_button = wx.Button(self.panel, label='')
        for widget in (
            self.prev_button,
            self.play_button,
            self.pause_button,
            self.next_button,
            self.stop_button,
            self.fullscreen_button,
        ):
            controls_row.Add(widget, 0, wx.ALL, 4)
        center_horizontal = getattr(wx, 'ALIGN_CENTER_HORIZONTAL', 0)
        root.Add(self.progress_slider, 0, wx.ALL | wx.EXPAND, 8)
        root.Add(controls_row, 0, wx.ALL | center_horizontal, 4)
        self.panel.SetSizer(root)

    def _bind_controls(self) -> None:
        wx = self._wx
        self.prev_button.Bind(wx.EVT_BUTTON, lambda _event: self._invoke_player_action('previous'))
        self.play_button.Bind(wx.EVT_BUTTON, lambda _event: self._invoke_player_action('play_action'))
        self.pause_button.Bind(wx.EVT_BUTTON, lambda _event: self._invoke_player_action('pause'))
        self.next_button.Bind(wx.EVT_BUTTON, lambda _event: self._invoke_player_action('next'))
        self.stop_button.Bind(wx.EVT_BUTTON, lambda _event: self._invoke_player_action('stop'))
        self.fullscreen_button.Bind(wx.EVT_BUTTON, lambda _event: self._invoke_fullscreen_toggle())
        slider_event = getattr(wx, 'EVT_SLIDER', None)
        if slider_event is not None:
            self.progress_slider.Bind(slider_event, self._on_progress_slider_changed)
        pointer_down_event = getattr(wx, 'EVT_LEFT_DOWN', None)
        if pointer_down_event is not None:
            self.progress_slider.Bind(pointer_down_event, self._on_progress_slider_pointer_down)
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

    def set_bounds(self, width: int, height: int) -> None:
        if not self._is_widget_alive(self._root_window) or not self._is_widget_alive(self.panel):
            return
        clamped_width = max(1, int(width or 1))
        clamped_height = max(1, int(height or 1))
        overlay_height = min(self._DEFAULT_HEIGHT, clamped_height)
        host_rect = self._get_host_screen_rect()
        if host_rect is not None:
            root_position = (int(host_rect[0]), int(host_rect[1] + max(0, clamped_height - overlay_height)))
            root_position_setter = getattr(self._root_window, 'SetPosition', None)
            if callable(root_position_setter):
                root_position_setter(root_position)
        root_set_size = getattr(self._root_window, 'SetSize', None)
        if callable(root_set_size):
            root_set_size((clamped_width, overlay_height))
        panel_position = getattr(self.panel, 'SetPosition', None)
        if callable(panel_position):
            panel_position((0, 0))
        panel_set_size = getattr(self.panel, 'SetSize', None)
        if callable(panel_set_size):
            panel_set_size((clamped_width, overlay_height))
        layout = getattr(self.panel, 'Layout', None)
        if callable(layout):
            layout()
        root_layout = getattr(self._root_window, 'Layout', None)
        if callable(root_layout):
            root_layout()
        self._ensure_overlay_on_top()

    def show_temporarily(self) -> None:
        if not self._is_widget_alive(self._root_window) or not self._is_widget_alive(self.panel):
            return
        if not self._overlay_visible:
            self._show_overlay_widgets()
            self._ensure_overlay_on_top()
        self._restart_hide_timer()

    def hide_now(self) -> None:
        self._stop_hide_timer()
        self._set_overlay_visibility(False)

    def on_pointer_activity(self, event: Any | None = None) -> None:
        self.show_temporarily()
        if event is not None:
            skipper = getattr(event, 'Skip', None)
            if callable(skipper):
                skipper()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
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

    def _create_overlay_root_window(self, owner: Any) -> Any:
        frame_cls = getattr(self._wx, 'Frame', None)
        if callable(frame_cls):
            frame_style = 0
            for style_name in ('FRAME_NO_TASKBAR', 'FRAME_TOOL_WINDOW', 'BORDER_NONE', 'STAY_ON_TOP'):
                frame_style |= int(getattr(self._wx, style_name, 0) or 0)
            try:
                return frame_cls(owner, title='', style=frame_style)
            except TypeError:
                return frame_cls(owner, title='')
        popup_cls = getattr(self._wx, 'PopupWindow', None)
        if callable(popup_cls):
            try:
                return popup_cls(owner)
            except TypeError:
                try:
                    return popup_cls(owner, flags=0)
                except TypeError:
                    logger.debug('External video overlay PopupWindow fallback failed.', exc_info=True)
        return owner

    def _resolve_overlay_owner(self, widget: Any | None) -> Any | None:
        current = widget
        while current is not None:
            parent_getter = getattr(current, 'GetParent', None)
            parent = parent_getter() if callable(parent_getter) else getattr(current, 'parent', None)
            if parent is None:
                return current
            current = parent
        return widget

    def _is_widget_alive(self, widget: Any | None) -> bool:
        if self._closed or widget is None:
            return False
        is_being_deleted = getattr(widget, 'IsBeingDeleted', None)
        if callable(is_being_deleted):
            try:
                if bool(is_being_deleted()):
                    return False
            except WX_CALLBACK_EXCEPTIONS:
                return False
        getter = getattr(widget, 'GetHandle', None)
        if callable(getter):
            try:
                getter()
            except WX_CALLBACK_EXCEPTIONS:
                return False
        return True

    def _can_touch_progress_slider(self) -> bool:
        return self._is_widget_alive(self._root_window) and self._is_widget_alive(self.panel) and self._is_widget_alive(self.progress_slider)

    def _restart_hide_timer(self) -> None:
        self._stop_hide_timer()
        self._hide_timer = self._wx.CallLater(self._HIDE_DELAY_MS, self._hide_after_idle)

    def _hide_after_idle(self) -> None:
        self._hide_timer = None
        self._set_overlay_visibility(False)

    def _stop_hide_timer(self) -> None:
        timer = self._hide_timer
        self._hide_timer = None
        stopper = getattr(timer, 'Stop', None)
        if callable(stopper):
            stopper()

    def _schedule_poll(self) -> None:
        if self._closed or self._poll_scheduled:
            return
        self._poll_scheduled = True
        self._poll_timer = self._wx.CallLater(self._POLL_INTERVAL_MS, self._on_poll_timer)

    def _on_poll_timer(self) -> None:
        self._poll_scheduled = False
        self._poll_timer = None
        if self._closed:
            return
        self._monitor_pointer_activity()
        self._poll_progress()
        self._schedule_poll()

    def _stop_poll_timer(self) -> None:
        timer = self._poll_timer
        self._poll_timer = None
        self._poll_scheduled = False
        stopper = getattr(timer, 'Stop', None)
        if callable(stopper):
            stopper()

    def _ensure_overlay_on_top(self) -> None:
        if not self._overlay_visible or not self._is_widget_alive(self._root_window) or not self._is_widget_alive(self.panel):
            return
        self._raise_overlay_root()
        self._raise_overlay_native()

    def _iter_overlay_widgets(self) -> tuple[Any, ...]:
        return (
            self._root_window,
            self.panel,
            self.progress_slider,
            self.prev_button,
            self.play_button,
            self.pause_button,
            self.next_button,
            self.stop_button,
            self.fullscreen_button,
        )

    def _set_overlay_visibility(self, visible: bool) -> None:
        desired_visibility = bool(visible)
        visibility_changed = self._overlay_visible != desired_visibility
        self._overlay_visible = desired_visibility
        for widget in self._iter_overlay_widgets():
            if not self._is_widget_alive(widget):
                continue
            is_shown = getattr(widget, 'IsShown', None)
            widget_visible = bool(is_shown()) if callable(is_shown) else desired_visibility
            if widget_visible != desired_visibility:
                show = getattr(widget, 'Show', None)
                if callable(show):
                    show(desired_visibility)
                elif not desired_visibility:
                    hide = getattr(widget, 'Hide', None)
                    if callable(hide):
                        hide()
            if visibility_changed:
                refresh = getattr(widget, 'Refresh', None)
                if callable(refresh):
                    refresh()
                update = getattr(widget, 'Update', None)
                if callable(update):
                    update()

    def _show_overlay_widgets(self) -> None:
        self._show_root_without_activation()
        self._set_overlay_visibility(True)
        self._prime_overlay_children_for_visibility()
        self._clear_button_hover_state()

    def _prime_overlay_children_for_visibility(self) -> None:
        for widget in self._iter_overlay_widgets()[2:]:
            if not self._is_widget_alive(widget):
                continue
            raiser = getattr(widget, 'Raise', None)
            if callable(raiser):
                raiser()
            refresh = getattr(widget, 'Refresh', None)
            if callable(refresh):
                refresh()
            update = getattr(widget, 'Update', None)
            if callable(update):
                update()

    def _clear_button_hover_state(self) -> None:
        for widget in self._iter_overlay_widgets():
            if not self._is_widget_alive(widget):
                continue
            mouse_capture = getattr(widget, 'ReleaseMouse', None)
            has_capture = getattr(widget, 'HasCapture', None)
            if callable(has_capture) and callable(mouse_capture):
                try:
                    if bool(has_capture()):
                        mouse_capture()
                except WX_CALLBACK_EXCEPTIONS:
                    logger.debug('External video overlay release mouse failed.', exc_info=True)
            unfocus = getattr(widget, 'SetFocusIgnoringChildren', None)
            if callable(unfocus):
                try:
                    unfocus()
                    return
                except WX_CALLBACK_EXCEPTIONS:
                    logger.debug('External video overlay SetFocusIgnoringChildren failed.', exc_info=True)
            unfocus = getattr(widget, 'SetFocus', None)
            if callable(unfocus) and widget is self.panel:
                try:
                    unfocus()
                except WX_CALLBACK_EXCEPTIONS:
                    logger.debug('External video overlay SetFocus failed.', exc_info=True)

    def _raise_overlay_root(self) -> None:
        for widget in (self._root_window, self.panel):
            if not self._is_widget_alive(widget):
                continue
            raiser = getattr(widget, 'Raise', None)
            if callable(raiser):
                raiser()

    def _show_root_without_activation(self) -> None:
        if not self._is_widget_alive(self._root_window):
            return
        root_show_without_activating = getattr(self._root_window, 'ShowWithoutActivating', None)
        if callable(root_show_without_activating):
            try:
                root_show_without_activating()
                return
            except TypeError:
                try:
                    root_show_without_activating(True)
                    return
                except WX_CALLBACK_EXCEPTIONS:
                    logger.debug('External video overlay ShowWithoutActivating(True) fallback failed.', exc_info=True)
            except WX_CALLBACK_EXCEPTIONS:
                logger.debug('External video overlay ShowWithoutActivating() failed.', exc_info=True)
        root_show = getattr(self._root_window, 'Show', None)
        if callable(root_show):
            try:
                root_show(True)
            except WX_CALLBACK_EXCEPTIONS:
                logger.debug('External video overlay Show(True) fallback failed.', exc_info=True)

    def _raise_overlay_native(self) -> None:
        set_window_pos = self._resolve_set_window_pos()
        overlay_handle = self._get_widget_handle(self._root_window)
        if set_window_pos is None or overlay_handle <= 0:
            return
        flags = 0x0001 | 0x0002 | 0x0010 | 0x0200
        try:
            set_window_pos(overlay_handle, 0, 0, 0, 0, 0, flags)
        except (AttributeError, OSError, TypeError, ValueError):
            logger.debug('External video overlay native raise failed.', exc_info=True)

    def _resolve_set_window_pos(self) -> Any | None:
        windll = getattr(ctypes, 'windll', None)
        user32 = getattr(windll, 'user32', None) if windll is not None else None
        set_window_pos = getattr(user32, 'SetWindowPos', None)
        return set_window_pos if callable(set_window_pos) else None

    def _get_widget_handle(self, widget: Any | None) -> int:
        if widget is None:
            return 0
        getter = getattr(widget, 'GetHandle', None)
        if not callable(getter):
            return 0
        try:
            return int(getter() or 0)
        except (AttributeError, OSError, TypeError, ValueError):
            return 0

    def _monitor_pointer_activity(self) -> None:
        if not self._is_widget_alive(self._root_window) or not self._is_widget_alive(self.panel):
            return
        pointer_position = self._get_global_pointer_position()
        if pointer_position is None:
            return
        is_inside_host = self._is_pointer_inside_host(pointer_position)
        did_move = pointer_position != self._last_pointer_signature
        did_enter_host = is_inside_host and not self._pointer_inside_host
        self._last_pointer_signature = pointer_position
        self._pointer_inside_host = is_inside_host
        if is_inside_host and (did_move or did_enter_host):
            self.show_temporarily()

    def _get_global_pointer_position(self) -> tuple[int, int] | None:
        native_position = self._get_native_pointer_position()
        if native_position is not None:
            return native_position
        getter = getattr(self._wx, 'GetMousePosition', None)
        if not callable(getter):
            return None
        try:
            position = getter()
        except WX_CALLBACK_EXCEPTIONS:
            return None
        return self._coerce_point(position)

    def _get_native_pointer_position(self) -> tuple[int, int] | None:
        windll = getattr(ctypes, 'windll', None)
        user32 = getattr(windll, 'user32', None) if windll is not None else None
        get_cursor_pos = getattr(user32, 'GetCursorPos', None)
        if not callable(get_cursor_pos):
            return None

        class _Point(ctypes.Structure):
            _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]

        point = _Point()
        try:
            ok = bool(get_cursor_pos(ctypes.byref(point)))
        except (AttributeError, OSError, TypeError, ValueError):
            return None
        if not ok:
            return None
        return (int(point.x), int(point.y))

    def _is_pointer_inside_host(self, pointer_position: tuple[int, int]) -> bool:
        host_rect = self._get_host_screen_rect()
        if host_rect is None:
            return False
        left, top, width, height = host_rect
        pointer_x, pointer_y = pointer_position
        return left <= pointer_x < (left + width) and top <= pointer_y < (top + height)

    def _get_host_screen_rect(self) -> tuple[int, int, int, int] | None:
        native_rect = self._get_native_host_rect()
        if native_rect is not None:
            return native_rect
        host_widget = self._anchor_window or self._host_window
        if host_widget is None:
            return None
        rect_getter = getattr(host_widget, 'GetScreenRect', None)
        if callable(rect_getter):
            try:
                rect = rect_getter()
            except WX_CALLBACK_EXCEPTIONS:
                rect = None
            rect_tuple = self._coerce_rect(rect)
            if rect_tuple is not None:
                return rect_tuple
        position = self._get_widget_screen_position(host_widget)
        size = self._get_widget_size(host_widget)
        if position is None or size is None:
            return None
        return (position[0], position[1], size[0], size[1])

    def _get_native_host_rect(self) -> tuple[int, int, int, int] | None:
        windll = getattr(ctypes, 'windll', None)
        user32 = getattr(windll, 'user32', None) if windll is not None else None
        get_window_rect = getattr(user32, 'GetWindowRect', None)
        host_handle = self._get_widget_handle(self._anchor_window or self._host_window)
        if not callable(get_window_rect) or host_handle <= 0:
            return None

        class _Rect(ctypes.Structure):
            _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long), ('right', ctypes.c_long), ('bottom', ctypes.c_long)]

        rect = _Rect()
        try:
            ok = bool(get_window_rect(host_handle, ctypes.byref(rect)))
        except (AttributeError, OSError, TypeError, ValueError):
            return None
        if not ok:
            return None
        width = max(1, int(rect.right - rect.left))
        height = max(1, int(rect.bottom - rect.top))
        return (int(rect.left), int(rect.top), width, height)

    def _get_widget_screen_position(self, widget: Any) -> tuple[int, int] | None:
        getter = getattr(widget, 'GetScreenPosition', None)
        if not callable(getter):
            return None
        try:
            position = getter()
        except WX_CALLBACK_EXCEPTIONS:
            return None
        return self._coerce_point(position)

    def _get_widget_size(self, widget: Any) -> tuple[int, int] | None:
        getter = getattr(widget, 'GetSize', None)
        if not callable(getter):
            return None
        try:
            size = getter()
        except WX_CALLBACK_EXCEPTIONS:
            return None
        return self._coerce_size(size)

    def _coerce_point(self, value: Any) -> tuple[int, int] | None:
        if value is None:
            return None
        if isinstance(value, tuple):
            raw_x, raw_y = value
        else:
            raw_x = getattr(value, 'x', None)
            raw_y = getattr(value, 'y', None)
        try:
            return (int(raw_x), int(raw_y))
        except (OverflowError, TypeError, ValueError):
            return None

    def _coerce_size(self, value: Any) -> tuple[int, int] | None:
        if value is None:
            return None
        if isinstance(value, tuple):
            raw_width, raw_height = value
        else:
            raw_width = getattr(value, 'width', None)
            raw_height = getattr(value, 'height', None)
        try:
            width = max(1, int(raw_width))
            height = max(1, int(raw_height))
        except (OverflowError, TypeError, ValueError):
            return None
        return (width, height)

    def _coerce_rect(self, value: Any) -> tuple[int, int, int, int] | None:
        if value is None:
            return None
        if isinstance(value, tuple) and len(value) == 4:
            raw_left, raw_top, raw_width, raw_height = value
        else:
            raw_left = getattr(value, 'x', None)
            raw_top = getattr(value, 'y', None)
            raw_width = getattr(value, 'width', None)
            raw_height = getattr(value, 'height', None)
        try:
            left = int(raw_left)
            top = int(raw_top)
            width = max(1, int(raw_width))
            height = max(1, int(raw_height))
        except (OverflowError, TypeError, ValueError):
            return None
        return (left, top, width, height)

    def _poll_progress(self) -> None:
        if self._dragging or self._player_controller is None or not is_video_current(self._player_controller):
            return
        duration = get_duration(self._player_controller) or self._current_duration
        position = get_position(self._player_controller) or self._current_position
        self._current_duration = max(0.0, float(duration or 0.0))
        self._current_position = max(0.0, min(self._current_duration or float(position or 0.0), float(position or 0.0)))
        self._sync_progress_slider()

    def _handle_progress_event(self, payload: Any) -> None:
        self._defer_ui(self._apply_progress_payload, payload if isinstance(payload, dict) else {})

    def _handle_state_event(self, payload: Any) -> None:
        self._defer_ui(self._apply_state_payload, payload if isinstance(payload, dict) else {})

    def _handle_video_duration_event(self, payload: Any) -> None:
        self._defer_ui(self._apply_video_duration_payload, payload if isinstance(payload, dict) else {})

    def _defer_ui(self, callback: Any, payload: dict[str, Any]) -> None:
        if self._closed:
            return
        dispatcher = getattr(self._wx, 'CallAfter', None)
        if callable(dispatcher):
            dispatcher(callback, payload)
            return
        callback(payload)

    def _apply_progress_payload(self, payload: dict[str, Any]) -> None:
        if not self._can_touch_progress_slider():
            return
        self._current_position = max(0.0, self._safe_float(payload.get('current_time', payload.get('position', 0.0))))
        duration = payload.get('total_duration', payload.get('duration'))
        if duration is not None:
            self._current_duration = max(0.0, self._safe_float(duration))
        self._sync_progress_slider()

    def _apply_state_payload(self, payload: dict[str, Any]) -> None:
        if not self._can_touch_progress_slider():
            return
        if payload.get('stopped'):
            self._current_position = 0.0
            if not payload.get('playing'):
                self._sync_progress_slider()

    def _apply_video_duration_payload(self, payload: dict[str, Any]) -> None:
        if not self._can_touch_progress_slider():
            return
        duration = payload.get('duration')
        if duration is None:
            return
        self._current_duration = max(0.0, self._safe_float(duration))
        self._sync_progress_slider()

    def _safe_float(self, value: Any) -> float:
        try:
            return float(value or 0.0)
        except (OverflowError, TypeError, ValueError):
            return 0.0

    def _sync_progress_slider(self) -> None:
        if self._dragging or not self._can_touch_progress_slider():
            return
        ratio = 0.0
        if self._current_duration > 0:
            ratio = max(0.0, min(1.0, self._current_position / self._current_duration))
        self._updating_progress_slider = True
        try:
            self.progress_slider.SetValue(int(round(ratio * 1000.0)))
        except WX_CALLBACK_EXCEPTIONS:
            self._closed = True
            logger.debug('External video overlay progress sync ignored after widget teardown.', exc_info=True)
        finally:
            self._updating_progress_slider = False

    def _set_progress_slider_value(self, ratio: float) -> None:
        if not self._can_touch_progress_slider():
            return
        self._updating_progress_slider = True
        try:
            self.progress_slider.SetValue(int(round(max(0.0, min(1.0, ratio)) * 1000.0)))
        except WX_CALLBACK_EXCEPTIONS:
            self._closed = True
            logger.debug('External video overlay slider update ignored after widget teardown.', exc_info=True)
        finally:
            self._updating_progress_slider = False

    def _seek_from_ratio(self, ratio: float) -> bool:
        if self._current_duration <= 0 or self._player_controller is None:
            return False
        target = max(0.0, min(1.0, ratio)) * self._current_duration
        self._current_position = target
        return bool(seek_to(self._player_controller, target))

    def _pointer_ratio_from_event(self, event: Any | None) -> float | None:
        if event is None or not self._can_touch_progress_slider():
            return None
        getter = getattr(event, 'GetX', None)
        raw_position = getter() if callable(getter) else None
        try:
            pointer_x = float(raw_position)
        except (OverflowError, TypeError, ValueError):
            return None
        size_getter = getattr(self.progress_slider, 'GetClientSize', None)
        slider_size = size_getter() if callable(size_getter) else None
        if slider_size is None:
            size_getter = getattr(self.progress_slider, 'GetSize', None)
            slider_size = size_getter() if callable(size_getter) else None
        slider_width = slider_size[0] if isinstance(slider_size, tuple) else getattr(slider_size, 'width', 0)
        try:
            width = float(slider_width)
        except (OverflowError, TypeError, ValueError):
            return None
        if width <= 1.0:
            return None
        return max(0.0, min(width - 1.0, pointer_x)) / (width - 1.0)

    def _on_progress_slider_changed(self, _event: Any | None = None) -> None:
        if self._updating_progress_slider or not self._can_touch_progress_slider():
            return
        self.show_temporarily()
        self._dragging = True
        self._seek_from_ratio(float(self.progress_slider.GetValue()) / 1000.0)
        self._dragging = False
        self._sync_progress_slider()

    def _on_progress_slider_pointer_down(self, event: Any | None = None) -> None:
        if self._updating_progress_slider or not self._can_touch_progress_slider():
            return
        self.show_temporarily()
        ratio = self._pointer_ratio_from_event(event)
        if ratio is None or self._current_duration <= 0:
            skipper = getattr(event, 'Skip', None)
            if callable(skipper):
                skipper()
            return
        self._dragging = True
        self._set_progress_slider_value(ratio)
        self._seek_from_ratio(ratio)
        self._dragging = False
        self._sync_progress_slider()

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
