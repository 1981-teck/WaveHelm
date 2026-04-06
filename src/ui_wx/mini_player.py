from __future__ import annotations

import importlib
import logging
from typing import Any, Callable

from src.audio.audio_event_models import AudioEventType
from src.controller.playback_status import get_duration, get_position, is_video_current, seek_to
from src.ui_wx.common import (
    WX_CALLBACK_EXCEPTIONS,
    apply_colors,
    create_flow_sizer,
    get_localized_text,
    get_theme_colors,
    register_callback,
    set_label_text,
    unregister_callback,
)
from src.ui_wx.mini_player_shared import format_time, resolve_track_title

logger = logging.getLogger(__name__)

_INTERRUPT_ACTION_EVENTS = {
    'stop': AudioEventType.STOP_REQUESTED,
    'previous': AudioEventType.PREVIOUS_REQUESTED,
    'next': AudioEventType.NEXT_REQUESTED,
}


class MiniPlayer:
    """wx-based mini player for the temporary migration shell.

    Edge cases handled deterministically:
    1. Stale playback progress from a previous track is ignored using the current media path snapshot.
    2. Missing timer helpers or controller hooks degrade to manual refresh without breaking playback controls.
    3. Event bus callbacks are marshalled through wx.CallAfter when available to avoid cross-thread UI updates.
    4. Progress bar pointer seeks clamp zero-width surfaces and out-of-range mouse coordinates before issuing seek requests.
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
        self._build_ui()
        self._bind_controls()
        self._register_callbacks()
        self._subscribe_events()
        self._sync_initial_state()
        self.update_localization()
        self.update_theme_colors()
        self._schedule_progress_timer()

    def _build_ui(self) -> None:
        wx = self._wx
        root = wx.BoxSizer(wx.VERTICAL)
        self.track_label = wx.StaticText(self.panel, label='WaveHelm')
        self.state_label = wx.StaticText(self.panel, label='--')
        info_row = wx.BoxSizer(wx.HORIZONTAL)
        info_row.Add(self.track_label, 1, wx.ALL | wx.EXPAND, 6)
        info_row.Add(self.state_label, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 6)
        self.shuffle_button = wx.Button(self.panel, label='Shuffle')
        self.loop_button = wx.Button(self.panel, label='Loop')
        self.prev_button = wx.Button(self.panel, label='Prev')
        self.play_button = wx.Button(self.panel, label='Play')
        self.pause_button = wx.Button(self.panel, label='Pause')
        self.next_button = wx.Button(self.panel, label='Next')
        self.stop_button = wx.Button(self.panel, label='Stop')
        self.volume_caption = wx.StaticText(self.panel, label='Volume')
        self.volume_slider = wx.Slider(self.panel, value=100, minValue=0, maxValue=100)
        self.mute_button = wx.Button(self.panel, label='Mute')
        controls_row = create_flow_sizer(wx)
        for widget in (
            self.shuffle_button,
            self.loop_button,
            self.prev_button,
            self.play_button,
            self.pause_button,
            self.next_button,
            self.stop_button,
            self.volume_caption,
            self.volume_slider,
            self.mute_button,
        ):
            controls_row.Add(widget, 0 if widget is not self.volume_slider else 1, wx.ALL | wx.EXPAND, 4)
        self.time_left_label = wx.StaticText(self.panel, label='--:--')
        self.progress_slider = wx.Slider(self.panel, value=0, minValue=0, maxValue=1000)
        self.time_right_label = wx.StaticText(self.panel, label='--:--')
        progress_row = wx.BoxSizer(wx.HORIZONTAL)
        progress_row.Add(self.time_left_label, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 6)
        progress_row.Add(self.progress_slider, 1, wx.ALL | wx.EXPAND, 4)
        progress_row.Add(self.time_right_label, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 6)
        root.Add(info_row, 0, wx.ALL | wx.EXPAND, 0)
        root.Add(controls_row, 0, wx.ALL | wx.EXPAND, 0)
        root.Add(progress_row, 0, wx.ALL | wx.EXPAND, 0)
        self.panel.SetSizer(root)

    def _bind_controls(self) -> None:
        wx = self._wx
        self.shuffle_button.Bind(wx.EVT_BUTTON, lambda _event: self._defer_player_action('toggle_shuffle'))
        self.loop_button.Bind(wx.EVT_BUTTON, lambda _event: self._defer_player_action('toggle_loop'))
        self.prev_button.Bind(wx.EVT_BUTTON, lambda _event: self._defer_player_action('previous'))
        self.play_button.Bind(wx.EVT_BUTTON, lambda _event: self._defer_player_action('play_action'))
        self.pause_button.Bind(wx.EVT_BUTTON, lambda _event: self._defer_player_action('pause'))
        self.next_button.Bind(wx.EVT_BUTTON, lambda _event: self._defer_player_action('next'))
        self.stop_button.Bind(wx.EVT_BUTTON, lambda _event: self._defer_player_action('stop'))
        self.mute_button.Bind(wx.EVT_BUTTON, self._on_mute_clicked)
        slider_event = getattr(wx, 'EVT_SLIDER', wx.EVT_BUTTON)
        self.volume_slider.Bind(slider_event, self._on_volume_slider_changed)
        self.progress_slider.Bind(slider_event, self._on_progress_slider_changed)
        pointer_down_event = getattr(wx, 'EVT_LEFT_DOWN', None)
        if pointer_down_event is not None:
            self.progress_slider.Bind(pointer_down_event, self._on_progress_slider_pointer_down)

    def _register_callbacks(self) -> None:
        register_callback(
            self.localization_manager,
            'register_language_change_callback',
            self.update_localization,
            logger=logger,
            message='Unable to register wx MiniPlayer language callback.',
        )
        register_callback(
            self.theme_manager,
            'register_theme_change_callback',
            self.update_theme_colors,
            logger=logger,
            message='Unable to register wx MiniPlayer theme callback.',
        )

    def _subscribe_events(self) -> None:
        subscribe = getattr(self.event_bus, 'subscribe', None)
        if not callable(subscribe):
            return
        bindings = {
            AudioEventType.PLAYBACK_PROGRESS: self._handle_progress_event,
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
        state_getter = getattr(self.player_controller, 'get_current_player_state_payload', None)
        if callable(state_getter):
            try:
                payload = state_getter() or {}
            except WX_CALLBACK_EXCEPTIONS:
                payload = {}
            self._apply_player_state(payload if isinstance(payload, dict) else {})
        self._update_time_labels()

    def _read_initial_volume(self) -> float:
        getter = getattr(self.audio_engine, 'get_volume', None)
        if not callable(getter):
            return 1.0
        try:
            volume = float(getter() or 1.0)
        except (OverflowError, TypeError, ValueError) + WX_CALLBACK_EXCEPTIONS:
            logger.debug('wx MiniPlayer volume bootstrap failed.', exc_info=True)
            return 1.0
        return max(0.0, min(1.0, volume))

    def _schedule_progress_timer(self) -> None:
        if self._closed or self._timer_running:
            return
        call_later = getattr(self._wx, 'CallLater', None)
        if not callable(call_later):
            return
        self._timer_running = True
        self._progress_timer = call_later(200, self._on_progress_timer_tick)

    def _on_progress_timer_tick(self) -> None:
        self._timer_running = False
        if self._closed:
            return
        self._poll_progress()
        self._schedule_progress_timer()

    def _poll_progress(self) -> None:
        if self._dragging or self.player_controller is None:
            return
        duration = get_duration(self.player_controller) or self._current_duration
        position = get_position(self.player_controller) or self._current_position
        self._current_duration = max(0.0, float(duration or 0.0))
        self._current_position = max(0.0, float(position or 0.0))
        self._sync_progress_slider()
        self._update_time_labels()

    def _defer_ui(self, callback: Callable[..., None], *args: Any) -> None:
        call_after = getattr(self._wx, 'CallAfter', None)
        if callable(call_after):
            call_after(callback, *args)
            return
        callback(*args)

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

    def _handle_progress_event(self, payload: Any) -> None:
        self._defer_ui(self._apply_progress_payload, payload if isinstance(payload, dict) else {})

    def _handle_state_event(self, payload: Any) -> None:
        self._defer_ui(self._apply_player_state, payload if isinstance(payload, dict) else {})

    def _handle_volume_event(self, payload: Any) -> None:
        self._defer_ui(self._apply_volume_payload, payload if isinstance(payload, dict) else {})

    def _handle_mute_event(self, payload: Any) -> None:
        self._defer_ui(self._apply_mute_payload, payload if isinstance(payload, dict) else {})

    def _handle_shuffle_event(self, payload: Any) -> None:
        self._defer_ui(self._apply_shuffle_payload, payload if isinstance(payload, dict) else {})

    def _handle_loop_event(self, payload: Any) -> None:
        self._defer_ui(self._apply_loop_payload, payload if isinstance(payload, dict) else {})

    def _handle_video_duration_event(self, payload: Any) -> None:
        self._defer_ui(self._apply_video_duration_payload, payload if isinstance(payload, dict) else {})

    def _apply_player_state(self, payload: dict[str, Any]) -> None:
        if not payload:
            return
        track = payload.get('current_track')
        title = resolve_track_title(track)
        if title:
            self._current_track_title = title
            self._current_media_path = self._extract_path(track)
        self._is_playing = bool(payload.get('playing', False))
        if payload.get('reset_progress') or (payload.get('stopped') and not title):
            self._reset_progress_state(clear_path=True)
        self._sync_enabled_buttons(payload)
        self._apply_shuffle_payload(payload)
        self._apply_loop_payload(payload)
        self._refresh_track_and_state_text(payload)

    def _extract_path(self, track: Any) -> str:
        if isinstance(track, dict):
            value = track.get('path')
        else:
            value = getattr(track, 'path', None)
        return str(value or '').strip()

    def _sync_enabled_buttons(self, payload: dict[str, Any]) -> None:
        state_map = {
            self.play_button: payload.get('play'),
            self.pause_button: payload.get('pause'),
            self.stop_button: payload.get('stop'),
            self.next_button: payload.get('next'),
            self.prev_button: payload.get('prev'),
        }
        for widget, state in state_map.items():
            self._set_enabled(widget, state != 'disabled')

    def _set_enabled(self, widget: Any, enabled: bool) -> None:
        setter = getattr(widget, 'Enable', None)
        if callable(setter):
            setter(bool(enabled))

    def _refresh_track_and_state_text(self, payload: dict[str, Any] | None = None) -> None:
        title = self._current_track_title or get_localized_text(
            self.localization_manager,
            'mini_player_no_media',
            'No media loaded in MiniPlayer.',
        )
        set_label_text(self.track_label, title)
        if (payload and payload.get('playing')) or self._is_playing:
            state = get_localized_text(self.localization_manager, 'mini_player_state_playing', 'MiniPlayer state: playing.')
        else:
            state = get_localized_text(self.localization_manager, 'mini_player_state_paused_stopped', 'MiniPlayer state: paused/stopped.')
        set_label_text(self.state_label, state)

    def _apply_progress_payload(self, payload: dict[str, Any]) -> None:
        path_value = str(payload.get('path') or '').strip()
        if path_value and self._current_media_path and path_value.lower() != self._current_media_path.lower():
            return
        if path_value:
            self._current_media_path = path_value
        self._current_position = max(0.0, self._safe_float(payload.get('current_time', payload.get('position', 0.0))))
        duration = payload.get('total_duration', payload.get('duration'))
        if duration is not None:
            self._current_duration = max(0.0, self._safe_float(duration))
        self._sync_progress_slider()
        self._update_time_labels()

    def _apply_video_duration_payload(self, payload: dict[str, Any]) -> None:
        duration = payload.get('duration')
        if duration is None:
            return
        path_value = str(payload.get('path') or '').strip()
        if path_value and self._current_media_path and path_value.lower() != self._current_media_path.lower():
            return
        if path_value:
            self._current_media_path = path_value
        self._current_duration = max(0.0, self._safe_float(duration))
        self._sync_progress_slider()
        self._update_time_labels()

    def _safe_float(self, value: Any) -> float:
        try:
            return float(value or 0.0)
        except (OverflowError, TypeError, ValueError):
            return 0.0

    def _apply_volume_payload(self, payload: dict[str, Any]) -> None:
        volume = payload.get('volume')
        if volume is None:
            return
        try:
            value = max(0.0, min(1.0, float(volume)))
        except (OverflowError, TypeError, ValueError):
            return
        self._set_volume_slider(value)

    def _set_volume_slider(self, value: float) -> None:
        if value > 0.0:
            self._last_nonzero_volume = value
        self._volume_sync = True
        self.volume_slider.SetValue(int(round(value * 100)))
        self._volume_sync = False

    def _apply_mute_payload(self, payload: dict[str, Any]) -> None:
        self._muted = bool(payload.get('muted', False))
        self._refresh_mute_label()

    def _apply_shuffle_payload(self, payload: dict[str, Any]) -> None:
        self._shuffle_enabled = bool(payload.get('shuffle_enabled', payload.get('shuffled', self._shuffle_enabled)))
        self._refresh_toggle_labels()

    def _apply_loop_payload(self, payload: dict[str, Any]) -> None:
        self._loop_enabled = bool(payload.get('loop_enabled', self._loop_enabled))
        self._refresh_toggle_labels()

    def _refresh_toggle_labels(self) -> None:
        shuffle = get_localized_text(self.localization_manager, 'btn_shuffle', 'Shuffle')
        loop = get_localized_text(self.localization_manager, 'btn_loop', 'Loop')
        set_label_text(self.shuffle_button, f'{shuffle} ✓' if self._shuffle_enabled else shuffle)
        set_label_text(self.loop_button, f'{loop} ✓' if self._loop_enabled else loop)

    def _refresh_mute_label(self) -> None:
        mute = get_localized_text(self.localization_manager, 'tooltip_mute', 'Mute/Unmute')
        suffix = ' ✓' if self._muted else ''
        set_label_text(self.mute_button, f'{mute}{suffix}')

    def _reset_progress_state(self, *, clear_path: bool) -> None:
        self._current_position = 0.0
        self._current_duration = 0.0
        if clear_path:
            self._current_media_path = ''
        self._sync_progress_slider()
        self._update_time_labels()

    def _sync_progress_slider(self) -> None:
        if self._dragging:
            return
        value = 0
        if self._current_duration > 0:
            ratio = max(0.0, min(1.0, self._current_position / self._current_duration))
            value = int(round(ratio * 1000))
        self._updating_progress_slider = True
        self.progress_slider.SetValue(value)
        self._updating_progress_slider = False

    def _update_time_labels(self) -> None:
        set_label_text(self.time_left_label, format_time(self._current_position))
        right_value = self._current_duration or self._current_position
        set_label_text(self.time_right_label, format_time(right_value))

    def _set_progress_slider_value(self, ratio: float) -> None:
        slider_value = int(round(max(0.0, min(1.0, ratio)) * 1000.0))
        self._updating_progress_slider = True
        self.progress_slider.SetValue(slider_value)
        self._updating_progress_slider = False

    def _seek_from_progress_ratio(self, ratio: float) -> bool:
        if self._current_duration <= 0:
            return False
        target = max(0.0, min(1.0, ratio)) * self._current_duration
        self._current_position = target
        self._update_time_labels()
        return bool(seek_to(self.player_controller, target))

    def _pointer_ratio_from_progress_event(self, event: Any | None) -> float | None:
        if event is None:
            return None
        position_getter = getattr(event, 'GetX', None)
        if callable(position_getter):
            raw_position = position_getter()
        else:
            point_getter = getattr(event, 'GetPosition', None)
            raw_position = point_getter() if callable(point_getter) else None
        if isinstance(raw_position, tuple):
            raw_position = raw_position[0] if raw_position else None
        elif hasattr(raw_position, 'x'):
            raw_position = getattr(raw_position, 'x')
        try:
            pointer_x = float(raw_position)
        except (OverflowError, TypeError, ValueError):
            return None
        size_getter = getattr(self.progress_slider, 'GetClientSize', None)
        slider_size = size_getter() if callable(size_getter) else None
        if slider_size is None:
            size_getter = getattr(self.progress_slider, 'GetSize', None)
            slider_size = size_getter() if callable(size_getter) else None
        if isinstance(slider_size, tuple):
            slider_width = slider_size[0] if slider_size else 0
        else:
            slider_width = getattr(slider_size, 'width', getattr(slider_size, 'x', 0))
        try:
            width = float(slider_width)
        except (OverflowError, TypeError, ValueError):
            return None
        if width <= 1.0:
            return None
        clamped_x = max(0.0, min(width - 1.0, pointer_x))
        return clamped_x / (width - 1.0)

    def _on_progress_slider_changed(self, _event: Any | None = None) -> None:
        if self._updating_progress_slider:
            return
        self._dragging = True
        ratio = float(self.progress_slider.GetValue()) / 1000.0
        self._seek_from_progress_ratio(ratio)
        self._dragging = False
        self._sync_progress_slider()

    def _on_progress_slider_pointer_down(self, event: Any | None = None) -> None:
        if self._updating_progress_slider:
            return
        ratio = self._pointer_ratio_from_progress_event(event)
        if ratio is None or self._current_duration <= 0:
            skipper = getattr(event, 'Skip', None)
            if callable(skipper):
                skipper()
            return
        self._dragging = True
        self._set_progress_slider_value(ratio)
        self._seek_from_progress_ratio(ratio)
        self._dragging = False
        self._sync_progress_slider()

    def _on_volume_slider_changed(self, _event: Any | None = None) -> None:
        if self._volume_sync:
            return
        value = max(0.0, min(1.0, float(self.volume_slider.GetValue()) / 100.0))
        if value > 0.0:
            self._last_nonzero_volume = value
        setter = getattr(self.player_controller, 'set_volume', None)
        if callable(setter):
            setter(value)
        if self._muted and value > 0.0:
            mute_setter = getattr(self.audio_engine, 'set_mute', None)
            if callable(mute_setter):
                mute_setter(False)
            self._muted = False
            self._refresh_mute_label()

    def _on_mute_clicked(self, _event: Any | None = None) -> None:
        new_muted = not self._muted
        if is_video_current(self.player_controller):
            target = 0.0 if new_muted else self._last_nonzero_volume
            setter = getattr(self.player_controller, 'set_volume', None)
            if callable(setter):
                setter(target)
            self._set_volume_slider(target)
            self._muted = new_muted
            self._refresh_mute_label()
            return
        mute_setter = getattr(self.audio_engine, 'set_mute', None)
        if callable(mute_setter):
            mute_setter(new_muted)
        self._muted = new_muted
        self._refresh_mute_label()

    def update_localization(self, *_: Any) -> None:
        set_label_text(self.volume_caption, get_localized_text(self.localization_manager, 'btn_volume', 'Volume'))
        set_label_text(self.prev_button, get_localized_text(self.localization_manager, 'btn_prev', 'Previous'))
        set_label_text(self.play_button, get_localized_text(self.localization_manager, 'btn_play', 'Play'))
        set_label_text(self.pause_button, get_localized_text(self.localization_manager, 'btn_pause', 'Pause'))
        set_label_text(self.next_button, get_localized_text(self.localization_manager, 'btn_next', 'Next'))
        set_label_text(self.stop_button, get_localized_text(self.localization_manager, 'btn_stop', 'Stop'))
        self._refresh_toggle_labels()
        self._refresh_mute_label()
        self._refresh_track_and_state_text()

    def update_theme_colors(self, *_: Any) -> None:
        colors = get_theme_colors(self.theme_manager)
        background = colors.get('footer_bg') or colors.get('panel_bg') or colors.get('bg_color')
        foreground = colors.get('text_color')
        accent = colors.get('button_color') or background
        widgets = [
            self.panel,
            self.track_label,
            self.state_label,
            self.volume_caption,
            self.time_left_label,
            self.progress_slider,
            self.time_right_label,
            self.volume_slider,
        ]
        for widget in widgets:
            apply_colors(widget, background=background, foreground=foreground)
        for button in (
            self.shuffle_button,
            self.loop_button,
            self.prev_button,
            self.play_button,
            self.pause_button,
            self.next_button,
            self.stop_button,
            self.mute_button,
        ):
            apply_colors(button, background=accent, foreground=foreground)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
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
        stopper = getattr(self._progress_timer, 'Stop', None)
        if callable(stopper):
            stopper()
        self._progress_timer = None
        self._timer_running = False
