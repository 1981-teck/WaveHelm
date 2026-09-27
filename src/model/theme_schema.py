from __future__ import annotations

"""Canonical schema and defensive-copy helpers for WaveHelm themes."""

from collections.abc import Mapping, Sequence
from copy import deepcopy
import logging
import re
import unicodedata
from typing import Final, TypeAlias

ThemeColors: TypeAlias = dict[str, str]
ThemeCatalog: TypeAlias = dict[str, ThemeColors]

THEME_COLOR_KEYS: Final[tuple[str, ...]] = (
    "bg_color",
    "fg_color",
    "tree_bg",
    "background",
    "window_bg",
    "panel_bg",
    "entry_bg",
    "header_bg",
    "table_alt_bg",
    "text_color",
    "muted_text_color",
    "button_color",
    "button_hover",
    "button_text_color",
    "toolbar_button_color",
    "toolbar_button_hover",
    "toolbar_button_text",
    "accent_color",
    "progress_color",
    "secondary_color",
    "selection_bg",
    "selection_text",
    "danger_color",
    "danger_hover",
    "danger_text",
    "playing_bg",
    "playing_text",
)
THEME_COLOR_KEY_SET: Final[frozenset[str]] = frozenset(THEME_COLOR_KEYS)
OPTIONAL_THEME_COLOR_KEYS: Final[tuple[str, ...]] = ("footer_bg",)
EDITABLE_THEME_COLOR_KEY_SET: Final[frozenset[str]] = (
    THEME_COLOR_KEY_SET | frozenset(OPTIONAL_THEME_COLOR_KEYS)
)
VIRTUAL_THEME_NAMES: Final[frozenset[str]] = frozenset({"system"})
REQUIRED_BUILTIN_THEME_NAMES: Final[frozenset[str]] = frozenset({"dark", "light"})
MAX_BUILTIN_THEME_COUNT: Final[int] = 32
MAX_CUSTOM_THEME_COUNT: Final[int] = 64
MAX_COLOR_THEME_NAME_COUNT: Final[int] = 32
MAX_THEME_NAME_CODEPOINTS: Final[int] = 64
MAX_THEME_NAME_UTF8_BYTES: Final[int] = 256
_HEX_COLOR_PATTERN: Final[re.Pattern[str]] = re.compile(r"^#[0-9A-Fa-f]{6}$")

def safe_theme_log(
    logger: logging.Logger,
    level: int,
    message: str,
    *args: object,
    exc_info: bool = False,
) -> None:
    """Keep logging-handler failures outside theme state contracts."""
    try:
        logger.log(level, message, *args, exc_info=exc_info)
    except Exception:  # explicit logging-handler boundary
        return


def reject_duplicate_object_pairs(
    pairs: Sequence[tuple[str, object]],
) -> dict[str, object]:
    """Build a JSON object while rejecting duplicate keys at every depth."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def normalize_theme_name(raw_name: object) -> str:
    """Return a bounded, deterministic theme identity.

    Edge cases:
        1. Compatibility-equivalent names must resolve to one identity.
        2. Control and format characters must not become identifiers.
        3. Oversized names must be rejected before catalog insertion.
    """
    if type(raw_name) is not str:
        raise ValueError("theme names must be strings")
    if any(unicodedata.category(char).startswith("C") for char in raw_name):
        raise ValueError("theme name contains control or format characters")
    normalized = unicodedata.normalize("NFKC", raw_name).strip().casefold()
    normalized = " ".join(normalized.split(" "))
    if not normalized:
        raise ValueError("theme names must not be empty")
    if len(normalized) > MAX_THEME_NAME_CODEPOINTS:
        raise ValueError("theme name exceeds the code-point limit")
    try:
        encoded = normalized.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("theme name is not valid UTF-8 text") from error
    if len(encoded) > MAX_THEME_NAME_UTF8_BYTES:
        raise ValueError("theme name exceeds the UTF-8 byte limit")
    if any(_is_forbidden_theme_name_character(char) for char in normalized):
        raise ValueError("theme name contains control or format characters")
    return normalized


def _is_forbidden_theme_name_character(char: str) -> bool:
    category = unicodedata.category(char)
    return category.startswith("C") or category in {"Zl", "Zp"}


def validate_color_theme_names(raw_names: object) -> tuple[str, ...]:
    """Validate and snapshot toolkit color-theme identifiers."""
    if not isinstance(raw_names, Sequence) or isinstance(
        raw_names, (str, bytes, bytearray)
    ):
        raise ValueError("built-in color theme names must be a sequence")
    if not 1 <= len(raw_names) <= MAX_COLOR_THEME_NAME_COUNT:
        raise ValueError("built-in color theme name count is invalid")
    result: list[str] = []
    seen: set[str] = set()
    for raw_name in raw_names:
        name = normalize_theme_name(raw_name)
        if name in seen:
            raise ValueError(f"duplicate built-in color theme name: {name!r}")
        seen.add(name)
        result.append(name)
    return tuple(result)


def reject_nonstandard_json_number(value: str) -> object:
    """Reject JSON constants such as NaN and Infinity."""
    raise ValueError(f"non-standard JSON number: {value}")


def validate_theme_color_key(raw_key: object) -> str:
    """Validate one editable color slot name."""
    if type(raw_key) is not str or raw_key not in EDITABLE_THEME_COLOR_KEY_SET:
        raise ValueError("theme color key is not part of the canonical schema")
    return raw_key


def validate_theme_color(raw_color: object, *, key: str) -> str:
    """Validate and canonicalize one ``#RRGGBB`` color value."""
    if type(raw_color) is not str:
        raise ValueError(f"theme color {key!r} must be a string")
    if _HEX_COLOR_PATTERN.fullmatch(raw_color) is None:
        raise ValueError(f"theme color {key!r} must use #RRGGBB syntax")
    return raw_color.lower()


