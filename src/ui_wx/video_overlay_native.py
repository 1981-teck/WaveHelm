"""Existing wx appearance/native-window methods, extracted without behavior changes.

This is the dynamic wx/Win32 boundary, not the typed playback model. Ownership and
native-call behavior are retained; progress observation lives in the main controller.
"""
from __future__ import annotations

import ctypes
import logging
from typing import Any

from src.ui_wx.common import WX_CALLBACK_EXCEPTIONS

logger = logging.getLogger(__name__)



class VideoOverlayNative:
    """Cohesive inherited view boundary; original public owner class is retained."""

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
