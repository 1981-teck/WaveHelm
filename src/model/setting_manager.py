# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import os
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from src.model.localization_manager import LocalizationError, LocalizationManager
from src.model.settings_schema import (
    JsonValue,
    SETTINGS_DOCUMENT_JSON_LIMITS,
    MAX_SETTINGS_ROOT_ITEMS,
    PORTABLE_SETTING_KEYS,
    SettingsData,
    SettingsSchema,
    SettingsSnapshot,
    read_settings_document,
    to_settings_snapshot,
)
from src.utils.bounded_json import BoundedJsonError, serialize_json_bytes
from src.utils.durable_io import durable_replace, sync_parent_directory
from src.utils.exceptions import SettingsError
from src.utils.helpers import get_user_data_dir

logger = logging.getLogger(__name__)

MANAGED_DIRECTORY_MARKER = ".wavehelm-managed"
MAX_IMPORT_ITEMS = MAX_SETTINGS_ROOT_ITEMS
MAX_COLLECTION_ITEMS = MAX_SETTINGS_ROOT_ITEMS
LOAD_EXCEPTIONS = (OSError, TypeError, ValueError, SettingsError)
SERIALIZATION_EXCEPTIONS = (BoundedJsonError, OSError, TypeError, ValueError)


@dataclass(frozen=True, slots=True)
class SettingsImportResult:
    """Result of an atomic settings import."""

    updated_keys: tuple[str, ...]
    ignored_keys: tuple[str, ...]