def validate_theme_palette(
    raw_palette: object,
    *,
    theme_name: str,
    allow_legacy_name: bool,
) -> ThemeColors:
    """Validate one complete palette and return a detached canonical copy."""
    if not isinstance(raw_palette, Mapping):
        raise ValueError("theme palettes must be JSON objects")
    entries = dict(raw_palette)
    _validate_palette_keys_are_strings(entries)
    _consume_legacy_name(entries, theme_name, allow_legacy_name)
    present = frozenset(entries)
    missing = THEME_COLOR_KEY_SET - present
    unexpected = present - EDITABLE_THEME_COLOR_KEY_SET
    if missing:
        raise ValueError(f"theme palette is missing keys: {sorted(missing)!r}")
    if unexpected:
        raise ValueError(f"theme palette has unsupported keys: {sorted(unexpected)!r}")
    result = {
        key: validate_theme_color(entries[key], key=key)
        for key in THEME_COLOR_KEYS
    }
    for key in OPTIONAL_THEME_COLOR_KEYS:
        if key in entries:
            result[key] = validate_theme_color(entries[key], key=key)
    return result


def _validate_palette_keys_are_strings(entries: Mapping[object, object]) -> None:
    if not all(type(key) is str for key in entries):
        raise ValueError("theme palette keys must be strings")


def _consume_legacy_name(
    entries: dict[object, object], theme_name: str, allow_legacy_name: bool
) -> None:
    if "name" not in entries:
        return
    if not allow_legacy_name:
        raise ValueError("built-in theme palettes must not contain metadata keys")
    raw_name = entries.pop("name")
    if normalize_theme_name(raw_name) != theme_name:
        raise ValueError("legacy theme name metadata does not match its catalog key")


def validate_builtin_theme_catalog(raw_catalog: object) -> ThemeCatalog:
    """Validate the trusted built-in catalog before it becomes runtime state."""
    if not isinstance(raw_catalog, Mapping):
        raise ValueError("built-in theme catalog must be a mapping")
    if not 1 <= len(raw_catalog) <= MAX_BUILTIN_THEME_COUNT:
        raise ValueError("built-in theme catalog size is invalid")
    result: ThemeCatalog = {}
    for raw_name, raw_palette in raw_catalog.items():
        name = normalize_theme_name(raw_name)
        if name in VIRTUAL_THEME_NAMES or name == "custom":
            raise ValueError(f"built-in theme name is reserved: {name!r}")
        if name in result:
            raise ValueError(f"duplicate canonical built-in theme name: {name!r}")
        result[name] = validate_theme_palette(
            raw_palette,
            theme_name=name,
            allow_legacy_name=False,
        )
    missing = REQUIRED_BUILTIN_THEME_NAMES - frozenset(result)
    if missing:
        raise ValueError(f"required built-in themes are missing: {sorted(missing)!r}")
    return result


def validate_custom_theme_catalog(
    raw_catalog: object,
    *,
    builtin_names: frozenset[str],
) -> ThemeCatalog:
    """Validate custom themes without permitting reserved-name shadowing."""
    if not isinstance(raw_catalog, Mapping):
        raise ValueError("custom themes JSON must be an object")
    if len(raw_catalog) > MAX_CUSTOM_THEME_COUNT:
        raise ValueError("custom theme catalog exceeds the theme-count limit")
    result: ThemeCatalog = {}
    reserved = builtin_names | VIRTUAL_THEME_NAMES
    for raw_name, raw_palette in raw_catalog.items():
        name = normalize_theme_name(raw_name)
        if name in reserved:
            raise ValueError(f"custom theme name is reserved: {name!r}")
        if name in result:
            raise ValueError(f"duplicate canonical custom theme name: {name!r}")
        result[name] = validate_theme_palette(
            raw_palette,
            theme_name=name,
            allow_legacy_name=True,
        )
    return result


def normalize_runtime_theme_catalog(
    candidate: object,
    *,
    builtin_catalog: Mapping[str, ThemeColors],
) -> ThemeCatalog:
    """Validate a mutation candidate and preserve built-ins byte-for-byte logically."""
    if not isinstance(candidate, Mapping):
        raise ValueError("runtime theme catalog must be a mapping")
    builtins = validate_builtin_theme_catalog(builtin_catalog)
    candidate_entries = dict(candidate)
    for name, expected in builtins.items():
        if name not in candidate_entries:
            raise ValueError(f"built-in theme was removed: {name!r}")
        actual = validate_theme_palette(
            candidate_entries[name],
            theme_name=name,
            allow_legacy_name=False,
        )
        if actual != expected:
            raise ValueError(f"built-in theme was modified: {name!r}")
    custom_raw = {
        name: palette
        for name, palette in candidate_entries.items()
        if name not in builtins
    }
    custom = validate_custom_theme_catalog(
        custom_raw,
        builtin_names=frozenset(builtins),
    )
    result = clone_theme_catalog(builtins)
    result.update(custom)
    return result


def extract_custom_theme_catalog(
    catalog: Mapping[str, ThemeColors],
    *,
    builtin_names: frozenset[str],
) -> ThemeCatalog:
    """Return a validated detached custom-only catalog."""
    raw_custom = {
        name: palette for name, palette in catalog.items() if name not in builtin_names
    }
    return validate_custom_theme_catalog(raw_custom, builtin_names=builtin_names)


def clone_theme_colors(colors: Mapping[str, str]) -> ThemeColors:
    """Return a detached palette snapshot."""
    return deepcopy(dict(colors))


def clone_theme_catalog(catalog: Mapping[str, ThemeColors]) -> ThemeCatalog:
    """Return a detached catalog snapshot."""
    return deepcopy(dict(catalog))
