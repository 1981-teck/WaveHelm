from __future__ import annotations

import inspect
import logging
import os
import re
import stat
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from src.utils.app_paths import get_app_data_dir
from src.utils.bounded_json import BoundedJsonError, JsonLimits, parse_json_bytes

logger = logging.getLogger(__name__)

LOCALES_ENV_VAR = "WAVEHELM_LOCALES"
MAX_LOCALE_DOCUMENT_BYTES = 1_048_576
MAX_LOCALE_DIRECTORY_ENTRIES = 512
MAX_LOCALE_FILES = 128
MAX_LOCALE_ENTRIES = 4_096
MAX_LOCALE_KEY_CHARS = 128
MAX_LOCALE_VALUE_CHARS = 4_096
LOCALE_JSON_LIMITS = JsonLimits(
    max_bytes=MAX_LOCALE_DOCUMENT_BYTES,
    max_depth=3,
    max_nodes=(MAX_LOCALE_ENTRIES * 2) + 1,
    max_container_items=MAX_LOCALE_ENTRIES,
    max_key_chars=MAX_LOCALE_KEY_CHARS,
    max_key_bytes=512,
    max_string_chars=MAX_LOCALE_VALUE_CHARS,
    max_string_bytes=16_384,
    max_total_text_chars=MAX_LOCALE_DOCUMENT_BYTES,
    max_total_text_bytes=MAX_LOCALE_DOCUMENT_BYTES,
    max_number_chars=64,
)
_LANGUAGE_CODE_PATTERN = re.compile(r"^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$", re.ASCII)
LOCALIZATION_FORMAT_EXCEPTIONS = (IndexError, KeyError, TypeError, ValueError)
LOCALIZATION_CALLBACK_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)


class LocalizationError(ValueError):
    """Raised when a locale boundary or locale document is invalid."""


@dataclass(frozen=True, slots=True)
class PreparedLocale:
    """Immutable locale snapshot prepared before a persistence commit."""

    code: str
    translations: tuple[tuple[str, str], ...]
    _owner: object


def _callback_requires_language_arg(callback: Callable[..., None]) -> bool:
    try:
        signature = inspect.signature(callback)
    except (TypeError, ValueError):
        return False

    required_positional = 0
    positional_capacity = 0
    has_varargs = False
    for parameter in signature.parameters.values():
        if parameter.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            positional_capacity += 1
            if parameter.default is inspect.Signature.empty:
                required_positional += 1
        elif parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            has_varargs = True
    return required_positional == 1 and (positional_capacity >= 1 or has_varargs)

def _is_link_or_junction(path: Path) -> bool:
    """Return whether a sensitive path is a symbolic link or junction."""
    try:
        if path.is_symlink():
            return True
        junction_check = getattr(path, "is_junction", None)
        return bool(callable(junction_check) and junction_check())
    except OSError as error:
        raise LocalizationError(f"Unable to inspect locale path: {path}") from error

def _absolute_path(value: str | Path) -> Path:
    try:
        return Path(os.path.abspath(os.path.expanduser(os.fspath(value))))
    except (OSError, TypeError, ValueError) as error:
        raise LocalizationError("The locales directory path is invalid.") from error

def _validate_root_candidate(candidate: str | Path, *, required: bool) -> Path | None:
    path = _absolute_path(candidate)
    if _is_link_or_junction(path):
        if required:
            raise LocalizationError("The locales directory cannot be a link or junction.")
        return None
    if not path.exists():
        if required:
            raise LocalizationError(f"The locales directory does not exist: {path}")
        return None
    if not path.is_dir():
        if required:
            raise LocalizationError(f"The locales path is not a directory: {path}")
        return None
    try:
        return path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        if required:
            raise LocalizationError(f"Unable to resolve locales directory: {path}") from error
        return None

