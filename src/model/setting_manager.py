# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from src.model.localization_manager import LocalizationManager
from src.utils.helpers import get_user_data_dir

logger = logging.getLogger(__name__)

LOAD_EXCEPTIONS = (OSError, TypeError, ValueError, json.JSONDecodeError)
SAVE_EXCEPTIONS = (OSError, TypeError, ValueError)
DIR_EXCEPTIONS = (OSError, TypeError, ValueError)
FORMAT_EXCEPTIONS = (TypeError, ValueError)


class SettingsManager:
    """Minimal, robust settings store used across the app."""

    def __init__(
        self, localization_manager: Optional[LocalizationManager] = None
    ) -> None:
        self.localization_manager = localization_manager

        user_data_dir = Path(get_user_data_dir())
        self.SETTINGS_FILE = user_data_dir / 'settings.json'

        self.DEFAULT_SETTINGS: Dict[str, Any] = {
            'language': 'en',
            'theme': 'System',
            'primary_color': 'blue',
            'volume': 70,
            'shuffle_enabled': False,
            'loop_enabled': False,
            'video_hw_accel_enabled': True,
            'video_hw_device': 'auto',
            'video_target_fps': 60,
            'video_fast_seek': True,
            'video_drop_late_frames': True,
            'video_frame_queue_size': 10,
            'video_decode_threads': 0,
            'video_resize_quality_high': False,
            'cache_dir': str(user_data_dir / 'cache'),
            'ambient_muted': False,
            'ambient_volume': 0.4,
            'ambient_presets': {},
        }

        self._settings: Dict[str, Any] = {}
        self.load_settings()

    def load_settings(self) -> None:
        """Load settings from disk, falling back to defaults on any error."""
        try:
            if not self.SETTINGS_FILE.exists():
                logger.warning(
                    'Settings file not found. Creating defaults at %s',
                    self.SETTINGS_FILE,
                )
                self._settings = dict(self.DEFAULT_SETTINGS)
                self._ensure_dirs()
                self.save_settings()
                return

            with self.SETTINGS_FILE.open('r', encoding='utf-8') as file_obj:
                data = json.load(file_obj)
            if not isinstance(data, dict):
                raise ValueError('settings.json invalid structure')

            merged = dict(self.DEFAULT_SETTINGS)
            for key, value in data.items():
                merged[key] = self._normalize_setting(key, value)
            self._settings = merged
            self._ensure_dirs()
            logger.info('Settings loaded from %s', self.SETTINGS_FILE)
        except LOAD_EXCEPTIONS as exc:
            logger.error('Failed loading settings: %s', exc, exc_info=True)
            self._settings = dict(self.DEFAULT_SETTINGS)
            self._ensure_dirs()
            try:
                self.save_settings()
            except SAVE_EXCEPTIONS:
                logger.debug('Saving default settings after load failure did not succeed.', exc_info=True)

    def save_settings(self) -> None:
        try:
            self.SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with self.SETTINGS_FILE.open('w', encoding='utf-8') as file_obj:
                json.dump(self._settings, file_obj, indent=2, ensure_ascii=False)
        except SAVE_EXCEPTIONS as exc:
            logger.error('Failed saving settings: %s', exc, exc_info=True)

    def _ensure_dirs(self) -> None:
        for key in ('cache_dir',):
            directory = self._settings.get(key)
            if isinstance(directory, str):
                try:
                    Path(directory).mkdir(parents=True, exist_ok=True)
                except DIR_EXCEPTIONS:
                    logger.debug('Failed creating managed directory for key %s.', key, exc_info=True)

    def get_setting(self, key: str, default: Any = None) -> Any:
        return self._settings.get(key, default)

    def _normalize_setting(self, key: str, value: Any) -> Any:
        if key == 'volume':
            try:
                return max(0, min(100, int(value)))
            except FORMAT_EXCEPTIONS:
                return 70
        if key == 'video_target_fps':
            try:
                return max(1, min(120, int(value)))
            except FORMAT_EXCEPTIONS:
                return 60
        if key == 'video_frame_queue_size':
            try:
                return max(3, min(100, int(value)))
            except FORMAT_EXCEPTIONS:
                return 10
        if key == 'video_decode_threads':
            try:
                return max(0, min(64, int(value)))
            except FORMAT_EXCEPTIONS:
                return 0
        return value

    def set_setting(self, key: str, value: Any) -> None:
        self._settings[key] = self._normalize_setting(key, value)
        self.save_settings()

    def get_all_settings(self) -> Dict[str, Any]:
        return dict(self._settings)

    def reset_to_defaults(self) -> None:
        self._settings = dict(self.DEFAULT_SETTINGS)
        self._ensure_dirs()
        self.save_settings()
        logger.info('Settings reset to defaults')

    def is_ambient_muted(self) -> bool:
        return bool(self.get_setting('ambient_muted', False))

    def set_ambient_muted(self, muted: bool) -> None:
        self.set_setting('ambient_muted', bool(muted))
