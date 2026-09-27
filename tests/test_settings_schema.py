from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from src.controller.settings_controller import SettingsController
from src.model.settings_schema import (
    ALL_SETTING_KEYS,
    BOOLEAN_SETTING_KEYS,
    INTEGER_SETTING_BOUNDS,
    MAX_SETTINGS_DOCUMENT_BYTES,
    PORTABLE_SETTING_KEYS,
    SettingsSchema,
    SettingsSnapshot,
    parse_settings_document,
    read_settings_document,
)
from src.utils.exceptions import SettingsError


def _schema(tmp_path: Path) -> SettingsSchema:
    return SettingsSchema(tmp_path / "cache")


def test_schema_declares_complete_defaults_and_protects_cache_path(tmp_path):
    schema = _schema(tmp_path)

    defaults = schema.default_settings()

    assert set(defaults) == ALL_SETTING_KEYS
    assert PORTABLE_SETTING_KEYS == ALL_SETTING_KEYS - {"cache_dir"}
    assert defaults["cache_dir"] == str(tmp_path / "cache")
    assert defaults["equalizer_enabled"] is True
    assert defaults["ui_library_column_widths"] == []


def test_complete_default_snapshot_is_already_canonical(tmp_path):
    schema = _schema(tmp_path)
    defaults = schema.default_settings()

    normalized = schema.normalize_persisted(defaults)

    assert normalized.settings == defaults
    assert normalized.needs_persist is False
    assert normalized.ignored_keys == ()


@pytest.mark.parametrize("key", sorted(BOOLEAN_SETTING_KEYS))
def test_every_boolean_setting_rejects_truthy_string_values(tmp_path, key):
    with pytest.raises(SettingsError, match="must be a JSON boolean"):
        _schema(tmp_path).normalize_runtime(key, "false")


@pytest.mark.parametrize("key", sorted(INTEGER_SETTING_BOUNDS))
def test_every_integer_setting_rejects_boolean_values(tmp_path, key):
    with pytest.raises(SettingsError, match="must be a JSON integer"):
        _schema(tmp_path).normalize_runtime(key, True)


def test_parser_rejects_duplicate_keys_and_nonstandard_numbers():
    with pytest.raises(SettingsError, match="Duplicate JSON key"):
        parse_settings_document('{"volume": 10, "volume": 20}')

    with pytest.raises(SettingsError, match="Non-standard JSON numeric constant"):
        parse_settings_document('{"ambient_volume": NaN}')


def test_schema_rejects_excessive_nesting_and_oversized_roots(tmp_path):
    deeply_nested: object = 0
    for _index in range(20):
        deeply_nested = {"nested": deeply_nested}
    with pytest.raises(SettingsError, match="JSON depth limit"):
        _schema(tmp_path).normalize_persisted({"ambient_presets": deeply_nested})

    oversized_root = "{" + ",".join(
        f'"key_{index}": {index}' for index in range(65)
    ) + "}"
    with pytest.raises(SettingsError, match="too many root entries"):
        parse_settings_document(oversized_root)


def test_read_settings_document_enforces_byte_limit_before_full_parse(tmp_path):
    settings_file = tmp_path / "settings.json"
    settings_file.write_bytes(b"{" + (b" " * MAX_SETTINGS_DOCUMENT_BYTES))

    with pytest.raises(SettingsError, match="exceeds the 1 MiB limit"):
        read_settings_document(settings_file)


def test_boolean_integer_and_language_types_are_strict(tmp_path):
    schema = _schema(tmp_path)

    with pytest.raises(SettingsError, match="JSON boolean"):
        schema.normalize_runtime("ambient_muted", "false")
    with pytest.raises(SettingsError, match="JSON integer"):
        schema.normalize_runtime("volume", True)
    with pytest.raises(SettingsError, match="locale code, not a path"):
        schema.normalize_runtime("language", "../secret")

    assert schema.normalize_runtime("ambient_muted", False) == (
        "ambient_muted",
        False,
    )
    assert schema.normalize_runtime("volume", 500) == ("volume", 100)


def test_structured_settings_are_normalized_and_bounded(tmp_path):
    schema = _schema(tmp_path)

    assert schema.normalize_runtime("ambient_volume", 3) == ("ambient_volume", 1.0)
    assert schema.normalize_runtime("eq_last_gains", {"31Hz": 99}) == (
        "eq_last_gains",
        {"31Hz": 12.0},
    )
    assert schema.normalize_runtime("ui_library_column_widths", [1, 500]) == (
        "ui_library_column_widths",
        [24, 500],
    )

    with pytest.raises(SettingsError, match="bounded JSON array"):
        schema.normalize_runtime("ui_library_column_widths", "100,200")


def test_persisted_unknown_keys_are_removed_but_imported_unknown_keys_fail(tmp_path):
    schema = _schema(tmp_path)

    normalized = schema.normalize_persisted(
        {"language": "it", "unknown_future_key": "active"}
    )

    assert normalized.settings["language"] == "it"
    assert "unknown_future_key" not in normalized.settings
    assert normalized.ignored_keys == ("unknown_future_key",)
    assert normalized.needs_persist is True

    with pytest.raises(SettingsError, match="Unsupported imported setting"):
        schema.normalize_import(
            {"language": "it", "unknown_future_key": "active"}
        )


def test_runtime_cache_path_accepts_only_the_managed_location(tmp_path):
    schema = _schema(tmp_path)
    managed = str(tmp_path / "cache")

    assert schema.normalize_runtime("cache_dir", managed) == ("cache_dir", managed)
    with pytest.raises(SettingsError, match="managed by WaveHelm"):
        schema.normalize_runtime("cache_dir", str(tmp_path / "external"))


def test_import_ignores_only_valid_protected_cache_values(tmp_path):
    schema = _schema(tmp_path)

    normalized = schema.normalize_import(
        {"language": "fr", "cache_dir": str(tmp_path / "external")}
    )

    assert normalized.values == {"language": "fr"}
    assert normalized.ignored_keys == ("cache_dir",)

    with pytest.raises(SettingsError, match="must be a JSON string"):
        schema.normalize_import({"language": "fr", "cache_dir": {"path": "x"}})


class _VideoRuntimeProbe:
    def __init__(self) -> None:
        self.hw_enabled: bool | None = None
        self.runtime: dict[str, object] = {}

    def set_hw_accel(self, enabled: bool) -> None:
        self.hw_enabled = enabled

    def configure_video_runtime(self, **values: object) -> None:
        self.runtime = values


def test_controller_never_truthiness_coerces_invalid_boolean_settings():
    controller = object.__new__(SettingsController)
    video = _VideoRuntimeProbe()
    controller.video_player = video
    settings = cast(
        SettingsSnapshot,
        {
            "video_hw_accel_enabled": False,
            "video_drop_late_frames": True,
            "video_frame_queue_size": 10,
            "video_decode_threads": 0,
            "video_fast_seek": True,
            "video_resize_quality_high": "false",
        },
    )

    controller._apply_video_runtime_from_settings(settings)

    assert video.hw_enabled is False
    assert video.runtime["resize_quality_high"] is False
