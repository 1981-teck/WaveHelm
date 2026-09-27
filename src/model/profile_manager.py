from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
import logging
from typing import TypeAlias, cast

from src.utils.bounded_json import (
    BoundedJsonError,
    JsonLimits,
    JsonValue,
    normalize_json_mapping,
    read_json_file,
    serialize_json_bytes,
)
from src.utils.durable_io import (
    DurabilityStatus,
    SerializedCommitGate,
    write_bytes_atomic_durable,
)
from src.utils.exceptions import ProfileError
from src.utils.helpers import get_app_data_path

logger = logging.getLogger(__name__)

APP_DIR = get_app_data_path()
APP_DIR.mkdir(parents=True, exist_ok=True)
PROFILE_PATH = APP_DIR / "profile.json"

MAX_PROFILE_DOCUMENT_BYTES = 4 * 1024 * 1024
MAX_PROFILE_KEY_CHARS = 256
MAX_PROFILE_KEY_BYTES = 1_024
PROFILE_JSON_LIMITS = JsonLimits(
    max_bytes=MAX_PROFILE_DOCUMENT_BYTES,
    max_depth=24,
    max_nodes=50_000,
    max_container_items=4_096,
    max_key_chars=MAX_PROFILE_KEY_CHARS,
    max_key_bytes=MAX_PROFILE_KEY_BYTES,
    max_string_chars=262_144,
    max_string_bytes=1_048_576,
    max_total_text_chars=2_000_000,
    max_total_text_bytes=MAX_PROFILE_DOCUMENT_BYTES,
)

JsonObject: TypeAlias = dict[str, JsonValue]
ProfileMutation: TypeAlias = Callable[["UserProfile"], None]

PROFILE_SECTION_NAMES = frozenset(
    {
        "stats",
        "effects_settings",
        "eq_settings",
        "custom_eq_presets",
        "custom_effects_settings",
        "home_stats_prefs",
    }
)

PROFILE_LOAD_EXCEPTIONS = (
    BoundedJsonError,
    OSError,
    TypeError,
    UnicodeError,
    ValueError,
)
PROFILE_MUTATION_EXCEPTIONS = (RuntimeError, TypeError, ValueError)
PROFILE_SERIALIZATION_EXCEPTIONS = (RecursionError, TypeError, ValueError)


@dataclass(slots=True)
class UserProfile:
    """In-memory snapshot of the persisted profile document."""

    stats: JsonObject = field(default_factory=dict)
    effects_settings: JsonObject = field(default_factory=dict)
    eq_settings: JsonObject = field(default_factory=dict)
    custom_eq_presets: JsonObject = field(default_factory=dict)
    custom_effects_settings: JsonObject = field(default_factory=dict)
    home_stats_prefs: JsonObject = field(default_factory=dict)


def _copy_profile(profile: UserProfile) -> UserProfile:
    return deepcopy(profile)


def _read_profile_section(data: Mapping[str, object], section_name: str) -> JsonObject:
    raw_section = data.get(section_name, {})
    if not isinstance(raw_section, dict):
        raise ValueError(f"profile section '{section_name}' must be a JSON object")
    return deepcopy(cast(JsonObject, raw_section))


def _profile_from_object(raw_data: object) -> UserProfile:
    if not isinstance(raw_data, dict):
        raise ValueError("profile document root must be a JSON object")
    data = cast(Mapping[str, object], raw_data)
    unknown_sections = set(data).difference(PROFILE_SECTION_NAMES)
    if unknown_sections:
        names = ", ".join(sorted(unknown_sections))
        raise ValueError(f"unsupported profile sections: {names}")
    return UserProfile(
        stats=_read_profile_section(data, "stats"),
        effects_settings=_read_profile_section(data, "effects_settings"),
        eq_settings=_read_profile_section(data, "eq_settings"),
        custom_eq_presets=_read_profile_section(data, "custom_eq_presets"),
        custom_effects_settings=_read_profile_section(
            data, "custom_effects_settings"
        ),
        home_stats_prefs=_read_profile_section(data, "home_stats_prefs"),
    )


def _profile_to_object(profile: UserProfile) -> JsonObject:
    return {
        "stats": profile.stats,
        "effects_settings": profile.effects_settings,
        "eq_settings": profile.eq_settings,
        "custom_eq_presets": profile.custom_eq_presets,
        "custom_effects_settings": profile.custom_effects_settings,
        "home_stats_prefs": profile.home_stats_prefs,
    }


