from __future__ import annotations

import math
import os
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias, TypedDict, cast

from src.utils.bounded_json import (
    BoundedJsonError,
    JsonLimits,
    JsonValue,
    normalize_json_value,
    parse_json_text,
    read_json_file,
)
from src.utils.exceptions import SettingsError

SettingsData: TypeAlias = dict[str, JsonValue]
MAX_SETTINGS_DOCUMENT_BYTES = 1_048_576
MAX_SETTINGS_ROOT_ITEMS = 64
MAX_JSON_DEPTH = 12
MAX_JSON_COLLECTION_ITEMS = 1_024
MAX_JSON_TOTAL_NODES = 10_000
MAX_JSON_STRING_CHARS = 4_096
MAX_SETTING_TEXT_CHARS = 128
MAX_COLUMN_WIDTH_ITEMS = 64
MIN_COLUMN_WIDTH = 24
MAX_COLUMN_WIDTH = 32_768

SETTINGS_DOCUMENT_JSON_LIMITS = JsonLimits(
    max_bytes=MAX_SETTINGS_DOCUMENT_BYTES,
    max_depth=MAX_JSON_DEPTH + 1,
    max_nodes=MAX_JSON_TOTAL_NODES,
    max_container_items=MAX_JSON_COLLECTION_ITEMS,
    max_key_chars=MAX_SETTING_TEXT_CHARS,
    max_key_bytes=512,
    max_string_chars=MAX_JSON_STRING_CHARS,
    max_string_bytes=16_384,
    max_total_text_chars=262_144,
    max_total_text_bytes=MAX_SETTINGS_DOCUMENT_BYTES,
)
SETTINGS_VALUE_JSON_LIMITS = JsonLimits(
    max_bytes=MAX_SETTINGS_DOCUMENT_BYTES,
    max_depth=MAX_JSON_DEPTH,
    max_nodes=MAX_JSON_TOTAL_NODES,
    max_container_items=MAX_JSON_COLLECTION_ITEMS,
    max_key_chars=MAX_SETTING_TEXT_CHARS,
    max_key_bytes=512,
    max_string_chars=MAX_JSON_STRING_CHARS,
    max_string_bytes=16_384,
    max_total_text_chars=262_144,
    max_total_text_bytes=MAX_SETTINGS_DOCUMENT_BYTES,
)

BOOLEAN_SETTING_KEYS = frozenset(
    {
        "shuffle_enabled",
        "loop_enabled",
        "video_hw_accel_enabled",
        "video_fast_seek",
        "video_drop_late_frames",
        "video_resize_quality_high",
        "ambient_muted",
        "equalizer_enabled",
    }
)
INTEGER_SETTING_BOUNDS = {
    "volume": (0, 100),
    "video_target_fps": (1, 120),
    "video_frame_queue_size": (3, 100),
    "video_decode_threads": (0, 64),
}
TEXT_SETTING_KEYS = frozenset(
    {"theme", "primary_color", "video_hw_device", "eq_last_preset"}
)
COLUMN_WIDTH_SETTING_KEYS = frozenset(
    {
        "ui_library_column_widths",
        "ui_playlist_playlist_column_widths",
        "ui_playlist_track_column_widths",
        "ui_favorites_column_widths",
    }
)
_LANGUAGE_CODE_PATTERN = re.compile(r"^[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})*$")

_STATIC_DEFAULT_SETTINGS: SettingsData = {
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
    "ambient_muted": False,
    "ambient_volume": 0.4,
    "ambient_presets": {},
    "equalizer_enabled": True,
    "eq_last_preset": "Flat",
    "eq_last_gains": {},
    "ui_library_column_widths": [],
    "ui_playlist_playlist_column_widths": [],
    "ui_playlist_track_column_widths": [],
    "ui_favorites_column_widths": [],
}
ALL_SETTING_KEYS = frozenset((*_STATIC_DEFAULT_SETTINGS.keys(), "cache_dir"))
PROTECTED_SETTING_KEYS = frozenset({"cache_dir"})
PORTABLE_SETTING_KEYS = ALL_SETTING_KEYS - PROTECTED_SETTING_KEYS

class SettingsSnapshot(TypedDict):
    language: str
    theme: str
    primary_color: str
    volume: int
    shuffle_enabled: bool
    loop_enabled: bool
    video_hw_accel_enabled: bool
    video_hw_device: str
    video_target_fps: int
    video_fast_seek: bool
    video_drop_late_frames: bool
    video_frame_queue_size: int
    video_decode_threads: int
    video_resize_quality_high: bool
    cache_dir: str
    ambient_muted: bool
    ambient_volume: float
    ambient_presets: dict[str, JsonValue]
    equalizer_enabled: bool
    eq_last_preset: str
    eq_last_gains: dict[str, float]
    ui_library_column_widths: list[int]
    ui_playlist_playlist_column_widths: list[int]
    ui_playlist_track_column_widths: list[int]
    ui_favorites_column_widths: list[int]

