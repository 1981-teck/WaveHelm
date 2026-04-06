from __future__ import annotations

import importlib
import logging
from typing import Any

from src.audio.audio_event_models import AudioEventType
from src.ui_wx.common import (
    WX_CALLBACK_EXCEPTIONS,
    apply_colors,
    autosize_choice_control,
    get_localized_text,
    get_theme_colors,
    open_directory,
    register_callback,
    set_label_text,
    unregister_callback,
)

logger = logging.getLogger(__name__)

MESSAGE_BOX_YES = 1
MESSAGE_BOX_NO = 0


class SettingsView:
    """wx-based general settings and maintenance view.

    Edge cases handled deterministically:
    1. Missing controller APIs degrade to best-effort persistence without crashing the page.
    2. Unsupported language/theme lists fall back to canonical defaults so the selectors stay usable.
    3. Maintenance cleanup results may be ints or mappings; totals are normalized before user feedback.
    """

    def __init__(
        self,
        parent: Any,
        settings_controller: Any = None,
        localization_manager: Any = None,
        settings_manager: Any = None,
        theme_manager: Any = None,
        event_bus: Any = None,
        video_controller: Any = None,
        player_controller: Any = None,
        **_: Any,
    ) -> None:
        self.parent = parent
        self.settings_controller = settings_controller
        self.localization_manager = localization_manager
        self.settings_manager = settings_manager
        self.theme_manager = theme_manager
        self.event_bus = event_bus
        self.video_controller = video_controller
        self.player_controller = player_controller
        self._video_event_subscriptions: list[tuple[Any, Any]] = []
        self._audio_track_stream_indices: tuple[int, ...] = ()
        self._subtitle_track_ids: tuple[int, ...] = ()
        self._wx = self._import_wx_module()
        self.panel = self._wx.ScrolledWindow(parent) if hasattr(self._wx, 'ScrolledWindow') else self._wx.Panel(parent)
        self.panel.owner = self
        self._language_codes: list[str] = []
        self._theme_names: list[str] = []
        self._build_ui()
        self._bind_events()
        self._register_callbacks()
        self._subscribe_video_events()
        self._load_state()
        self.update_localization()
        self.update_theme_colors()

    @staticmethod
    def _import_wx_module() -> Any:
        return importlib.import_module('wx')

    def _build_ui(self) -> None:
        """Build the settings page with video track controls relocated from the video view.

        Edge cases handled deterministically:
        1. The current runtime can have no active video session, so video track selectors must remain visible but disabled without raising.
        2. External-only playback removes host toggles from the UX, so only audio/subtitle selectors are exposed here while maintenance controls remain unchanged.
        3. wx sizer refreshes can occur repeatedly during localization/theme updates, so the root layout is constructed exactly once and reused.
        """
        wx = self._wx
        if hasattr(self.panel, 'SetScrollRate'):
            self.panel.SetScrollRate(16, 16)
        root = wx.BoxSizer(wx.VERTICAL)
        self.title_label = wx.StaticText(self.panel, label='')
        self.language_label = wx.StaticText(self.panel, label='')
        self.language_choice = wx.Choice(self.panel)
        self.volume_text_label = wx.StaticText(self.panel, label='')
        self.volume_slider = wx.Slider(self.panel, value=70, minValue=0, maxValue=100)
        self.volume_value_label = wx.StaticText(self.panel, label='70%')
        self.theme_label = wx.StaticText(self.panel, label='')
        self.theme_choice = wx.Choice(self.panel)
        self.video_section_title_label = wx.StaticText(self.panel, label='')
        self.video_section_note_label = wx.StaticText(self.panel, label='')
        self.video_audio_track_label = wx.StaticText(self.panel, label='')
        self.video_audio_track_choice = wx.Choice(self.panel)
        self.video_subtitle_track_label = wx.StaticText(self.panel, label='')
        self.video_subtitle_track_choice = wx.Choice(self.panel)
        self.video_track_feedback_label = wx.StaticText(self.panel, label='')
        self.maintenance_title_label = wx.StaticText(self.panel, label='')
        self.maintenance_note_label = wx.StaticText(self.panel, label='')
        self.open_app_data_button = wx.Button(self.panel, label='')
        self.open_processed_audio_button = wx.Button(self.panel, label='')
        self.open_logs_button = wx.Button(self.panel, label='')
        self.open_third_party_notices_button = wx.Button(self.panel, label='')
        self.clear_processed_audio_button = wx.Button(self.panel, label='')
        self.clear_runtime_button = wx.Button(self.panel, label='')
        self.feedback_label = wx.StaticText(self.panel, label='')
        widgets = (
            self.title_label,
            self._build_row(self.language_label, self.language_choice),
            self._build_volume_row(),
            self._build_row(self.theme_label, self.theme_choice),
            self.video_section_title_label,
            self.video_section_note_label,
            self._build_row(self.video_audio_track_label, self.video_audio_track_choice),
            self._build_row(self.video_subtitle_track_label, self.video_subtitle_track_choice),
            self.video_track_feedback_label,
            self.maintenance_title_label,
            self.maintenance_note_label,
            self.open_app_data_button,
            self.open_processed_audio_button,
            self.open_logs_button,
            self.open_third_party_notices_button,
            self.clear_processed_audio_button,
            self.clear_runtime_button,
            self.feedback_label,
        )
        for widget in widgets:
            root.Add(widget, 0, wx.ALL | wx.EXPAND, 8)
        self.panel.SetSizer(root)

    def _build_row(self, label: Any, control: Any) -> Any:
        row = self._wx.BoxSizer(self._wx.HORIZONTAL)
        row.Add(label, 0, self._wx.ALL | self._wx.ALIGN_CENTER_VERTICAL, 0)
        row.Add(control, 1, self._wx.ALL | self._wx.EXPAND, 8)
        return row

    def _build_volume_row(self) -> Any:
        row = self._wx.BoxSizer(self._wx.HORIZONTAL)
        row.Add(self.volume_text_label, 0, self._wx.ALL | self._wx.ALIGN_CENTER_VERTICAL, 0)
        row.Add(self.volume_slider, 1, self._wx.ALL | self._wx.EXPAND, 8)
        row.Add(self.volume_value_label, 0, self._wx.ALL | self._wx.ALIGN_CENTER_VERTICAL, 0)
        return row

    def _bind_events(self) -> None:
        self.language_choice.Bind(self._wx.EVT_CHOICE, self._on_language_changed)
        self.theme_choice.Bind(self._wx.EVT_CHOICE, self._on_theme_changed)
        self.volume_slider.Bind(self._wx.EVT_SLIDER, self._on_volume_changed)
        self.video_audio_track_choice.Bind(self._wx.EVT_CHOICE, self._on_video_audio_track_changed)
        self.video_subtitle_track_choice.Bind(self._wx.EVT_CHOICE, self._on_video_subtitle_track_changed)
        self.open_app_data_button.Bind(self._wx.EVT_BUTTON, self._on_open_app_data)
        self.open_processed_audio_button.Bind(self._wx.EVT_BUTTON, self._on_open_processed_audio)
        self.open_logs_button.Bind(self._wx.EVT_BUTTON, self._on_open_logs)
        self.open_third_party_notices_button.Bind(self._wx.EVT_BUTTON, self._on_open_third_party_notices)
        self.clear_processed_audio_button.Bind(self._wx.EVT_BUTTON, self._on_clear_processed_audio)
        self.clear_runtime_button.Bind(self._wx.EVT_BUTTON, self._on_clear_runtime)

    def _register_callbacks(self) -> None:
        register_callback(
            self.localization_manager,
            'register_language_change_callback',
            self.update_localization,
            logger=logger,
            message='Unable to register wx SettingsView language callback.',
        )
        register_callback(
            self.theme_manager,
            'register_theme_change_callback',
            self.update_theme_colors,
            logger=logger,
            message='Unable to register wx SettingsView theme callback.',
        )

    def _subscribe_video_events(self) -> None:
        """Track video-session changes so relocated audio/subtitle selectors stay in sync.

        Edge cases handled deterministically:
        1. The settings page can exist without an event bus, so subscriptions degrade to a safe no-op.
        2. Video lifecycle events can arrive in quick succession, so all handlers funnel into a single bounded refresh path.
        3. Legacy buses may return callback objects or explicit subscription tokens, so the shutdown path stores whatever subscribe returns.
        """
        subscribe = getattr(self.event_bus, 'subscribe', None)
        if not callable(subscribe):
            return
        subscriptions = (
            AudioEventType.PREPARE_VIDEO_PLAYBACK,
            AudioEventType.VIDEO_PLAYBACK_READY,
            AudioEventType.VIDEO_PLAYBACK_STARTED,
            AudioEventType.VIDEO_PLAYBACK_STOPPED,
            AudioEventType.VIDEO_PLAYBACK_ENDED,
            AudioEventType.VIDEO_PLAYBACK_ERROR,
            AudioEventType.CANCEL_VIDEO_PLAYBACK,
            AudioEventType.VIDEO_WINDOW_CLOSED,
            AudioEventType.PLAYBACK_STARTED,
        )
        for event_type in subscriptions:
            try:
                subscription = subscribe(event_type, self._on_video_state_changed)
            except WX_CALLBACK_EXCEPTIONS:
                logger.debug('Unable to subscribe SettingsView to %s.', event_type, exc_info=True)
                continue
            token = subscription if subscription is not None else self._on_video_state_changed
            self._video_event_subscriptions.append((event_type, token))

    def _resolve_video_controller(self) -> Any | None:
        if self.video_controller is not None:
            return self.video_controller
        video_controller = getattr(self.player_controller, 'video_controller', None)
        return video_controller if video_controller is not None else None

    def _refresh_video_track_controls(self) -> None:
        self._refresh_video_audio_track_controls()
        self._refresh_video_subtitle_track_controls()

    def _refresh_video_audio_track_controls(self) -> None:
        """Refresh the relocated audio-track selector from controller state.

        Edge cases handled deterministically:
        1. Active sessions can disappear between event delivery and UI refresh, so the selector falls back to a disabled empty state.
        2. Controller payloads can be malformed or duplicated, so stream ids are normalized before they drive selection.
        3. Track lists can rebuild after a language change, so labels are regenerated every refresh instead of trusting stale widget items.
        """
        controller = self._resolve_video_controller()
        getter = getattr(controller, 'get_audio_track_options', None)
        try:
            options = tuple(getter() or ()) if callable(getter) else ()
        except WX_CALLBACK_EXCEPTIONS:
            options = ()
        labels: list[str] = []
        stream_indices: list[int] = []
        selected_index = -1
        for option in options:
            if not isinstance(option, dict):
                continue
            try:
                stream_index = int(option.get('stream_index'))
            except (TypeError, ValueError):
                continue
            label = str(option.get('menu_label') or option.get('label') or f'Audio stream {stream_index}').strip()
            stream_indices.append(stream_index)
            labels.append(label)
            if option.get('selected') and selected_index < 0:
                selected_index = len(stream_indices) - 1
        self._audio_track_stream_indices = tuple(stream_indices)
        self.video_audio_track_choice.SetItems(labels)
        autosize_choice_control(self.video_audio_track_choice, labels)
        self.video_audio_track_choice.Enable(bool(labels))
        if labels:
            self.video_audio_track_choice.SetSelection(selected_index if selected_index >= 0 else 0)
            active_label = labels[self.video_audio_track_choice.GetSelection()]
            set_label_text(self.video_audio_track_label, f"{self._t('settings_video_audio_track', 'Video audio track')}: {active_label}")
            set_label_text(self.video_track_feedback_label, '')
            return
        self.video_audio_track_choice.SetSelection(-1)
        set_label_text(self.video_audio_track_label, self._t('settings_video_audio_track', 'Video audio track'))

    def _refresh_video_subtitle_track_controls(self) -> None:
        """Refresh the relocated subtitle selector from controller state.

        Edge cases handled deterministically:
        1. Subtitle sessions can legitimately have no text tracks, so the selector exposes a stable Off-only state instead of disappearing.
        2. Controllers can report invalid or duplicated ids, so track identifiers are normalized before they reach the widget.
        3. Disable state must survive event races, so the selector always keeps an explicit Off entry at index zero.
        """
        controller = self._resolve_video_controller()
        getter = getattr(controller, 'get_text_track_options', None)
        try:
            options = tuple(getter() or ()) if callable(getter) else ()
        except WX_CALLBACK_EXCEPTIONS:
            options = ()
        labels = [self._t('video_subtitles_off', 'Off')]
        track_ids = [0]
        selected_index = 0
        for option in options:
            if not isinstance(option, dict):
                continue
            try:
                track_id = int(option.get('track_id'))
            except (TypeError, ValueError):
                continue
            label = str(option.get('menu_label') or option.get('label') or f'Subtitle track {track_id}').strip()
            track_ids.append(track_id)
            labels.append(label)
            if option.get('selected'):
                selected_index = len(track_ids) - 1
        self._subtitle_track_ids = tuple(track_ids)
        self.video_subtitle_track_choice.SetItems(labels)
        autosize_choice_control(self.video_subtitle_track_choice, labels)
        self.video_subtitle_track_choice.Enable(len(labels) > 1)
        self.video_subtitle_track_choice.SetSelection(selected_index)
        active_label = labels[selected_index]
        set_label_text(self.video_subtitle_track_label, f"{self._t('settings_video_subtitles', 'Video subtitles')}: {active_label}")

    def _set_video_track_feedback(self, message: str, color: str = 'green') -> None:
        set_label_text(self.video_track_feedback_label, message)
        if self.event_bus is not None:
            self.event_bus.publish(AudioEventType.FEEDBACK_MESSAGE, {'message': message, 'color': color})

    def _on_video_state_changed(self, _payload: Any | None = None) -> None:
        self._refresh_video_track_controls()

    def _on_video_audio_track_changed(self, _event: Any | None = None) -> None:
        """Apply relocated audio-track changes through the video controller.

        Edge cases handled deterministically:
        1. Choice widgets can report invalid indices, so selection must be validated against normalized stream ids before calling the controller.
        2. Some backends support live track switching while others need a controlled reload path, so only the stable controller boundary is used here.
        3. Failed switches must rebuild widget state from controller truth instead of leaving the selector on a transient optimistic value.
        """
        selection = int(self.video_audio_track_choice.GetSelection())
        if selection < 0 or selection >= len(self._audio_track_stream_indices):
            self._refresh_video_audio_track_controls()
            return
        stream_index = int(self._audio_track_stream_indices[selection])
        controller = self._resolve_video_controller()
        selector = getattr(controller, 'select_audio_stream', None)
        changed = False
        if callable(selector):
            try:
                changed = bool(selector(stream_index))
            except WX_CALLBACK_EXCEPTIONS:
                changed = False
        self._refresh_video_track_controls()
        if changed:
            label = self.video_audio_track_choice.items[self.video_audio_track_choice.GetSelection()]
            self._set_video_track_feedback(f"Audio track switched: {label}")
            return
        self._set_video_track_feedback('Unable to change audio track.', 'orange')

    def _on_video_subtitle_track_changed(self, _event: Any | None = None) -> None:
        """Apply relocated subtitle changes through the video controller.

        Edge cases handled deterministically:
        1. Off selections must stay explicit, so index zero always maps to a disable request instead of a missing track id.
        2. Runtime enable/disable failures must restore the selector from controller state immediately.
        3. Subtitle lists can refresh while the user interacts, so the chosen id is revalidated against the normalized track-id cache before use.
        """
        selection = int(self.video_subtitle_track_choice.GetSelection())
        controller = self._resolve_video_controller()
        changed = False
        if selection <= 0:
            disabler = getattr(controller, 'disable_text_tracks', None)
            if callable(disabler):
                try:
                    changed = bool(disabler())
                except WX_CALLBACK_EXCEPTIONS:
                    changed = False
            self._refresh_video_track_controls()
            if changed:
                self._set_video_track_feedback(self._t('video_subtitles_disabled_feedback', 'Subtitles turned off.'))
                return
            self._set_video_track_feedback('Unable to turn off subtitles.', 'orange')
            return
        if selection >= len(self._subtitle_track_ids):
            self._refresh_video_subtitle_track_controls()
            return
        track_id = int(self._subtitle_track_ids[selection])
        selector = getattr(controller, 'select_text_track', None)
        if callable(selector):
            try:
                changed = bool(selector(track_id))
            except WX_CALLBACK_EXCEPTIONS:
                changed = False
        self._refresh_video_track_controls()
        if changed:
            label = self.video_subtitle_track_choice.items[self.video_subtitle_track_choice.GetSelection()]
            self._set_video_track_feedback(f"Subtitles enabled: {label}")
            return
        self._set_video_track_feedback('Unable to change subtitles.', 'orange')

    def _t(self, key: str, default: str | None = None, **kwargs: Any) -> str:
        fallback = default if default is not None else key
        return get_localized_text(self.localization_manager, key, fallback, **kwargs)

    def _load_state(self) -> None:
        self._reload_language_choices()
        self._reload_theme_choices()
        volume = max(0, min(100, self._read_saved_int('volume', 70)))
        self.volume_slider.SetValue(volume)
        set_label_text(self.volume_value_label, f'{volume}%')
        self._refresh_video_track_controls()

    def _read_saved_int(self, key: str, default: int) -> int:
        getter = getattr(self.settings_manager, 'get_setting', None)
        if not callable(getter):
            return int(default)
        try:
            return int(getter(key, default))
        except WX_CALLBACK_EXCEPTIONS:
            return int(default)

    def _available_languages(self) -> list[tuple[str, str]]:
        getter = getattr(self.localization_manager, 'get_available_languages', None)
        if not callable(getter):
            return [('en', 'English'), ('it', 'Italiano')]
        try:
            available = getter()
        except WX_CALLBACK_EXCEPTIONS:
            return [('en', 'English'), ('it', 'Italiano')]
        if not isinstance(available, dict) or not available:
            return [('en', 'English'), ('it', 'Italiano')]
        return [(str(code), str(label)) for code, label in available.items() if code]

    def _current_language(self) -> str:
        getter = getattr(self.localization_manager, 'get_current_language', None)
        if callable(getter):
            try:
                value = getter()
                if value:
                    return str(value).lower()
            except WX_CALLBACK_EXCEPTIONS:
                logger.debug('Unable to read current language for wx SettingsView.', exc_info=True)
        return 'en'

    def _reload_language_choices(self) -> None:
        pairs = self._available_languages()
        self._language_codes = [code for code, _label in pairs]
        labels = [label for _code, label in pairs]
        self.language_choice.SetItems(labels)
        autosize_choice_control(self.language_choice, labels)
        current = self._current_language()
        selection = self._language_codes.index(current) if current in self._language_codes else 0
        if self._language_codes:
            self.language_choice.SetSelection(selection)

    def _available_themes(self) -> list[str]:
        themes = ['system']
        getter = getattr(self.theme_manager, 'get_available_theme_names', None)
        if not callable(getter):
            return themes
        try:
            values = getter()
        except WX_CALLBACK_EXCEPTIONS:
            return themes
        for value in values if isinstance(values, list) else []:
            normalized = str(value or '').strip().lower()
            if normalized and normalized not in themes:
                themes.append(normalized)
        return themes

    def _current_theme(self) -> str:
        getter = getattr(self.settings_manager, 'get_setting', None)
        if callable(getter):
            try:
                return str(getter('theme', 'system') or 'system').lower()
            except WX_CALLBACK_EXCEPTIONS:
                logger.debug('Unable to read saved theme for wx SettingsView.', exc_info=True)
        getter = getattr(self.theme_manager, 'get_current_theme_name', None)
        if callable(getter):
            try:
                return str(getter() or 'system').lower()
            except WX_CALLBACK_EXCEPTIONS:
                logger.debug('Unable to read runtime theme for wx SettingsView.', exc_info=True)
        return 'system'

    def _reload_theme_choices(self) -> None:
        self._theme_names = self._available_themes()
        labels = [self._t(f'theme_{name}', name.replace('_', ' ').title()) for name in self._theme_names]
        self.theme_choice.SetItems(labels)
        autosize_choice_control(self.theme_choice, labels)
        current = self._current_theme()
        selection = self._theme_names.index(current) if current in self._theme_names else 0
        if self._theme_names:
            self.theme_choice.SetSelection(selection)

    def _selected_value(self, names: list[str], selection: int) -> str | None:
        if 0 <= selection < len(names):
            return names[selection]
        return None

    def _set_setting(self, key: str, value: Any) -> None:
        setter = getattr(self.settings_controller, 'set_setting', None)
        if callable(setter):
            setter(key, value)
            return
        setter = getattr(self.settings_manager, 'set_setting', None)
        if callable(setter):
            setter(key, value)
        if self.event_bus is not None:
            self.event_bus.publish(AudioEventType.SETTINGS_UPDATED, {'key': key, 'value': value})

    def _set_feedback(self, message: str, color: str = 'green') -> None:
        set_label_text(self.feedback_label, message)
        if self.event_bus is not None:
            self.event_bus.publish(AudioEventType.FEEDBACK_MESSAGE, {'message': message, 'color': color})

    def _on_language_changed(self, _event: Any | None = None) -> None:
        code = self._selected_value(self._language_codes, self.language_choice.GetSelection())
        if not code or code == self._current_language():
            self.update_localization()
            return
        self._set_setting('language', code)

    def _on_theme_changed(self, _event: Any | None = None) -> None:
        theme_name = self._selected_value(self._theme_names, self.theme_choice.GetSelection())
        if not theme_name or theme_name == self._current_theme():
            self._reload_theme_choices()
            return
        self._set_setting('theme', theme_name)

    def _on_volume_changed(self, _event: Any | None = None) -> None:
        volume = max(0, min(100, int(self.volume_slider.GetValue())))
        set_label_text(self.volume_value_label, f'{volume}%')
        self._set_setting('volume', volume)

    def _controller_path(self, method_name: str) -> str:
        method = getattr(self.settings_controller, method_name, None)
        if not callable(method):
            return ''
        value = method()
        return str(value or '')

    def _open_controller_path(self, method_name: str) -> None:
        path = self._controller_path(method_name)
        if not path:
            self._set_feedback(self._t('settings_open_folder_failed', 'Unable to open folder.'), 'orange')
            return
        try:
            open_directory(path)
        except OSError:
            self._set_feedback(self._t('settings_open_folder_failed', 'Unable to open folder.'), 'orange')

    def _confirm_cleanup(self, name: str) -> bool:
        title = self._t('settings_clear_cache_confirm_title', 'Confirm cleanup')
        body = self._t('settings_clear_cache_confirm_body', "Do you want to clean '{name}'?", name=name)
        message_box = getattr(self._wx, 'MessageBox', None)
        if not callable(message_box):
            return True
        yes_no = getattr(self._wx, 'YES_NO', 0)
        icon = getattr(self._wx, 'ICON_QUESTION', 0)
        result = message_box(body, title, yes_no | icon)
        if isinstance(result, bool):
            return bool(result)
        if isinstance(result, int):
            return int(result) == int(getattr(self._wx, 'YES', MESSAGE_BOX_YES))
        return bool(result)

    def _cleanup_block_message(self) -> str:
        getter = getattr(self.settings_controller, 'get_processed_audio_cleanup_block_message', None)
        if not callable(getter):
            return ''
        try:
            return str(getter() or '')
        except WX_CALLBACK_EXCEPTIONS:
            return ''

    def _normalize_cleanup_total(self, result: Any) -> int:
        if isinstance(result, int):
            return int(result)
        if isinstance(result, dict):
            return int(result.get('total_removed', 0))
        if hasattr(result, 'get'):
            return int(result.get('total_removed', 0))
        return 0

    def _run_cleanup(self, label_key: str, label_default: str, method_name: str) -> None:
        block_message = self._cleanup_block_message()
        if block_message:
            self._set_feedback(block_message, 'orange')
            return
        label = self._t(label_key, label_default)
        if not self._confirm_cleanup(label):
            return
        action = getattr(self.settings_controller, method_name, None)
        if not callable(action):
            return
        try:
            count = self._normalize_cleanup_total(action())
        except (OSError, RuntimeError, TypeError, ValueError):
            message = self._t('settings_error_clearing_cache', 'Cleanup error: {error}', error='runtime')
            self._set_feedback(message, 'red')
            return
        message = self._t('settings_clear_cache_done', 'Cleanup complete. Removed {count} item(s).', count=count)
        self._set_feedback(message, 'green')

    def _on_open_app_data(self, _event: Any | None = None) -> None:
        self._open_controller_path('get_app_data_dir')

    def _on_open_processed_audio(self, _event: Any | None = None) -> None:
        self._open_controller_path('get_processed_audio_dir')

    def _on_open_logs(self, _event: Any | None = None) -> None:
        self._open_controller_path('get_logs_dir')

    def _on_open_third_party_notices(self, _event: Any | None = None) -> None:
        self._open_controller_path('get_third_party_notices_dir')

    def _on_clear_processed_audio(self, _event: Any | None = None) -> None:
        self._run_cleanup('settings_clear_processed_audio', 'Clear processed audio cache', 'clear_processed_audio_cache')

    def _on_clear_runtime(self, _event: Any | None = None) -> None:
        self._run_cleanup('settings_clear_general_cache', 'Clear app cache and logs', 'clear_runtime_artifacts')

    def update_localization(self, *_: Any) -> None:
        set_label_text(self.title_label, self._t('settings_title', 'Settings'))
        set_label_text(self.language_label, self._t('settings_language', 'Language'))
        set_label_text(self.volume_text_label, self._t('settings_default_volume', 'Default volume'))
        set_label_text(self.theme_label, self._t('settings_app_theme', 'App theme'))
        set_label_text(self.video_section_title_label, self._t('settings_video_section_title', 'Video playback'))
        set_label_text(self.video_section_note_label, self._t('settings_video_section_note', 'Audio track and subtitle options for the currently active video.'))
        set_label_text(self.maintenance_title_label, self._t('settings_maintenance_section_title', 'Maintenance'))
        set_label_text(self.maintenance_note_label, self._t('settings_maintenance_section_note', 'Tools for app data, logs and cache.'))
        set_label_text(self.open_app_data_button, self._t('settings_open_app_data', 'Open app data'))
        set_label_text(self.open_processed_audio_button, self._t('settings_open_processed_audio', 'Open processed audio'))
        set_label_text(self.open_logs_button, self._t('settings_open_logs', 'Open logs folder'))
        set_label_text(self.open_third_party_notices_button, self._t('settings_open_third_party_notices', 'ThirdPartyNotices'))
        set_label_text(self.clear_processed_audio_button, self._t('settings_clear_processed_audio', 'Clear processed audio cache'))
        set_label_text(self.clear_runtime_button, self._t('settings_clear_general_cache', 'Clear app cache and logs'))
        self._reload_language_choices()
        self._reload_theme_choices()
        set_label_text(self.volume_value_label, f'{int(self.volume_slider.GetValue())}%')
        self._refresh_video_track_controls()

    def update_theme_colors(self, *_: Any) -> None:
        colors = get_theme_colors(self.theme_manager)
        background = colors.get('panel_bg') or colors.get('bg_color')
        foreground = colors.get('text_color')
        accent = colors.get('button_color') or background
        widgets = [
            self.panel,
            self.title_label,
            self.language_label,
            self.volume_text_label,
            self.volume_value_label,
            self.theme_label,
            self.video_section_title_label,
            self.video_section_note_label,
            self.video_audio_track_label,
            self.video_subtitle_track_label,
            self.video_track_feedback_label,
            self.maintenance_title_label,
            self.maintenance_note_label,
            self.feedback_label,
        ]
        for widget in widgets:
            apply_colors(widget, background=background, foreground=foreground)
        for control in (
            self.language_choice,
            self.theme_choice,
            self.volume_slider,
            self.video_audio_track_choice,
            self.video_subtitle_track_choice,
            self.open_app_data_button,
            self.open_processed_audio_button,
            self.open_logs_button,
            self.open_third_party_notices_button,
            self.clear_processed_audio_button,
            self.clear_runtime_button,
        ):
            apply_colors(control, background=accent, foreground=foreground)

    def shutdown(self) -> None:
        unregister_callback(
            self.localization_manager,
            'unregister_language_change_callback',
            self.update_localization,
            logger=logger,
            message='Unable to unregister wx SettingsView language callback.',
        )
        unregister_callback(
            self.theme_manager,
            'unregister_theme_change_callback',
            self.update_theme_colors,
            logger=logger,
            message='Unable to unregister wx SettingsView theme callback.',
        )
        unsubscribe = getattr(self.event_bus, 'unsubscribe', None)
        if callable(unsubscribe):
            for event_type, subscription in self._video_event_subscriptions:
                try:
                    unsubscribe(event_type, subscription=subscription)
                except WX_CALLBACK_EXCEPTIONS:
                    logger.debug('Unable to unsubscribe SettingsView from %s.', event_type, exc_info=True)

    def show(self) -> None:
        self.panel.Show(True)
