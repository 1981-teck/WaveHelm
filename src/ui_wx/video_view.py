from __future__ import annotations

import logging
from typing import Any

from src.ui_wx.common import apply_colors, set_label_text
from src.ui_wx.video_overlay_controls import ExternalVideoControlOverlay

logger = logging.getLogger(__name__)

VIDEO_VIEW_EXCEPTIONS = (AttributeError, ImportError, RuntimeError, TypeError, ValueError)


class NativeVideoSurface:
    """Native wx host surface used for Media Foundation HWND playback.

    Edge cases handled deterministically:
    1. Native handles can be unavailable during early layout, so handle resolution falls back to a stable cached value.
    2. Host refreshes can be requested repeatedly, so callbacks stay idempotent and deduplicated by handle value.
    3. Surface callbacks may be missing during teardown, so notifications become no-ops instead of raising.
    """

    def __init__(
        self,
        wx_module: Any,
        parent: Any,
        *,
        hwnd_ready_callback: Any = None,
        surface_changed_callback: Any = None,
    ) -> None:
        self._wx = wx_module
        self.panel = wx_module.Panel(parent)
        self._hwnd_ready_callback = hwnd_ready_callback
        self._surface_changed_callback = surface_changed_callback
        self._last_hwnd: int | None = None
        self._hwnd_notified = False
        self._last_surface_signature: tuple[int, int, int] | None = None
        self._status_label = wx_module.StaticText(self.panel, label='')
        sizer = wx_module.BoxSizer(wx_module.VERTICAL)
        sizer.Add(self._status_label, 0, wx_module.ALL | wx_module.EXPAND, 12)
        self.panel.SetSizer(sizer)
        size_event = getattr(wx_module, 'EVT_SIZE', None)
        if size_event is not None:
            self.panel.Bind(size_event, self._on_surface_resized)
        apply_colors(self.panel, background='#000000', foreground='#ffffff')
        apply_colors(self._status_label, background='#000000', foreground='#ffffff')
        set_label_text(self._status_label, 'External video surface ready.')

    def set_hwnd_ready_callback(self, callback: Any | None) -> None:
        self._hwnd_ready_callback = callback
        self._hwnd_notified = False

    def set_surface_changed_callback(self, callback: Any | None) -> None:
        self._surface_changed_callback = callback

    def request_hwnd_ready(self, force: bool = False) -> None:
        if force:
            self._last_hwnd = None
            self._hwnd_notified = False
            self._last_surface_signature = None
        hwnd = self.get_hwnd()
        if hwnd <= 0:
            return
        self._notify_hwnd_ready(hwnd)
        self._notify_surface_changed(hwnd)

    def get_hwnd(self) -> int:
        if self._last_hwnd and self._last_hwnd > 0:
            return int(self._last_hwnd)
        getter = getattr(self.panel, 'GetHandle', None)
        hwnd = 0
        if callable(getter):
            try:
                hwnd = int(getter() or 0)
            except VIDEO_VIEW_EXCEPTIONS:
                hwnd = 0
        if hwnd <= 0:
            hwnd = int(id(self.panel) & 0x7FFFFFFF)
        self._last_hwnd = hwnd
        return int(hwnd)

    def apply_theme(self, colors: dict[str, str]) -> None:
        foreground = colors.get('text_color') or '#ffffff'
        apply_colors(self.panel, background='#000000', foreground=foreground)
        apply_colors(self._status_label, background='#000000', foreground=foreground)

    def destroy(self) -> None:
        self._hwnd_ready_callback = None
        self._surface_changed_callback = None
        self._last_surface_signature = None

    def _resolve_surface_size(self) -> tuple[int, int]:
        getter = getattr(self.panel, 'GetClientSize', None)
        if not callable(getter):
            getter = getattr(self.panel, 'GetSize', None)
        if not callable(getter):
            return (1280, 720)
        try:
            size = getter()
        except VIDEO_VIEW_EXCEPTIONS:
            return (1280, 720)
        width = getattr(size, 'width', None)
        height = getattr(size, 'height', None)
        if width is None or height is None:
            try:
                width, height = size
            except VIDEO_VIEW_EXCEPTIONS:
                return (1280, 720)
        try:
            width_i = max(1, int(width))
            height_i = max(1, int(height))
        except VIDEO_VIEW_EXCEPTIONS:
            return (1280, 720)
        return (width_i, height_i)

    def _notify_hwnd_ready(self, hwnd: int) -> None:
        if self._hwnd_notified:
            return
        callback = self._hwnd_ready_callback
        if not callable(callback):
            return
        try:
            callback(int(hwnd))
            self._hwnd_notified = True
        except VIDEO_VIEW_EXCEPTIONS:
            logger.debug('NativeVideoSurface hwnd_ready callback failed.', exc_info=True)

    def _notify_surface_changed(self, hwnd: int) -> None:
        callback = self._surface_changed_callback
        if not callable(callback):
            return
        width, height = self._resolve_surface_size()
        signature = (int(hwnd), int(width), int(height))
        if signature == self._last_surface_signature:
            return
        payload = {'hwnd': signature[0], 'width': signature[1], 'height': signature[2]}
        try:
            callback(payload)
            self._last_surface_signature = signature
        except VIDEO_VIEW_EXCEPTIONS:
            logger.debug('NativeVideoSurface surface_changed callback failed.', exc_info=True)

    def _on_surface_resized(self, event: Any | None = None) -> None:
        hwnd = self.get_hwnd()
        if hwnd > 0:
            self._notify_surface_changed(hwnd)
        if event is not None:
            skip = getattr(event, 'Skip', None)
            if callable(skip):
                skip()