@dataclass(frozen=True, slots=True)
class NormalizedSettingsDocument:
    """Canonical persisted settings plus sanitization evidence."""

    settings: SettingsData
    needs_persist: bool
    ignored_keys: tuple[str, ...]

@dataclass(frozen=True, slots=True)
class NormalizedSettingsImport:
    """Validated portable settings ready for an atomic merge."""

    values: SettingsData
    ignored_keys: tuple[str, ...]

def read_settings_document(path: Path) -> dict[str, object]:
    """Read one bounded UTF-8 JSON object before schema normalization."""
    try:
        parsed = read_json_file(
            path,
            limits=SETTINGS_DOCUMENT_JSON_LIMITS,
            root="object",
        )
    except BoundedJsonError as error:
        if "document byte limit" in str(error):
            raise SettingsError("The settings document exceeds the 1 MiB limit.") from error
        raise SettingsError(str(error)) from error
    return cast(dict[str, object], parsed)


def parse_settings_document(text: str) -> dict[str, object]:
    """Parse and budget the entire settings document, including unknown keys."""
    try:
        parsed = parse_json_text(
            text,
            limits=SETTINGS_DOCUMENT_JSON_LIMITS,
            root="object",
        )
    except BoundedJsonError as error:
        raise SettingsError(str(error)) from error
    document = cast(dict[str, object], parsed)
    if len(document) > MAX_SETTINGS_ROOT_ITEMS:
        raise SettingsError("The settings document contains too many root entries.")
    return document