def _canonicalize_profile(profile: UserProfile) -> tuple[UserProfile, bytes]:
    """Return one strict, bounded snapshot and the exact payload to persist.

    Edge cases:
        1. Cyclic or non-JSON input fails before file replacement.
        2. Non-finite or oversized values cannot enter the persisted profile.
        3. The normalized in-memory state matches the exact serialized JSON model.
    """
    normalized, payload = serialize_json_bytes(
        _profile_to_object(profile),
        limits=PROFILE_JSON_LIMITS,
        root="object",
        indent=2,
    )
    return _profile_from_object(normalized), payload


def _clone_input_mapping(value: Mapping[str, JsonValue] | None, label: str) -> JsonObject:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ProfileError(f"{label} must be a mapping.")
    try:
        normalized = normalize_json_mapping(
            value,
            limits=PROFILE_JSON_LIMITS,
        )
    except (BoundedJsonError, TypeError, ValueError) as error:
        raise ProfileError(
            f"Unable to serialize {label} within the bounded JSON contract.",
            details=f"{type(error).__name__}: {error}",
        ) from error
    return cast(JsonObject, normalized)


def _require_text_key(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ProfileError(f"{label} must be a string.")
    if not value or len(value) > MAX_PROFILE_KEY_CHARS:
        raise ProfileError(f"{label} must contain 1 to {MAX_PROFILE_KEY_CHARS} characters.")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ProfileError(f"{label} cannot contain control characters.")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ProfileError(f"{label} is not valid UTF-8 text.") from error
    if len(encoded) > MAX_PROFILE_KEY_BYTES:
        raise ProfileError(f"{label} exceeds the UTF-8 byte limit.")
    return value


class ProfileManager:
    """Manage the file-backed user profile with persist-before-commit semantics."""

    def __init__(
        self,
        db_manager: object | None = None,
        localization_manager: object | None = None,
        event_bus: object | None = None,
    ) -> None:
        self.db_manager = db_manager
        self.localization_manager = localization_manager
        self.event_bus = event_bus
        self._commit_gate = SerializedCommitGate()
        self._user_profile, self._profile_write_block = self._load_profile()
        logger.info("ProfileManager initialized with file-backed persistence.")

    @property
    def user_profile(self) -> UserProfile:
        """Return a detached snapshot; callers cannot mutate live profile state."""
        return _copy_profile(self._user_profile)

    def _load_profile(self) -> tuple[UserProfile, str | None]:
        """Load one profile snapshot and block writes after a corrupt source.

        Edge cases:
            1. A missing file is a valid empty profile and remains writable.
            2. An unreadable or malformed existing file must never be overwritten.
            3. Unknown top-level sections must block destructive downgrade writes.
        """
        try:
            document = read_json_file(
                PROFILE_PATH,
                limits=PROFILE_JSON_LIMITS,
                root="object",
            )
            return _profile_from_object(document), None
        except FileNotFoundError:
            return UserProfile(), None
        except PROFILE_LOAD_EXCEPTIONS as error:
            reason = f"{type(error).__name__}: {error}"
            logger.error("Profile load failed; writes are blocked: %s", reason)
            return UserProfile(), reason

    def _ensure_profile_writable(self) -> None:
        if self._profile_write_block is None:
            return
        raise ProfileError(
            "Profile writes are blocked because the existing file could not be loaded.",
            details=self._profile_write_block,
        )

    def _persist_candidate(
        self, candidate: UserProfile
    ) -> tuple[UserProfile, DurabilityStatus]:
        try:
            normalized, payload = _canonicalize_profile(candidate)
            directory_synced = write_bytes_atomic_durable(PROFILE_PATH, payload)
            status = DurabilityStatus.from_directory_sync(directory_synced)
        except PROFILE_SERIALIZATION_EXCEPTIONS as error:
            raise ProfileError(
                "Unable to serialize the user profile.",
                details=f"{type(error).__name__}: {error}",
            ) from error
        except OSError as error:
            raise ProfileError(
                "Unable to persist the user profile.",
                details=f"{type(error).__name__}: {error}",
            ) from error
        return normalized, status

    def _mutate_profile(self, mutation: ProfileMutation) -> DurabilityStatus:
        """Persist a detached candidate before replacing live state.

        Edge cases:
            1. A pre-replace write failure must leave live state unchanged.
            2. A post-replace directory-sync failure must commit the matching snapshot.
            3. Concurrent mutations must not reorder file and memory commits.
            4. Reentrant persistence must fail instead of deadlocking.
        """
        try:
            with self._commit_gate.transaction():
                self._ensure_profile_writable()
                candidate = _copy_profile(self._user_profile)
                mutation(candidate)
                committed, status = self._persist_candidate(candidate)
                self._user_profile = committed
        except ProfileError:
            raise
        except PROFILE_MUTATION_EXCEPTIONS as error:
            raise ProfileError(
                "Unable to apply the user profile mutation.",
                details=f"{type(error).__name__}: {error}",
            ) from error
        if not status.is_fully_durable:
            logger.warning(
                "Profile committed without confirmed parent-directory sync: %s",
                PROFILE_PATH,
            )
        return status

    def increment_stat(self, key: str, amount: int = 1) -> DurabilityStatus:
        stat_key = _require_text_key(key, "Statistic key")
        if isinstance(amount, bool) or not isinstance(amount, int):
            raise ProfileError("Statistic increments must be integers.")

        def update(candidate: UserProfile) -> None:
            current = candidate.stats.get(stat_key, 0)
            if isinstance(current, bool) or not isinstance(current, int):
                raise ProfileError(f"Statistic '{stat_key}' is not an integer.")
            candidate.stats[stat_key] = current + amount

        return self._mutate_profile(update)

    def get_stat(self, key: str) -> int:
        value = self._user_profile.stats.get(key, 0)
        if isinstance(value, bool) or not isinstance(value, int):
            return 0
        return value

    def set_effects_settings(
        self, settings: Mapping[str, JsonValue] | None
    ) -> DurabilityStatus:
        candidate_settings = _clone_input_mapping(settings, "Effects settings")
        return self._mutate_profile(
            lambda candidate: setattr(candidate, "effects_settings", candidate_settings)
        )

    def get_effects_settings(self) -> JsonObject:
        return deepcopy(self._user_profile.effects_settings)

    def set_eq_settings(
        self, settings: Mapping[str, JsonValue] | None
    ) -> DurabilityStatus:
        candidate_settings = _clone_input_mapping(settings, "Equalizer settings")
        return self._mutate_profile(
            lambda candidate: setattr(candidate, "eq_settings", candidate_settings)
        )

    def get_eq_settings(self) -> JsonObject:
        return deepcopy(self._user_profile.eq_settings)

    def get_custom_eq_presets(self) -> JsonObject:
        return deepcopy(self._user_profile.custom_eq_presets)

    def save_custom_eq_preset(
        self, name: str, preset_data: Mapping[str, JsonValue] | None
    ) -> DurabilityStatus:
        preset_name = _require_text_key(name, "Preset name")
        copied_preset = _clone_input_mapping(preset_data, "Preset data")

        def update(candidate: UserProfile) -> None:
            candidate.custom_eq_presets[preset_name] = copied_preset

        return self._mutate_profile(update)

    def delete_custom_eq_preset(self, name: str) -> DurabilityStatus:
        preset_name = _require_text_key(name, "Preset name")
        return self._mutate_profile(
            lambda candidate: candidate.custom_eq_presets.pop(preset_name, None)
        )

    def get_custom_effects_settings(self) -> JsonObject:
        return deepcopy(self._user_profile.custom_effects_settings)

    def save_custom_effects_setting(
        self, name: str, settings: Mapping[str, JsonValue] | None
    ) -> DurabilityStatus:
        setting_name = _require_text_key(name, "Effects setting name")
        copied_settings = _clone_input_mapping(settings, "Custom effects settings")

        def update(candidate: UserProfile) -> None:
            candidate.custom_effects_settings[setting_name] = copied_settings

        return self._mutate_profile(update)

    def delete_custom_effects_setting(self, name: str) -> DurabilityStatus:
        setting_name = _require_text_key(name, "Effects setting name")
        return self._mutate_profile(
            lambda candidate: candidate.custom_effects_settings.pop(setting_name, None)
        )

    def get_profile_setting(self, key: str, default: str = "") -> str:
        try:
            profile_data = getattr(self, "profile", None)
            if isinstance(profile_data, Mapping) and key in profile_data:
                return str(profile_data.get(key, default))
            settings_data = getattr(self, "settings", None)
            if isinstance(settings_data, Mapping) and key in settings_data:
                return str(settings_data.get(key, default))
        except PROFILE_MUTATION_EXCEPTIONS as error:
            logger.debug(
                "get_profile_setting fallback for %s: %s",
                key,
                error,
                exc_info=True,
            )
        return str(default)

    def set_home_stats_prefs(
        self, prefs: Mapping[str, JsonValue] | None
    ) -> DurabilityStatus:
        candidate_prefs = _clone_input_mapping(prefs, "Home statistics preferences")
        return self._mutate_profile(
            lambda candidate: setattr(candidate, "home_stats_prefs", candidate_prefs)
        )

    def get_home_stats_prefs(self) -> JsonObject:
        return deepcopy(self._user_profile.home_stats_prefs)
