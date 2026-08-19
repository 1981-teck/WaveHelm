from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

APP_NAME_FALLBACK = "WaveHelm"
APP_VERSION_FALLBACK = "1.0.1"
APP_TARGET_PLATFORM_FALLBACK = "Windows"
APP_WEBSITE_URL_FALLBACK = "https://1981-teck.github.io/WaveHelm/"
APP_AUTHOR_FALLBACK = "Pastoris Marco Vincenzo"
APP_LICENSE_FALLBACK = "GNU GPL v3.0 or later (with Section 7 additional terms)"

APP_INFO_PATH = Path(__file__).with_name("app_info.json")
APP_METADATA_READ_EXCEPTIONS = (OSError, json.JSONDecodeError, TypeError, ValueError)
_TOP_LEVEL_DICT_SECTIONS = ("general", "contact")
_TOP_LEVEL_LIST_SECTIONS = ("modules_enabled", "libraries_used")

DEFAULT_APP_METADATA: dict[str, Any] = {
    "general": {
        "app_name": APP_NAME_FALLBACK,
        "organization_name": APP_NAME_FALLBACK,
        "version": APP_VERSION_FALLBACK,
        "author": APP_AUTHOR_FALLBACK,
        "license": APP_LICENSE_FALLBACK,
        "website_url": APP_WEBSITE_URL_FALLBACK,
        "target_platform": APP_TARGET_PLATFORM_FALLBACK,
    },
    "modules_enabled": [],
    "libraries_used": [],
    "contact": {},
}


def resolve_app_metadata_path(app_info_path: Path | None = None) -> Path:
    return Path(app_info_path) if app_info_path is not None else APP_INFO_PATH


def get_default_app_metadata() -> dict[str, Any]:
    return {
        "general": dict(DEFAULT_APP_METADATA["general"]),
        "modules_enabled": list(DEFAULT_APP_METADATA["modules_enabled"]),
        "libraries_used": list(DEFAULT_APP_METADATA["libraries_used"]),
        "contact": dict(DEFAULT_APP_METADATA["contact"]),
    }


def _coerce_non_empty_string(value: Any, fallback: str) -> str:
    if isinstance(value, str):
        normalized = value.strip()
        if normalized:
            return normalized
    return fallback


def normalize_app_metadata(data: Mapping[str, Any] | None) -> dict[str, Any]:
    metadata = get_default_app_metadata()
    if not isinstance(data, Mapping):
        return metadata

    for key, value in data.items():
        if key not in metadata:
            metadata[key] = value

    raw_general = data.get("general", {})
    if isinstance(raw_general, Mapping):
        general = dict(metadata["general"])
        general.update(raw_general)
        raw_website_url = raw_general.get("website_url")
        if (
            not isinstance(raw_website_url, str) or not raw_website_url.strip()
        ) and isinstance(raw_general.get("github_url"), str):
            general["website_url"] = raw_general["github_url"]
        general.pop("github_url", None)
        general["app_name"] = _coerce_non_empty_string(general.get("app_name"), APP_NAME_FALLBACK)
        general["organization_name"] = _coerce_non_empty_string(
            general.get("organization_name"),
            general["app_name"],
        )
        general["version"] = _coerce_non_empty_string(general.get("version"), APP_VERSION_FALLBACK)
        general["author"] = _coerce_non_empty_string(general.get("author"), APP_AUTHOR_FALLBACK)
        general["license"] = _coerce_non_empty_string(general.get("license"), APP_LICENSE_FALLBACK)
        general["website_url"] = _coerce_non_empty_string(
            general.get("website_url"),
            APP_WEBSITE_URL_FALLBACK,
        )
        general["target_platform"] = _coerce_non_empty_string(
            general.get("target_platform"),
            APP_TARGET_PLATFORM_FALLBACK,
        )
        metadata["general"] = general

    for section_name in _TOP_LEVEL_LIST_SECTIONS:
        section_value = data.get(section_name)
        if isinstance(section_value, list):
            metadata[section_name] = list(section_value)

    raw_contact = data.get("contact", {})
    if isinstance(raw_contact, Mapping):
        contact = dict(metadata["contact"])
        contact.update(raw_contact)
        metadata["contact"] = contact

    return metadata


def merge_app_metadata(
    current: Mapping[str, Any] | None,
    updates: Mapping[str, Any] | None,
) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    if isinstance(current, Mapping):
        merged.update(current)
    if isinstance(updates, Mapping):
        merged.update(updates)

    for section_name in _TOP_LEVEL_DICT_SECTIONS:
        section_value: dict[str, Any] = {}
        current_section = current.get(section_name, {}) if isinstance(current, Mapping) else {}
        update_section = updates.get(section_name, {}) if isinstance(updates, Mapping) else {}
        if isinstance(current_section, Mapping):
            section_value.update(current_section)
        if isinstance(update_section, Mapping):
            section_value.update(update_section)
        if section_value:
            merged[section_name] = section_value

    return normalize_app_metadata(merged)


def read_app_metadata(app_info_path: Path | None = None) -> dict[str, Any]:
    resolved_path = resolve_app_metadata_path(app_info_path)
    with resolved_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return normalize_app_metadata(data)


def load_app_metadata(app_info_path: Path | None = None) -> dict[str, Any]:
    try:
        return read_app_metadata(app_info_path)
    except APP_METADATA_READ_EXCEPTIONS:
        return get_default_app_metadata()


def get_app_general_metadata(app_info_path: Path | None = None) -> dict[str, Any]:
    return dict(load_app_metadata(app_info_path).get("general", {}))


def get_app_name(app_info_path: Path | None = None) -> str:
    return get_app_general_metadata(app_info_path).get("app_name", APP_NAME_FALLBACK)


def get_app_version(app_info_path: Path | None = None) -> str:
    return get_app_general_metadata(app_info_path).get("version", APP_VERSION_FALLBACK)


def get_app_organization_name(app_info_path: Path | None = None) -> str:
    general = get_app_general_metadata(app_info_path)
    return general.get("organization_name", general.get("app_name", APP_NAME_FALLBACK))


def get_app_website_url(app_info_path: Path | None = None) -> str:
    return get_app_general_metadata(app_info_path).get("website_url", APP_WEBSITE_URL_FALLBACK)