class SettingsSchema:
    """Canonical key, type, range, and portability contract for settings."""

    def __init__(self, managed_cache_dir: Path) -> None:
        self._managed_cache_dir = Path(os.path.abspath(managed_cache_dir))

    def default_settings(self) -> SettingsData:
        defaults = deepcopy(_STATIC_DEFAULT_SETTINGS)
        defaults["cache_dir"] = str(self._managed_cache_dir)
        return defaults

    def normalize_persisted(self, document: object) -> NormalizedSettingsDocument:
        mapping = self._require_mapping(document)
        settings = self.default_settings()
        ignored_keys: list[str] = []
        needs_persist = set(mapping) != ALL_SETTING_KEYS
        for raw_key, raw_value in mapping.items():
            key = self._validate_key(raw_key)
            if key not in ALL_SETTING_KEYS:
                ignored_keys.append(key)
                needs_persist = True
                continue
            normalized = self.normalize_value(key, raw_value, repair_protected=True)
            settings[key] = normalized
            needs_persist = needs_persist or normalized != raw_value
        return NormalizedSettingsDocument(
            settings=settings,
            needs_persist=needs_persist,
            ignored_keys=tuple(ignored_keys),
        )

    def normalize_runtime(self, raw_key: object, value: object) -> tuple[str, JsonValue]:
        """Validate one direct runtime mutation against the canonical schema."""
        key = self._validate_key(raw_key)
        if key not in ALL_SETTING_KEYS:
            raise SettingsError(f"Unsupported setting: {key}")
        normalized = self.normalize_value(key, value, repair_protected=False)
        return key, normalized

    def normalize_import(self, document: object) -> NormalizedSettingsImport:
        mapping = self._require_mapping(document)
        values: SettingsData = {}
        ignored_keys: list[str] = []
        for raw_key, raw_value in mapping.items():
            key = self._validate_key(raw_key)
            if key not in ALL_SETTING_KEYS:
                raise SettingsError(f"Unsupported imported setting: {key}")
            if key in PROTECTED_SETTING_KEYS:
                self._require_text(raw_value, key, maximum=MAX_JSON_STRING_CHARS)
                ignored_keys.append(key)
                continue
            values[key] = self.normalize_value(key, raw_value, repair_protected=False)
        if not values:
            raise SettingsError("The import contains no portable settings.")
        return NormalizedSettingsImport(values=values, ignored_keys=tuple(ignored_keys))

    def normalize_value(
        self,
        key: str,
        value: object,
        *,
        repair_protected: bool,
    ) -> JsonValue:
        if key not in ALL_SETTING_KEYS:
            raise SettingsError(f"Unsupported setting: {key}")
        if key == "cache_dir":
            return self._normalize_cache_dir(value, repair=repair_protected)
        if key == "language":
            return self._normalize_language(value)
        if key in BOOLEAN_SETTING_KEYS:
            return self._require_boolean(value, key)
        if key in INTEGER_SETTING_BOUNDS:
            minimum, maximum = INTEGER_SETTING_BOUNDS[key]
            return self._bounded_integer(value, key, minimum=minimum, maximum=maximum)
        if key == "ambient_volume":
            return self._bounded_float(value, key, minimum=0.0, maximum=1.0)
        if key in TEXT_SETTING_KEYS:
            return self._require_text(value, key, maximum=MAX_SETTING_TEXT_CHARS)
        if key == "ambient_presets":
            return self._normalize_json_object(value, key)
        if key == "eq_last_gains":
            return self._normalize_gain_map(value, key)
        if key in COLUMN_WIDTH_SETTING_KEYS:
            return self._normalize_column_widths(value, key)
        raise SettingsError(f"No schema rule is registered for setting: {key}")

    @staticmethod
    def _require_mapping(document: object) -> dict[object, object]:
        if type(document) is not dict:
            raise SettingsError("The settings document root must be a strict JSON object.")
        if len(document) > MAX_SETTINGS_ROOT_ITEMS:
            raise SettingsError("The settings document contains too many root entries.")
        return cast(dict[object, object], document)

    @staticmethod
    def _validate_key(raw_key: object) -> str:
        if type(raw_key) is not str:
            raise SettingsError("Setting keys must be strings.")
        if raw_key != raw_key.strip() or not raw_key or len(raw_key) > 128:
            raise SettingsError("Setting keys must be canonical and contain 1 to 128 characters.")
        if any(ord(character) < 32 or ord(character) == 127 for character in raw_key):
            raise SettingsError("Setting keys cannot contain control characters.")
        return raw_key

    def _normalize_cache_dir(self, value: object, *, repair: bool) -> str:
        canonical = str(self._managed_cache_dir)
        if repair:
            return canonical
        requested_text = self._require_text(value, "cache_dir", maximum=MAX_JSON_STRING_CHARS)
        try:
            requested = Path(os.path.abspath(requested_text))
        except (OSError, RuntimeError, ValueError) as error:
            raise SettingsError("The cache directory path is invalid.") from error
        if os.path.normcase(requested) != os.path.normcase(self._managed_cache_dir):
            raise SettingsError("The cache directory is managed by WaveHelm.")
        return canonical

    @staticmethod
    def _normalize_language(value: object) -> str:
        language = SettingsSchema._require_text(value, "language", maximum=32)
        if not _LANGUAGE_CODE_PATTERN.fullmatch(language):
            raise SettingsError("Setting 'language' must be a locale code, not a path.")
        return language

    @staticmethod
    def _require_boolean(value: object, key: str) -> bool:
        if type(value) is not bool:
            raise SettingsError(f"Setting '{key}' must be a JSON boolean.")
        return value

    @staticmethod
    def _bounded_integer(value: object, key: str, *, minimum: int, maximum: int) -> int:
        if type(value) is not int:
            raise SettingsError(f"Setting '{key}' must be a JSON integer.")
        return max(minimum, min(maximum, value))

    @staticmethod
    def _bounded_float(value: object, key: str, *, minimum: float, maximum: float) -> float:
        if type(value) not in (int, float):
            raise SettingsError(f"Setting '{key}' must be a finite JSON number.")
        normalized = float(value)
        if not math.isfinite(normalized):
            raise SettingsError(f"Setting '{key}' must be a finite JSON number.")
        return max(minimum, min(maximum, normalized))

    @staticmethod
    def _require_text(value: object, key: str, *, maximum: int) -> str:
        if type(value) is not str:
            raise SettingsError(f"Setting '{key}' must be a JSON string.")
        normalized = value.strip()
        if not normalized or len(normalized) > maximum:
            raise SettingsError(f"Setting '{key}' must contain 1 to {maximum} characters.")
        if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
            raise SettingsError(f"Setting '{key}' cannot contain control characters.")
        return normalized

    def _normalize_json_object(self, value: object, key: str) -> dict[str, JsonValue]:
        try:
            normalized = normalize_json_value(
                value,
                limits=SETTINGS_VALUE_JSON_LIMITS,
                root="object",
            )
        except BoundedJsonError as error:
            raise SettingsError(f"Setting '{key}' violates the {error}.") from error
        return cast(dict[str, JsonValue], normalized)

    def _normalize_gain_map(self, value: object, key: str) -> dict[str, float]:
        if type(value) is not dict or len(value) > 64:
            raise SettingsError(f"Setting '{key}' must be a bounded JSON object.")
        gains: dict[str, float] = {}
        for raw_band, raw_gain in value.items():
            band = self._require_text(raw_band, key, maximum=32)
            if band in gains:
                raise SettingsError(f"Setting '{key}' contains duplicate normalized bands.")
            gains[band] = self._bounded_float(raw_gain, key, minimum=-12.0, maximum=12.0)
        return gains

    def _normalize_column_widths(self, value: object, key: str) -> list[int]:
        if type(value) is not list or len(value) > MAX_COLUMN_WIDTH_ITEMS:
            raise SettingsError(f"Setting '{key}' must be a bounded JSON array.")
        return [
            self._bounded_integer(
                width,
                key,
                minimum=MIN_COLUMN_WIDTH,
                maximum=MAX_COLUMN_WIDTH,
            )
            for width in value
        ]


def to_settings_snapshot(settings: SettingsData) -> SettingsSnapshot:
    """Return an isolated typed snapshot after schema validation."""
    return cast(SettingsSnapshot, deepcopy(settings))
