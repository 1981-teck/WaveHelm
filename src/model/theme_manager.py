from __future__ import annotations

"""
Toolkit-agnostic ThemeManager for the maintained wxPython runtime.
"""

import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.audio.audio_events import AudioEventType
from src.model.theme_manager_builtins import BUILTIN_COLOR_THEME_NAMES, get_builtin_themes
from src.utils.durable_io import write_bytes_atomic_durable
from src.utils.helpers import get_app_data_path

logger = logging.getLogger(__name__)

LOCALIZATION_EXCEPTIONS = (AttributeError, KeyError, TypeError, ValueError)
FILE_EXCEPTIONS = (OSError, TypeError, ValueError, json.JSONDecodeError)
CALLBACK_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)
EVENT_BUS_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)
FORMAT_EXCEPTIONS = (KeyError, IndexError, ValueError)


class ThemeManager:
    def __init__(
        self,
        localization_manager: Optional[Any] = None,
        event_bus: Optional[Any] = None,
        initial_mode: str = 'dark',
        initial_color_theme: str = 'blue',
        default_mode: str = 'dark',
    ) -> None:
        self.localization_manager = localization_manager
        self.event_bus = event_bus

        self.mode = (initial_mode or 'dark').lower()
        self.color_theme = (initial_color_theme or 'blue').lower()
        self.default_mode = (default_mode or 'dark').lower()

        app_data_dir = Path(get_app_data_path())
        self.custom_themes_file = app_data_dir / 'custom_themes.json'

        self.themes: Dict[str, Dict[str, str]] = get_builtin_themes()
        self._load_custom_themes()
        self._theme_change_callbacks: List[Callable[[], None]] = []

        self._apply_theme_core()
        logger.info(
            self._get_localized_text('theme_manager_initialized').format(
                theme=self.mode,
                color=self.color_theme,
            )
        )

    # ------------------------------------------------------------------
    # Localizzazione
    # ------------------------------------------------------------------
    def _get_localized_text(self, key: str, **kwargs: Any) -> str:
        try:
            if self.localization_manager is not None:
                txt = self.localization_manager.get_text(key, **kwargs)
                if isinstance(txt, str) and txt.strip():
                    return txt
        except LOCALIZATION_EXCEPTIONS as error:
            logger.debug(
                "ThemeManager localization fallback for %s: %s",
                key,
                error,
                exc_info=True,
            )
        try:
            return f'[{key}]'.format(**kwargs) if kwargs else f'[{key}]'
        except FORMAT_EXCEPTIONS:
            return f'[{key}]'

    # ------------------------------------------------------------------
    # Temi: builtin + custom
    # ------------------------------------------------------------------
    def _load_builtin_themes(self) -> Dict[str, Dict[str, str]]:
        return get_builtin_themes()

    def _load_custom_themes(self) -> None:
        if not self.custom_themes_file.exists():
            logger.info(self._get_localized_text('custom_themes_file_not_found'))
            return

        try:
            with self.custom_themes_file.open('r', encoding='utf-8') as file_obj:
                custom_data = json.load(file_obj)
            if not isinstance(custom_data, dict):
                raise ValueError('custom themes JSON must be an object')
            for theme_name, colors in custom_data.items():
                if isinstance(colors, dict):
                    self.themes[str(theme_name).lower()] = colors
            logger.info('Temi personalizzati caricati da %s', self.custom_themes_file)
        except json.JSONDecodeError as exc:
            logger.error(
                'Errore lettura temi personalizzati JSON da %s: %s',
                self.custom_themes_file,
                exc,
            )
        except FILE_EXCEPTIONS as exc:
            logger.error(
                'Errore caricamento temi personalizzati da %s: %s',
                self.custom_themes_file,
                exc,
            )

    def save_custom_themes(self) -> None:
        builtin_names = set(self._load_builtin_themes().keys())
        custom_themes_to_save = {
            name: colors
            for name, colors in self.themes.items()
            if name not in builtin_names
        }
        try:
            payload = json.dumps(custom_themes_to_save, indent=4).encode('utf-8')
            directory_synced = write_bytes_atomic_durable(
                self.custom_themes_file, payload
            )
            if not directory_synced:
                logger.warning(
                    'Temi personalizzati salvati ma sync directory fallita: %s',
                    self.custom_themes_file,
                )
            logger.info('Temi personalizzati salvati in %s', self.custom_themes_file)
        except FILE_EXCEPTIONS as exc:
            logger.error(
                'Errore salvataggio temi personalizzati in %s: %s',
                self.custom_themes_file,
                exc,
            )

    # ------------------------------------------------------------------
    # Applicazione tema (toolkit-agnostica)
    # ------------------------------------------------------------------
    def _apply_theme_core(self) -> None:
        try:
            self.notify_theme_change()
        except CALLBACK_EXCEPTIONS:
            logger.debug('notify_theme_change failed', exc_info=True)

        try:
            if self.event_bus is not None:
                self.event_bus.publish(
                    AudioEventType.THEME_CHANGED,
                    {'mode': self.mode, 'color_theme': self.color_theme},
                )
        except EVENT_BUS_EXCEPTIONS:
            logger.debug('Could not publish THEME_CHANGED', exc_info=True)

    def _apply_ctk_theme(self) -> None:  # pragma: no cover - alias compatibilità
        self._apply_theme_core()

    # ------------------------------------------------------------------
    # API pubblica
    # ------------------------------------------------------------------
    def set_theme(self, new_mode: str, new_color_theme: str) -> bool:
        new_mode = (new_mode or self.default_mode).lower()
        new_color_theme = (new_color_theme or self.color_theme).lower()

        if new_mode not in self.themes and new_mode != 'system':
            new_mode = self.default_mode

        changed = (self.mode != new_mode) or (self.color_theme != new_color_theme)
        if not changed:
            logger.debug(self._get_localized_text('theme_no_change'))
            return False

        self.mode, self.color_theme = new_mode, new_color_theme
        self._apply_theme_core()
        logger.info(
            self._get_localized_text('theme_changed').format(
                mode=self.mode,
                color=self.color_theme,
            )
        )
        return True

    def get_current_theme_colors(self) -> Dict[str, str]:
        if self.mode == 'system':
            return self.themes.get(self.default_mode, self._load_builtin_themes()['dark'])
        return self.themes.get(self.mode, self.themes.get(self.default_mode, {}))

    def get_current_theme(self) -> Dict[str, str]:
        return self.get_current_theme_colors()

    def get_current_theme_name(self) -> str:
        return self.mode

    @property
    def current_theme_name(self) -> str:
        return self.mode

    @property
    def current_theme(self) -> Dict[str, str]:
        return self.get_current_theme_colors()

    def get_current_color_theme_name(self) -> str:
        return self.color_theme

    def get_default_theme_name(self) -> str:
        return self.default_mode

    def get_default_color_theme_name(self) -> str:
        return 'blue'

    def get_available_theme_names(self) -> List[str]:
        return list(self.themes.keys())

    def get_available_color_theme_names(self) -> List[str]:
        return list(BUILTIN_COLOR_THEME_NAMES)

    # ------------------------------------------------------------------
    # Tema personalizzato
    # ------------------------------------------------------------------
    def set_custom_theme_color(self, color_key: str, hex_color: str) -> None:
        if 'custom' not in self.themes:
            base = self._load_builtin_themes()['dark'].copy()
            base['name'] = 'custom'
            self.themes['custom'] = base

        self.themes['custom'][color_key] = hex_color
        self.save_custom_themes()
        self.set_theme('custom', self.color_theme)

    def get_custom_theme_colors(self) -> Dict[str, str]:
        return self.themes.get('custom', {})

    def reset_custom_theme_colors(self) -> None:
        base = self._load_builtin_themes()['dark'].copy()
        base['name'] = 'custom'
        self.themes['custom'] = base
        self.save_custom_themes()
        self.set_theme('custom', self.color_theme)

    # ------------------------------------------------------------------
    # Callback di cambio tema
    # ------------------------------------------------------------------
    def _callback_name(self, callback: Callable[[], None]) -> str:
        try:
            return getattr(callback, '__name__', repr(callback))
        except CALLBACK_EXCEPTIONS:
            return '<callback>'

    def register_theme_change_callback(
        self, callback: Callable[[], None]
    ) -> Callable[[], None]:
        if callback not in self._theme_change_callbacks:
            self._theme_change_callbacks.append(callback)
            logger.debug(
                self._get_localized_text('registered_theme_callback').format(
                    callback=self._callback_name(callback)
                )
            )
        return callback

    def unregister_theme_change_callback(self, callback: Callable[[], None]) -> None:
        if callback in self._theme_change_callbacks:
            self._theme_change_callbacks.remove(callback)
            logger.debug(
                self._get_localized_text('unregistered_theme_callback').format(
                    callback=self._callback_name(callback)
                )
            )

    def notify_theme_change(self) -> None:
        callbacks_snapshot = list(self._theme_change_callbacks)
        logger.debug(
            self._get_localized_text('notifying_theme_change').format(
                count=len(callbacks_snapshot)
            )
        )
        for callback in callbacks_snapshot:
            try:
                callback()
            except CALLBACK_EXCEPTIONS as exc:
                logger.exception(
                    self._get_localized_text('error_in_theme_callback').format(
                        callback=self._callback_name(callback),
                        error=exc,
                    )
                )

    # ------------------------------------------------------------------
    # Compatibilità con vecchio ensure_ttk_treeview_style
    # ------------------------------------------------------------------
    def ensure_ttk_treeview_style(
        self, style_name: str
    ) -> None:  # pragma: no cover - legacy no-op
        logger.debug(
            'ensure_ttk_treeview_style(%s) called - no-op in the current runtime',
            style_name,
        )

    # ------------------------------------------------------------------
    # Chiusura
    # ------------------------------------------------------------------
    def close(self) -> None:
        logger.info(self._get_localized_text('theme_manager_closing'))
        self._theme_change_callbacks.clear()
        logger.info(self._get_localized_text('theme_manager_closed'))