def _resolve_locales_dir(explicit: str | Path | None = None) -> Path:
    """Resolve one real, non-linked locales directory in deterministic order."""
    if explicit is not None:
        resolved = _validate_root_candidate(explicit, required=True)
        if resolved is None:
            raise LocalizationError("The explicit locales directory is unavailable.")
        return resolved

    environment_path = os.environ.get(LOCALES_ENV_VAR)
    if environment_path:
        resolved = _validate_root_candidate(environment_path, required=True)
        if resolved is None:
            raise LocalizationError("The environment locales directory is unavailable.")
        return resolved

    candidates: list[Path] = []
    pyinstaller_root = getattr(sys, "_MEIPASS", None)
    if pyinstaller_root:
        candidates.extend([Path(pyinstaller_root) / "locales", Path(pyinstaller_root) / "src" / "locales"])
    candidates.extend(
        [
            Path(__file__).resolve().parents[1] / "locales",
            Path(__file__).resolve().parents[2] / "locales",
            Path.cwd() / "src" / "locales",
            Path.cwd() / "locales",
            get_app_data_dir() / "locales",
        ]
    )
    for candidate in candidates:
        resolved = _validate_root_candidate(candidate, required=False)
        if resolved is not None:
            return resolved
    raise LocalizationError("No safe locales directory is available.")

def _normalize_language_code(language: object) -> str:
    if not isinstance(language, str):
        raise LocalizationError("The language code must be a string.")
    normalized = language.strip().lower().replace("_", "-")
    if not _LANGUAGE_CODE_PATTERN.fullmatch(normalized):
        raise LocalizationError("The language code is invalid or path-like.")
    return normalized

