from __future__ import annotations

"""Toolkit-agnostic theme management for the maintained wxPython runtime."""

from collections.abc import Callable
import logging
from pathlib import Path
from typing import Protocol, TypeAlias

from src.audio.audio_events import AudioEventType
from src.utils.bounded_json import (
    BoundedJsonError,
    JsonLimits,
    read_json_file,
    serialize_json_bytes,
)
from src.model.theme_manager_builtins import BUILTIN_COLOR_THEME_NAMES, get_builtin_themes
from src.model.theme_schema import (
    ThemeCatalog,
    ThemeColors,
    clone_theme_catalog,
    clone_theme_colors,
    extract_custom_theme_catalog,
    normalize_runtime_theme_catalog,
    normalize_theme_name,
    safe_theme_log,
    validate_builtin_theme_catalog,
    validate_color_theme_names,
    validate_custom_theme_catalog,
    validate_theme_color,
    validate_theme_color_key,
)
from src.utils.durable_io import DurabilityStatus, SerializedCommitGate, write_bytes_atomic_durable
from src.utils.exceptions import SettingsError
from src.utils.helpers import get_app_data_path

logger = logging.getLogger(__name__)
MAX_THEME_DOCUMENT_BYTES = 512 * 1024
THEME_JSON_LIMITS = JsonLimits(
    max_bytes=MAX_THEME_DOCUMENT_BYTES,
    max_depth=4,
    max_nodes=5_000,
    max_container_items=128,
    max_key_chars=256,
    max_key_bytes=1_024,
    max_string_chars=256,
    max_string_bytes=1_024,
    max_total_text_chars=262_144,
    max_total_text_bytes=MAX_THEME_DOCUMENT_BYTES,
)
ThemeMutation: TypeAlias = Callable[[ThemeCatalog], None]
FILE_EXCEPTIONS = (
    BoundedJsonError,
    OSError,
    RecursionError,
    TypeError,
    UnicodeError,
    ValueError,
)

SCHEMA_EXCEPTIONS = (RecursionError, TypeError, UnicodeError, ValueError)


def _log(level: int, message: str, *args: object, exc_info: bool = False) -> None:
    safe_theme_log(logger, level, message, *args, exc_info=exc_info)


class LocalizationProvider(Protocol):
    def get_text(self, key: str, **kwargs: object) -> str:
        """Return localized text for *key*."""


class EventPublisher(Protocol):
    def publish(self, event_type: AudioEventType, payload: object) -> object:
        """Publish one event payload."""