class ExternalVideoWindow:
    """Dedicated wx frame used as the external native host for video playback.

    Edge cases handled deterministically:
    1. External-host resizes can happen during fullscreen toggles, so child layout recomputes from the live client size on every size event.
    2. Overlay hide timers can race with pointer motion, so visibility changes always restart from a single authoritative timer.
    3. Playback controls can outlive the video session during teardown, so button callbacks degrade to safe no-ops once shutdown starts.
    """

    def __init__(
        self,
        wx_module: Any,
        *,
        hwnd_ready_callback: Any = None,
        surface_changed_callback: Any = None,
        close_callback: Any = None,
        player_controller: Any = None,
        event_bus: Any = None,
        localization_manager: Any = None,
        theme_manager: Any = None,
        fullscreen_callback: Any = None,
    ) -> None:
        self._wx = wx_module
        self._close_callback = close_callback
        self._emit_close_notification = True
        self.frame = wx_module.Frame(None, title='WaveHelm Video')
        self.frame.SetSize((1280, 720))
        self.frame.SetMinSize((640, 360))
        self.panel = wx_module.Panel(self.frame)
        self.video_surface = NativeVideoSurface(
            wx_module,
            self.panel,
            hwnd_ready_callback=hwnd_ready_callback,
            surface_changed_callback=surface_changed_callback,
        )
        self.control_overlay = ExternalVideoControlOverlay(
            wx_module,
            self.panel,
            player_controller=player_controller,
            event_bus=event_bus,
            localization_manager=localization_manager,
            theme_manager=theme_manager,
            fullscreen_callback=fullscreen_callback,
        )
        self._is_layouting = False
        self.frame.Bind(wx_module.EVT_CLOSE, self._on_close)
        self.frame.Bind(wx_module.EVT_SIZE, self._on_layout_changed)
        move_event = getattr(wx_module, 'EVT_MOVE', None)
        if move_event is not None:
            self.frame.Bind(move_event, self._on_layout_changed)
        self.panel.Bind(wx_module.EVT_SIZE, self._on_layout_changed)
        self._bind_pointer_activity(self.frame)
        self._bind_pointer_activity(self.panel)
        self._bind_pointer_activity(self.video_surface.panel)
        self._layout_children()

    def show_window(self) -> None:
        show_normal = getattr(self.frame, 'ShowNormal', None)
        is_minimized = getattr(self.frame, 'IsMinimized', None)
        if callable(is_minimized) and is_minimized() and callable(show_normal):
            show_normal()
        self.frame.Show(True)
        self._layout_children()
        self.control_overlay.show_temporarily()
        raiser = getattr(self.frame, 'Raise', None)
        if callable(raiser):
            raiser()
        activate = getattr(self.frame, 'Activate', None)
        if callable(activate):
            activate()

    def request_hwnd_ready(self, force: bool = False) -> None:
        self._layout_children()
        self.video_surface.request_hwnd_ready(force=force)

    def get_video_surface(self) -> NativeVideoSurface:
        return self.video_surface

    def get_video_view(self) -> NativeVideoSurface:
        return self.video_surface

    def suppress_close_notification_once(self) -> None:
        self._emit_close_notification = False

    def _destroy_frame(self) -> None:
        """Tear down the external frame instead of only detaching child widgets.

        Edge cases handled deterministically:
        1. Silent teardown paths suppress the close callback first, so the frame must still receive the close event to release the native window.
        2. Lightweight wx doubles may expose only ``Destroy`` without a full close lifecycle, so the implementation must degrade safely.
        3. Repeated teardown requests during stop/audio handoff races must stay idempotent once the frame is already gone.
        """
        frame = self.frame
        if frame is None:
            return
        close = getattr(frame, 'Close', None)
        if callable(close):
            close()
            return
        self.control_overlay.close()
        self.video_surface.destroy()
        destroy = getattr(frame, 'Destroy', None)
        if callable(destroy):
            destroy()

    def close(self) -> None:
        self._destroy_frame()

    def destroy(self) -> None:
        self._destroy_frame()

    def update_theme(self, colors: dict[str, str]) -> None:
        self.video_surface.apply_theme(colors)
        self.control_overlay.apply_theme(colors)

    def _bind_pointer_activity(self, widget: Any) -> None:
        motion_event = getattr(self._wx, 'EVT_MOTION', None)
        left_down_event = getattr(self._wx, 'EVT_LEFT_DOWN', None)
        if motion_event is not None:
            widget.Bind(motion_event, self._on_pointer_motion)
        if left_down_event is not None:
            widget.Bind(left_down_event, self._on_pointer_motion)

    def _resolve_client_size(self) -> tuple[int, int]:
        getter = getattr(self.frame, 'GetClientSize', None)
        if not callable(getter):
            getter = getattr(self.frame, 'GetSize', None)
        if not callable(getter):
            return (1280, 720)
        try:
            size = getter()
        except VIDEO_VIEW_EXCEPTIONS:
            return (1280, 720)
        width = getattr(size, 'width', None)
        height = getattr(size, 'height', None)
        if width is None or height is None:
            try:
                width, height = size
            except VIDEO_VIEW_EXCEPTIONS:
                return (1280, 720)
        try:
            return (max(1, int(width)), max(1, int(height)))
        except VIDEO_VIEW_EXCEPTIONS:
            return (1280, 720)

    def _layout_children(self) -> None:
        if self._is_layouting:
            return
        self._is_layouting = True
        try:
            width, height = self._resolve_client_size()
            root_position_setter = getattr(self.panel, 'SetPosition', None)
            if callable(root_position_setter):
                root_position_setter((0, 0))
            root_size_getter = getattr(self.panel, 'GetSize', None)
            root_current_size = root_size_getter() if callable(root_size_getter) else None
            root_size_setter = getattr(self.panel, 'SetSize', None)
            if callable(root_size_setter) and root_current_size != (width, height):
                root_size_setter((width, height))
            video_size_getter = getattr(self.video_surface.panel, 'GetSize', None)
            video_current_size = video_size_getter() if callable(video_size_getter) else None
            video_size_setter = getattr(self.video_surface.panel, 'SetSize', None)
            if callable(video_size_setter) and video_current_size != (width, height):
                video_size_setter((width, height))
            video_position_setter = getattr(self.video_surface.panel, 'SetPosition', None)
            if callable(video_position_setter):
                video_position_setter((0, 0))
            self.control_overlay.set_bounds(width, height)
        finally:
            self._is_layouting = False

    def _on_layout_changed(self, event: Any | None = None) -> None:
        self._layout_children()
        if event is not None:
            skip = getattr(event, 'Skip', None)
            if callable(skip):
                skip()

    def _on_pointer_motion(self, event: Any | None = None) -> None:
        self.control_overlay.on_pointer_activity(event)

    def _on_close(self, event: Any | None = None) -> None:
        self.control_overlay.close()
        self.video_surface.destroy()
        if self._emit_close_notification and callable(self._close_callback):
            try:
                self._close_callback()
            except VIDEO_VIEW_EXCEPTIONS:
                logger.debug('ExternalVideoWindow close callback failed.', exc_info=True)
        self._emit_close_notification = True
        destroy = getattr(self.frame, 'Destroy', None)
        if callable(destroy):
            destroy()
        if event is not None:
            skip = getattr(event, 'Skip', None)
            if callable(skip):
                skip()


class VideoView:
    """Compatibility stub for the removed embedded video page.

    Edge cases handled deterministically:
    1. Legacy imports may still resolve ``VideoView`` after the external-only migration, so the symbol remains available.
    2. Any attempt to instantiate the removed page fails fast with a precise runtime error instead of creating a partially wired embedded host.
    3. Callers can still inspect ``external_only_mode`` to branch away from legacy paths without touching wx state.
    """

    external_only_mode = True

    def __init__(self, *_: Any, **__: Any) -> None:
        raise RuntimeError(
            'VideoView has been removed from the app. WaveHelm now uses only the external video window.'
        )
