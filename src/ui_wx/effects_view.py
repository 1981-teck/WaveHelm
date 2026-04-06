from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from src.audio.audio_event_models import AudioEventType
from src.ui_wx.common import (
    apply_colors,
    create_flow_sizer,
    get_localized_text,
    get_theme_colors,
    open_directory,
    register_callback,
    set_label_text,
    unregister_callback,
)
from src.utils.helpers import get_app_data_path, safe_filename

logger = logging.getLogger(__name__)

EFFECTS_VIEW_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)


class EffectsView:
    """wx-based DSP effects page.

    Edge cases handled deterministically:
    1. Effects settings may be missing one effect branch, so each section falls back to safe defaults instead of failing the full page.
    2. Slider callbacks may receive stale values during event-driven refresh, so each parameter write is clamped to the expected runtime range.
    3. Profile-save and reset actions can race with runtime updates, so event-bus state is reloaded after every write.
    """

    EFFECT_FIELDS: dict[str, tuple[tuple[str, float, float, float], ...]] = {
        'echo': (
            ('delay_ms', 0.0, 2000.0, 1.0),
            ('decay', 0.0, 1.0, 100.0),
        ),
        'reverb': (
            ('decay_time', 0.1, 10.0, 10.0),
            ('wet_level', 0.0, 1.0, 100.0),
        ),
        'vintage_filter': (
            ('cutoff_freq', 20.0, 20000.0, 1.0),
            ('resonance', 0.0, 1.0, 100.0),
        ),
    }

    def __init__(
        self,
        parent: Any,
        effects_engine: Any = None,
        effects_controller: Any = None,
        audio_engine: Any = None,
        player_controller: Any = None,
        localization_manager: Any = None,
        theme_manager: Any = None,
        event_bus: Any = None,
        **_: Any,
    ) -> None:
        self._wx = self._import_wx_module()
        self.effects_engine = effects_engine
        self.effects_controller = effects_controller
        self.audio_engine = audio_engine
        self.player_controller = player_controller
        self.localization_manager = localization_manager
        self.theme_manager = theme_manager
        self.event_bus = event_bus
        self.panel = self._wx.ScrolledWindow(parent) if hasattr(self._wx, 'ScrolledWindow') else self._wx.Panel(parent)
        self.panel.owner = self
        self._subscriptions: list[tuple[AudioEventType, Any]] = []
        self._is_shutting_down = False
        self._section_widgets: dict[str, dict[str, Any]] = {}
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
            raise RuntimeError('wx is required to instantiate the wx EffectsView.') from exc

    def _build_ui(self) -> None:
        wx = self._wx
        if hasattr(self.panel, 'SetScrollRate'):
            self.panel.SetScrollRate(16, 16)
        root = wx.BoxSizer(wx.VERTICAL)
        self.title_label = wx.StaticText(self.panel, label='')
        root.Add(self.title_label, 0, wx.ALL | wx.EXPAND, 10)
        toolbar = create_flow_sizer(wx)
        self.save_button = wx.Button(self.panel, label='')
        self.save_audio_button = wx.Button(self.panel, label='')
        self.open_saved_folder_button = wx.Button(self.panel, label='')
        self.reset_all_button = wx.Button(self.panel, label='')
        toolbar.Add(self.save_button, 0, wx.ALL | wx.EXPAND, 6)
        toolbar.Add(self.save_audio_button, 0, wx.ALL | wx.EXPAND, 6)
        toolbar.Add(self.open_saved_folder_button, 0, wx.ALL | wx.EXPAND, 6)
        toolbar.Add(self.reset_all_button, 0, wx.ALL | wx.EXPAND, 6)
        root.Add(toolbar, 0, wx.ALL | wx.EXPAND, 0)
        self.sections_container = wx.Panel(self.panel)
        self.sections_sizer = wx.BoxSizer(wx.VERTICAL)
        self.sections_container.SetSizer(self.sections_sizer)
        root.Add(self.sections_container, 0, wx.ALL | wx.EXPAND, 8)
        self.feedback_label = wx.StaticText(self.panel, label='')
        root.Add(self.feedback_label, 0, wx.ALL | wx.EXPAND, 8)
        self.panel.SetSizer(root)
        self._build_sections()

    def _build_sections(self) -> None:
        for effect_name, fields in self.EFFECT_FIELDS.items():
            section_panel = self._wx.Panel(self.sections_container)
            section_sizer = self._wx.BoxSizer(self._wx.VERTICAL)
            title_label = self._wx.StaticText(section_panel, label='')
            enabled_checkbox = self._wx.CheckBox(section_panel, label='')
            section_sizer.Add(title_label, 0, self._wx.ALL | self._wx.EXPAND, 6)
            section_sizer.Add(enabled_checkbox, 0, self._wx.ALL | self._wx.EXPAND, 6)
            controls: dict[str, Any] = {
                'panel': section_panel,
                'title': title_label,
                'enabled': enabled_checkbox,
            }
            for param_name, min_value, max_value, scale in fields:
                row = self._wx.BoxSizer(self._wx.HORIZONTAL)
                row_label = self._wx.StaticText(section_panel, label='')
                slider = self._wx.Slider(
                    section_panel,
                    value=int(round(min_value * scale)),
                    minValue=int(round(min_value * scale)),
                    maxValue=int(round(max_value * scale)),
                )
                value_label = self._wx.StaticText(section_panel, label='')
                row.Add(row_label, 0, self._wx.ALL | self._wx.ALIGN_CENTER_VERTICAL, 6)
                row.Add(slider, 1, self._wx.ALL | self._wx.EXPAND, 6)
                row.Add(value_label, 0, self._wx.ALL | self._wx.ALIGN_CENTER_VERTICAL, 6)
                section_sizer.Add(row, 0, self._wx.ALL | self._wx.EXPAND, 0)
                controls[f'{param_name}_label'] = row_label
                controls[f'{param_name}_slider'] = slider
                controls[f'{param_name}_value'] = value_label
            section_panel.SetSizer(section_sizer)
            self.sections_sizer.Add(section_panel, 0, self._wx.ALL | self._wx.EXPAND, 6)
            self._section_widgets[effect_name] = controls

    def _bind_events(self) -> None:
        self.save_button.Bind(self._wx.EVT_BUTTON, self._on_save)
        self.save_audio_button.Bind(self._wx.EVT_BUTTON, self._on_save_audio)
        self.open_saved_folder_button.Bind(self._wx.EVT_BUTTON, self._on_open_saved_folder)
        self.reset_all_button.Bind(self._wx.EVT_BUTTON, self._on_reset_all)
        for effect_name, controls in self._section_widgets.items():
            controls['enabled'].Bind(self._wx.EVT_CHECKBOX, self._make_enabled_handler(effect_name))
            for param_name, _min_value, _max_value, _scale in self.EFFECT_FIELDS[effect_name]:
                controls[f'{param_name}_slider'].Bind(self._wx.EVT_SLIDER, self._make_slider_handler(effect_name, param_name))

    def _register_callbacks(self) -> None:
        register_callback(
            self.localization_manager,
            'register_language_change_callback',
            self.update_localization,
            logger=logger,
            message='Unable to register wx EffectsView language callback.',
        )
        register_callback(
            self.theme_manager,
            'register_theme_change_callback',
            self.update_theme_colors,
            logger=logger,
            message='Unable to register wx EffectsView theme callback.',
        )

    def _subscribe_to_events(self) -> None:
        self._subscribe(AudioEventType.EFFECTS_CHANGED, self._on_effects_changed)
        self._subscribe(AudioEventType.FEEDBACK_MESSAGE, self._on_feedback_message)

    def _subscribe(self, event_type: AudioEventType, callback: Any) -> None:
        subscribe = getattr(self.event_bus, 'subscribe', None)
        if not callable(subscribe):
            return
        try:
            subscription = subscribe(event_type, callback)
        except EFFECTS_VIEW_EXCEPTIONS:
            logger.debug('EffectsView subscription failed for %s.', event_type, exc_info=True)
            return
        self._subscriptions.append((event_type, subscription))

    def _t(self, key: str, default: str | None = None, **kwargs: Any) -> str:
        fallback = default if default is not None else key
        return get_localized_text(self.localization_manager, key, fallback, **kwargs)

    def _get_all_settings(self) -> dict[str, Any]:
        getter = getattr(self.effects_controller, 'get_all_effects_settings', None)
        if callable(getter):
            try:
                settings = getter()
            except EFFECTS_VIEW_EXCEPTIONS:
                logger.debug('Unable to read effects settings from controller.', exc_info=True)
                settings = {}
            return dict(settings or {}) if isinstance(settings, dict) else {}
        getter = getattr(self.effects_engine, 'get_current_settings', None)
        if callable(getter):
            try:
                settings = getter()
            except EFFECTS_VIEW_EXCEPTIONS:
                logger.debug('Unable to read effects settings from engine.', exc_info=True)
                settings = {}
            return dict(settings or {}) if isinstance(settings, dict) else {}
        return {}

    def _load_state(self) -> None:
        self._refresh_from_settings(self._get_all_settings())

    def _refresh_from_settings(self, settings: dict[str, Any]) -> None:
        for effect_name, controls in self._section_widgets.items():
            effect_settings = dict(settings.get(effect_name, {}) or {})
            controls['enabled'].SetValue(bool(effect_settings.get('enabled', False)))
            for param_name, min_value, max_value, scale in self.EFFECT_FIELDS[effect_name]:
                raw_value = effect_settings.get(param_name, min_value)
                try:
                    numeric = float(raw_value)
                except EFFECTS_VIEW_EXCEPTIONS:
                    numeric = float(min_value)
                clamped = max(min_value, min(max_value, numeric))
                controls[f'{param_name}_slider'].SetValue(int(round(clamped * scale)))
                set_label_text(controls[f'{param_name}_value'], self._format_value(param_name, clamped))

    def _format_value(self, param_name: str, value: float) -> str:
        if param_name.endswith('_ms') or param_name.endswith('_freq'):
            return f'{int(round(value))}'
        if param_name == 'decay_time':
            return f'{value:.1f}s'
        return f'{value:.2f}'

    def _slider_value(self, effect_name: str, param_name: str) -> float:
        value = float(self._section_widgets[effect_name][f'{param_name}_slider'].GetValue())
        for current_name, min_value, max_value, scale in self.EFFECT_FIELDS[effect_name]:
            if current_name != param_name:
                continue
            normalized = value / scale
            return max(min_value, min(max_value, normalized))
        return value

    def update_localization(self, *_: Any) -> None:
        set_label_text(self.title_label, self._t('nav_effects', self._t('effects_title', 'Effects')))
        set_label_text(self.save_button, self._t('save_effects_settings_button', 'Save settings'))
        set_label_text(self.save_audio_button, self._t('effects_save_audio_button', 'Save audio'))
        set_label_text(self.open_saved_folder_button, self._t('effects_open_saved_folder_button', 'Open saved folder'))
        set_label_text(self.reset_all_button, self._t('effects_all_reset', 'Reset all effects'))
        section_titles = {
            'echo': self._t('effect_echo', 'Echo'),
            'reverb': self._t('effect_reverb', 'Reverb'),
            'vintage_filter': self._t('effect_vintage_filter', 'Vintage filter'),
        }
        field_titles = {
            'delay_ms': self._t('effect_echo_delay', 'Delay ms'),
            'decay': self._t('effect_echo_decay', 'Decay'),
            'decay_time': self._t('effect_reverb_decay', 'Decay time'),
            'wet_level': self._t('effect_reverb_wet', 'Wet level'),
            'cutoff_freq': self._t('effect_vintage_cutoff', 'Cutoff Hz'),
            'resonance': self._t('effect_vintage_resonance', 'Resonance'),
        }
        for effect_name, controls in self._section_widgets.items():
            set_label_text(controls['title'], section_titles[effect_name])
            set_label_text(controls['enabled'], self._t('effects_toggle', 'Enabled'))
            for param_name, _min_value, _max_value, _scale in self.EFFECT_FIELDS[effect_name]:
                set_label_text(controls[f'{param_name}_label'], field_titles[param_name])

    def update_theme_colors(self, *_: Any) -> None:
        colors = get_theme_colors(self.theme_manager)
        background = colors.get('panel_bg') or colors.get('bg_color')
        foreground = colors.get('text_color')
        accent = colors.get('button_color') or background
        widgets = [self.panel, self.sections_container, self.title_label, self.feedback_label]
        for effect_name, controls in self._section_widgets.items():
            widgets.extend([controls['panel'], controls['title'], controls['enabled']])
            for param_name, _min_value, _max_value, _scale in self.EFFECT_FIELDS[effect_name]:
                widgets.extend([controls[f'{param_name}_label'], controls[f'{param_name}_value']])
        for widget in widgets:
            apply_colors(widget, background=background, foreground=foreground)
        for control in [self.save_button, self.save_audio_button, self.open_saved_folder_button, self.reset_all_button]:
            apply_colors(control, background=accent, foreground=foreground)
        for effect_name, controls in self._section_widgets.items():
            for param_name, _min_value, _max_value, _scale in self.EFFECT_FIELDS[effect_name]:
                apply_colors(controls[f'{param_name}_slider'], background=accent, foreground=foreground)

    def _get_effects_export_dir(self) -> Path:
        getter = getattr(self.effects_controller, 'get_effects_export_dir', None)
        if callable(getter):
            try:
                candidate = getter()
            except EFFECTS_VIEW_EXCEPTIONS:
                logger.debug('Effects export folder resolution failed via controller.', exc_info=True)
            else:
                resolved = Path(str(candidate))
                resolved.mkdir(parents=True, exist_ok=True)
                return resolved
        return get_app_data_path('effects saved')

    def _resolve_export_source(self) -> str | None:
        current_track = getattr(self.player_controller, 'current_track', None)
        media_type = getattr(current_track, 'media_type', None)
        media_type_name = str(getattr(media_type, 'name', media_type) or '').upper()
        if media_type_name == 'VIDEO':
            return None

        current_file = getattr(self.audio_engine, 'current_file', None)
        if current_file:
            return str(current_file)

        track_path = getattr(current_track, 'path', None)
        if track_path:
            try:
                if Path(str(track_path)).suffix.lower() not in {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.webm'}:
                    return str(track_path)
            except OSError:
                return str(track_path)

        playback_source = getattr(self.audio_engine, 'playback_source_file', None)
        if playback_source:
            return str(playback_source)
        return None

    def _build_default_export_filename(self) -> str:
        current_track = getattr(self.player_controller, 'current_track', None)
        track_title = getattr(current_track, 'title', None)
        track_path = getattr(current_track, 'path', None)
        base_name = str(track_title or '').strip()
        if not base_name and track_path:
            try:
                base_name = Path(str(track_path)).stem
            except (OSError, TypeError, ValueError):
                base_name = ''
        if not base_name:
            source_path = self._resolve_export_source()
            if source_path:
                try:
                    base_name = Path(source_path).stem
                except (OSError, TypeError, ValueError):
                    base_name = ''
        stem = safe_filename(base_name or 'effects_audio')
        return f'{stem}_effects.wav'

    def _prompt_save_audio_path(self) -> Path | None:
        export_dir = self._get_effects_export_dir()
        default_name = self._build_default_export_filename()
        dialog_cls = getattr(self._wx, 'FileDialog', None)
        if dialog_cls is None:
            return export_dir / default_name
        style = getattr(self._wx, 'FD_SAVE', 0) | getattr(self._wx, 'FD_OVERWRITE_PROMPT', 0)
        dialog = dialog_cls(
            self.panel,
            message=self._t('effects_save_audio_dialog_title', 'Save processed audio'),
            defaultDir=str(export_dir),
            defaultFile=default_name,
            wildcard='WAV files (*.wav)|*.wav',
            style=style,
        )
        try:
            if dialog.ShowModal() == getattr(self._wx, 'ID_CANCEL', 0):
                return None
            getter = getattr(dialog, 'GetPath', None)
            if not callable(getter):
                return None
            selected = str(getter() or '').strip()
            if not selected:
                return None
            output_path = Path(selected)
            if output_path.suffix.lower() != '.wav':
                output_path = output_path.with_suffix('.wav')
            return output_path
        finally:
            destroy = getattr(dialog, 'Destroy', None)
            if callable(destroy):
                destroy()

    def _export_effects_audio(self, source_path: str, output_path: str) -> Path:
        source = Path(source_path)
        output = Path(output_path)
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f'Effects export source not found: {source}')

        audio_data, sample_rate = sf.read(str(source), dtype='float32', always_2d=True)
        processed_data = np.asarray(audio_data, dtype=np.float32)
        if processed_data.ndim != 2 or processed_data.shape[0] == 0:
            raise ValueError('Effects export source is empty or malformed.')

        if self.effects_engine is not None:
            try:
                setattr(self.effects_engine, 'sample_rate', int(sample_rate))
                setattr(self.effects_engine, 'channels', int(processed_data.shape[1]))
            except EFFECTS_VIEW_EXCEPTIONS:
                logger.debug('Unable to sync effects engine context before export.', exc_info=True)
            processed_data = np.asarray(self.effects_engine.apply_effects(processed_data), dtype=np.float32)

        if processed_data.size:
            peak = float(np.max(np.abs(processed_data)))
            if peak > 1.0:
                processed_data = processed_data / peak
            processed_data = np.clip(processed_data, -1.0, 1.0)

        output.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(output), processed_data.astype(np.float32), int(sample_rate), subtype='PCM_16')
        return output

    def _make_enabled_handler(self, effect_name: str) -> Any:
        def _handler(_event: Any | None = None) -> None:
            enabled = bool(self._section_widgets[effect_name]['enabled'].GetValue())
            setter = getattr(self.effects_controller, 'set_effect_enabled', None)
            if callable(setter):
                setter(effect_name, enabled)
            else:
                setter = getattr(self.effects_engine, 'set_effect_enabled', None)
                if callable(setter):
                    setter(effect_name, enabled)
        return _handler

    def _make_slider_handler(self, effect_name: str, param_name: str) -> Any:
        def _handler(_event: Any | None = None) -> None:
            value = self._slider_value(effect_name, param_name)
            setter = getattr(self.effects_controller, 'set_effect_parameter', None)
            if callable(setter):
                setter(effect_name, param_name, value)
            else:
                setter = getattr(self.effects_engine, 'set_effect_parameter', None)
                if callable(setter):
                    setter(effect_name, param_name, value)
            set_label_text(self._section_widgets[effect_name][f'{param_name}_value'], self._format_value(param_name, value))
        return _handler

    def _on_save(self, _event: Any | None = None) -> None:
        saver = getattr(self.effects_controller, 'save_current_settings_to_profile', None)
        if callable(saver):
            saver()

    def _on_open_saved_folder(self, _event: Any | None = None) -> None:
        export_dir = self._get_effects_export_dir()
        try:
            export_dir.mkdir(parents=True, exist_ok=True)
            open_directory(export_dir)
        except OSError:
            set_label_text(self.feedback_label, self._t('effects_open_saved_folder_error', 'Unable to open the saved effects folder.'))
            return
        set_label_text(self.feedback_label, self._t('effects_open_saved_folder_success', 'Effects folder opened: {folder}.', folder=str(export_dir)))

    def _on_save_audio(self, _event: Any | None = None) -> None:
        source_path = self._resolve_export_source()
        if not source_path:
            set_label_text(self.feedback_label, self._t('effects_save_audio_requires_audio', 'Start an audio track before exporting the processed audio.'))
            return

        output_path = self._prompt_save_audio_path()
        if output_path is None:
            return

        try:
            exported = self._export_effects_audio(source_path, str(output_path))
        except (OSError, RuntimeError, TypeError, ValueError):
            logger.debug('Effects audio export failed.', exc_info=True)
            set_label_text(self.feedback_label, self._t('effects_save_audio_error', 'Unable to export the processed audio.'))
            return

        set_label_text(self.feedback_label, self._t('effects_save_audio_success', 'Processed audio exported to {path}.', path=str(exported)))

    def _on_reset_all(self, _event: Any | None = None) -> None:
        resetter = getattr(self.effects_controller, 'reset_all_effects', None)
        if callable(resetter):
            resetter()
        else:
            resetter = getattr(self.effects_engine, 'reset_all_effects', None)
            if callable(resetter):
                resetter()
        self._refresh_from_settings(self._get_all_settings())

    def _on_effects_changed(self, payload: Any) -> None:
        if self._is_shutting_down or not isinstance(payload, dict):
            return
        settings = payload.get('settings', payload)
        if isinstance(settings, dict):
            self._refresh_from_settings(settings)

    def _on_feedback_message(self, payload: Any) -> None:
        if self._is_shutting_down or not isinstance(payload, dict):
            return
        message = str(payload.get('message') or '').strip()
        if message:
            set_label_text(self.feedback_label, message)

    def shutdown(self) -> None:
        self._is_shutting_down = True
        unregister_callback(
            self.localization_manager,
            'unregister_language_change_callback',
            self.update_localization,
            logger=logger,
            message='Unable to unregister wx EffectsView language callback.',
        )
        unregister_callback(
            self.theme_manager,
            'unregister_theme_change_callback',
            self.update_theme_colors,
            logger=logger,
            message='Unable to unregister wx EffectsView theme callback.',
        )
        unsubscribe = getattr(self.event_bus, 'unsubscribe', None)
        if callable(unsubscribe):
            for event_type, subscription in self._subscriptions:
                try:
                    unsubscribe(event_type, subscription)
                except EFFECTS_VIEW_EXCEPTIONS:
                    logger.debug('Unable to unsubscribe EffectsView from %s.', event_type, exc_info=True)
        self._subscriptions.clear()

    def show(self) -> None:
        self.panel.Show(True)
