"""wx appearance/native-window methods with guarded progress gesture visibility.

This is the dynamic wx/Win32 boundary, not the typed playback model. Ownership and
native positioning are retained; hidden/idle behavior now respects active gestures.
"""
from __future__ import annotations

import logging
from typing import Any

from src.ui_wx.common import WX_CALLBACK_EXCEPTIONS

logger = logging.getLogger(__name__)



class VideoOverlayLayout:
    """Cohesive inherited view boundary; original public owner class is retained."""

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
        self._cancel_progress_gesture(repaint=False)
        self._stop_hide_timer()
        self._set_overlay_visibility(False)


    def on_pointer_activity(self, event: Any | None = None) -> None:
        self.show_temporarily()
        if event is not None:
            skipper = getattr(event, 'Skip', None)
            if callable(skipper):
                skipper()


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
        if self._dragging and not self._closed:
            self._restart_hide_timer()
            return
        self._set_overlay_visibility(False)


    def _stop_hide_timer(self) -> None:
        timer = self._hide_timer
        self._hide_timer = None
        stopper = getattr(timer, 'Stop', None)
        if callable(stopper):
            try:
                stopper()
            except WX_CALLBACK_EXCEPTIONS:
                logger.debug('Overlay hide timer cancellation failed after invalidation.', exc_info=True)



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
        if not desired_visibility and self._dragging:
            self._cancel_progress_gesture(repaint=False)
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


    def _stop_poll_timer(self) -> None:
        timer = self._poll_timer
        self._poll_timer = None
        self._poll_scheduled = False
        stopper = getattr(timer, 'Stop', None)
        if callable(stopper):
            try:
                stopper()
            except WX_CALLBACK_EXCEPTIONS:
                logger.debug('Overlay timer cancellation failed after invalidation.', exc_info=True)
