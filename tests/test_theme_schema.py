from __future__ import annotations

from copy import deepcopy
import json
import logging
from pathlib import Path

import pytest

import src.model.theme_manager as theme_manager_module
from src.model.theme_manager import ThemeManager
from src.model.theme_manager_builtins import get_builtin_themes
from src.model.theme_schema import (
    MAX_CUSTOM_THEME_COUNT,
    OPTIONAL_THEME_COLOR_KEYS,
    THEME_COLOR_KEYS,
    THEME_COLOR_KEY_SET,
    clone_theme_catalog,
    normalize_runtime_theme_catalog,
    normalize_theme_name,
    reject_duplicate_object_pairs,
    validate_builtin_theme_catalog,
    validate_color_theme_names,
    validate_custom_theme_catalog,
    validate_theme_color,
    validate_theme_palette,
)
from src.utils.durable_io import DurabilityStatus
from src.utils.exceptions import SettingsError


def _builtin_subset() -> dict[str, dict[str, str]]:
    catalog = get_builtin_themes()
    return {
        "dark": deepcopy(catalog["dark"]),
        "light": deepcopy(catalog["light"]),
    }


def _complete_palette(
    source: str = "dark",
    **overrides: str,
) -> dict[str, str]:
    palette = deepcopy(get_builtin_themes()[source])
    palette.update(overrides)
    return palette


def _make_manager(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    custom_payload: object | None = None,
    raw_json: str | None = None,
    builtins: object | None = None,
) -> ThemeManager:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    if raw_json is not None:
        (runtime_dir / "custom_themes.json").write_text(raw_json, encoding="utf-8")
    elif custom_payload is not None:
        (runtime_dir / "custom_themes.json").write_text(
            json.dumps(custom_payload),
            encoding="utf-8",
        )
    monkeypatch.setattr(theme_manager_module, "get_app_data_path", lambda: runtime_dir)
    catalog = _builtin_subset() if builtins is None else builtins
    monkeypatch.setattr(
        theme_manager_module,
        "get_builtin_themes",
        lambda: deepcopy(catalog),
    )
    return ThemeManager()


def test_production_builtin_catalog_matches_canonical_schema() -> None:
    validated = validate_builtin_theme_catalog(get_builtin_themes())

    assert set(validated) == {
        "dark",
        "light",
        "pink",
        "red",
        "orange",
        "yellow",
        "blue",
        "green",
    }
    assert all(set(palette) == THEME_COLOR_KEY_SET for palette in validated.values())
    assert all(
        value == value.lower()
        for palette in validated.values()
        for value in palette.values()
    )


@pytest.mark.parametrize(
    ("raw_name", "expected"),
    [
        (" Ocean ", "ocean"),
        ("ＯＣＥＡＮ", "ocean"),
        ("Café", "café"),
        ("Cafe\u0301", "café"),
        ("sweet-pink", "sweet-pink"),
    ],
)
def test_theme_names_have_deterministic_nfkc_casefold_identity(
    raw_name: str,
    expected: str,
) -> None:
    assert normalize_theme_name(raw_name) == expected


@pytest.mark.parametrize(
    "raw_name",
    ["", "   ", "a\nname", "\tocean\t", "bad\ud800"],
)
def test_theme_names_reject_empty_or_control_input(raw_name: str) -> None:
    with pytest.raises(ValueError):
        normalize_theme_name(raw_name)




def test_printable_theme_name_punctuation_remains_compatible() -> None:
    assert normalize_theme_name(" Ocean/Dark! v2 ") == "ocean/dark! v2"


def test_color_theme_names_are_validated_unique_and_detached() -> None:
    assert validate_color_theme_names(["Blue", "sweet-pink"]) == (
        "blue",
        "sweet-pink",
    )
    with pytest.raises(ValueError, match="duplicate"):
        validate_color_theme_names(["Blue", "ＢＬＵＥ"])
    with pytest.raises(ValueError):
        validate_color_theme_names("blue")


