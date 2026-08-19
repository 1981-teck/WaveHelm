# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
import math
import os
import tempfile
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

from src.model.localization_manager import LocalizationManager
from src.utils.durable_io import durable_replace, sync_parent_directory
from src.utils.exceptions import SettingsError
from src.utils.helpers import get_user_data_dir

logger = logging.getLogger(__name__)

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
SettingsData: TypeAlias = dict[str, JsonValue]

MANAGED_DIRECTORY_MARKER = ".wavehelm-managed"
MAX_IMPORT_ITEMS = 256
MAX_JSON_DEPTH = 12
MAX_COLLECTION_ITEMS = 10_000

PORTABLE_SETTING_KEYS = frozenset(
    {
        "language",
        "theme",
        "primary_color",
        "volume",
        "shuffle_enabled",
        "loop_enabled",
        "video_hw_accel_enabled",
        "video_hw_device",
        "video_target_fps",
        "video_fast_seek",
        "video_drop_late_frames",
        "video_frame_queue_size",
        "video_decode_threads",
        "video_resize_quality_high",
        "ambient_muted",
        "ambient_volume",
        "ambient_presets",
        "equalizer_enabled",
        "eq_last_preset",
        "eq_last_gains",
        "ui_library_column_widths",
        "ui_playlist_playlist_column_widths",
        "ui_playlist_track_column_widths",
        "ui_favorites_column_widths",
    }
)
PROTECTED_SETTING_KEYS = frozenset({"cache_dir"})

LOAD_EXCEPTIONS = (OSError, TypeError, ValueError, json.JSONDecodeError, SettingsError)
SERIALIZATION_EXCEPTIONS = (OSError, TypeError, ValueError)
FORMAT_EXCEPTIONS = (TypeError, ValueError)


@dataclass(frozen=True, slots=True)
class SettingsImportResult:
    """Result of an atomic settings import."""

    updated_keys: tuple[str, ...]
    ignored_keys: tuple[str, ...]


