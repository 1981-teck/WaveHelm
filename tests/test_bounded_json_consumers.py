from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
import json
from pathlib import Path

import pytest

import src.controller.library_catalog_store as catalog_module
import src.model.profile_manager as profile_manager_module
import src.model.setting_manager as setting_manager_module
import src.utils.bounded_json as bounded_json_module
from src.controller.library_catalog_store import (
    LibraryCatalogStore,
    LibraryCatalogWriteBlockedError,
    LibraryCatalogWriteError,
)
from src.model.localization_manager import LocalizationError, LocalizationManager
from src.model.profile_manager import _clone_input_mapping
from src.model.setting_manager import SettingsManager
from src.model.settings_schema import SettingsSchema
from src.utils.bounded_json import (
    BoundedJsonError,
    JsonLimits,
    serialize_json_bytes,
)
from src.utils.exceptions import ProfileError, SettingsError


def _write_fallback(locales_dir: Path) -> None:
    (locales_dir / "en.json").write_text(
        json.dumps({"_language_name": "English", "hello": "Hello"}),
        encoding="utf-8",
    )


def test_localization_rejects_nested_document_before_domain_use(tmp_path: Path) -> None:
    locales_dir = tmp_path / "locales"
    locales_dir.mkdir()
    _write_fallback(locales_dir)
    nested: object = "value"
    for _index in range(5):
        nested = {"nested": nested}
    (locales_dir / "de.json").write_text(
        json.dumps({"hello": nested}),
        encoding="utf-8",
    )
    manager = LocalizationManager(locales_dir=locales_dir)

    with pytest.raises(LocalizationError, match="JSON depth limit"):
        manager.prepare_language("de")

    assert manager.get_available_languages() == {"en": "English"}


def test_localization_rejects_global_node_budget_before_flat_schema_check(
    tmp_path: Path,
) -> None:
    locales_dir = tmp_path / "locales"
    locales_dir.mkdir()
    _write_fallback(locales_dir)
    wide = {f"key_{index}": [0, 1] for index in range(2_500)}
    (locales_dir / "wi.json").write_text(json.dumps(wide), encoding="utf-8")
    manager = LocalizationManager(locales_dir=locales_dir)

    with pytest.raises(LocalizationError, match="JSON node limit"):
        manager.prepare_language("wi")

    assert manager.get_current_language() == "en"
    assert manager.get_text("hello") == "Hello"


def test_library_catalog_rejects_duplicate_keys_in_nested_invalid_entry(
    tmp_path: Path,
) -> None:
    target = tmp_path / "library.json"
    original = b'["valid.wav", {"x": 1, "x": 2}]'
    target.write_bytes(original)
    store = LibraryCatalogStore(target)

    loaded = store.load()

    assert loaded.paths == ()
    assert loaded.write_blocked is True
    assert "Duplicate JSON key" in loaded.issues[0]
    with pytest.raises(LibraryCatalogWriteBlockedError):
        store.commit(["replacement.wav"])
    assert target.read_bytes() == original


def test_library_catalog_rejects_global_node_budget_without_replacement(
    tmp_path: Path,
) -> None:
    target = tmp_path / "library.json"
    hostile = {f"key_{index}": [0, 1] for index in range(50_000)}
    original = json.dumps(["valid.wav", hostile], separators=(",", ":")).encode("utf-8")
    target.write_bytes(original)
    store = LibraryCatalogStore(target)

    loaded = store.load()

    assert loaded.paths == ()
    assert loaded.write_blocked is True
    assert "JSON node limit" in loaded.issues[0]
    with pytest.raises(LibraryCatalogWriteBlockedError):
        store.commit(["replacement.wav"])
    assert target.read_bytes() == original


def test_library_serializer_enforces_shared_document_budget_before_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    limits = JsonLimits(
        max_bytes=16,
        max_depth=8,
        max_nodes=32,
        max_container_items=16,
        max_key_chars=32,
        max_key_bytes=64,
        max_string_chars=64,
        max_string_bytes=128,
        max_total_text_chars=128,
        max_total_text_bytes=256,
    )
    monkeypatch.setattr(catalog_module, "_LIBRARY_JSON_LIMITS", limits)
    target = tmp_path / "library.json"
    store = LibraryCatalogStore(target)
    assert store.load().write_blocked is False

    with pytest.raises(LibraryCatalogWriteError, match="serialize library catalog"):
        store.commit(["long-file-name.wav"])

    assert not target.exists()