class LocalizationManager:
    """Load translations only from validated regular files inside one trusted root.

    Edge cases include path-like language input, linked or oversized files, and
    malformed, ambiguous, nested, or non-string JSON. Codes are resolved through
    a discovered allowlist; reads are descriptor-bound and byte-limited; parsed
    documents must satisfy a bounded ``dict[str, str]`` contract.
    """
    def __init__(
        self,
        locales_dir: str | Path | None = None,
        fallback_language: str = "en",
    ) -> None:
        self.locales_dir = _resolve_locales_dir(locales_dir)
        self._locales_identity: tuple[int, int] | None = None
        self._locales_root = self._validate_locales_root()
        self._prepared_owner = object()
        fallback_code, fallback_strings = self._load_available_language(fallback_language)
        self.fallback_language = fallback_code
        self.current_language = fallback_code
        self._fallback_strings = fallback_strings
        self._strings = dict(fallback_strings)
        self._language_change_callbacks: list[Callable[..., None]] = []
    def prepare_language(self, language: object) -> PreparedLocale:
        """Load one immutable locale snapshot without changing active runtime state."""
        code, strings = self._load_available_language(language)
        return PreparedLocale(code, tuple(strings.items()), self._prepared_owner)
    def validate_language(self, language: object) -> str:
        """Return the canonical code only when its locale file exists and is valid."""
        return self.prepare_language(language).code
    def activate_prepared_language(self, prepared: PreparedLocale) -> None:
        """Activate a snapshot created by this manager after persistence succeeds."""
        if not isinstance(prepared, PreparedLocale) or prepared._owner is not self._prepared_owner:
            raise LocalizationError("The prepared locale does not belong to this manager.")
        strings = {**self._fallback_strings, **dict(prepared.translations)}
        if prepared.code == self.current_language and strings == self._strings:
            return
        self._strings = strings
        self.current_language = prepared.code
        self._notify_language_change_callbacks()
    def set_language(self, language: str) -> None:
        """Activate one valid available locale, leaving state unchanged on failure."""
        code = _normalize_language_code(language)
        if code == self.current_language and self._strings:
            return
        self.activate_prepared_language(self.prepare_language(code))
    def get_current_language(self) -> str:
        """Return the currently active canonical language code."""
        return self.current_language
    def get_language(self) -> str:
        """Compatibility alias for ``get_current_language``."""
        return self.get_current_language()
    def get_text(self, key: str, default: str | None = None, **kwargs: object) -> str:
        if not key:
            return default or ""
        text = self._strings.get(key) or self._fallback_strings.get(key) or (default or key)
        if kwargs:
            try:
                text = str(text).format(**kwargs)
            except LOCALIZATION_FORMAT_EXCEPTIONS as error:
                logger.debug(
                    "Localization formatting failed for key %s: %s",
                    key,
                    error,
                    exc_info=True,
                )
        return str(text)
    def register_language_change_callback(self, callback: Callable[..., None]) -> None:
        if callable(callback) and callback not in self._language_change_callbacks:
            self._language_change_callbacks.append(callback)
    def unregister_language_change_callback(self, callback: Callable[..., None]) -> None:
        try:
            self._language_change_callbacks.remove(callback)
        except ValueError as error:
            logger.debug(
                "Language change callback already absent during unregister: %r (%s)",
                callback,
                error,
                exc_info=True,
            )
    def _validate_locales_root(self) -> Path:
        if _is_link_or_junction(self.locales_dir):
            raise LocalizationError("The locales directory cannot be a link or junction.")
        try:
            resolved = self.locales_dir.resolve(strict=True)
            metadata = resolved.stat()
        except (OSError, RuntimeError) as error:
            raise LocalizationError("The locales directory is unavailable.") from error
        if not resolved.is_dir():
            raise LocalizationError("The locales path is not a directory.")
        identity = (metadata.st_dev, metadata.st_ino)
        if self._locales_identity not in (None, identity):
            raise LocalizationError("The locales directory identity changed.")
        self._locales_identity = identity
        return resolved
    def _discover_locale_sources(self) -> dict[str, str]:
        root = self._validate_locales_root()
        sources: dict[str, str] = {}
        ambiguous_codes: set[str] = set()
        json_files = 0
        try:
            with os.scandir(root) as entries:
                for entry_index, entry in enumerate(entries, start=1):
                    if entry_index > MAX_LOCALE_DIRECTORY_ENTRIES:
                        raise LocalizationError("The locales directory contains too many entries.")
                    if Path(entry.name).suffix != ".json":
                        continue
                    json_files += 1
                    if json_files > MAX_LOCALE_FILES:
                        raise LocalizationError("The locales directory contains too many locale files.")
                    source = self._validate_locale_entry(entry)
                    if source is None:
                        continue
                    code, filename = source
                    if code in sources or code in ambiguous_codes:
                        sources.pop(code, None)
                        ambiguous_codes.add(code)
                        continue
                    sources[code] = filename
        except OSError as error:
            raise LocalizationError("Unable to scan the locales directory.") from error
        return sources

    @staticmethod
    def _validate_locale_entry(entry: os.DirEntry[str]) -> tuple[str, str] | None:
        try:
            if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                return None
        except OSError:
            return None
        stem = Path(entry.name).stem
        try:
            code = _normalize_language_code(stem)
        except LocalizationError:
            return None
        if Path(entry.name).name != entry.name:
            return None
        return code, entry.name
    def _load_available_language(self, language: object) -> tuple[str, dict[str, str]]:
        code = _normalize_language_code(language)
        filename = self._discover_locale_sources().get(code)
        if filename is None:
            raise LocalizationError(f"Language is not available: {code}")
        return code, self._read_locale_document(filename, code)
    def _load_language_file(self, language: str) -> dict[str, str]:
        """Compatibility helper that never reads outside the validated locale allowlist."""
        try:
            _code, strings = self._load_available_language(language)
            return strings
        except LocalizationError as error:
            logger.debug(
                "Failed to load locale file for language %s: %s",
                str(language).encode("unicode_escape").decode("ascii"),
                error,
                exc_info=True,
            )
            return {}
    def _read_locale_document(self, filename: str, code: str) -> dict[str, str]:
        payload = self._read_locale_payload(filename)
        try:
            parsed = parse_json_bytes(
                payload,
                limits=LOCALE_JSON_LIMITS,
                root="object",
            )
        except BoundedJsonError as error:
            raise LocalizationError(
                f"Locale {code} violates the bounded JSON contract: {error}."
            ) from error
        return self._validate_locale_mapping(parsed, code)
    def _read_locale_payload(self, filename: str) -> bytes:
        root = self._validate_locales_root()
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = self._open_locale_descriptor(root, filename, flags)
        try:
            metadata = os.fstat(descriptor)
            self._validate_open_locale(metadata)
            return self._read_bounded_descriptor(descriptor)
        except OSError as error:
            raise LocalizationError(f"Unable to read locale file: {filename}") from error
        finally:
            os.close(descriptor)

    def _open_locale_descriptor(self, root: Path, filename: str, flags: int) -> int:
        if os.open in os.supports_dir_fd and hasattr(os, "O_DIRECTORY"):
            root_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
            root_flags |= getattr(os, "O_NOFOLLOW", 0)
            try:
                root_descriptor = os.open(root, root_flags)
            except OSError as error:
                raise LocalizationError("Unable to open the locales directory.") from error
            try:
                metadata = os.fstat(root_descriptor)
                if self._locales_identity != (metadata.st_dev, metadata.st_ino):
                    raise LocalizationError("The opened locales directory identity changed.")
                return os.open(filename, flags, dir_fd=root_descriptor)
            except OSError as error:
                raise LocalizationError(f"Unable to open locale file: {filename}") from error
            finally:
                os.close(root_descriptor)

        candidate = root / filename
        if _is_link_or_junction(candidate):
            raise LocalizationError(f"Locale file cannot be a link: {filename}")
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise LocalizationError(f"Unable to resolve locale file: {filename}") from error
        if resolved.parent != root:
            raise LocalizationError(f"Locale file escapes the locale root: {filename}")
        try:
            return os.open(resolved, flags)
        except OSError as error:
            raise LocalizationError(f"Unable to open locale file: {filename}") from error

    @staticmethod
    def _validate_open_locale(metadata: os.stat_result) -> None:
        if not stat.S_ISREG(metadata.st_mode):
            raise LocalizationError("Locale input is not a regular file.")
        if metadata.st_nlink > 1:
            raise LocalizationError("Hard-linked locale files are not allowed.")
        if metadata.st_size > MAX_LOCALE_DOCUMENT_BYTES:
            raise LocalizationError("The locale document exceeds the 1 MiB limit.")

    @staticmethod
    def _read_bounded_descriptor(descriptor: int) -> bytes:
        remaining = MAX_LOCALE_DOCUMENT_BYTES + 1
        chunks: list[bytes] = []
        while remaining > 0:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > MAX_LOCALE_DOCUMENT_BYTES:
            raise LocalizationError("The locale document exceeds the 1 MiB limit.")
        return payload

    @staticmethod
    def _validate_locale_mapping(parsed: object, code: str) -> dict[str, str]:
        if not isinstance(parsed, dict):
            raise LocalizationError(f"Locale {code} root must be a JSON object.")
        if not parsed or len(parsed) > MAX_LOCALE_ENTRIES:
            raise LocalizationError(f"Locale {code} has an invalid number of entries.")
        result: dict[str, str] = {}
        for key, value in parsed.items():
            if not isinstance(key, str) or not key or len(key) > MAX_LOCALE_KEY_CHARS:
                raise LocalizationError(f"Locale {code} contains an invalid key.")
            if any(ord(character) < 32 or ord(character) == 127 for character in key):
                raise LocalizationError(f"Locale {code} contains a control character key.")
            if not isinstance(value, str) or len(value) > MAX_LOCALE_VALUE_CHARS:
                raise LocalizationError(f"Locale {code} values must be bounded strings.")
            result[key] = value
        return result
    def _notify_language_change_callbacks(self) -> None:
        for callback in list(self._language_change_callbacks):
            try:
                if _callback_requires_language_arg(callback):
                    callback(self.current_language)
                else:
                    callback()
            except LOCALIZATION_CALLBACK_EXCEPTIONS:
                logger.debug(
                    "Language change callback failed: %r",
                    callback,
                    exc_info=True,
                )
    def get_locales_dir(self) -> Path:
        return self.locales_dir

    def get_available_languages(self) -> dict[str, str]:
        """Return valid locales; one malformed file cannot hide other valid files."""
        fallback_names = {
            "en": "English", "es": "Español", "fr": "Français", "it": "Italiano",
        }
        try:
            sources = self._discover_locale_sources()
        except LocalizationError as error:
            logger.error("Unable to inventory locales: %s", error, exc_info=True)
            return {}

        languages: dict[str, str] = {}
        for code, filename in sorted(sources.items()):
            try:
                data = self._read_locale_document(filename, code)
            except LocalizationError as error:
                logger.debug("Ignoring invalid locale %s: %s", code, error, exc_info=True)
                continue
            name = data.get("_language_name") or data.get("_language_name_")
            languages[code] = name or fallback_names.get(code, code.capitalize())
        return languages