def test_complete_palette_is_required_and_canonicalized() -> None:
    palette = _complete_palette(bg_color="#AABBCC")
    validated = validate_theme_palette(
        palette,
        theme_name="custom",
        allow_legacy_name=True,
    )

    assert list(validated) == list(THEME_COLOR_KEYS)
    assert validated["bg_color"] == "#aabbcc"
    optional = validate_theme_palette(
        _complete_palette(footer_bg="#010203"),
        theme_name="custom",
        allow_legacy_name=True,
    )
    assert optional[OPTIONAL_THEME_COLOR_KEYS[0]] == "#010203"

    incomplete = {"bg_color": "#ffffff"}
    with pytest.raises(ValueError, match="missing keys"):
        validate_theme_palette(
            incomplete,
            theme_name="custom",
            allow_legacy_name=True,
        )

    unexpected = _complete_palette(unknown_color="#ffffff")
    with pytest.raises(ValueError, match="unsupported keys"):
        validate_theme_palette(
            unexpected,
            theme_name="custom",
            allow_legacy_name=True,
        )


@pytest.mark.parametrize(
    "raw_color",
    ["white", "#fff", "#ffffffff", "#gggggg", " #ffffff", "#ffffff ", 1, None],
)
def test_color_values_require_exact_hex_rgb(raw_color: object) -> None:
    with pytest.raises(ValueError):
        validate_theme_color(raw_color, key="bg_color")


def test_custom_catalog_rejects_builtin_and_virtual_names() -> None:
    palette = _complete_palette()
    builtin_names = frozenset({"dark", "light"})

    for name in ("dark", "ＤＡＲＫ", "system", " SYSTEM "):
        with pytest.raises(ValueError, match="reserved"):
            validate_custom_theme_catalog(
                {name: palette},
                builtin_names=builtin_names,
            )


def test_custom_catalog_rejects_nfkc_collisions_and_excess_count() -> None:
    palette = _complete_palette()
    with pytest.raises(ValueError, match="duplicate canonical"):
        validate_custom_theme_catalog(
            {"Café": palette, "Cafe\u0301": palette},
            builtin_names=frozenset({"dark", "light"}),
        )

    oversized = {
        f"theme-{index}": palette
        for index in range(MAX_CUSTOM_THEME_COUNT + 1)
    }
    with pytest.raises(ValueError, match="theme-count"):
        validate_custom_theme_catalog(
            oversized,
            builtin_names=frozenset({"dark", "light"}),
        )


def test_duplicate_json_keys_are_rejected_at_every_depth() -> None:
    with pytest.raises(ValueError, match="duplicate JSON key"):
        json.loads(
            '{"ocean": {"bg_color": "#111111"}, "ocean": {}}',
            object_pairs_hook=reject_duplicate_object_pairs,
        )
    with pytest.raises(ValueError, match="duplicate JSON key"):
        json.loads(
            '{"ocean": {"bg_color": "#111111", "bg_color": "#222222"}}',
            object_pairs_hook=reject_duplicate_object_pairs,
        )


def test_manager_blocks_builtin_override_and_preserves_original_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    palette = _complete_palette(bg_color="#abcdef")
    raw = json.dumps({"ＤＡＲＫ": palette})
    manager = _make_manager(monkeypatch, tmp_path, raw_json=raw)

    assert manager.get_current_theme_colors()["bg_color"] == "#1e1e1e"
    with pytest.raises(SettingsError, match="writes are blocked"):
        manager.set_custom_theme_color("bg_color", "#123456")
    assert manager.custom_themes_file.read_text(encoding="utf-8") == raw


def test_manager_blocks_incomplete_or_invalid_custom_palettes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manager = _make_manager(
        monkeypatch,
        tmp_path,
        custom_payload={"ocean": {"bg_color": "not-a-color"}},
    )

    assert "ocean" not in manager.get_available_theme_names()
    with pytest.raises(SettingsError, match="writes are blocked"):
        manager.save_custom_themes()