def test_settings_serializer_enforces_document_budget_before_temp_file_creation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    limits = JsonLimits(
        max_bytes=16,
        max_depth=4,
        max_nodes=32,
        max_container_items=16,
        max_key_chars=32,
        max_key_bytes=64,
        max_string_chars=64,
        max_string_bytes=128,
        max_total_text_chars=128,
        max_total_text_bytes=256,
    )
    monkeypatch.setattr(setting_manager_module, "SETTINGS_DOCUMENT_JSON_LIMITS", limits)
    manager = object.__new__(SettingsManager)
    manager.SETTINGS_FILE = tmp_path / "settings.json"

    with pytest.raises(SettingsError, match="Unable to persist settings"):
        manager._persist_snapshot({"language": "english"})

    assert not manager.SETTINGS_FILE.exists()
    assert list(tmp_path.glob(".settings.json.*.tmp")) == []

def test_library_valid_catalog_remains_canonical_and_round_trips(tmp_path: Path) -> None:
    target = tmp_path / "library.json"
    store = LibraryCatalogStore(target)
    assert store.load().paths == ()

    store.commit(["one.wav", "two.wav", "one.wav"])

    assert json.loads(target.read_text(encoding="utf-8")) == ["one.wav", "two.wav"]
    reopened = LibraryCatalogStore(target).load()
    assert reopened.paths == ("one.wav", "two.wav")
    assert reopened.write_blocked is False

class _LyingMapping(Mapping[str, object]):
    def __init__(self, count: int) -> None:
        self._count = count
        self.keys_yielded = 0
        self.values_requested = 0

    def __len__(self) -> int:
        return 1

    def __iter__(self):
        for index in range(self._count):
            self.keys_yielded += 1
            yield f"unknown_{index}"

    def __getitem__(self, key: str) -> object:
        del key
        self.values_requested += 1
        return 0


class _LyingSequence(Sequence[str]):
    def __init__(self, count: int) -> None:
        self._count = count
        self.items_requested = 0

    def __len__(self) -> int:
        return 1

    def __getitem__(self, index: int) -> str:
        if index >= self._count:
            raise IndexError
        self.items_requested += 1
        return "same.wav"


def test_settings_rejects_dynamic_root_mapping_without_iteration(tmp_path: Path) -> None:
    mapping = _LyingMapping(100)
    schema = SettingsSchema(tmp_path / "cache")

    with pytest.raises(SettingsError, match="strict JSON object"):
        schema.normalize_persisted(mapping)

    assert mapping.keys_yielded == 0
    assert mapping.values_requested == 0


def test_profile_mapping_boundary_stops_after_configured_item_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limits = replace(
        profile_manager_module.PROFILE_JSON_LIMITS,
        max_container_items=4,
        max_nodes=32,
    )
    monkeypatch.setattr(profile_manager_module, "PROFILE_JSON_LIMITS", limits)
    mapping = _LyingMapping(100)

    with pytest.raises(ProfileError, match="mapping item limit"):
        _clone_input_mapping(mapping, "probe")  # type: ignore[arg-type]

    assert mapping.keys_yielded == 5
    assert mapping.values_requested == 5


def test_library_sequence_boundary_does_not_trust_reported_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(catalog_module, "_MAX_CATALOG_ENTRIES", 4)
    values = _LyingSequence(100)

    with pytest.raises(LibraryCatalogWriteError, match="entry limit"):
        catalog_module._normalize_catalog_paths(values)

    assert values.items_requested == 5


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"indent": 9}, "indent"),
        ({"sort_keys": 1}, "sort_keys"),
        ({"trailing_newline": 1}, "trailing_newline"),
    ],
)
def test_serializer_rejects_amplifying_options_before_json_dumps(
    monkeypatch: pytest.MonkeyPatch,
    options: dict[str, object],
    message: str,
) -> None:
    called = False

    def fail_if_called(*_args: object, **_kwargs: object) -> str:
        nonlocal called
        called = True
        raise AssertionError("json.dumps must not run")

    monkeypatch.setattr(bounded_json_module.json, "dumps", fail_if_called)
    limits = JsonLimits(
        max_bytes=1_024,
        max_depth=4,
        max_nodes=32,
        max_container_items=16,
        max_key_chars=32,
        max_key_bytes=64,
        max_string_chars=64,
        max_string_bytes=128,
        max_total_text_chars=128,
        max_total_text_bytes=256,
    )

    with pytest.raises(BoundedJsonError, match=message):
        serialize_json_bytes({"x": {"y": 1}}, limits=limits, **options)  # type: ignore[arg-type]

    assert called is False