class SettingsManager:
    """Persistent application settings with atomic, fail-closed writes.

    Edge cases:
    - A write can fail after a user-visible setting change has been requested.
    - An imported file can contain unknown keys, non-JSON values, or a hostile cache path.
    - A process interruption can occur while the settings file is being replaced.

    Mitigations:
    - Persist a complete candidate snapshot before committing it to memory.
    - Validate imports against an explicit portable-key allowlist and ignore protected paths.
    - Write to a same-directory temporary file, flush it, and replace atomically.
    """

    def __init__(
        self,
        localization_manager: LocalizationManager | None = None,
    ) -> None:
        self.localization_manager = localization_manager
        user_data_dir = Path(get_user_data_dir()).resolve()
        self.SETTINGS_FILE = user_data_dir / "settings.json"
        self._managed_cache_dir = user_data_dir / "cache"
        self.DEFAULT_SETTINGS: SettingsData = {
            "language": "en",
            "theme": "System",
            "primary_color": "blue",
            "volume": 70,
            "shuffle_enabled": False,
            "loop_enabled": False,
            "video_hw_accel_enabled": True,
            "video_hw_device": "auto",
            "video_target_fps": 60,
            "video_fast_seek": True,
            "video_drop_late_frames": True,
            "video_frame_queue_size": 10,
            "video_decode_threads": 0,
            "video_resize_quality_high": False,
            "cache_dir": str(self._managed_cache_dir),
            "ambient_muted": False,
            "ambient_volume": 0.4,
            "ambient_presets": {},
        }
        self._settings: SettingsData = {}
        self.load_settings()

    def load_settings(self) -> None:
        """Load settings and repair protected values to managed defaults."""
        try:
            candidate, needs_persist = self._load_candidate()
            self._ensure_managed_directories(candidate)
            self._settings = candidate
            if needs_persist:
                self.save_settings()
            logger.info("Settings loaded from %s", self.SETTINGS_FILE)
        except LOAD_EXCEPTIONS as error:
            logger.error("Failed loading settings: %s", error, exc_info=True)
            self._settings = deepcopy(self.DEFAULT_SETTINGS)
            self._recover_default_settings()

    def _load_candidate(self) -> tuple[SettingsData, bool]:
        if not self.SETTINGS_FILE.exists():
            return deepcopy(self.DEFAULT_SETTINGS), True
        if self.SETTINGS_FILE.is_symlink():
            raise SettingsError("The settings file cannot be a symbolic link.")
        if self.SETTINGS_FILE.resolve(strict=False) != self.SETTINGS_FILE:
            raise SettingsError("The settings file escapes application data.")
        with self.SETTINGS_FILE.open("r", encoding="utf-8") as file_obj:
            raw_data = json.load(file_obj)
        if not isinstance(raw_data, dict):
            raise ValueError("settings.json must contain a JSON object")

        candidate = deepcopy(self.DEFAULT_SETTINGS)
        protected_value_changed = False
        for raw_key, value in raw_data.items():
            key = self._validate_key(raw_key)
            self._require_json_value(value, key)
            normalized = self._normalize_setting(key, value)
            candidate[key] = normalized
            if key in PROTECTED_SETTING_KEYS and normalized != value:
                protected_value_changed = True
        return candidate, protected_value_changed

    def _recover_default_settings(self) -> None:
        try:
            self._ensure_managed_directories(self._settings)
            self.save_settings()
        except SettingsError as error:
            logger.error(
                "Default settings could not be persisted: %s",
                error,
                exc_info=True,
            )

    def save_settings(self) -> None:
        """Persist the current settings atomically or raise ``SettingsError``."""
        self._persist_snapshot(self._settings)

    def _persist_snapshot(self, snapshot: SettingsData) -> None:
        temporary_path: Path | None = None
        try:
            self.SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=self.SETTINGS_FILE.parent,
                prefix=f".{self.SETTINGS_FILE.name}.",
                suffix=".tmp",
                delete=False,
            ) as file_obj:
                temporary_path = Path(file_obj.name)
                json.dump(
                    snapshot,
                    file_obj,
                    indent=2,
                    ensure_ascii=False,
                    allow_nan=False,
                )
                file_obj.flush()
                os.fsync(file_obj.fileno())
            durable_replace(temporary_path, self.SETTINGS_FILE)
            temporary_path = None
            try:
                sync_parent_directory(self.SETTINGS_FILE.parent)
            except OSError as error:
                logger.warning(
                    "Settings file replaced but parent directory sync failed: %s",
                    error,
                    exc_info=True,
                )
        except SERIALIZATION_EXCEPTIONS as error:
            self._remove_temporary_file(temporary_path)
            raise SettingsError(
                "Unable to persist settings.",
                details=f"{type(error).__name__}: {error}",
            ) from error

    @staticmethod
    def _remove_temporary_file(temporary_path: Path | None) -> None:
        if temporary_path is None:
            return
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            logger.warning(
                "Unable to remove temporary settings file %s",
                temporary_path,
                exc_info=True,
            )

    def _ensure_managed_directories(self, snapshot: SettingsData) -> None:
        cache_value = snapshot.get("cache_dir")
        if cache_value != str(self._managed_cache_dir):
            raise SettingsError("The cache directory is not application-managed.")
        if self._is_directory_link(self._managed_cache_dir):
            raise SettingsError("The managed cache directory cannot be a link.")
        try:
            resolved_cache = self._managed_cache_dir.resolve(strict=False)
            if resolved_cache != self._managed_cache_dir:
                raise SettingsError("The managed cache directory escapes app data.")
            self._managed_cache_dir.mkdir(parents=True, exist_ok=True)
            marker = self._managed_cache_dir / MANAGED_DIRECTORY_MARKER
            if marker.is_symlink():
                raise SettingsError("The managed cache marker cannot be a link.")
            if marker.exists() and not marker.is_file():
                raise SettingsError("The managed cache marker must be a regular file.")
            if not marker.exists():
                marker.write_text("WaveHelm managed cache\n", encoding="utf-8")
        except OSError as error:
            raise SettingsError(
                "Unable to prepare the managed cache directory.",
                details=f"{type(error).__name__}: {error}",
            ) from error

    @staticmethod
    def _is_directory_link(directory: Path) -> bool:
        if directory.is_symlink():
            return True
        junction_check = getattr(directory, "is_junction", None)
        return bool(callable(junction_check) and junction_check())

    def get_setting(self, key: str, default: JsonValue = None) -> JsonValue:
        return self._settings.get(key, default)

    def set_setting(self, key: str, value: JsonValue) -> JsonValue:
        normalized_key = self._validate_key(key)
        self._require_json_value(value, normalized_key)
        if normalized_key in PROTECTED_SETTING_KEYS:
            self._reject_external_cache_path(value)

        normalized_value = self._normalize_setting(normalized_key, value)
        candidate = deepcopy(self._settings)
        candidate[normalized_key] = normalized_value
        self._commit_candidate(candidate)
        return normalized_value

    def apply_imported_settings(
        self,
        imported_settings: Mapping[str, JsonValue],
    ) -> SettingsImportResult:
        """Validate and persist an imported snapshot as one transaction."""
        if len(imported_settings) > MAX_IMPORT_ITEMS:
            raise SettingsError("The imported settings file contains too many entries.")

        candidate = deepcopy(self._settings)
        updated_keys: list[str] = []
        ignored_keys: list[str] = []
        for raw_key, value in imported_settings.items():
            key = self._validate_key(raw_key)
            self._require_json_value(value, key)
            if key in PROTECTED_SETTING_KEYS:
                ignored_keys.append(key)
                continue
            if key not in PORTABLE_SETTING_KEYS:
                raise SettingsError(f"Unsupported imported setting: {key}")
            candidate[key] = self._normalize_setting(key, value)
            updated_keys.append(key)

        if not updated_keys:
            raise SettingsError("The import contains no portable settings.")
        self._commit_candidate(candidate)
        return SettingsImportResult(tuple(updated_keys), tuple(ignored_keys))

    def _commit_candidate(self, candidate: SettingsData) -> None:
        committed = deepcopy(candidate)
        self._ensure_managed_directories(committed)
        self._persist_snapshot(committed)
        self._settings = committed

    def get_all_settings(self) -> SettingsData:
        return deepcopy(self._settings)

    def get_exportable_settings(self) -> SettingsData:
        return {
            key: deepcopy(value)
            for key, value in self._settings.items()
            if key in PORTABLE_SETTING_KEYS
        }

    def reset_to_defaults(self) -> None:
        candidate = deepcopy(self.DEFAULT_SETTINGS)
        self._commit_candidate(candidate)
        logger.info("Settings reset to defaults")

    def is_ambient_muted(self) -> bool:
        return bool(self.get_setting("ambient_muted", False))

    def set_ambient_muted(self, muted: bool) -> None:
        self.set_setting("ambient_muted", bool(muted))

    def _normalize_setting(self, key: str, value: JsonValue) -> JsonValue:
        if key == "cache_dir":
            return str(self._managed_cache_dir)
        if key == "volume":
            return self._bounded_int(value, default=70, minimum=0, maximum=100)
        if key == "video_target_fps":
            return self._bounded_int(value, default=60, minimum=1, maximum=120)
        if key == "video_frame_queue_size":
            return self._bounded_int(value, default=10, minimum=3, maximum=100)
        if key == "video_decode_threads":
            return self._bounded_int(value, default=0, minimum=0, maximum=64)
        return value

    @staticmethod
    def _bounded_int(
        value: JsonValue,
        *,
        default: int,
        minimum: int,
        maximum: int,
    ) -> int:
        try:
            normalized = int(value)  # type: ignore[arg-type]
        except FORMAT_EXCEPTIONS:
            return default
        return max(minimum, min(maximum, normalized))

    @staticmethod
    def _validate_key(raw_key: object) -> str:
        if not isinstance(raw_key, str):
            raise SettingsError("Setting keys must be strings.")
        key = raw_key.strip()
        if not key or len(key) > 128:
            raise SettingsError("Setting keys must contain 1 to 128 characters.")
        return key

    def _reject_external_cache_path(self, value: JsonValue) -> None:
        try:
            requested = Path(str(value)).resolve()
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise SettingsError("The cache directory path is invalid.") from error
        if requested != self._managed_cache_dir:
            raise SettingsError("The cache directory is managed by WaveHelm.")

    def _require_json_value(self, value: object, key: str) -> None:
        if not self._is_json_value(value, depth=0):
            raise SettingsError(f"Setting '{key}' contains an unsupported value.")

    def _is_json_value(self, value: object, *, depth: int) -> bool:
        if depth > MAX_JSON_DEPTH:
            return False
        if value is None or isinstance(value, (str, bool, int)):
            return True
        if isinstance(value, float):
            return math.isfinite(value)
        if isinstance(value, list):
            return len(value) <= MAX_COLLECTION_ITEMS and all(
                self._is_json_value(item, depth=depth + 1) for item in value
            )
        if isinstance(value, dict):
            return len(value) <= MAX_COLLECTION_ITEMS and all(
                isinstance(item_key, str)
                and self._is_json_value(item_value, depth=depth + 1)
                for item_key, item_value in value.items()
            )
        return False