def test_legacy_name_metadata_is_validated_then_removed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    palette = _complete_palette(bg_color="#AABBCC")
    palette["name"] = " CUSTOM "
    manager = _make_manager(
        monkeypatch,
        tmp_path,
        custom_payload={"CUSTOM": palette},
    )

    colors = manager.get_custom_theme_colors()
    assert colors["bg_color"] == "#aabbcc"
    assert "name" not in colors
    manager.save_custom_themes()
    persisted = json.loads(manager.custom_themes_file.read_text(encoding="utf-8"))
    assert "name" not in persisted["custom"]


def test_all_catalog_and_palette_getters_are_defensive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manager = _make_manager(monkeypatch, tmp_path)
    manager.set_custom_theme_color("bg_color", "#123456")

    catalog = manager.themes
    current = manager.get_current_theme_colors()
    alias = manager.current_theme
    custom = manager.get_custom_theme_colors()
    catalog["dark"]["bg_color"] = "#000000"
    current["bg_color"] = "#000000"
    alias["bg_color"] = "#000000"
    custom["bg_color"] = "#000000"

    assert manager.themes["dark"]["bg_color"] == "#1e1e1e"
    assert manager.get_current_theme_colors()["bg_color"] == "#123456"
    assert manager.get_custom_theme_colors()["bg_color"] == "#123456"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("unknown_key", "#123456"),
        ("bg_color", "not-a-color"),
        ("bg_color", "#fff"),
        ("bg_color", "#12345678"),
    ],
)
def test_mutator_rejects_unknown_keys_and_invalid_colors_before_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    key: str,
    value: str,
) -> None:
    manager = _make_manager(monkeypatch, tmp_path)
    before = manager.themes

    with pytest.raises(SettingsError, match="Invalid custom theme color"):
        manager.set_custom_theme_color(key, value)

    assert manager.themes == before
    assert not manager.custom_themes_file.exists()


def test_runtime_catalog_rejects_builtin_mutation() -> None:
    builtins = validate_builtin_theme_catalog(_builtin_subset())
    candidate = clone_theme_catalog(builtins)
    candidate["dark"]["bg_color"] = "#000000"

    with pytest.raises(ValueError, match="built-in theme was modified"):
        normalize_runtime_theme_catalog(candidate, builtin_catalog=builtins)


def test_invalid_default_mode_fails_manager_initialization(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "invalid-default"
    runtime_dir.mkdir()
    monkeypatch.setattr(theme_manager_module, "get_app_data_path", lambda: runtime_dir)
    monkeypatch.setattr(theme_manager_module, "get_builtin_themes", _builtin_subset)

    with pytest.raises(SettingsError, match="built-in palette"):
        ThemeManager(default_mode="custom")


def test_invalid_builtin_catalog_fails_manager_initialization(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    broken = _builtin_subset()
    del broken["dark"]["text_color"]

    with pytest.raises(SettingsError, match="built-in theme catalog is invalid"):
        _make_manager(monkeypatch, tmp_path, builtins=broken)


class _RaisingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        del record
        raise RuntimeError("logging backend failed")


def test_logging_handler_failure_cannot_reclassify_committed_theme(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manager = _make_manager(monkeypatch, tmp_path)
    manager.set_custom_theme_color("bg_color", "#111111")
    handler = _RaisingHandler()
    theme_manager_module.logger.addHandler(handler)
    try:
        status = manager.set_custom_theme_color("bg_color", "#222222")
    finally:
        theme_manager_module.logger.removeHandler(handler)

    persisted = json.loads(manager.custom_themes_file.read_text(encoding="utf-8"))
    assert status is DurabilityStatus.DURABLE
    assert persisted["custom"]["bg_color"] == "#222222"
    assert manager.get_custom_theme_colors()["bg_color"] == "#222222"
