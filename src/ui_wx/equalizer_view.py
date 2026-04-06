from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Any

from src.audio.audio_event_models import AudioEventType
from src.ui_wx.common import (
    apply_colors,
    autosize_choice_control,
    create_flow_sizer,
    get_localized_text,
    get_theme_colors,
    open_directory,
    register_callback,
    set_label_text,
    unregister_callback,
)
from src.utils.helpers import get_app_data_path

logger = logging.getLogger(__name__)

EQUALIZER_VIEW_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)
FALLBACK_BUILTIN_EQ_PRESETS = frozenset({
    'Flat',
    'Bass Boost',
    'Vocal Boost',
    'Treble Boost',
    'Rock',
    'Pop',
    'Jazz',
    'Classical',
})


class EqualizerView:
    """wx-based equalizer page.

    Edge cases handled deterministically:
    1. Equalizer state can be partially missing, so UI refresh falls back to safe defaults instead of raising.
    2. Slider callbacks can fire before preset/state synchronization completes, so gain writes are clamped and mirrored locally.
    3. Event-bus feedback can arrive out of order during teardown, so updates are best-effort and ignored after shutdown.
    """

    _BAND_VALUE_LABEL_MIN_SIZE = (72, 28)

    def __init__(
        self,
        parent: Any,
        equalizer: Any = None,
        equalizer_controller: Any = None,
        localization_manager: Any = None,
        theme_manager: Any = None,
        event_bus: Any = None,
        audio_engine: Any = None,
        **_: Any,
    ) -> None:
        self._wx = self._import_wx_module()
        self.equalizer = equalizer
        self.equalizer_controller = equalizer_controller
        self.localization_manager = localization_manager
        self.theme_manager = theme_manager
        self.event_bus = event_bus
        self.audio_engine = audio_engine
        self.panel = self._wx.ScrolledWindow(parent) if hasattr(self._wx, 'ScrolledWindow') else self._wx.Panel(parent)
        self.panel.owner = self
        self._subscriptions: list[tuple[AudioEventType, Any]] = []
        self._is_shutting_down = False
        self._current_enabled = False
        self._current_preset = 'Flat'
        self._band_names: list[str] = []
        self._band_sliders: dict[str, Any] = {}
        self._band_value_labels: dict[str, Any] = {}
        self._band_rows: dict[str, Any] = {}
        self._preset_names: list[str] = []
        self._last_preset_choice_items: tuple[str, ...] = ()
        self._build_ui()
        self._bind_events()
        self._register_callbacks()
        self._subscribe_to_events()
        self._load_state()
        self.update_localization()
        self.update_theme_colors()

    @staticmethod
    def _import_wx_module() -> Any:
        try:
            return importlib.import_module('wx')
        except ImportError as exc:
            raise RuntimeError('wx is required to instantiate the wx EqualizerView.') from exc

    def _build_ui(self) -> None:
        wx = self._wx
        if hasattr(self.panel, 'SetScrollRate'):
            self.panel.SetScrollRate(16, 16)
        root = wx.BoxSizer(wx.VERTICAL)
        self.title_label = wx.StaticText(self.panel, label='')
        root.Add(self.title_label, 0, wx.ALL | wx.EXPAND, 10)
        toolbar = create_flow_sizer(wx)
        self.toggle_button = wx.Button(self.panel, label='')
        self.reset_button = wx.Button(self.panel, label='')
        self.preset_choice = wx.Choice(self.panel)
        self.save_preset_button = wx.Button(self.panel, label='')
        self.delete_preset_button = wx.Button(self.panel, label='')
        self.open_processed_audio_button = wx.Button(self.panel, label='')
        for control in (self.toggle_button, self.reset_button, self.preset_choice, self.save_preset_button, self.delete_preset_button, self.open_processed_audio_button):
            toolbar.Add(control, 0, wx.ALL | wx.EXPAND, 6)
        root.Add(toolbar, 0, wx.ALL | wx.EXPAND, 0)
        self.status_label = wx.StaticText(self.panel, label='')
        root.Add(self.status_label, 0, wx.ALL | wx.EXPAND, 8)
        self.bands_container = wx.Panel(self.panel)
        self.bands_sizer = wx.BoxSizer(wx.VERTICAL)
        self.bands_container.SetSizer(self.bands_sizer)
        root.Add(self.bands_container, 0, wx.ALL | wx.EXPAND, 8)
        self.feedback_label = wx.StaticText(self.panel, label='')
        root.Add(self.feedback_label, 0, wx.ALL | wx.EXPAND, 8)
        self.panel.SetSizer(root)

    def _bind_events(self) -> None:
        wx = self._wx
        self.toggle_button.Bind(wx.EVT_BUTTON, self._on_toggle)
        self.reset_button.Bind(wx.EVT_BUTTON, self._on_reset)
        self.preset_choice.Bind(wx.EVT_CHOICE, self._on_preset_selected)
        self.save_preset_button.Bind(wx.EVT_BUTTON, self._on_save_preset)
        self.delete_preset_button.Bind(wx.EVT_BUTTON, self._on_delete_preset)
        self.open_processed_audio_button.Bind(wx.EVT_BUTTON, self._on_open_processed_audio)

    def _register_callbacks(self) -> None:
        register_callback(
            self.localization_manager,
            'register_language_change_callback',
            self.update_localization,
            logger=logger,
            message='Unable to register wx EqualizerView language callback.',
        )
        register_callback(
            self.theme_manager,
            'register_theme_change_callback',
            self.update_theme_colors,
            logger=logger,
            message='Unable to register wx EqualizerView theme callback.',
        )

    def _subscribe_to_events(self) -> None:
        self._subscribe(AudioEventType.EQ_CHANGED, self._on_eq_changed)
        self._subscribe(AudioEventType.FEEDBACK_MESSAGE, self._on_feedback_message)
        self._subscribe(AudioEventType.CUSTOM_PRESETS_UPDATED, self._on_custom_presets_updated)

    def _subscribe(self, event_type: AudioEventType, callback: Any) -> None:
        subscribe = getattr(self.event_bus, 'subscribe', None)
        if not callable(subscribe):
            return
        try:
            subscription = subscribe(event_type, callback)
        except EQUALIZER_VIEW_EXCEPTIONS:
            logger.debug('EqualizerView subscription failed for %s.', event_type, exc_info=True)
            return
        self._subscriptions.append((event_type, subscription))

    def _t(self, key: str, default: str | None = None, **kwargs: Any) -> str:
        fallback = default if default is not None else key
        return get_localized_text(self.localization_manager, key, fallback, **kwargs)

    def _get_bands(self) -> dict[str, dict[str, Any]]:
        getter = getattr(self.equalizer_controller, 'get_all_bands', None)
        if callable(getter):
            try:
                bands = getter()
            except EQUALIZER_VIEW_EXCEPTIONS:
                logger.debug('Unable to read equalizer bands from controller.', exc_info=True)
                bands = {}
            if isinstance(bands, dict):
                return {str(name): dict(data or {}) for name, data in bands.items()}
        getter = getattr(self.equalizer, 'get_all_bands', None)
        if callable(getter):
            try:
                bands = getter()
            except EQUALIZER_VIEW_EXCEPTIONS:
                logger.debug('Unable to read equalizer bands from engine.', exc_info=True)
                return {}
            if isinstance(bands, dict):
                return {str(name): dict(data or {}) for name, data in bands.items()}
        return {}

    def _get_state(self) -> dict[str, Any]:
        getter = getattr(self.equalizer_controller, 'get_current_state', None)
        if callable(getter):
            try:
                state = getter()
            except EQUALIZER_VIEW_EXCEPTIONS:
                logger.debug('Unable to read equalizer state from controller.', exc_info=True)
                state = {}
            return dict(state or {}) if isinstance(state, dict) else {}
        getter = getattr(self.equalizer, 'get_current_state', None)
        if callable(getter):
            try:
                state = getter()
            except EQUALIZER_VIEW_EXCEPTIONS:
                logger.debug('Unable to read equalizer state from engine.', exc_info=True)
                state = {}
            return dict(state or {}) if isinstance(state, dict) else {}
        return {}

    def _get_presets(self) -> list[str]:
        getter = getattr(self.equalizer_controller, 'get_all_preset_names', None)
        if callable(getter):
            try:
                values = getter()
            except EQUALIZER_VIEW_EXCEPTIONS:
                logger.debug('Unable to read equalizer presets from controller.', exc_info=True)
                values = []
            return [str(value) for value in values or [] if value]
        getter = getattr(self.equalizer, 'get_available_presets', None)
        if callable(getter):
            try:
                values = getter()
            except EQUALIZER_VIEW_EXCEPTIONS:
                logger.debug('Unable to read equalizer presets from engine.', exc_info=True)
                return []
            return [str(value) for value in values or [] if value]
        return []

    def _load_state(self) -> None:
        self._ensure_band_rows()
        self._reload_presets()
        self._refresh_from_state(self._get_state())

    def _ensure_band_rows(self) -> None:
        bands = self._get_bands()
        if not bands:
            bands = {
                'sub_bass': {'freq': 60, 'gain': 0.0},
                'bass': {'freq': 170, 'gain': 0.0},
                'mid': {'freq': 1000, 'gain': 0.0},
                'treble': {'freq': 8000, 'gain': 0.0},
            }
        self._band_names = list(bands.keys())
        self._band_rows.clear()
        self._band_sliders.clear()
        self._band_value_labels.clear()
        if hasattr(self.bands_sizer, 'items'):
            self.bands_sizer.items.clear()
        for band_name in self._band_names:
            band_data = dict(bands.get(band_name, {}) or {})
            row = self._wx.BoxSizer(self._wx.HORIZONTAL)
            label = self._wx.StaticText(self.bands_container, label=self._band_title(band_name, band_data))
            slider = self._wx.Slider(self.bands_container, value=120, minValue=0, maxValue=240)
            value_label = self._wx.StaticText(self.bands_container, label='0.0 dB')
            self._set_stable_value_label_size(value_label)
            slider.Bind(self._wx.EVT_SLIDER, self._make_band_handler(band_name))
            row.Add(label, 0, self._wx.ALL | self._wx.ALIGN_CENTER_VERTICAL, 6)
            row.Add(slider, 1, self._wx.ALL | self._wx.EXPAND, 6)
            row.Add(value_label, 0, self._wx.ALL | self._wx.ALIGN_CENTER_VERTICAL, 6)
            self.bands_sizer.Add(row, 0, self._wx.ALL | self._wx.EXPAND, 0)
            self._band_rows[band_name] = label
            self._band_sliders[band_name] = slider
            self._band_value_labels[band_name] = value_label

    def _band_title(self, band_name: str, band_data: dict[str, Any]) -> str:
        freq = int(float(band_data.get('freq', 0) or 0))
        return f'{band_name} ({freq}Hz)' if freq else band_name

    @staticmethod
    def _get_widget_text(widget: Any) -> str:
        getter = getattr(widget, 'GetLabel', None)
        if callable(getter):
            try:
                return str(getter())
            except EQUALIZER_VIEW_EXCEPTIONS:
                return str(getattr(widget, 'label', '') or '')
        return str(getattr(widget, 'label', '') or '')

    def _set_widget_text_if_changed(self, widget: Any, text: str) -> None:
        normalized = str(text)
        if self._get_widget_text(widget) == normalized:
            return
        set_label_text(widget, normalized)

    @classmethod
    def _set_stable_value_label_size(cls, value_label: Any) -> None:
        set_min_size = getattr(value_label, 'SetMinSize', None)
        if callable(set_min_size):
            try:
                set_min_size(cls._BAND_VALUE_LABEL_MIN_SIZE)
            except EQUALIZER_VIEW_EXCEPTIONS:
                logger.debug('Unable to set equalizer value label min size.', exc_info=True)
        set_initial_size = getattr(value_label, 'SetInitialSize', None)
        if callable(set_initial_size):
            try:
                set_initial_size(cls._BAND_VALUE_LABEL_MIN_SIZE)
            except EQUALIZER_VIEW_EXCEPTIONS:
                logger.debug('Unable to set equalizer value label initial size.', exc_info=True)

    @staticmethod
    def _set_slider_value_if_changed(slider: Any, value: int) -> None:
        getter = getattr(slider, 'GetValue', None)
        if callable(getter):
            try:
                current_value = int(getter())
            except EQUALIZER_VIEW_EXCEPTIONS:
                current_value = None
            if current_value == int(value):
                return
        slider.SetValue(int(value))

    def _resolve_builtin_preset_checker(self) -> Any:
        candidates = [
            self.equalizer_controller,
            getattr(self.equalizer_controller, 'equalizer', None),
            self.equalizer,
        ]
        for candidate in candidates:
            checker = getattr(candidate, 'is_builtin_preset', None)
            if callable(checker):
                return checker
        return None

    def _is_builtin_preset(self, preset_name: str) -> bool:
        normalized = str(preset_name).strip()
        if not normalized:
            return False
        checker = self._resolve_builtin_preset_checker()
        if callable(checker):
            try:
                return bool(checker(normalized))
            except EQUALIZER_VIEW_EXCEPTIONS:
                logger.debug('Unable to resolve built-in EQ preset status for %s.', normalized, exc_info=True)
        return normalized in FALLBACK_BUILTIN_EQ_PRESETS

    def _set_delete_button_state(self, preset_name: str) -> None:
        can_delete = bool(preset_name) and preset_name in self._preset_names and not self._is_builtin_preset(preset_name)
        enabler = getattr(self.delete_preset_button, 'Enable', None)
        if callable(enabler):
            enabler(can_delete)

    def _sync_preset_choice(self, preferred_preset: str | None = None) -> None:
        target = str(preferred_preset or self._current_preset or '').strip()
        current_selection = str(self.preset_choice.GetStringSelection() or '').strip()
        if target:
            if current_selection == target:
                self._set_delete_button_state(target)
                return
            if self.preset_choice.SetStringSelection(target):
                self._set_delete_button_state(target)
                return
        if self._preset_names:
            fallback_target = self._current_preset if self._current_preset in self._preset_names else self._preset_names[0]
            if current_selection == fallback_target:
                self._set_delete_button_state(fallback_target)
                return
            if not self.preset_choice.SetStringSelection(fallback_target):
                current_index = getattr(self.preset_choice, 'GetSelection', lambda: -1)()
                if int(current_index) != 0:
                    self.preset_choice.SetSelection(0)
            self._set_delete_button_state(self.preset_choice.GetStringSelection())
            return
        self._set_delete_button_state('')

    def _get_preset_choice_items(self) -> list[str]:
        get_count = getattr(self.preset_choice, 'GetCount', None)
        get_string = getattr(self.preset_choice, 'GetString', None)
        if callable(get_count) and callable(get_string):
            try:
                count = max(0, int(get_count()))
            except EQUALIZER_VIEW_EXCEPTIONS:
                count = 0
            values: list[str] = []
            for index in range(count):
                try:
                    values.append(str(get_string(index)))
                except EQUALIZER_VIEW_EXCEPTIONS:
                    logger.debug('Unable to read EQ preset choice item at index %s.', index, exc_info=True)
                    return values
            return values
        items = getattr(self.preset_choice, 'items', None)
        return [str(item) for item in items] if isinstance(items, list) else []

    def _apply_preset_choice_items(self, choice_items: list[str]) -> None:
        normalized_items = tuple(str(item) for item in choice_items)
        if normalized_items == self._last_preset_choice_items and normalized_items == tuple(self._get_preset_choice_items()):
            return
        self.preset_choice.SetItems(list(normalized_items))
        autosize_choice_control(self.preset_choice, normalized_items)
        self._last_preset_choice_items = normalized_items

    def _reload_presets(self, preferred_preset: str | None = None, event_presets: list[str] | None = None) -> None:
        controller_presets = self._get_presets() or ['Flat']
        merged_presets: list[str] = []
        for candidate in [*controller_presets, *(event_presets or [])]:
            normalized = str(candidate).strip()
            if normalized and normalized not in merged_presets:
                merged_presets.append(normalized)
        self._preset_names = merged_presets or ['Flat']
        target = str(preferred_preset or self._current_preset or '').strip()
        choice_items = list(self._preset_names)
        if target and target not in choice_items and (target == 'Custom' or not self._is_builtin_preset(target)):
            choice_items.append(target)
            if target != 'Custom' and target not in self._preset_names:
                self._preset_names.append(target)
        self._apply_preset_choice_items(choice_items)
        self._sync_preset_choice(preferred_preset)

    @staticmethod
    def _get_text_entry_dialog_value(dialog: Any) -> str:
        for getter_name in ('GetValue', 'GetTextValue'):
            getter = getattr(dialog, getter_name, None)
            if not callable(getter):
                continue
            try:
                return str(getter()).strip()
            except EQUALIZER_VIEW_EXCEPTIONS:
                logger.debug('Unable to read EQ preset dialog value via %s.', getter_name, exc_info=True)
        return ''

    def _promote_slider_change_to_custom_preset_ui(self) -> None:
        self._current_preset = 'Custom'
        choice_items = self._get_preset_choice_items()
        if 'Custom' in choice_items:
            if str(self.preset_choice.GetStringSelection() or '').strip() != 'Custom':
                self._sync_preset_choice('Custom')
            else:
                self._set_delete_button_state('Custom')
            return
        self._reload_presets(preferred_preset='Custom')

    @staticmethod
    def _gain_to_slider(gain: float) -> int:
        clamped = max(-12.0, min(12.0, float(gain)))
        return int(round((clamped + 12.0) * 10.0))

    @staticmethod
    def _slider_to_gain(value: int) -> float:
        return max(-12.0, min(12.0, (float(value) / 10.0) - 12.0))

    def _refresh_from_state(self, state: dict[str, Any] | None = None) -> None:
        state = dict(state or {})
        next_enabled = bool(state.get('enabled', False))
        next_preset = str(state.get('preset_name') or 'Custom')
        enabled_changed = next_enabled != self._current_enabled
        preset_changed = next_preset != self._current_preset
        self._current_enabled = next_enabled
        self._current_preset = next_preset
        band_gains = dict(state.get('band_gains', {}) or {})
        for band_name, slider in self._band_sliders.items():
            gain = float(band_gains.get(band_name, 0.0) or 0.0)
            self._set_slider_value_if_changed(slider, self._gain_to_slider(gain))
            self._set_widget_text_if_changed(self._band_value_labels[band_name], f'{gain:.1f} dB')
        choice_items = self._get_preset_choice_items()
        if self._current_preset and self._current_preset not in choice_items:
            self._reload_presets(preferred_preset=self._current_preset)
        elif preset_changed or self.preset_choice.GetStringSelection() != self._current_preset:
            self._sync_preset_choice(self._current_preset)
        if enabled_changed:
            self._update_status_label()

    def _update_status_label(self) -> None:
        status_key = 'equalizer_state_enabled' if self._current_enabled else 'equalizer_state_disabled'
        self._set_widget_text_if_changed(
            self.status_label,
            self._t(status_key, 'Equalizer enabled.' if self._current_enabled else 'Equalizer disabled.'),
        )
        toggle_key = 'equalizer_disable' if self._current_enabled else 'equalizer_enable'
        self._set_widget_text_if_changed(
            self.toggle_button,
            self._t(toggle_key, 'Disable EQ' if self._current_enabled else 'Enable EQ'),
        )

    def update_localization(self, *_: Any) -> None:
        self._set_widget_text_if_changed(self.title_label, self._t('nav_equalizer', self._t('equalizer_title', 'Equalizer')))
        self._set_widget_text_if_changed(self.reset_button, self._t('equalizer_reset', 'Reset EQ'))
        self._set_widget_text_if_changed(self.save_preset_button, self._t('profile_save_effects_setting_button', 'Save'))
        self._set_widget_text_if_changed(self.delete_preset_button, self._t('profile_delete_effects_setting_button', 'Delete'))
        self._set_widget_text_if_changed(self.open_processed_audio_button, self._t('settings_open_processed_audio', 'Open processed audio folder'))
        self._update_status_label()
        bands = self._get_bands()
        for band_name, label in self._band_rows.items():
            self._set_widget_text_if_changed(label, self._band_title(band_name, bands.get(band_name, {})))

    def update_theme_colors(self, *_: Any) -> None:
        colors = get_theme_colors(self.theme_manager)
        background = colors.get('panel_bg') or colors.get('bg_color')
        foreground = colors.get('text_color')
        accent = colors.get('button_color') or background
        for widget in [self.panel, self.bands_container, self.title_label, self.status_label, self.feedback_label, *self._band_rows.values(), *self._band_value_labels.values()]:
            apply_colors(widget, background=background, foreground=foreground)
        for control in [self.toggle_button, self.reset_button, self.preset_choice, self.save_preset_button, self.delete_preset_button, self.open_processed_audio_button, *self._band_sliders.values()]:
            apply_colors(control, background=accent, foreground=foreground)

    def _publish_feedback(self, message: str, color: str = 'green') -> None:
        self._set_widget_text_if_changed(self.feedback_label, message)
        publish = getattr(self.event_bus, 'publish', None)
        if callable(publish):
            try:
                publish(AudioEventType.FEEDBACK_MESSAGE, {'message': message, 'color': color})
            except EQUALIZER_VIEW_EXCEPTIONS:
                logger.debug('Unable to publish EqualizerView feedback.', exc_info=True)

    def _get_processed_audio_dir(self) -> Path:
        """Resolve the processed-audio folder for the equalizer page.

        Edge cases handled deterministically:
        1. Audio engine instances can expose the processed cache path as Path-like or string-like objects, so the value is normalized before use.
        2. Legacy runtimes can miss the private processed-audio attribute, so the view falls back to the canonical app-data folder.
        3. Directory creation can be required after cache cleanup, so the resolved folder is recreated before opening it.
        """
        candidate = getattr(self.audio_engine, '_processed_audio_dir', None)
        if isinstance(candidate, Path):
            resolved = candidate.resolve()
        elif candidate is not None:
            try:
                resolved = Path(str(candidate)).resolve()
            except (OSError, RuntimeError, TypeError, ValueError):
                resolved = get_app_data_path('processed_audio', create=False).resolve()
        else:
            resolved = get_app_data_path('processed_audio', create=False).resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        return resolved

    def _set_band_gain(self, band_name: str, gain: float) -> None:
        setter = getattr(self.equalizer_controller, 'set_band_gain', None)
        if callable(setter):
            setter(band_name, gain)
        else:
            setter = getattr(self.equalizer, 'set_band_gain', None)
            if callable(setter):
                setter(band_name, gain)
        self._set_widget_text_if_changed(self._band_value_labels[band_name], f'{gain:.1f} dB')

    def _make_band_handler(self, band_name: str) -> Any:
        def _handler(_event: Any | None = None) -> None:
            gain = self._slider_to_gain(self._band_sliders[band_name].GetValue())
            self._set_band_gain(band_name, gain)
            self._promote_slider_change_to_custom_preset_ui()
        return _handler

    def _on_toggle(self, _event: Any | None = None) -> None:
        enable = not self._current_enabled
        toggler = getattr(self.equalizer_controller, 'toggle_equalizer', None)
        if callable(toggler):
            toggler(enable)
        else:
            toggler = getattr(self.equalizer, 'enable', None)
            if callable(toggler):
                toggler(enable)
        self._current_enabled = enable
        self._update_status_label()

    def _on_reset(self, _event: Any | None = None) -> None:
        applier = getattr(self.equalizer_controller, 'apply_preset', None)
        if callable(applier):
            applier('Flat')
        else:
            applier = getattr(self.equalizer, 'set_preset', None)
            if callable(applier):
                applier('Flat')
        self._current_preset = 'Flat'
        self._refresh_from_state(self._get_state())

    def _on_preset_selected(self, _event: Any | None = None) -> None:
        preset_name = self.preset_choice.GetStringSelection()
        if not preset_name:
            return
        applier = getattr(self.equalizer_controller, 'apply_preset', None)
        if callable(applier):
            applier(preset_name)
        else:
            applier = getattr(self.equalizer, 'set_preset', None)
            if callable(applier):
                applier(preset_name)
        self._current_preset = preset_name
        self._refresh_from_state(self._get_state())

    def _on_save_preset(self, _event: Any | None = None) -> None:
        dialog = self._wx.TextEntryDialog(self.panel, self._t('profile_effects_setting_name_placeholder', 'Enter preset name'))
        try:
            if dialog.ShowModal() != getattr(self._wx, 'ID_OK', 1):
                return
            preset_name = self._get_text_entry_dialog_value(dialog)
            if not preset_name:
                return
            save_succeeded = False
            saver = getattr(self.equalizer_controller, 'save_current_as_custom_preset', None)
            if callable(saver):
                result = saver(preset_name)
                save_succeeded = result is not False
            else:
                saver = getattr(self.equalizer, 'save_custom_preset', None)
                if callable(saver):
                    result = saver(preset_name)
                    save_succeeded = result is not False
            if not save_succeeded:
                return
            self._current_preset = preset_name
            self._reload_presets(preferred_preset=preset_name)
            self._refresh_from_state(self._get_state())
        finally:
            destroy = getattr(dialog, 'Destroy', None)
            if callable(destroy):
                destroy()

    def _on_open_processed_audio(self, _event: Any | None = None) -> None:
        try:
            open_directory(self._get_processed_audio_dir())
        except OSError:
            self._publish_feedback(self._t('settings_open_folder_failed', 'Unable to open folder.'), 'orange')

    def _on_delete_preset(self, _event: Any | None = None) -> None:
        preset_name = self.preset_choice.GetStringSelection()
        if not preset_name or self._is_builtin_preset(preset_name):
            self._set_delete_button_state(preset_name)
            return
        result = self._wx.MessageBox(
            self._t('profile_confirm_delete_effects_setting_message', 'Delete preset {name}?', name=preset_name),
            self._t('profile_confirm_delete_effects_setting_title', 'Confirm delete'),
            getattr(self._wx, 'YES_NO', 0) | getattr(self._wx, 'ICON_QUESTION', 0),
        )
        if result != getattr(self._wx, 'YES', 1):
            return
        deleter = getattr(self.equalizer_controller, 'delete_custom_preset', None)
        if callable(deleter):
            deleter(preset_name)
        else:
            deleter = getattr(self.equalizer, 'delete_custom_preset', None)
            if callable(deleter):
                deleter(preset_name)
        next_state = self._get_state()
        self._current_preset = str(next_state.get('preset_name') or self._current_preset or '').strip() if isinstance(next_state, dict) else self._current_preset
        self._reload_presets(preferred_preset=self._current_preset)
        self._refresh_from_state(next_state)

    def _on_eq_changed(self, payload: Any) -> None:
        if self._is_shutting_down:
            return
        if isinstance(payload, dict):
            self._refresh_from_state(payload)

    def _on_custom_presets_updated(self, payload: Any) -> None:
        if self._is_shutting_down or not isinstance(payload, dict):
            return
        if str(payload.get('type') or '').strip().lower() != 'eq':
            return
        latest_state = self._get_state()
        if isinstance(latest_state, dict):
            next_preset = str(latest_state.get('preset_name') or '').strip()
            if next_preset:
                self._current_preset = next_preset
        event_presets = payload.get('presets') if isinstance(payload.get('presets'), list) else None
        normalized_event_presets = [str(name).strip() for name in (event_presets or []) if str(name).strip()]
        self._reload_presets(preferred_preset=self._current_preset, event_presets=normalized_event_presets)

    def _on_feedback_message(self, payload: Any) -> None:
        if self._is_shutting_down or not isinstance(payload, dict):
            return
        message = str(payload.get('message') or '').strip()
        if message:
            self._set_widget_text_if_changed(self.feedback_label, message)

    def shutdown(self) -> None:
        self._is_shutting_down = True
        unregister_callback(
            self.localization_manager,
            'unregister_language_change_callback',
            self.update_localization,
            logger=logger,
            message='Unable to unregister wx EqualizerView language callback.',
        )
        unregister_callback(
            self.theme_manager,
            'unregister_theme_change_callback',
            self.update_theme_colors,
            logger=logger,
            message='Unable to unregister wx EqualizerView theme callback.',
        )
        unsubscribe = getattr(self.event_bus, 'unsubscribe', None)
        if callable(unsubscribe):
            for event_type, subscription in self._subscriptions:
                try:
                    unsubscribe(event_type, subscription)
                except EQUALIZER_VIEW_EXCEPTIONS:
                    logger.debug('Unable to unsubscribe EqualizerView from %s.', event_type, exc_info=True)
        self._subscriptions.clear()

    def show(self) -> None:
        self.panel.Show(True)