class SettingsManager:
    """Persistent settings governed by one typed, fail-closed schema.

    Edge cases:
        1. Persisted roots can be oversized, ambiguous, or contain unknown keys.
        2. Direct callers can pass Python values whose truthiness hides type errors.
        3. A write can fail before or after an atomic replacement is attempted.

    Mitigations:
        1. Parse bounded JSON and normalize every known key through ``SettingsSchema``.
        2. Reject wrong top-level types and isolate all returned nested values.
        3. Persist a complete candidate before publishing it as in-memory state.
    """

    def __init__(
        self,
        localization_manager: LocalizationManager | None = None,
    ) -> None:
        self.localization_manager = (
            localization_manager if localization_manager is not None else LocalizationManager()
        )
        user_data_dir = Path(get_user_data_dir()).resolve()
        self.SETTINGS_FILE = user_data_dir / "settings.json"
        self._managed_cache_dir = user_data_dir / "cache"
        self._schema = SettingsSchema(self._managed_cache_dir)
        self.DEFAULT_SETTINGS: SettingsData = self._schema.default_settings()
        self._canonicalize_language(self.DEFAULT_SETTINGS)
        self._settings: SettingsData = {}
        self.load_settings()

    def load_settings(self) -> None:
        """Load a canonical snapshot, repairing only safe schema drift."""
        try:
            candidate, needs_persist = self._load_candidate()
        except LOAD_EXCEPTIONS as error:
            logger.error("Failed loading settings: %s", error, exc_info=True)
            self._settings = deepcopy(self.DEFAULT_SETTINGS)
            self._recover_default_settings()
            return

        try:
            self._ensure_managed_directories(candidate)
            if needs_persist:
                self._persist_snapshot(candidate)
        except LOAD_EXCEPTIONS as error:
            logger.error(
                "Canonical settings rewrite failed; using the validated in-memory snapshot: %s",
                error,
                exc_info=True,
            )
            self._settings = deepcopy(candidate)
            return

        self._settings = deepcopy(candidate)
        logger.info("Settings loaded from %s", self.SETTINGS_FILE)

    def _load_candidate(self) -> tuple[SettingsData, bool]:
        if not self.SETTINGS_FILE.exists():
            return deepcopy(self.DEFAULT_SETTINGS), True
        if self.SETTINGS_FILE.is_symlink():
            raise SettingsError("The settings file cannot be a symbolic link.")
        if self.SETTINGS_FILE.resolve(strict=False) != self.SETTINGS_FILE:
            raise SettingsError("The settings file escapes application data.")

        normalized = self._schema.normalize_persisted(
            read_settings_document(self.SETTINGS_FILE)
        )
        if normalized.ignored_keys:
            logger.warning(
                "Ignored unsupported persisted settings: %s",
                ", ".join(normalized.ignored_keys),
            )
        language_changed = self._canonicalize_language(normalized.settings, repair=True)
        return normalized.settings, normalized.needs_persist or language_changed

    def _recover_default_settings(self) -> None:
        try:
            self._ensure_managed_directories(self._settings)
            self._persist_snapshot(self._settings)
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
            _normalized, payload = serialize_json_bytes(
                snapshot,
                limits=SETTINGS_DOCUMENT_JSON_LIMITS,
                root="object",
                indent=2,
            )
            self.SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=self.SETTINGS_FILE.parent,
                prefix=f".{self.SETTINGS_FILE.name}.",
                suffix=".tmp",
                delete=False,
            ) as file_obj:
                temporary_path = Path(file_obj.name)
                file_obj.write(payload)
                file_obj.flush()
                os.fsync(file_obj.fileno())
            durable_replace(temporary_path, self.SETTINGS_FILE)
            temporary_path = None
            self._sync_settings_directory()
        except SERIALIZATION_EXCEPTIONS as error:
            self._remove_temporary_file(temporary_path)
            raise SettingsError(
                "Unable to persist settings.",
                details=f"{type(error).__name__}: {error}",
            ) from error

    def _sync_settings_directory(self) -> None:
        try:
            sync_parent_directory(self.SETTINGS_FILE.parent)
        except OSError as error:
            logger.warning(
                "Settings file replaced but parent directory sync failed: %s",
                error,
                exc_info=True,
            )

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
            self._ensure_cache_marker()
        except OSError as error:
            raise SettingsError(
                "Unable to prepare the managed cache directory.",
                details=f"{type(error).__name__}: {error}",
            ) from error

    def _ensure_cache_marker(self) -> None:
        marker = self._managed_cache_dir / MANAGED_DIRECTORY_MARKER
        if marker.is_symlink():
            raise SettingsError("The managed cache marker cannot be a link.")
        if marker.exists() and not marker.is_file():
            raise SettingsError("The managed cache marker must be a regular file.")
        if not marker.exists():
            marker.write_text("WaveHelm managed cache\n", encoding="utf-8")

    @staticmethod
    def _is_directory_link(directory: Path) -> bool:
        if directory.is_symlink():
            return True
        junction_check = getattr(directory, "is_junction", None)
        return bool(callable(junction_check) and junction_check())

    def get_setting(self, key: str, default: JsonValue = None) -> JsonValue:
        return deepcopy(self._settings.get(key, default))

    def set_setting(self, key: str, value: object) -> JsonValue:
        normalized_key, normalized_value = self._schema.normalize_runtime(key, value)
        candidate = deepcopy(self._settings)
        candidate[normalized_key] = normalized_value
        if normalized_key == "language":
            self._canonicalize_language(candidate)
        self._commit_candidate(candidate)
        return deepcopy(candidate[normalized_key])

    def apply_imported_settings(self, imported_settings: object) -> SettingsImportResult:
        """Validate and persist all portable imported values as one transaction."""
        normalized = self._schema.normalize_import(imported_settings)
        candidate = deepcopy(self._settings)
        candidate.update(normalized.values)
        if "language" in normalized.values:
            self._canonicalize_language(candidate)
        self._commit_candidate(candidate)
        return SettingsImportResult(
            updated_keys=tuple(normalized.values),
            ignored_keys=normalized.ignored_keys,
        )

    def _commit_candidate(self, candidate: SettingsData) -> None:
        committed = deepcopy(candidate)
        self._ensure_managed_directories(committed)
        self._persist_snapshot(committed)
        self._settings = committed

    def _canonicalize_language(
        self,
        candidate: SettingsData,
        *,
        repair: bool = False,
    ) -> bool:
        """Validate locale availability before any settings snapshot is committed.

        Edge cases:
            1. A syntactically valid code may not have a locale file.
            2. Case or underscore variants may map to one canonical locale code.
            3. A locale file may disappear or become invalid between settings operations.
        """
        language = candidate.get("language")
        try:
            canonical = self.localization_manager.validate_language(language)
        except LocalizationError as error:
            if not repair:
                raise SettingsError(
                    "Setting 'language' is not an available valid locale.",
                    details=str(error),
                ) from error
            canonical = self.localization_manager.fallback_language
            logger.warning(
                "Persisted language %r is unavailable; repaired to %s.",
                language,
                canonical,
            )
        changed = language != canonical
        candidate["language"] = canonical
        return changed

    def get_all_settings(self) -> SettingsSnapshot:
        return to_settings_snapshot(self._settings)

    def get_exportable_settings(self) -> SettingsData:
        return {
            key: deepcopy(value)
            for key, value in self._settings.items()
            if key in PORTABLE_SETTING_KEYS
        }

    def reset_to_defaults(self) -> None:
        defaults = self._schema.default_settings()
        self._canonicalize_language(defaults)
        self._commit_candidate(defaults)
        logger.info("Settings reset to defaults")

    def is_ambient_muted(self) -> bool:
        value = self.get_setting("ambient_muted", False)
        return value if type(value) is bool else False

    def set_ambient_muted(self, muted: bool) -> None:
        self.set_setting("ambient_muted", muted)
