from __future__ import annotations

import json
from pathlib import Path

import pytest

import src.model.profile_manager as profile_module
import src.model.theme_manager as theme_module
from src.model.profile_manager import (
    MAX_PROFILE_DOCUMENT_BYTES,
    MAX_PROFILE_KEY_CHARS,
    ProfileManager,
)
from src.model.settings_schema import parse_settings_document
from src.model.theme_manager import MAX_THEME_DOCUMENT_BYTES, ThemeManager
from src.utils.bounded_json import (
    BoundedJsonError,
    JsonLimits,
    normalize_json_value,
    parse_json_bytes,
    parse_json_text,
    read_json_file,
    serialize_json_bytes,
)
from src.utils.exceptions import ProfileError, SettingsError


def _limits(**overrides: int) -> JsonLimits:
    values = {
        "max_bytes": 256,
        "max_depth": 4,
        "max_nodes": 32,
        "max_container_items": 8,
        "max_key_chars": 8,
        "max_key_bytes": 16,
        "max_string_chars": 16,
        "max_string_bytes": 32,
        "max_total_text_chars": 48,
        "max_total_text_bytes": 96,
        "max_number_chars": 16,
    }
    values.update(overrides)
    return JsonLimits(**values)


def test_parser_returns_detached_strict_json_tree() -> None:
    parsed = parse_json_text(
        '{"items": [1, 2.5, true, null, "ok"]}',
        limits=_limits(),
        root="object",
    )

    assert parsed == {"items": [1, 2.5, True, None, "ok"]}
    assert type(parsed) is dict
    assert type(parsed["items"]) is list


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ('{"outer": {"x": 1, "x": 2}}', "Duplicate JSON key"),
        ('{"value": NaN}', "Non-standard JSON numeric constant"),
        ('{"value": 1e400}', "must be finite"),
        ('{"value": 12345678901234567}', "integer token limit"),
    ],
)
def test_parser_rejects_ambiguous_or_unbounded_numbers(
    payload: str,
    message: str,
) -> None:
    with pytest.raises(BoundedJsonError, match=message):
        parse_json_text(payload, limits=_limits(), root="object")


def test_file_reader_stops_at_byte_cap_before_json_parse(tmp_path: Path) -> None:
    path = tmp_path / "oversized.json"
    path.write_bytes(b"{" + (b" " * 256))

    with pytest.raises(BoundedJsonError, match="byte limit"):
        read_json_file(path, limits=_limits(), root="object")


@pytest.mark.parametrize(
    ("value", "overrides", "message"),
    [
        ({"a": {"b": {"c": {"d": {"e": 1}}}}}, {}, "depth limit"),
        ({"a": list(range(9))}, {}, "container item limit"),
        ({"key-too-long": 1}, {}, "key character limit"),
        ({"a": "x" * 17}, {}, "string character limit"),
        ({"a": "x" * 16, "b": "y" * 16, "c": "z" * 16}, {}, "text character budget"),
        ({"a": [0] * 8, "b": [0] * 8}, {"max_nodes": 18}, "node limit"),
    ],
)
def test_complete_tree_budget_is_enforced(
    value: object,
    overrides: dict[str, int],
    message: str,
) -> None:
    with pytest.raises(BoundedJsonError, match=message):
        normalize_json_value(value, limits=_limits(**overrides), root="object")


def test_runtime_normalizer_rejects_cycles_non_string_keys_and_tuples() -> None:
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic

    with pytest.raises(BoundedJsonError, match="cyclic"):
        normalize_json_value(cyclic, limits=_limits(), root="object")
    with pytest.raises(BoundedJsonError, match="keys must be strings"):
        normalize_json_value({1: "value"}, limits=_limits(), root="object")
    with pytest.raises(BoundedJsonError, match="strict JSON"):
        normalize_json_value({"items": (1, 2)}, limits=_limits(), root="object")


def test_serializer_returns_matching_snapshot_and_enforces_encoded_size() -> None:
    source = {"name": "café", "items": [1, 2]}
    normalized, payload = serialize_json_bytes(
        source,
        limits=_limits(),
        root="object",
        indent=2,
        sort_keys=True,
        trailing_newline=True,
    )

    assert json.loads(payload) == normalized == source
    assert payload.endswith(b"\n")

    with pytest.raises(BoundedJsonError, match="serialized JSON"):
        serialize_json_bytes(
            {"value": "x" * 16},
            limits=_limits(max_bytes=16),
            root="object",
        )


def test_bytes_parser_rejects_invalid_utf8_and_wrong_root() -> None:
    with pytest.raises(BoundedJsonError, match="not valid UTF-8"):
        parse_json_bytes(b'"\xff"', limits=_limits())
    with pytest.raises(BoundedJsonError, match="root must be an object"):
        parse_json_text("[]", limits=_limits(), root="object")


def test_settings_parser_budgets_unknown_subtrees_before_schema_ignore() -> None:
    nested = {f"k{index}": [0] * 9 for index in range(1_000)}
    payload = json.dumps({"future_setting": nested})

    with pytest.raises(SettingsError, match="JSON node limit"):
        parse_settings_document(payload)


def test_profile_oversized_existing_document_blocks_destructive_writes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    profile_path = tmp_path / "profile.json"
    original = b'{"stats":{"blob":"' + (b"x" * MAX_PROFILE_DOCUMENT_BYTES) + b'"}}'
    profile_path.write_bytes(original)
    monkeypatch.setattr(profile_module, "PROFILE_PATH", profile_path)

    manager = ProfileManager()

    assert manager.get_stat("plays") == 0
    with pytest.raises(ProfileError, match="writes are blocked"):
        manager.increment_stat("plays")
    assert profile_path.read_bytes() == original


def test_profile_candidate_budgets_keys_strings_and_cycles_before_replace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    profile_path = tmp_path / "profile.json"
    monkeypatch.setattr(profile_module, "PROFILE_PATH", profile_path)
    manager = ProfileManager()

    with pytest.raises(ProfileError, match="bounded JSON contract"):
        manager.set_effects_settings({"x" * (MAX_PROFILE_KEY_CHARS + 1): True})
    with pytest.raises(ProfileError, match="bounded JSON contract"):
        manager.set_effects_settings({"blob": "x" * 262_145})

    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic
    with pytest.raises(ProfileError, match="serialize"):
        manager.set_effects_settings(cyclic)  # type: ignore[arg-type]

    assert manager.get_effects_settings() == {}
    assert not profile_path.exists()


def test_theme_oversized_existing_document_blocks_writes_without_replacement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    theme_path = runtime_dir / "custom_themes.json"
    original = b"{" + (b" " * MAX_THEME_DOCUMENT_BYTES) + b"}"
    theme_path.write_bytes(original)
    monkeypatch.setattr(theme_module, "get_app_data_path", lambda: runtime_dir)

    manager = ThemeManager()

    with pytest.raises(SettingsError, match="writes are blocked"):
        manager.save_custom_themes()
    assert theme_path.read_bytes() == original


def test_theme_candidate_cannot_commit_when_serialized_budget_is_reduced(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    monkeypatch.setattr(theme_module, "get_app_data_path", lambda: runtime_dir)
    manager = ThemeManager()
    before = manager.themes
    monkeypatch.setattr(
        theme_module,
        "THEME_JSON_LIMITS",
        _limits(max_bytes=64, max_nodes=5_000, max_container_items=128),
    )

    with pytest.raises(SettingsError, match="serialize custom themes"):
        manager.set_custom_theme_color("bg_color", "#123456")

    assert manager.themes == before
    assert not manager.custom_themes_file.exists()