class ThemeManager:
    """Manage trusted built-ins and schema-valid persisted custom themes."""

    def __init__(
        self,
        localization_manager: LocalizationProvider | None = None,
        event_bus: EventPublisher | None = None,
        initial_mode: str = "dark",
        initial_color_theme: str = "blue",
        default_mode: str = "dark",
    ) -> None:
        self.localization_manager = localization_manager
        self.event_bus = event_bus
        self.custom_themes_file = Path(get_app_data_path()) / "custom_themes.json"
        self._commit_gate = SerializedCommitGate()
        self._custom_theme_write_block: str | None = None
        self._builtin_themes = self._validated_builtin_catalog()
        self._builtin_names = frozenset(self._builtin_themes)
        self._color_theme_names = self._validated_color_theme_names()
        self._themes = clone_theme_catalog(self._builtin_themes)
        self.default_mode = self._resolve_default_mode(default_mode)
        self._load_custom_themes()
        self.mode = self._resolve_mode(initial_mode, self.default_mode)
        self.color_theme = self._resolve_color_theme(initial_color_theme)
        self._theme_change_callbacks: list[Callable[[], None]] = []
        self._apply_theme_core()
        _log(
            logging.INFO,
            self._get_localized_text(
                "theme_manager_initialized", theme=self.mode, color=self.color_theme
            ),
        )

    @property
    def themes(self) -> ThemeCatalog:
        """Return a detached catalog snapshot for legacy read-only consumers."""
        return clone_theme_catalog(self._themes)

    def _validated_builtin_catalog(self) -> ThemeCatalog:
        try:
            return validate_builtin_theme_catalog(get_builtin_themes())
        except SCHEMA_EXCEPTIONS as error:
            raise SettingsError(
                "The built-in theme catalog is invalid.",
                details=f"{type(error).__name__}: {error}",
            ) from error

    def _validated_color_theme_names(self) -> tuple[str, ...]:
        try:
            return validate_color_theme_names(BUILTIN_COLOR_THEME_NAMES)
        except SCHEMA_EXCEPTIONS as error:
            raise SettingsError(
                "Built-in color theme names are invalid.",
                details=f"{type(error).__name__}: {error}",
            ) from error

    def _resolve_default_mode(self, raw_mode: object) -> str:
        try:
            mode = normalize_theme_name(raw_mode)
        except ValueError as error:
            raise SettingsError("Default theme mode is invalid.") from error
        if mode == "system" or mode not in self._builtin_themes:
            raise SettingsError("Default theme mode must name a built-in palette.")
        return mode

    def _resolve_mode(self, raw_mode: object, fallback: str) -> str:
        try:
            mode = normalize_theme_name(raw_mode)
        except ValueError:
            return fallback
        if mode == "system":
            return mode
        return mode if mode in self._themes else fallback

    def _resolve_color_theme(self, raw_name: object) -> str:
        try:
            name = normalize_theme_name(raw_name)
        except ValueError:
            return self.get_default_color_theme_name()
        return name if name in self._color_theme_names else self.get_default_color_theme_name()

    def _get_localized_text(self, key: str, **kwargs: object) -> str:
        try:
            if self.localization_manager is not None:
                text = self.localization_manager.get_text(key, **kwargs)
                if isinstance(text, str) and text.strip():
                    return text
        except Exception as error:  # explicit localization-provider boundary
            _log(
                logging.DEBUG,
                "ThemeManager localization fallback for %s: %s",
                key,
                error,
                exc_info=True,
            )
        return f"[{key}]"

    def _load_builtin_themes(self) -> ThemeCatalog:
        """Return a detached built-in snapshot for compatibility consumers."""
        return clone_theme_catalog(self._builtin_themes)

    def _load_custom_themes(self) -> None:
        """Load custom themes or block writes without altering invalid bytes.

        Edge cases: missing files allow creation; invalid files remain unchanged;
        reserved names cannot shadow trusted or virtual themes.
        """
        try:
            raw_catalog = self._read_custom_catalog()
            custom_catalog = validate_custom_theme_catalog(
                raw_catalog, builtin_names=self._builtin_names
            )
        except FileNotFoundError:
            _log(logging.INFO, self._get_localized_text("custom_themes_file_not_found"))
            return
        except FILE_EXCEPTIONS as error:
            self._block_custom_writes(error)
            return
        self._themes.update(custom_catalog)
        _log(logging.INFO, "Custom themes loaded from %s", self.custom_themes_file)

    def _read_custom_catalog(self) -> object:
        return read_json_file(
            self.custom_themes_file,
            limits=THEME_JSON_LIMITS,
            root="object",
        )

    def _block_custom_writes(self, error: Exception) -> None:
        reason = f"{type(error).__name__}: {error}"
        self._custom_theme_write_block = reason
        _log(
            logging.ERROR,
            "Unable to load custom themes from %s; writes are blocked: %s",
            self.custom_themes_file,
            reason,
        )

    def _ensure_custom_catalog_writable(self) -> None:
        if self._custom_theme_write_block is not None:
            raise SettingsError(
                "Custom theme writes are blocked because the existing file could not be loaded.",
                details=self._custom_theme_write_block,
            )

    def _persist_catalog(self, candidate: ThemeCatalog) -> DurabilityStatus:
        """Persist a validated custom-only snapshot before live-state replacement.

        Edge cases: serialization or replace failure preserves live state; a failed
        parent-directory sync reports a committed but degraded durability result.
        """
        custom_catalog = extract_custom_theme_catalog(
            candidate, builtin_names=self._builtin_names
        )
        try:
            _normalized, payload = serialize_json_bytes(
                custom_catalog,
                limits=THEME_JSON_LIMITS,
                root="object",
                indent=4,
                sort_keys=True,
            )
            synced = write_bytes_atomic_durable(self.custom_themes_file, payload)
        except BoundedJsonError as error:
            raise SettingsError(
                "Unable to serialize custom themes.",
                details=f"{type(error).__name__}: {error}",
            ) from error
        except OSError as error:
            raise SettingsError(
                "Unable to persist custom themes.",
                details=f"{type(error).__name__}: {error}",
            ) from error
        return DurabilityStatus.from_directory_sync(synced)

    def _mutate_custom_catalog(self, mutation: ThemeMutation) -> DurabilityStatus:
        """Serialize concurrent mutations and publish only committed state.

        Edge cases: reentry fails instead of deadlocking; schema/write failures release
        the next writer; no candidate is exposed before persistence completes.
        """
        try:
            with self._commit_gate.transaction():
                self._ensure_custom_catalog_writable()
                candidate = clone_theme_catalog(self._themes)
                mutation(candidate)
                normalized = normalize_runtime_theme_catalog(
                    candidate, builtin_catalog=self._builtin_themes
                )
                status = self._persist_catalog(normalized)
                self._themes = normalized
        except SettingsError:
            raise
        except SCHEMA_EXCEPTIONS + (RuntimeError,) as error:
            raise SettingsError(
                "Unable to apply the custom theme mutation.",
                details=f"{type(error).__name__}: {error}",
            ) from error
        if not status.is_fully_durable:
            _log(
                logging.WARNING,
                "Custom themes committed without confirmed parent-directory sync: %s",
                self.custom_themes_file,
            )
        return status

    def save_custom_themes(self) -> DurabilityStatus:
        return self._mutate_custom_catalog(lambda _candidate: None)

    def _apply_theme_core(self) -> None:
        self.notify_theme_change()
        if self.event_bus is None:
            return
        try:
            published = self.event_bus.publish(
                AudioEventType.THEME_CHANGED,
                {"mode": self.mode, "color_theme": self.color_theme},
            )
            if published is False:
                _log(logging.WARNING, "THEME_CHANGED was rejected by the event bus.")
        except Exception as error:  # explicit event-publisher boundary
            _log(logging.ERROR, "Could not publish THEME_CHANGED: %s", error, exc_info=True)

    def _apply_ctk_theme(self) -> None:  # pragma: no cover - compatibility alias
        self._apply_theme_core()

    def set_theme(self, new_mode: str, new_color_theme: str) -> bool:
        resolved_mode = self._resolve_mode(new_mode, self.default_mode)
        resolved_color = self._resolve_color_theme(new_color_theme)
        if self.mode == resolved_mode and self.color_theme == resolved_color:
            _log(logging.DEBUG, self._get_localized_text("theme_no_change"))
            return False
        self.mode, self.color_theme = resolved_mode, resolved_color
        self._apply_theme_core()
        _log(
            logging.INFO,
            self._get_localized_text(
                "theme_changed", mode=self.mode, color=self.color_theme
            ),
        )
        return True

    def get_current_theme_colors(self) -> ThemeColors:
        selected = self.default_mode if self.mode == "system" else self.mode
        palette = self._themes.get(selected, self._builtin_themes["dark"])
        return clone_theme_colors(palette)

    def get_current_theme(self) -> ThemeColors:
        return self.get_current_theme_colors()

    def get_current_theme_name(self) -> str:
        return self.mode

    @property
    def current_theme_name(self) -> str:
        return self.mode

    @property
    def current_theme(self) -> ThemeColors:
        return self.get_current_theme_colors()

    def get_current_color_theme_name(self) -> str:
        return self.color_theme

    def get_default_theme_name(self) -> str:
        return self.default_mode

    def get_default_color_theme_name(self) -> str:
        return "blue" if "blue" in self._color_theme_names else self._color_theme_names[0]

    def get_available_theme_names(self) -> list[str]:
        return list(self._themes)

    def get_available_color_theme_names(self) -> list[str]:
        return list(self._color_theme_names)

    def _build_custom_base(self) -> ThemeColors:
        return clone_theme_colors(self._builtin_themes["dark"])

    def _announce_custom_theme_commit(self) -> None:
        if not self.set_theme("custom", self.color_theme):
            self._apply_theme_core()
            _log(logging.INFO, "Custom theme palette changed while already active.")

    def set_custom_theme_color(self, color_key: str, hex_color: str) -> DurabilityStatus:
        try:
            validated_key = validate_theme_color_key(color_key)
            validated_color = validate_theme_color(hex_color, key=validated_key)
        except ValueError as error:
            raise SettingsError(
                "Invalid custom theme color.",
                details=f"{type(error).__name__}: {error}",
            ) from error

        def update(candidate: ThemeCatalog) -> None:
            custom = candidate.setdefault("custom", self._build_custom_base())
            custom[validated_key] = validated_color

        status = self._mutate_custom_catalog(update)
        self._announce_custom_theme_commit()
        return status

    def get_custom_theme_colors(self) -> ThemeColors:
        custom = self._themes.get("custom")
        return clone_theme_colors(custom) if custom is not None else {}

    def reset_custom_theme_colors(self) -> DurabilityStatus:
        def reset(candidate: ThemeCatalog) -> None:
            candidate["custom"] = self._build_custom_base()

        status = self._mutate_custom_catalog(reset)
        self._announce_custom_theme_commit()
        return status

    def _callback_name(self, callback: Callable[[], None]) -> str:
        try:
            return getattr(callback, "__name__", repr(callback))
        except Exception:  # explicit diagnostic boundary for external callbacks
            return "<callback>"

    def register_theme_change_callback(self, callback: Callable[[], None]) -> Callable[[], None]:
        if callback not in self._theme_change_callbacks:
            self._theme_change_callbacks.append(callback)
            _log(
                logging.DEBUG,
                self._get_localized_text(
                    "registered_theme_callback", callback=self._callback_name(callback)
                ),
            )
        return callback

    def unregister_theme_change_callback(self, callback: Callable[[], None]) -> None:
        if callback in self._theme_change_callbacks:
            self._theme_change_callbacks.remove(callback)
            _log(
                logging.DEBUG,
                self._get_localized_text(
                    "unregistered_theme_callback", callback=self._callback_name(callback)
                ),
            )

    def notify_theme_change(self) -> None:
        callbacks = list(self._theme_change_callbacks)
        _log(
            logging.DEBUG,
            self._get_localized_text("notifying_theme_change", count=len(callbacks)),
        )
        for callback in callbacks:
            try:
                callback()
            except Exception as error:  # explicit external-callback boundary
                _log(
                    logging.ERROR,
                    "Theme callback %s failed: %s",
                    self._callback_name(callback),
                    error,
                    exc_info=True,
                )

    def ensure_ttk_treeview_style(self, style_name: str) -> None:  # pragma: no cover
        _log(
            logging.DEBUG,
            "ensure_ttk_treeview_style(%s) called - no-op in current runtime",
            style_name,
        )

    def close(self) -> None:
        _log(logging.INFO, self._get_localized_text("theme_manager_closing"))
        self._theme_change_callbacks.clear()
        _log(logging.INFO, self._get_localized_text("theme_manager_closed"))
