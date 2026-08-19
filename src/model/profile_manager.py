from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from src.utils.helpers import get_app_data_path

logger = logging.getLogger(__name__)

APP_DIR = get_app_data_path()
APP_DIR.mkdir(parents=True, exist_ok=True)
PROFILE_PATH = APP_DIR / "profile.json"

FILE_IO_EXCEPTIONS = (OSError, TypeError, ValueError)
JSON_LOAD_EXCEPTIONS = (json.JSONDecodeError, OSError, TypeError, ValueError)
PROFILE_DATA_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)


@dataclass
class UserProfile:
    stats: Dict[str, int] = field(default_factory=dict)
    effects_settings: Dict[str, Any] = field(default_factory=dict)
    eq_settings: Dict[str, Any] = field(default_factory=dict)
    custom_eq_presets: Dict[str, Any] = field(default_factory=dict)
    custom_effects_settings: Dict[str, Any] = field(default_factory=dict)
    home_stats_prefs: Dict[str, Any] = field(default_factory=dict)


class ProfileManager:
    """Gestione profilo file-based (~/.wavehelm/profile.json)."""

    def __init__(
        self,
        db_manager: Optional[object] = None,
        localization_manager: Optional[object] = None,
        event_bus: Optional[object] = None,
    ):
        self.db_manager = db_manager
        self.localization_manager = localization_manager
        self.event_bus = event_bus
        self.user_profile: UserProfile = self._load_profile()
        logger.info("[ProfileManager] inizializzato (file-based).")

    def _load_profile(self) -> UserProfile:
        try:
            if PROFILE_PATH.is_file():
                with open(PROFILE_PATH, "r", encoding="utf-8") as file_obj:
                    data = json.load(file_obj) or {}
                return UserProfile(
                    stats=dict(data.get("stats", {})),
                    effects_settings=dict(data.get("effects_settings", {})),
                    eq_settings=dict(data.get("eq_settings", {})),
                    custom_eq_presets=dict(data.get("custom_eq_presets", {})),
                    custom_effects_settings=dict(data.get("custom_effects_settings", {})),
                    home_stats_prefs=dict(data.get("home_stats_prefs", {})),
                )
        except JSON_LOAD_EXCEPTIONS as error:
            logger.warning("[ProfileManager] lettura profilo fallita: %s", error)
        return UserProfile()

    def _save(self) -> None:
        try:
            data = {
                "stats": self.user_profile.stats,
                "effects_settings": self.user_profile.effects_settings,
                "eq_settings": self.user_profile.eq_settings,
                "custom_eq_presets": self.user_profile.custom_eq_presets,
                "custom_effects_settings": self.user_profile.custom_effects_settings,
                "home_stats_prefs": self.user_profile.home_stats_prefs,
            }
            with open(PROFILE_PATH, "w", encoding="utf-8") as file_obj:
                json.dump(data, file_obj, ensure_ascii=False, indent=2)
        except FILE_IO_EXCEPTIONS as error:
            logger.error("[ProfileManager] salvataggio profilo fallito: %s", error, exc_info=True)

    def increment_stat(self, key: str, amount: int = 1) -> None:
        try:
            current_value = int(self.user_profile.stats.get(key, 0))
            self.user_profile.stats[key] = current_value + int(amount)
            self._save()
        except PROFILE_DATA_EXCEPTIONS as error:
            logger.error("[ProfileManager] increment_stat error: %s", error, exc_info=True)

    def get_stat(self, key: str) -> int:
        try:
            return int(self.user_profile.stats.get(key, 0))
        except PROFILE_DATA_EXCEPTIONS:
            return 0

    def set_effects_settings(self, settings: Dict[str, Any]) -> None:
        try:
            self.user_profile.effects_settings = dict(settings or {})
            self._save()
        except PROFILE_DATA_EXCEPTIONS as error:
            logger.error("[ProfileManager] set_effects_settings error: %s", error, exc_info=True)

    def get_effects_settings(self) -> Dict[str, Any]:
        try:
            return dict(self.user_profile.effects_settings or {})
        except PROFILE_DATA_EXCEPTIONS:
            return {}

    def set_eq_settings(self, settings: Dict[str, Any]) -> None:
        try:
            self.user_profile.eq_settings = dict(settings or {})
            self._save()
        except PROFILE_DATA_EXCEPTIONS as error:
            logger.error("[ProfileManager] set_eq_settings error: %s", error, exc_info=True)

    def get_eq_settings(self) -> Dict[str, Any]:
        try:
            return dict(self.user_profile.eq_settings or {})
        except PROFILE_DATA_EXCEPTIONS:
            return {}

    def get_custom_eq_presets(self) -> Dict[str, Any]:
        try:
            return dict(self.user_profile.custom_eq_presets or {})
        except PROFILE_DATA_EXCEPTIONS:
            return {}

    def save_custom_eq_preset(self, name: str, preset_data: Dict[str, Any]) -> None:
        try:
            self.user_profile.custom_eq_presets[str(name)] = dict(preset_data or {})
            self._save()
        except PROFILE_DATA_EXCEPTIONS as error:
            logger.error("[ProfileManager] save_custom_eq_preset error: %s", error, exc_info=True)

    def delete_custom_eq_preset(self, name: str) -> None:
        try:
            self.user_profile.custom_eq_presets.pop(str(name), None)
            self._save()
        except PROFILE_DATA_EXCEPTIONS as error:
            logger.error("[ProfileManager] delete_custom_eq_preset error: %s", error, exc_info=True)

    def get_custom_effects_settings(self) -> Dict[str, Any]:
        try:
            return dict(self.user_profile.custom_effects_settings or {})
        except PROFILE_DATA_EXCEPTIONS:
            return {}

    def save_custom_effects_setting(self, name: str, settings: Dict[str, Any]) -> None:
        try:
            self.user_profile.custom_effects_settings[str(name)] = dict(settings or {})
            self._save()
        except PROFILE_DATA_EXCEPTIONS as error:
            logger.error("[ProfileManager] save_custom_effects_setting error: %s", error, exc_info=True)

    def delete_custom_effects_setting(self, name: str) -> None:
        try:
            self.user_profile.custom_effects_settings.pop(str(name), None)
            self._save()
        except PROFILE_DATA_EXCEPTIONS as error:
            logger.error("[ProfileManager] delete_custom_effects_setting error: %s", error, exc_info=True)

    def get_profile_setting(self, key: str, default: str = "") -> str:
        try:
            profile_dict = getattr(self, "profile", None)
            if isinstance(profile_dict, dict) and key in profile_dict:
                return str(profile_dict.get(key, default))
            settings_dict = getattr(self, "settings", None)
            if isinstance(settings_dict, dict) and key in settings_dict:
                return str(settings_dict.get(key, default))
        except PROFILE_DATA_EXCEPTIONS as error:
            logger.debug(
                "[ProfileManager] get_profile_setting fallback for %s: %s",
                key,
                error,
                exc_info=True,
            )
        return str(default)

    def set_home_stats_prefs(self, prefs: Dict[str, Any]) -> None:
        try:
            self.user_profile.home_stats_prefs = dict(prefs or {})
            self._save()
        except PROFILE_DATA_EXCEPTIONS as error:
            logger.error("[ProfileManager] set_home_stats_prefs error: %s", error, exc_info=True)

    def get_home_stats_prefs(self) -> Dict[str, Any]:
        try:
            return dict(self.user_profile.home_stats_prefs or {})
        except PROFILE_DATA_EXCEPTIONS:
            return {}
