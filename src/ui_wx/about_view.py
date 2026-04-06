from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from src.config.app_metadata import load_app_metadata
from src.ui_wx.common import (
    apply_colors,
    get_localized_text,
    get_theme_colors,
    register_callback,
    set_label_text,
)
from src.utils.helpers import get_app_data_path

logger = logging.getLogger(__name__)


class AboutView:
    """wx-based application metadata view.

    Edge cases handled deterministically:
    1. Invalid or missing metadata files fall back to canonical app defaults.
    2. Theme/localization callback registration failures remain best-effort and non-fatal.
    3. Export failures surface a visible feedback message instead of silently discarding user intent.
    """

    CONTACT_FIELDS = ('email',)

    def __init__(self, parent: Any, localization_manager: Any = None, theme_manager: Any = None, app_info_path: Path | None = None, **_: Any) -> None:
        self.parent = parent
        self.localization_manager = localization_manager
        self.theme_manager = theme_manager
        self._wx = self._import_wx_module()
        self.panel = self._wx.ScrolledWindow(parent) if hasattr(self._wx, 'ScrolledWindow') else self._wx.Panel(parent)
        self.panel.owner = self
        self.app_info = load_app_metadata(app_info_path)
        self._info_labels: dict[str, Any] = {}
        self._module_labels: list[Any] = []
        self._library_labels: list[Any] = []
        self._contact_labels: dict[str, Any] = {}
        self._build_ui()
        self._register_callbacks()
        self.update_localization()
        self.update_theme_colors()

    @staticmethod
    def _import_wx_module() -> Any:
        import importlib
        return importlib.import_module('wx')

    def _build_ui(self) -> None:
        wx = self._wx
        if hasattr(self.panel, 'SetScrollRate'):
            self.panel.SetScrollRate(16, 16)
        root = wx.BoxSizer(wx.VERTICAL)
        self.title_label = wx.StaticText(self.panel, label='')
        root.Add(self.title_label, 0, wx.ALL | wx.EXPAND, 12)
        self._create_general_section(root)
        self.modules_title_label = wx.StaticText(self.panel, label='')
        root.Add(self.modules_title_label, 0, wx.ALL | wx.EXPAND, 12)
        self._module_labels = self._create_dynamic_labels(root, self.app_info.get('modules_enabled', []))
        self.libraries_title_label = wx.StaticText(self.panel, label='')
        root.Add(self.libraries_title_label, 0, wx.ALL | wx.EXPAND, 12)
        self._library_labels = self._create_dynamic_labels(root, self.app_info.get('libraries_used', []))
        self.contact_title_label = wx.StaticText(self.panel, label='')
        root.Add(self.contact_title_label, 0, wx.ALL | wx.EXPAND, 12)
        self._create_contact_labels(root)
        self.export_button = wx.Button(self.panel, label='')
        self.export_button.Bind(wx.EVT_BUTTON, self._on_export)
        root.Add(self.export_button, 0, wx.ALL | wx.EXPAND, 12)
        self.feedback_label = wx.StaticText(self.panel, label='')
        root.Add(self.feedback_label, 0, wx.ALL | wx.EXPAND, 12)
        self.panel.SetSizer(root)

    def _create_general_section(self, root: Any) -> None:
        field_names = ('version', 'author', 'license', 'target_platform', 'website_url')
        for field_name in field_names:
            label = self._wx.StaticText(self.panel, label='')
            root.Add(label, 0, self._wx.ALL | self._wx.EXPAND, 8)
            self._info_labels[field_name] = label

    def _create_dynamic_labels(self, root: Any, items: Any) -> list[Any]:
        labels: list[Any] = []
        for _item in items if isinstance(items, list) else []:
            label = self._wx.StaticText(self.panel, label='')
            root.Add(label, 0, self._wx.ALL | self._wx.EXPAND, 6)
            labels.append(label)
        return labels

    def _create_contact_labels(self, root: Any) -> None:
        for field_name in self.CONTACT_FIELDS:
            if field_name not in self.app_info.get('contact', {}):
                continue
            label = self._wx.StaticText(self.panel, label='')
            root.Add(label, 0, self._wx.ALL | self._wx.EXPAND, 6)
            self._contact_labels[field_name] = label

    def _register_callbacks(self) -> None:
        register_callback(
            self.theme_manager,
            'register_theme_change_callback',
            self.update_theme_colors,
            logger=logger,
            message='Unable to register wx AboutView theme callback.',
        )
        register_callback(
            self.localization_manager,
            'register_language_change_callback',
            self.update_localization,
            logger=logger,
            message='Unable to register wx AboutView language callback.',
        )

    def update_localization(self, *_: Any) -> None:
        general = dict(self.app_info.get('general', {}))
        set_label_text(self.title_label, general.get('app_name', 'WaveHelm'))
        mappings = {
            'version': ('version_label', 'Version: {version}'),
            'author': ('author_label', 'Author: {author}'),
            'license': ('license_label', 'License: {license}'),
            'target_platform': ('target_platform_label', 'Target platform: {target_platform}'),
            'website_url': ('website_label', 'Website: {website_url}'),
        }
        for field_name, (key, default) in mappings.items():
            value = str(general.get(field_name, ''))
            set_label_text(self._info_labels[field_name], get_localized_text(self.localization_manager, key, default, **{field_name: value}))
        set_label_text(self.modules_title_label, get_localized_text(self.localization_manager, 'modules_enabled_title', 'Enabled modules'))
        self._update_named_list(self._module_labels, self.app_info.get('modules_enabled', []), 'module_')
        set_label_text(self.libraries_title_label, get_localized_text(self.localization_manager, 'libraries_used_title', 'Libraries used'))
        self._update_named_list(self._library_labels, self.app_info.get('libraries_used', []), 'library_')
        set_label_text(self.contact_title_label, get_localized_text(self.localization_manager, 'contact_title', 'Contact'))
        self._update_contact_labels()
        set_label_text(self.export_button, get_localized_text(self.localization_manager, 'export_app_info_button', 'Export app info'))

    def _update_named_list(self, labels: list[Any], values: Any, prefix: str) -> None:
        items = values if isinstance(values, list) else []
        for label, value in zip(labels, items):
            key = f'{prefix}{value}'
            localized = get_localized_text(self.localization_manager, key, str(value).replace('_', ' ').title())
            set_label_text(label, f'• {localized}')

    def _update_contact_labels(self) -> None:
        contact = dict(self.app_info.get('contact', {}))
        for field_name, label in self._contact_labels.items():
            title = get_localized_text(self.localization_manager, f'contact_{field_name}', field_name.replace('_', ' ').title())
            set_label_text(label, f'{title}: {contact.get(field_name, "")}'.strip())

    def update_theme_colors(self) -> None:
        colors = get_theme_colors(self.theme_manager)
        background = colors.get('panel_bg') or colors.get('bg_color')
        foreground = colors.get('text_color')
        accent = colors.get('button_color') or background
        apply_colors(self.panel, background=background, foreground=foreground)
        for widget in [self.title_label, self.modules_title_label, self.libraries_title_label, self.contact_title_label, self.feedback_label, *self._info_labels.values(), *self._module_labels, *self._library_labels, *self._contact_labels.values()]:
            apply_colors(widget, background=background, foreground=foreground)
        apply_colors(self.export_button, background=accent, foreground=foreground)

    def _on_export(self, _event: Any | None = None) -> None:
        export_path = get_app_data_path('exports', 'wavehelm_app_info.json')
        try:
            export_path.write_text(json.dumps(self.app_info, indent=2, ensure_ascii=False), encoding='utf-8')
        except OSError:
            set_label_text(self.feedback_label, get_localized_text(self.localization_manager, 'error_exporting_app_info', 'Unable to export application metadata.'))
            return
        set_label_text(self.feedback_label, get_localized_text(self.localization_manager, 'app_info_exported_feedback', 'Application metadata exported to {path}.', path=str(export_path)))

    def shutdown(self) -> None:
        return None

    def show(self) -> None:
        self.panel.Show(True)
