from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from src.audio.audio_event_models import AudioEventType
from src.ui_wx.common import (
    WX_CALLBACK_EXCEPTIONS,
    apply_colors,
    autosize_choice_control,
    create_flow_sizer,
    get_localized_text,
    get_theme_colors,
    open_directory,
    register_callback,
    set_label_text,
)
from src.utils.helpers import get_app_data_path, safe_filename

logger = logging.getLogger(__name__)


class AmbientView:
    """wx-based ambient sound controller.

    Edge cases handled deterministically:
    1. Empty ambient libraries surface a visible selection hint instead of enabling a broken play path.
    2. Folder opening failures stay user-visible without breaking the rest of the panel.
    3. External ambient events resynchronize status, volume, and mute state to avoid stale controls.
    """

    def __init__(self, parent: Any, ambient_manager: Any = None, event_bus: Any = None, localization_manager: Any = None, theme_manager: Any = None, player_controller: Any = None, audio_engine: Any = None, **_: Any) -> None:
        self.parent = parent
        self.ambient_manager = ambient_manager
        self.event_bus = event_bus
        self.localization_manager = localization_manager
        self.theme_manager = theme_manager
        self.player_controller = player_controller
        self.audio_engine = audio_engine
        self._wx = self._import_wx_module()
        self.panel = self._wx.Panel(parent)
        self.panel.owner = self
        self._subscriptions: list[tuple[Any, Any]] = []
        self._selected_sound_name: str | None = None
        self._build_ui()
        self._bind_controls()
        self._register_callbacks()
        self._subscribe_to_events()
        self._sync_from_manager()
        self.update_localization()
        self.update_theme_colors()
        self._refresh_sound_list()

    @staticmethod
    def _import_wx_module() -> Any:
        import importlib
        return importlib.import_module('wx')

    def _build_ui(self) -> None:
        wx = self._wx
        root = wx.BoxSizer(wx.VERTICAL)
        self.title_label = wx.StaticText(self.panel, label='')
        self.subtitle_label = wx.StaticText(self.panel, label='')
        self.folder_label = wx.StaticText(self.panel, label='')
        self.sound_label = wx.StaticText(self.panel, label='')
        self.sound_choice = wx.Choice(self.panel)
        self.refresh_button = wx.Button(self.panel, label='')
        self.open_folder_button = wx.Button(self.panel, label='')
        self.play_button = wx.Button(self.panel, label='')
        self.stop_button = wx.Button(self.panel, label='')
        self.save_mix_button = wx.Button(self.panel, label='')
        self.open_mix_folder_button = wx.Button(self.panel, label='')
        self.status_label = wx.StaticText(self.panel, label='')
        self.volume_caption = wx.StaticText(self.panel, label='')
        self.volume_slider = wx.Slider(self.panel, value=40, minValue=0, maxValue=100)
        self.volume_value_label = wx.StaticText(self.panel, label='40%')
        self.mute_checkbox = wx.CheckBox(self.panel, label='')
        self.feedback_label = wx.StaticText(self.panel, label='')
        for widget in (self.title_label, self.subtitle_label, self.folder_label, self.sound_label, self.sound_choice, self.status_label, self.volume_caption, self.volume_slider, self.volume_value_label, self.mute_checkbox, self.feedback_label):
            root.Add(widget, 0, wx.ALL | wx.EXPAND, 8)
        button_row = create_flow_sizer(wx)
        for button in (
            self.refresh_button,
            self.open_folder_button,
            self.play_button,
            self.stop_button,
            self.save_mix_button,
            self.open_mix_folder_button,
        ):
            button_row.Add(button, 0, wx.ALL | wx.EXPAND, 6)
        root.Add(button_row, 0, wx.ALL | wx.EXPAND, 2)
        self.panel.SetSizer(root)

    def _bind_controls(self) -> None:
        wx = self._wx
        self.sound_choice.Bind(getattr(wx, 'EVT_CHOICE', wx.EVT_BUTTON), self._on_sound_selected)
        self.refresh_button.Bind(wx.EVT_BUTTON, self._on_refresh)
        self.open_folder_button.Bind(wx.EVT_BUTTON, self._on_open_folder)
        self.play_button.Bind(wx.EVT_BUTTON, self._on_play)
        self.stop_button.Bind(wx.EVT_BUTTON, self._on_stop)
        self.save_mix_button.Bind(wx.EVT_BUTTON, self._on_save_mix)
        self.open_mix_folder_button.Bind(wx.EVT_BUTTON, self._on_open_mix_folder)
        self.volume_slider.Bind(getattr(wx, 'EVT_SLIDER', wx.EVT_BUTTON), self._on_volume_changed)
        self.mute_checkbox.Bind(getattr(wx, 'EVT_CHECKBOX', wx.EVT_BUTTON), self._on_mute_changed)

    def _register_callbacks(self) -> None:
        register_callback(
            self.theme_manager,
            'register_theme_change_callback',
            self.update_theme_colors,
            logger=logger,
            message='Unable to register wx AmbientView theme callback.',
        )
        register_callback(
            self.localization_manager,
            'register_language_change_callback',
            self.update_localization,
            logger=logger,
            message='Unable to register wx AmbientView language callback.',
        )

    def _subscribe_to_events(self) -> None:
        subscribe = getattr(self.event_bus, 'subscribe', None)
        if not callable(subscribe):
            return
        bindings = {
            AudioEventType.AMBIENT_STARTED: self._handle_ambient_started,
            AudioEventType.AMBIENT_STOPPED: self._handle_ambient_stopped,
            AudioEventType.AMBIENT_VOLUME: self._handle_ambient_volume,
            AudioEventType.AMBIENT_MUTED_CHANGED: self._handle_ambient_muted,
        }
        for event_type, callback in bindings.items():
            try:
                subscription = subscribe(event_type, callback)
            except WX_CALLBACK_EXCEPTIONS:
                logger.debug('Ambient wx event subscription failed for %s.', event_type, exc_info=True)
                continue
            self._subscriptions.append((event_type, subscription))

    def _sync_from_manager(self) -> None:
        manager = self.ambient_manager
        if manager is None:
            return
        get_volume = getattr(manager, 'get_ambient_volume', None)
        get_muted = getattr(manager, 'is_ambient_muted', None)
        if callable(get_volume):
            self._set_volume_value(float(get_volume()))
        if callable(get_muted):
            self.mute_checkbox.SetValue(bool(get_muted()))
        self._update_status_label()

    def _refresh_sound_list(self) -> None:
        items = []
        getter = getattr(self.ambient_manager, 'get_available_ambient_sounds', None)
        if callable(getter):
            items = list(getter())
        self.sound_choice.SetItems(items)
        autosize_choice_control(self.sound_choice, items)
        selected = self._selected_sound_name or self._get_current_sound_name()
        if selected and selected in items:
            self.sound_choice.SetStringSelection(selected)
        elif items:
            self.sound_choice.SetSelection(0)
            self._selected_sound_name = self.sound_choice.GetStringSelection()
        else:
            self._selected_sound_name = None
        self._update_status_label()

    def _get_current_sound_name(self) -> str | None:
        getter = getattr(self.ambient_manager, 'get_current_sound_name', None)
        if not callable(getter):
            return None
        value = getter()
        return str(value) if value else None

    def _selected_sound(self) -> str | None:
        getter = getattr(self.sound_choice, 'GetStringSelection', None)
        if not callable(getter):
            return self._selected_sound_name
        selection = getter()
        return str(selection) if selection else self._selected_sound_name

    def _set_volume_value(self, volume: float) -> None:
        percent = max(0, min(100, int(round(float(volume) * 100))))
        self.volume_slider.SetValue(percent)
        set_label_text(self.volume_value_label, f'{percent}%')

    def _update_status_label(self) -> None:
        if self.ambient_manager is None:
            set_label_text(self.status_label, get_localized_text(self.localization_manager, 'ambient_status_idle', 'No ambient sound active.'))
            return
        is_playing = getattr(self.ambient_manager, 'is_playing', None)
        current_name = self._get_current_sound_name() or self._selected_sound_name
        if callable(is_playing) and is_playing() and current_name:
            text = get_localized_text(self.localization_manager, 'ambient_status_playing', 'Playing: {name}', name=current_name)
        elif current_name:
            text = get_localized_text(self.localization_manager, 'ambient_status_selected', 'Selected: {name}', name=current_name)
        else:
            text = get_localized_text(self.localization_manager, 'ambient_status_idle', 'No ambient sound active.')
        set_label_text(self.status_label, text)

    def update_localization(self, *_: Any) -> None:
        set_label_text(self.title_label, get_localized_text(self.localization_manager, 'ambient_sounds_title', 'Ambient sounds'))
        set_label_text(self.subtitle_label, get_localized_text(self.localization_manager, 'ambient_sounds_subtitle', 'Play background sounds independently from the main audio.'))
        set_label_text(self.sound_label, get_localized_text(self.localization_manager, 'ambient_sound_label', 'Ambient sound'))
        folder = self._get_primary_folder()
        set_label_text(self.folder_label, get_localized_text(self.localization_manager, 'ambient_folder_hint', 'Put ambient audio files here: {folder}', folder=str(folder)))
        set_label_text(self.refresh_button, get_localized_text(self.localization_manager, 'ambient_refresh_button', 'Refresh'))
        set_label_text(self.open_folder_button, get_localized_text(self.localization_manager, 'ambient_open_folder_button', 'Open folder'))
        set_label_text(self.play_button, get_localized_text(self.localization_manager, 'play_ambient_sound_button', 'Play'))
        set_label_text(self.stop_button, get_localized_text(self.localization_manager, 'stop_ambient_sound_button', 'Stop'))
        set_label_text(self.save_mix_button, get_localized_text(self.localization_manager, 'ambient_save_mix_button', 'Save mix'))
        set_label_text(self.open_mix_folder_button, get_localized_text(self.localization_manager, 'ambient_open_mix_folder_button', 'Open mix folder'))
        set_label_text(self.volume_caption, get_localized_text(self.localization_manager, 'ambient_volume_label', 'Volume'))
        set_label_text(self.mute_checkbox, get_localized_text(self.localization_manager, 'mute_ambient_label', 'Mute'))
        self._refresh_sound_list()

    def update_theme_colors(self) -> None:
        colors = get_theme_colors(self.theme_manager)
        background = colors.get('panel_bg') or colors.get('bg_color')
        foreground = colors.get('text_color')
        accent = colors.get('button_color') or background
        apply_colors(self.panel, background=background, foreground=foreground)
        widgets = [
            self.title_label,
            self.subtitle_label,
            self.folder_label,
            self.sound_label,
            self.sound_choice,
            self.status_label,
            self.volume_caption,
            self.volume_slider,
            self.volume_value_label,
            self.mute_checkbox,
            self.feedback_label,
        ]
        for widget in widgets:
            apply_colors(widget, background=background, foreground=foreground)
        for button in (
            self.refresh_button,
            self.open_folder_button,
            self.play_button,
            self.stop_button,
            self.save_mix_button,
            self.open_mix_folder_button,
        ):
            apply_colors(button, background=accent, foreground=foreground)

    def _get_primary_folder(self) -> Path:
        getter = getattr(self.ambient_manager, 'get_primary_ambient_dir', None)
        if callable(getter):
            try:
                return Path(getter())
            except (OSError, TypeError, ValueError):
                logger.debug('Ambient primary folder resolution failed.', exc_info=True)
        return Path.cwd()

    def _get_mix_export_dir(self) -> Path:
        getter = getattr(self.ambient_manager, 'get_mix_export_dir', None)
        if callable(getter):
            try:
                return Path(getter())
            except (OSError, TypeError, ValueError):
                logger.debug('Ambient mix export folder resolution failed.', exc_info=True)
        return get_app_data_path('ambient mix saved')

    def _build_default_mix_filename(self) -> str:
        current_track = getattr(self.player_controller, 'current_track', None)
        track_title = getattr(current_track, 'title', None)
        track_path = getattr(current_track, 'path', None)
        base_name = str(track_title or '').strip()
        if not base_name and track_path:
            try:
                base_name = Path(str(track_path)).stem
            except (OSError, TypeError, ValueError):
                base_name = ''
        ambient_name = self._selected_sound() or self._get_current_sound_name() or 'ambient'
        stem = safe_filename(base_name or 'ambient_mix')
        overlay = safe_filename(str(ambient_name) or 'ambient')
        combined = '_'.join(part for part in (stem, overlay, 'mix') if part)
        return f"{combined or 'ambient_mix'}.wav"

    def _prompt_save_mix_path(self) -> Path | None:
        export_dir = self._get_mix_export_dir()
        default_name = self._build_default_mix_filename()
        dialog_cls = getattr(self._wx, 'FileDialog', None)
        if dialog_cls is None:
            return export_dir / default_name
        style = getattr(self._wx, 'FD_SAVE', 0) | getattr(self._wx, 'FD_OVERWRITE_PROMPT', 0)
        dialog = dialog_cls(
            self.panel,
            message=get_localized_text(self.localization_manager, 'ambient_save_mix_dialog_title', 'Save mixed audio session'),
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

    def _resolve_main_mix_source(self) -> str | None:
        current_track = getattr(self.player_controller, 'current_track', None)
        media_type = getattr(current_track, 'media_type', None)
        media_type_name = str(getattr(media_type, 'name', media_type) or '').upper()
        if media_type_name == 'VIDEO':
            return None
        playback_source = getattr(self.audio_engine, 'playback_source_file', None)
        if playback_source:
            return str(playback_source)
        current_file = getattr(self.audio_engine, 'current_file', None)
        if current_file:
            return str(current_file)
        track_path = getattr(current_track, 'path', None)
        return str(track_path) if track_path else None

    def _on_sound_selected(self, _event: Any | None = None) -> None:
        self._selected_sound_name = self._selected_sound()
        self._update_status_label()

    def _on_refresh(self, _event: Any | None = None) -> None:
        self._refresh_sound_list()
        if self._selected_sound_name is None:
            set_label_text(self.feedback_label, get_localized_text(self.localization_manager, 'ambient_no_sounds_found', 'No sounds found. Add audio files to the ambient folder.'))
            return
        set_label_text(self.feedback_label, get_localized_text(self.localization_manager, 'ambient_refresh_button', 'Refresh'))

    def _on_open_folder(self, _event: Any | None = None) -> None:
        try:
            open_directory(self._get_primary_folder())
        except OSError:
            set_label_text(self.feedback_label, get_localized_text(self.localization_manager, 'ambient_open_folder_error', 'Unable to open the ambient folder.'))

    def _on_open_mix_folder(self, _event: Any | None = None) -> None:
        export_dir = self._get_mix_export_dir()
        try:
            export_dir.mkdir(parents=True, exist_ok=True)
            open_directory(export_dir)
        except OSError:
            set_label_text(self.feedback_label, get_localized_text(self.localization_manager, 'ambient_open_mix_folder_error', 'Unable to open the ambient mix folder.'))
            return
        set_label_text(self.feedback_label, get_localized_text(self.localization_manager, 'ambient_open_mix_folder_success', 'Ambient mix folder opened: {folder}', folder=str(export_dir)))

    def _on_play(self, _event: Any | None = None) -> None:
        selection = self._selected_sound()
        if not selection:
            set_label_text(self.feedback_label, get_localized_text(self.localization_manager, 'ambient_select_sound_first', 'Select an ambient sound first.'))
            return
        play = getattr(self.ambient_manager, 'play_ambient_sound', None)
        if callable(play) and play(selection):
            self._selected_sound_name = selection
            set_label_text(self.feedback_label, get_localized_text(self.localization_manager, 'ambient_sound_started', 'Ambient sound started: {file}', file=selection))
            self._update_status_label()
            return
        set_label_text(self.feedback_label, get_localized_text(self.localization_manager, 'ambient_play_error', 'Unable to start the selected ambient sound.'))

    def _on_stop(self, _event: Any | None = None) -> None:
        stop = getattr(self.ambient_manager, 'stop_ambient_sound', None)
        if callable(stop):
            stop()
        set_label_text(self.feedback_label, get_localized_text(self.localization_manager, 'ambient_sound_stopped', 'Ambient sound stopped.'))
        self._update_status_label()

    def _on_save_mix(self, _event: Any | None = None) -> None:
        current_source_getter = getattr(self.ambient_manager, 'get_current_source', None)
        selection = self._selected_sound() or self._get_current_sound_name()
        if not selection and callable(current_source_getter):
            current_source = current_source_getter()
            selection = str(current_source) if current_source else None
        if not selection:
            set_label_text(self.feedback_label, get_localized_text(self.localization_manager, 'ambient_select_sound_first', 'Select an ambient sound first.'))
            return

        main_source = self._resolve_main_mix_source()
        if not main_source:
            set_label_text(self.feedback_label, get_localized_text(self.localization_manager, 'ambient_save_mix_requires_audio', 'Start an audio track before exporting the mixed session.'))
            return

        output_path = self._prompt_save_mix_path()
        if output_path is None:
            return

        exporter = getattr(self.ambient_manager, 'export_audio_mix', None)
        if not callable(exporter):
            set_label_text(self.feedback_label, get_localized_text(self.localization_manager, 'ambient_save_mix_unavailable', 'Mixed-session export is not available in this runtime.'))
            return

        get_volume = getattr(self.audio_engine, 'get_volume', None)
        is_muted = getattr(self.audio_engine, 'is_muted', None)
        main_volume = float(get_volume()) if callable(get_volume) else 1.0
        main_muted = bool(is_muted()) if callable(is_muted) else False

        try:
            exported = exporter(
                main_source_path=main_source,
                output_path=str(output_path),
                ambient_sound_name_or_path=selection,
                main_volume=main_volume,
                main_muted=main_muted,
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            logger.debug('Ambient mix export failed.', exc_info=True)
            set_label_text(self.feedback_label, get_localized_text(self.localization_manager, 'ambient_save_mix_error', 'Unable to export the mixed audio session.'))
            return

        set_label_text(self.feedback_label, get_localized_text(self.localization_manager, 'ambient_save_mix_success', 'Mixed audio exported to {path}.', path=str(exported)))

    def _on_volume_changed(self, _event: Any | None = None) -> None:
        volume = float(self.volume_slider.GetValue()) / 100.0
        self._set_volume_value(volume)
        setter = getattr(self.ambient_manager, 'set_ambient_volume', None)
        if callable(setter):
            setter(volume)

    def _on_mute_changed(self, _event: Any | None = None) -> None:
        setter = getattr(self.ambient_manager, 'set_ambient_muted', None)
        if callable(setter):
            setter(bool(self.mute_checkbox.GetValue()))
        self._update_status_label()

    def _handle_ambient_started(self, payload: Any) -> None:
        name = str((payload or {}).get('name') or self._selected_sound_name or '')
        self._selected_sound_name = name or self._selected_sound_name
        self._update_status_label()

    def _handle_ambient_stopped(self, _payload: Any) -> None:
        self._update_status_label()

    def _handle_ambient_volume(self, payload: Any) -> None:
        self._set_volume_value(float((payload or {}).get('volume', 0.0)))

    def _handle_ambient_muted(self, payload: Any) -> None:
        self.mute_checkbox.SetValue(bool((payload or {}).get('muted', False)))
        self._update_status_label()

    def shutdown(self) -> None:
        unsubscribe = getattr(self.event_bus, 'unsubscribe', None)
        if callable(unsubscribe):
            for event_type, subscription in self._subscriptions:
                try:
                    unsubscribe(event_type, subscription=subscription)
                except WX_CALLBACK_EXCEPTIONS:
                    logger.debug('Ambient wx unsubscribe failed for %s.', event_type, exc_info=True)
        self._subscriptions.clear()

    def show(self) -> None:
        self.panel.Show(True)
