from __future__ import annotations

import importlib
import logging
from typing import Any

from src.audio.audio_event_models import AudioEventType
from src.ui_wx.common import apply_colors, get_localized_text, get_theme_colors, register_callback, set_label_text
from src.ui_wx.main_view_shell import (
    VIEW_LABELS,
    build_main_frame,
    create_placeholder_pages,
    switch_page,
    update_header_status,
    wrap_close_event,
)
from src.ui_wx.signal import WxSignal
from src.ui_wx.video_view import ExternalVideoWindow

logger = logging.getLogger(__name__)

MAIN_VIEW_EXCEPTIONS = (AttributeError, ImportError, RuntimeError, TypeError, ValueError)


class MainView:
    """Maintained wx main shell for the application runtime."""

    def __init__(self, master: Any, **dependencies: Any) -> None:
        self._root = master
        self._dependencies = dict(dependencies)
        self._wx = self._import_wx_module()
        self._event_bus = self._dependencies.get('event_bus')
        self._is_shutting_down = False
        self._request_app_shutdown: Any = None
        self._sidebar_buttons: dict[str, Any] = {}
        self._pages: dict[str, Any] = {}
        self._content_book: Any = None
        self._frame: Any = None
        self._title_label: Any = None
        self._status_label: Any = None
        self._footer_label: Any = None
        self._mini_player: Any = None
        self._current_view = ''
        self._event_subscriptions: list[tuple[AudioEventType, Any]] = []
        self._external_video_only_mode = True
        self._use_external_video_window = True
        self._external_video_window: Any = None
        self._video_hwnd: int | None = None
        self._pending_video_request: dict[str, Any] | None = None
        self._last_video_ready_signature: tuple[int, str, bool] | None = None
        self._last_video_surface_signature: tuple[int, int, int] | None = None
        self._hwnd_retry_timer: Any = None
        self._video_session_active = False
        self._deferred_external_window_close = False
        self._deferred_external_window_close_on_user_interrupt = False
        self._close_external_window_on_stop_request = False
        self.view_changed = WxSignal()
        self.shutdown_requested = WxSignal()
        self.video_playback_ready = WxSignal()
        self._frame = build_main_frame(self)
        create_placeholder_pages(self)
        self._register_callbacks()
        self._setup_event_subscriptions()
        self.update_localization()
        self.update_theme_colors()
        self._switch_view('library')

    @staticmethod
    def _import_wx_module() -> Any:
        try:
            return importlib.import_module('wx')
        except ImportError as exc:
            raise RuntimeError('wx is required to instantiate the wx main shell.') from exc

    def _register_callbacks(self) -> None:
        register_callback(
            self._dependencies.get('theme_manager'),
            'register_theme_change_callback',
            self.update_theme_colors,
            logger=logger,
            message='Unable to register wx MainView theme callback.',
        )
        register_callback(
            self._dependencies.get('localization_manager'),
            'register_language_change_callback',
            self.update_localization,
            logger=logger,
            message='Unable to register wx MainView language callback.',
        )

    def _setup_event_subscriptions(self) -> None:
        subscriptions = (
            (AudioEventType.PREPARE_VIDEO_PLAYBACK, self._on_prepare_video_playback),
            (AudioEventType.CANCEL_VIDEO_PLAYBACK, self._on_cancel_video_playback),
            (AudioEventType.VIDEO_PLAYBACK_STARTED, self._on_video_playback_started),
            (AudioEventType.VIDEO_PLAYBACK_STOPPED, self._on_video_playback_stopped),
            (AudioEventType.VIDEO_PLAYBACK_ERROR, self._on_video_playback_error),
            (AudioEventType.VIDEO_PLAYBACK_ENDED, self._on_video_playback_ended),
            (AudioEventType.PLAYBACK_STARTED, self._on_audio_playback_started),
            (AudioEventType.STOP_REQUESTED, self._on_video_stop_requested),
            (AudioEventType.PREVIOUS_REQUESTED, self._on_video_interruption_requested),
            (AudioEventType.NEXT_REQUESTED, self._on_video_interruption_requested),
            (AudioEventType.VIDEO_WINDOW_CLOSED, self._on_video_window_closed),
            (AudioEventType.UI_TOGGLE_FULLSCREEN, self._on_ui_toggle_fullscreen),
        )
        subscribe = getattr(self._event_bus, 'subscribe', None)
        if not callable(subscribe):
            return
        for event_type, callback in subscriptions:
            try:
                sub = subscribe(event_type, callback)
            except MAIN_VIEW_EXCEPTIONS:
                logger.debug('wx MainView event subscription failed for %s.', event_type, exc_info=True)
                continue
            self._event_subscriptions.append((event_type, sub if sub is not None else callback))

    def update_localization(self, *_: Any) -> None:
        set_title = getattr(self._frame, 'SetTitle', None)
        if callable(set_title):
            set_title('WaveHelm')
        for view_name, fallback in VIEW_LABELS:
            button = self._sidebar_buttons.get(view_name)
            if button is None:
                continue
            key = 'nav_playlists' if view_name == 'playlist' else f'nav_{view_name}'
            if view_name == 'visualizer':
                key = 'tab_visualizer'
            label = get_localized_text(self._dependencies.get('localization_manager'), key, fallback)
            set_label_text(button, label)
        if self._footer_label is not None:
            footer = get_localized_text(self._dependencies.get('localization_manager'), 'nav_readmi', 'Readmi')
            settings = get_localized_text(self._dependencies.get('localization_manager'), 'nav_settings', 'Settings')
            setter = getattr(self._footer_label, 'SetLabel', None)
            if callable(setter):
                setter(
                    f'wx migration shell active. Ported pages: Library, Playlist, Favorites, Video, Equalizer, Effects, Visualizer, Ambient, {settings}, {footer}, About, Mini Player.'
                )
        if self._mini_player is not None:
            updater = getattr(self._mini_player, 'update_localization', None)
            if callable(updater):
                updater()
        for page in self._pages.values():
            widget = getattr(page.panel, 'owner', None)
            updater = getattr(widget, 'update_localization', None)
            if callable(updater):
                updater()
        update_header_status(self)

    def update_theme_colors(self, *_: Any) -> None:
        colors = get_theme_colors(self._dependencies.get('theme_manager'))
        background = colors.get('panel_bg') or colors.get('bg_color')
        foreground = colors.get('text_color')
        accent = colors.get('button_color') or background
        for widget in filter(None, [self._frame, self.window, self._title_label, self._status_label, self._footer_label]):
            apply_colors(widget, background=background, foreground=foreground)
        if self._mini_player is not None:
            updater = getattr(self._mini_player, 'update_theme_colors', None)
            if callable(updater):
                updater()
        for button in self._sidebar_buttons.values():
            apply_colors(button, background=accent, foreground=foreground)
        for page in self._pages.values():
            widget = getattr(page.panel, 'owner', None)
            updater = getattr(widget, 'update_theme_colors', None)
            if callable(updater):
                updater()

    def _make_sidebar_handler(self, view_name: str) -> Any:
        def _handler(_event: Any) -> None:
            self._switch_view(view_name)
        return _handler

    def _switch_view(self, view_name: str) -> None:
        if self._is_shutting_down or view_name == self._current_view:
            return
        switch_page(self, view_name)
        update_header_status(self)

    def show_view(self, view_name: str) -> None:
        self._switch_view(view_name)

    def show(self) -> None:
        if self._frame is None:
            raise RuntimeError('wx main shell frame is not available.')
        set_top_window = getattr(self._root, 'SetTopWindow', None)
        if callable(set_top_window):
            set_top_window(self._frame)
        self._frame.Show(True)

    def set_use_external_video_window(self, enabled: bool) -> None:
        """Persist the external-host preference while enforcing external-only playback.

        Edge cases:
        1. Legacy callers can still pass ``False`` after the embedded host removal request, so the runtime must coerce the value without tearing down a live external session.
        2. UI widgets may mirror the stored state, so they must be re-synchronized after coercion to avoid showing a stale unchecked toggle.
        3. Deferred-close flags from older embedded-host flows must be cleared deterministically so the next external session does not inherit orphaned teardown intent.
        """
        requested_enabled = bool(enabled)
        previous_enabled = self._use_external_video_window
        normalized_enabled = True if self._external_video_only_mode else requested_enabled
        self._use_external_video_window = normalized_enabled
        if previous_enabled != normalized_enabled:
            self._reset_video_host_runtime_state()
        self._deferred_external_window_close = False
        self._deferred_external_window_close_on_user_interrupt = False
        if self._external_video_only_mode and not requested_enabled:
            self._update_video_status('External video window is required in the current build.')

    def _has_active_or_pending_video_session(self) -> bool:
        """Return True when host migration must wait for the live video session.

        Edge cases:
        1. UI state can lag behind backend playback events, so internal flags alone must not force an immediate external-window teardown.
        2. A pending prepare request must keep the current host alive until the next surface handshake completes, even before VIDEO_PLAYBACK_STARTED arrives.
        3. Legacy player-controller doubles may expose only partial playback APIs, so capability probes must degrade safely without raising.
        """
        if self._video_session_active or self._pending_video_request is not None:
            return True
        player_controller = self._dependencies.get('player_controller')
        if player_controller is None:
            return False
        payload_getter = getattr(player_controller, 'get_current_player_state_payload', None)
        if callable(payload_getter):
            try:
                payload = payload_getter()
            except MAIN_VIEW_EXCEPTIONS:
                payload = None
            if isinstance(payload, dict):
                state_name = str(payload.get('state') or '').strip().upper()
                if state_name in {'PLAYING_VIDEO', 'PAUSED_VIDEO', 'LOADING'}:
                    return True
                if state_name in {'STOPPED', 'IDLE', 'PLAYING_AUDIO', 'PAUSED_AUDIO', 'ERROR'}:
                    return False
                if bool(payload.get('stopped')):
                    return False
                if bool(payload.get('is_audio')):
                    return False
                if bool(payload.get('is_video')):
                    return True
        is_video = getattr(player_controller, 'is_video', None)
        try:
            video_current = bool(is_video()) if callable(is_video) else bool(is_video)
        except MAIN_VIEW_EXCEPTIONS:
            video_current = False
        if not video_current:
            current_track = getattr(player_controller, 'current_track', None)
            media_type = getattr(current_track, 'media_type', None)
            media_type_name = str(getattr(media_type, 'name', media_type)).upper()
            video_current = media_type_name == 'VIDEO'
        if not video_current:
            return False
        video_controller = getattr(player_controller, 'video_controller', None)
        is_playing = getattr(video_controller, 'is_playing', None)
        if callable(is_playing):
            try:
                return bool(is_playing())
            except MAIN_VIEW_EXCEPTIONS:
                return True
        if is_playing is not None:
            return bool(is_playing)
        return False

    def _close_external_video_window_if_session_inactive(
        self,
        *,
        reason: str,
        status_message: str,
        clear_pending_video_request: bool = True,
    ) -> bool:
        """Close the stale external window when the backend is already inactive.

        Edge cases:
        1. STOP_REQUESTED can be delivered after a nested VIDEO_PLAYBACK_STOPPED callback already ended the backend session.
        2. Audio handoffs can publish CANCEL_VIDEO_PLAYBACK before the next PLAYBACK_STARTED event arrives, so the old external window must still disappear deterministically.
        3. Stopped video tracks can remain selected in the queue, so stale current-track metadata must not block teardown once playback is inactive.
        """
        if self._external_video_window is None or self._has_active_or_pending_video_session():
            return False
        self._deferred_external_window_close = False
        self._deferred_external_window_close_on_user_interrupt = False
        self._close_external_window_on_stop_request = False
        self._close_external_video_window_silently(
            reason=reason,
            status_message=status_message,
            clear_pending_video_request=clear_pending_video_request,
        )
        return True

    def toggle_video_fullscreen(self) -> None:
        self._on_ui_toggle_fullscreen({})

    def refresh_video_surface(self, *, force: bool = False) -> None:
        video_surface = self._get_active_video_surface()
        if video_surface is None:
            return
        request = getattr(video_surface, 'request_hwnd_ready', None)
        if callable(request):
            request(force=force)
        self._try_dispatch_pending_video_request()

    def _reset_video_host_runtime_state(self) -> None:
        """Reset cached host metadata so migration never reuses a stale HWND.

        Edge cases:
        1. Host toggles can happen while playback is active, so cached HWND/signature values must not leak into the next prepare handshake.
        2. Repeated toggles between embedded and external hosts must remain idempotent and keep surface refreshes forceable.
        3. Late duplicate surface callbacks must be allowed after migration, so dedupe signatures are cleared before the next host bind.
        """
        self._video_hwnd = None
        self._last_video_ready_signature = None
        self._last_video_surface_signature = None

    def get_video_hwnd(self, *, allow_cached: bool = True) -> int | None:
        video_surface = self._get_active_video_surface()
        if video_surface is not None:
            getter = getattr(video_surface, 'get_hwnd', None)
            if callable(getter):
                try:
                    hwnd = int(getter() or 0)
                except MAIN_VIEW_EXCEPTIONS:
                    hwnd = 0
                if hwnd > 0:
                    self._video_hwnd = hwnd
                    return int(hwnd)
        return int(self._video_hwnd) if allow_cached and self._video_hwnd else None

    def _get_active_video_surface(self) -> Any | None:
        if self._external_video_window is None:
            return None
        getter = getattr(self._external_video_window, 'get_video_surface', None)
        return getter() if callable(getter) else None

    def _ensure_external_video_window(self) -> Any:
        if self._external_video_window is None:
            self._external_video_window = ExternalVideoWindow(
                self._wx,
                hwnd_ready_callback=self._on_video_hwnd_ready,
                surface_changed_callback=self._on_video_surface_changed,
                close_callback=self._handle_external_video_window_closed,
                player_controller=self._dependencies.get('player_controller'),
                event_bus=self._event_bus,
                localization_manager=self._dependencies.get('localization_manager'),
                theme_manager=self._dependencies.get('theme_manager'),
                fullscreen_callback=self.toggle_video_fullscreen,
            )
        return self._external_video_window

    def _show_external_video_window(self) -> Any | None:
        window = self._ensure_external_video_window()
        show_window = getattr(window, 'show_window', None)
        if callable(show_window):
            show_window()
        request_hwnd = getattr(window, 'request_hwnd_ready', None)
        if callable(request_hwnd):
            request_hwnd(force=False)
        return self._get_active_video_surface()

    def _schedule_hwnd_retry(self) -> None:
        if self._is_shutting_down or self._pending_video_request is None:
            return
        if self._hwnd_retry_timer is not None:
            stop = getattr(self._hwnd_retry_timer, 'Stop', None)
            if callable(stop):
                stop()
        self._hwnd_retry_timer = self._wx.CallLater(80, self._retry_pending_video_start)

    def _retry_pending_video_start(self) -> None:
        self._hwnd_retry_timer = None
        if self._pending_video_request is None or self._is_shutting_down:
            return
        self.refresh_video_surface(force=True)
        if self._pending_video_request is not None and self.get_video_hwnd(allow_cached=False) is None:
            self._schedule_hwnd_retry()

    def _handle_close_event(self, event: Any | None = None) -> None:
        close_event = wrap_close_event(event)
        if self._is_shutting_down:
            close_event.Skip()
            return
        if callable(self._request_app_shutdown):
            self.shutdown_requested.emit()
            if close_event.CanVeto():
                close_event.Veto()
            return
        self.shutdown()
        close_event.Skip()

    def _notify_video_playback_ready(self, hwnd: int, path: str, loop: bool) -> None:
        signature = (int(hwnd), str(path), bool(loop))
        if signature == self._last_video_ready_signature:
            return
        self._last_video_ready_signature = signature
        if self._event_bus is not None:
            publish = getattr(self._event_bus, 'publish', None)
            if callable(publish):
                try:
                    publish(AudioEventType.VIDEO_PLAYBACK_READY, {'hwnd': int(hwnd), 'path': str(path), 'loop': bool(loop)})
                    return
                except MAIN_VIEW_EXCEPTIONS:
                    logger.debug('wx MainView VIDEO_PLAYBACK_READY publish failed.', exc_info=True)
        self.video_playback_ready.emit(int(hwnd), str(path), bool(loop))

    def _try_dispatch_pending_video_request(self) -> None:
        if self._pending_video_request is None:
            return
        hwnd = self.get_video_hwnd(allow_cached=False)
        if hwnd is None or hwnd <= 0:
            return
        self._notify_video_playback_ready(hwnd, self._pending_video_request['path'], self._pending_video_request['loop'])

    def _on_prepare_video_playback(self, data: Any | None = None) -> None:
        """Prepare the correct host for the next video session.

        Edge cases:
        1. External-host disable can be deferred while the current video is still playing, so the stale external window must be torn down before the next internal session starts.
        2. Empty or malformed prepare payloads must not clear the existing host state or schedule retries.
        3. Late end/stop events from the previous session must not resurrect an already-dismissed external window after the next prepare request.
        """
        if self._is_shutting_down or not isinstance(data, dict):
            return
        self._video_session_active = True
        path = str(data.get('path') or '').strip()
        if not path:
            return
        self._pending_video_request = {'path': path, 'loop': bool(data.get('loop', False))}
        self._close_external_window_on_stop_request = False
        self._reset_video_host_runtime_state()
        self._show_external_video_window()
        self._try_dispatch_pending_video_request()
        if self._pending_video_request is not None and self.get_video_hwnd(allow_cached=False) is None:
            self._schedule_hwnd_retry()
        self._update_video_status('Preparing video host…')

    def _on_cancel_video_playback(self, _data: Any | None = None) -> None:
        self._pending_video_request = None
        self._video_session_active = False
        self._last_video_ready_signature = None
        self._close_external_window_on_stop_request = False
        self._deferred_external_window_close_on_user_interrupt = False
        self._stop_hwnd_retry_timer()
        if self._close_external_video_window_if_session_inactive(
            reason='video_playback_cancelled_inactive',
            status_message='Video playback cancelled.',
        ):
            return
        if self._close_external_video_window_if_deferred(
            reason='video_playback_cancelled',
            status_message='Embedded host enabled.',
        ):
            return
        self._update_video_status('Video playback cancelled.')

    def _on_video_playback_started(self, data: Any | None = None) -> None:
        self._video_session_active = True
        self._deferred_external_window_close_on_user_interrupt = False
        if not isinstance(data, dict):
            self._pending_video_request = None
            return
        path = str(data.get('path') or '').strip()
        if self._pending_video_request and path:
            pending_path = str(self._pending_video_request.get('path') or '')
            if pending_path.lower() == path.lower():
                self._pending_video_request = None
        self._stop_hwnd_retry_timer()
        self._update_video_status(f'Playing video: {path or "current media"}')

    def _on_video_interruption_requested(self, _data: Any | None = None) -> None:
        """Track explicit user stop/navigation requests during deferred host migration.

        Edge cases:
        1. Stop/next/previous requests can race with a deferred external-host toggle, so the flag must only arm when a window is still open.
        2. Audio-track controlled reloads can emit VIDEO_PLAYBACK_STOPPED without user intent, so they must not arm deferred teardown.
        3. Repeated user requests before the backend stops must remain idempotent and keep the teardown decision deterministic.
        """
        if not self._deferred_external_window_close or self._external_video_window is None:
            return
        self._deferred_external_window_close_on_user_interrupt = True

    def _on_video_stop_requested(self, _data: Any | None = None) -> None:
        """Arm external-host teardown only for an explicit stop request.

        Edge cases:
        1. Audio-only stop requests must not leak a stale close flag into the next video session.
        2. Repeated stop presses before backend confirmation must remain idempotent.
        3. Stop requests racing with deferred host migration must preserve the deferred close path.
        """
        self._close_external_window_on_stop_request = bool(
            self._external_video_window is not None or self._video_session_active
        )
        if self._close_external_video_window_if_session_inactive(
            reason='video_stop_requested_after_backend_stop',
            status_message='Video playback stopped.',
        ):
            return
        self._on_video_interruption_requested(_data)

    def _extract_video_event_path(self, data: Any | None) -> str:
        """Return the normalized video path from runtime event payloads.

        Edge cases:
        1. Legacy video events can still arrive with None or non-dict payloads during teardown races.
        2. payload['path'] can be missing, empty, or not safely coercible to string.
        3. Pending host migration must keep working when older publishers omit the path entirely.
        """
        if not isinstance(data, dict):
            return ''
        try:
            return str(data.get('path') or '').strip()
        except MAIN_VIEW_EXCEPTIONS:
            return ''

    def _should_ignore_video_playback_stopped(self, data: Any | None) -> bool:
        """Drop stale VIDEO_PLAYBACK_STOPPED events from a previous video session.

        Edge cases:
        1. A previous adapter can emit STOPPED after a different video has already been prepared.
        2. The next session can already have a cached ready signature even when pending_video_request is cleared.
        3. Legacy STOPPED events without a path must remain backward compatible and cannot be dropped blindly.
        """
        event_path = self._extract_video_event_path(data)
        if not event_path:
            return False

        pending_path = ''
        if isinstance(self._pending_video_request, dict):
            try:
                pending_path = str(self._pending_video_request.get('path') or '').strip()
            except MAIN_VIEW_EXCEPTIONS:
                pending_path = ''
        if pending_path and pending_path.lower() == event_path.lower():
            return False

        ready_path = ''
        signature = self._last_video_ready_signature
        if isinstance(signature, tuple) and len(signature) >= 2:
            try:
                ready_path = str(signature[1] or '').strip()
            except MAIN_VIEW_EXCEPTIONS:
                ready_path = ''
        if ready_path and ready_path.lower() == event_path.lower():
            return False

        if pending_path or ready_path or self._video_session_active:
            logger.debug(
                'wx MainView ignored stale VIDEO_PLAYBACK_STOPPED current=%s ready=%s event=%s',
                pending_path or '<none>',
                ready_path or '<none>',
                event_path,
            )
            return True
        return False

    def _on_video_playback_stopped(self, data: Any | None = None) -> None:
        if self._should_ignore_video_playback_stopped(data):
            return
        self._pending_video_request = None
        self._video_session_active = False
        self._stop_hwnd_retry_timer()
        should_close_on_stop_request = self._close_external_window_on_stop_request
        self._close_external_window_on_stop_request = False
        should_close_deferred_window = self._deferred_external_window_close_on_user_interrupt
        self._deferred_external_window_close_on_user_interrupt = False
        if should_close_on_stop_request and self._external_video_window is not None:
            self._deferred_external_window_close = False
            self._close_external_video_window_silently(
                reason='video_playback_stopped_stop_request',
                status_message='Video playback stopped.',
            )
            return
        if should_close_deferred_window and self._close_external_video_window_if_deferred(
            reason='video_playback_stopped',
            status_message='Embedded host enabled.',
        ):
            return
        if self._deferred_external_window_close:
            self._update_video_status('Embedded host scheduled when the current video session ends.')
            return
        self._update_video_status('Video playback stopped.')

    def _on_video_playback_error(self, data: Any | None = None) -> None:
        message = ''
        if isinstance(data, dict):
            message = str(data.get('user_message') or data.get('error') or '').strip()
        self._pending_video_request = None
        self._video_session_active = False
        self._close_external_window_on_stop_request = False
        self._deferred_external_window_close_on_user_interrupt = False
        self._stop_hwnd_retry_timer()
        if self._close_external_video_window_if_deferred(
            reason='video_playback_error',
            status_message='Embedded host enabled.',
        ):
            return
        self._update_video_status(message or 'Video playback error.')

    def _close_external_video_window_silently(
        self,
        *,
        reason: str,
        status_message: str,
        clear_pending_video_request: bool = True,
    ) -> None:
        if self._external_video_window is None:
            return
        suppress = getattr(self._external_video_window, 'suppress_close_notification_once', None)
        if callable(suppress):
            suppress()
        destroy = getattr(self._external_video_window, 'destroy', None)
        if callable(destroy):
            destroy()
        self._external_video_window = None
        self._video_hwnd = None
        if clear_pending_video_request:
            self._pending_video_request = None
        self._stop_hwnd_retry_timer()
        self._close_external_window_on_stop_request = False
        self._last_video_ready_signature = None
        self._last_video_surface_signature = None
        logger.debug('wx MainView external video window closed silently (%s).', reason)
        self._update_video_status(status_message)

    def _close_external_video_window_if_deferred(
        self,
        *,
        reason: str,
        status_message: str,
        clear_pending_video_request: bool = True,
        refresh_force: bool = True,
    ) -> bool:
        """Close a deferred external window without targeting the removed video page.

        Edge cases:
        1. External-only builds no longer register the video page, so teardown must not call ``_switch_view('video')`` after the page removal.
        2. Embedded-host refreshes were removed with the internal video page, so deferred teardown must stay external-window only.
        3. Duplicate interruption or end events must remain idempotent, so deferred flags are cleared before any close side effect runs.
        """
        if not self._deferred_external_window_close or self._external_video_window is None:
            return False
        self._deferred_external_window_close = False
        self._deferred_external_window_close_on_user_interrupt = False
        self._close_external_video_window_silently(
            reason=reason,
            status_message=status_message,
            clear_pending_video_request=clear_pending_video_request,
        )
        return True

    def _on_video_playback_ended(self, _data: Any | None = None) -> None:
        self._pending_video_request = None
        self._video_session_active = False
        self._close_external_window_on_stop_request = False
        self._deferred_external_window_close_on_user_interrupt = False
        self._stop_hwnd_retry_timer()
        if self._close_external_video_window_if_deferred(
            reason='video_playback_ended_deferred',
            status_message='Embedded host enabled.',
        ):
            return
        if self._external_video_window is not None:
            self._close_external_video_window_silently(
                reason='video_playback_ended',
                status_message='Video playback ended.',
            )
            return
        self._update_video_status('Video playback ended.')

    def _on_audio_playback_started(self, data: Any | None = None) -> None:
        self._video_session_active = False
        self._deferred_external_window_close = False
        self._close_external_window_on_stop_request = False
        self._deferred_external_window_close_on_user_interrupt = False
        if self._external_video_window is not None:
            self._close_external_video_window_silently(
                reason='audio_playback_started',
                status_message='Audio playback started.',
            )
            return
        path = ''
        if isinstance(data, dict):
            path = str(data.get('path') or '').strip()
        if path:
            self._update_video_status(f'Audio playback started: {path}')

    def _on_video_window_closed(self, _data: Any | None = None) -> None:
        self._pending_video_request = None
        self._video_session_active = False
        self._video_hwnd = None
        self._deferred_external_window_close = False
        self._close_external_window_on_stop_request = False
        self._deferred_external_window_close_on_user_interrupt = False
        self._stop_hwnd_retry_timer()
        self._update_video_status('Video window closed.')

    def _handle_external_video_window_closed(self) -> None:
        self._video_hwnd = None
        self._external_video_window = None
        self._pending_video_request = None
        self._video_session_active = False
        self._deferred_external_window_close = False
        self._close_external_window_on_stop_request = False
        self._deferred_external_window_close_on_user_interrupt = False
        self._stop_hwnd_retry_timer()
        publish = getattr(self._event_bus, 'publish', None)
        if callable(publish):
            try:
                publish(AudioEventType.VIDEO_WINDOW_CLOSED, {'source': 'wx_external_window'})
            except MAIN_VIEW_EXCEPTIONS:
                logger.debug('wx MainView VIDEO_WINDOW_CLOSED publish failed.', exc_info=True)
        self._update_video_status('External video window closed.')

    def _on_video_hwnd_ready(self, hwnd: int) -> None:
        try:
            self._video_hwnd = int(hwnd)
        except MAIN_VIEW_EXCEPTIONS:
            self._video_hwnd = None
            return
        self._try_dispatch_pending_video_request()

    def _on_video_surface_changed(self, data: Any | None = None) -> None:
        if not isinstance(data, dict):
            return
        try:
            signature = (int(data.get('hwnd') or 0), int(data.get('width') or 0), int(data.get('height') or 0))
        except MAIN_VIEW_EXCEPTIONS:
            return
        if signature == self._last_video_surface_signature:
            return
        self._last_video_surface_signature = signature
        publish = getattr(self._event_bus, 'publish', None)
        if callable(publish):
            try:
                publish(AudioEventType.VIDEO_WINDOW_MOVED, {'hwnd': signature[0], 'width': signature[1], 'height': signature[2]})
            except MAIN_VIEW_EXCEPTIONS:
                logger.debug('wx MainView VIDEO_WINDOW_MOVED publish failed.', exc_info=True)

    def _on_ui_toggle_fullscreen(self, _data: Any | None = None) -> None:
        target = self._external_video_window.frame if self._use_external_video_window and self._external_video_window is not None else self._frame
        if target is None:
            return
        is_full_screen = getattr(target, 'IsFullScreen', None)
        show_full_screen = getattr(target, 'ShowFullScreen', None)
        if callable(is_full_screen) and callable(show_full_screen):
            show_full_screen(not bool(is_full_screen()))
        self.refresh_video_surface(force=True)

    def _stop_hwnd_retry_timer(self) -> None:
        timer = self._hwnd_retry_timer
        self._hwnd_retry_timer = None
        stop = getattr(timer, 'Stop', None)
        if callable(stop):
            stop()

    def _update_video_status(self, message: str) -> None:
        if message:
            logger.debug('wx MainView video status: %s', message)

    def _unsubscribe_all_events(self) -> None:
        unsubscribe = getattr(self._event_bus, 'unsubscribe', None)
        if not callable(unsubscribe):
            self._event_subscriptions.clear()
            return
        for event_type, subscription in list(self._event_subscriptions):
            try:
                unsubscribe(event_type, subscription=subscription)
            except MAIN_VIEW_EXCEPTIONS:
                try:
                    unsubscribe(event_type, callback=subscription)
                except MAIN_VIEW_EXCEPTIONS:
                    logger.debug('wx MainView event unsubscribe failed for %s.', event_type, exc_info=True)
        self._event_subscriptions.clear()

    def shutdown(self) -> None:
        if self._is_shutting_down:
            return
        self._is_shutting_down = True
        self._stop_hwnd_retry_timer()
        self._unsubscribe_all_events()
        if self._external_video_window is not None:
            suppress = getattr(self._external_video_window, 'suppress_close_notification_once', None)
            if callable(suppress):
                suppress()
            destroy = getattr(self._external_video_window, 'destroy', None)
            if callable(destroy):
                destroy()
            self._external_video_window = None
        if self._mini_player is not None:
            close_mini_player = getattr(self._mini_player, 'close', None)
            if callable(close_mini_player):
                close_mini_player()
            self._mini_player = None
        for page in tuple(self._pages.values()):
            owner = getattr(getattr(page, 'panel', None), 'owner', None)
            shutdown = getattr(owner, 'shutdown', None)
            if callable(shutdown):
                shutdown()
        if self._frame is not None:
            destroy = getattr(self._frame, 'Destroy', None)
            if callable(destroy):
                destroy()
            self._frame = None
        self._pages.clear()

    def close(self) -> None:
        self.shutdown()

    def destroy(self) -> None:
        self.shutdown()

    @property
    def window(self) -> Any:
        return self._frame

    @property
    def current_view(self) -> str:
        return self._current_view

    @property
    def is_shutting_down(self) -> bool:
        return self._is_shutting_down

    def get_view(self, view_name: str) -> Any | None:
        page = self._pages.get(view_name)
        if page is None:
            return None
        return page.panel

    def get_stats(self) -> dict[str, Any]:
        return {
            'total_views': len(VIEW_LABELS),
            'current_view': self._current_view,
            'is_shutting_down': self._is_shutting_down,
            'video_external_window': self._use_external_video_window,
        }
