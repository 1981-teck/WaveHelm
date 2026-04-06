from __future__ import annotations

import json
from pathlib import Path

from src.config.app_metadata import get_app_general_metadata


ROOT = Path(__file__).resolve().parents[1]
APP_INFO_PATH = ROOT / "src" / "config" / "app_info.json"
LOCALES_DIR = ROOT / "src" / "locales"


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def test_app_info_uses_current_stack_metadata():
    app_info = _load_json(APP_INFO_PATH)
    general = app_info.get("general", {})
    libraries = app_info.get("libraries_used", [])

    assert general.get("app_name") == "WaveHelm"
    assert general.get("organization_name") == "WaveHelm"
    assert general.get("target_platform") == "Windows"
    assert general.get("license") == "GNU GPL v3.0 or later (with Section 7 additional terms)"
    assert general.get("website_url") == "https://1981-teck.github.io/M.V.SoundSystem/"
    assert "github_url" not in general
    assert "wxpython" in libraries
    assert "pygame" in libraries
    assert "comtypes" in libraries
    assert "pywin32" in libraries
    assert "customtkinter_gui" not in libraries
    assert "pydub_audio_segment" not in libraries
    assert "json_profiling_settings" not in libraries


def test_all_declared_libraries_have_localized_labels():
    app_info = _load_json(APP_INFO_PATH)
    libraries = app_info.get("libraries_used", [])
    locale_files = sorted(LOCALES_DIR.glob("*.json"))

    for locale_path in locale_files:
        locale_data = _load_json(locale_path)
        for library in libraries:
            assert f"library_{library}" in locale_data, f"{locale_path.name} missing library_{library}"


def test_target_platform_label_is_available_in_all_locales():
    locale_files = sorted(LOCALES_DIR.glob("*.json"))

    for locale_path in locale_files:
        locale_data = _load_json(locale_path)
        assert "target_platform_label" in locale_data, f"{locale_path.name} missing target_platform_label"
        assert "website_label" in locale_data, f"{locale_path.name} missing website_label"


def test_removed_legacy_library_labels_are_absent_from_all_locales():
    deprecated_keys = {
        "library_customtkinter",
        "library_customtkinter_gui",
        "library_pydub",
        "library_pydub_audio_segment",
    }
    locale_files = sorted(LOCALES_DIR.glob("*.json"))

    for locale_path in locale_files:
        locale_data = _load_json(locale_path)
        for key in deprecated_keys:
            assert key not in locale_data, f"{locale_path.name} still contains removed key {key}"


def test_canonical_metadata_loader_matches_product_identity():
    general = _load_json(APP_INFO_PATH).get("general", {})
    loaded_general = get_app_general_metadata(APP_INFO_PATH)

    assert loaded_general["app_name"] == general["app_name"]
    assert loaded_general["organization_name"] == general["organization_name"]
    assert loaded_general["version"] == general["version"]
    assert loaded_general["website_url"] == general["website_url"]
