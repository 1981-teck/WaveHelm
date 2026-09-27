from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import math
from typing import TypedDict

from src.utils.exceptions import DatabaseError, DomainError


DEFAULT_HISTORY_QUERY_LIMIT = 200
MAX_HISTORY_QUERY_LIMIT = 1_000
DEFAULT_HISTORY_RETENTION = 10_000
MAX_HISTORY_RETENTION = 100_000
MAX_HISTORY_TITLE_CHARS = 1_024
MAX_HISTORY_PATH_CHARS = 32_768
MAX_HISTORY_MEDIA_TYPE_CHARS = 128
MAX_HISTORY_TIMESTAMP_CHARS = 64
MAX_HISTORY_METADATA_BYTES = 65_536
MAX_HISTORY_METADATA_DEPTH = 12
MAX_HISTORY_METADATA_ITEMS = 512
MAX_HISTORY_METADATA_NODES = 4_096
MAX_HISTORY_METADATA_KEY_CHARS = 256
MAX_HISTORY_METADATA_STRING_CHARS = 32_768
MAX_HISTORY_INTEGER_ABS = 9_223_372_036_854_775_807


class HistoryDataError(DatabaseError):
    """Raised when persisted history data violates the supported contract."""


class HistoryCapabilityError(DomainError):
    """Raised when a requested history capability has no backing data model."""

    DEFAULT_MESSAGE = "The requested history capability is unavailable."
    ERROR_CODE = "HISTORY_CAPABILITY_UNAVAILABLE"


class HistoryRecord(TypedDict):
    """Public, JSON-compatible playback-history record."""

    id: int
    title: str
    path: str
    media_type: str
    duration: float
    metadata: dict[str, object]
    timestamp: str


@dataclass(frozen=True, slots=True)
class PreparedHistoryEntry:
    """Validated values ready for one atomic history insert."""

    path: str
    title: str
    media_type: str
    duration: float
    metadata_json: str | None
    timestamp: str


@dataclass(slots=True)
class _JsonBudget:
    remaining_nodes: int = MAX_HISTORY_METADATA_NODES

    def consume(self) -> None:
        self.remaining_nodes -= 1
        if self.remaining_nodes < 0:
            raise ValueError("History metadata exceeds the total node limit.")


def validate_history_limit(value: object) -> int:
    """Return a bounded query limit; ``None`` maps to the documented default."""
    if value is None:
        return DEFAULT_HISTORY_QUERY_LIMIT
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("History limit must be an integer or None.")
    if value < 1 or value > MAX_HISTORY_QUERY_LIMIT:
        raise ValueError(
            f"History limit must be between 1 and {MAX_HISTORY_QUERY_LIMIT}."
        )
    return value


def validate_history_retention(value: object) -> int:
    """Validate the hard cap enforced after every successful insert."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("History retention must be an integer.")
    if value < 1 or value > MAX_HISTORY_RETENTION:
        raise ValueError(
            f"History retention must be between 1 and {MAX_HISTORY_RETENTION}."
        )
    return value


def prepare_history_entry(
    *,
    path: object,
    title: object,
    media_type: object,
    duration: object,
    metadata: Mapping[str, object] | None,
    additional_data: Mapping[str, object] | None,
) -> PreparedHistoryEntry:
    """Validate and snapshot one history entry before opening a transaction.

    Edge cases:
        1. Boolean, non-finite, or negative durations are rejected.
        2. Metadata cycles and unsupported mutable values fail before any write.
        3. Extension fields cannot silently overwrite metadata with the same key.
    """
    normalized_path = _require_text(path, "path", MAX_HISTORY_PATH_CHARS)
    normalized_title = _require_text(title, "title", MAX_HISTORY_TITLE_CHARS)
    normalized_type = _require_text(
        media_type, "media_type", MAX_HISTORY_MEDIA_TYPE_CHARS
    )
    normalized_duration = _require_duration(duration)
    merged_metadata = _merge_metadata(metadata, additional_data)
    metadata_json = _serialize_metadata(merged_metadata)
    timestamp = datetime.now(UTC).isoformat(timespec="microseconds")
    return PreparedHistoryEntry(
        path=normalized_path,
        title=normalized_title,
        media_type=normalized_type,
        duration=normalized_duration,
        metadata_json=metadata_json,
        timestamp=timestamp,
    )


def decode_history_row(row: Mapping[str, object]) -> HistoryRecord:
    """Decode one database row without hiding schema or metadata corruption.

    Edge cases:
        1. Missing or mistyped required columns raise ``HistoryDataError``.
        2. Duplicate JSON keys or non-object metadata fail closed.
        3. Legacy naive and current UTC ISO timestamps remain readable.
    """
    record_id = _require_record_id(row.get("id"))
    try:
        title = _require_text(row.get("title"), "title", MAX_HISTORY_TITLE_CHARS)
        path = _require_text(row.get("path"), "path", MAX_HISTORY_PATH_CHARS)
        media_type = _require_text(
            row.get("media_type"), "media_type", MAX_HISTORY_MEDIA_TYPE_CHARS
        )
        duration = _require_duration(row.get("duration"))
        timestamp = _require_timestamp(row.get("timestamp"))
        metadata = _decode_metadata(row.get("metadata"))
    except (TypeError, ValueError) as error:
        raise HistoryDataError(
            "Persisted history row violates the supported contract.",
            details=f"id={record_id}; error={error}",
        ) from error
    return {
        "id": record_id,
        "title": title,
        "path": path,
        "media_type": media_type,
        "duration": duration,
        "metadata": metadata,
        "timestamp": timestamp,
    }


def _require_record_id(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise HistoryDataError(
            "Persisted history row has an invalid identifier.",
            details=f"id={value!r}",
        )
    return value


def _require_text(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise TypeError(f"History {field} must be a string.")
    if len(value) > maximum:
        raise ValueError(f"History {field} exceeds {maximum} characters.")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"History {field} must not contain control characters.")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError(f"History {field} must be valid UTF-8 text.") from error
    if not value.strip():
        raise ValueError(f"History {field} must not be empty.")
    return value


def _require_duration(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("History duration must be a real number.")
    try:
        normalized = float(value)
    except OverflowError as error:
        raise ValueError("History duration exceeds the supported numeric range.") from error
    if not math.isfinite(normalized) or normalized < 0.0:
        raise ValueError("History duration must be finite and non-negative.")
    return normalized


def _require_timestamp(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("History timestamp must be a string.")
    if not value or len(value) > MAX_HISTORY_TIMESTAMP_CHARS:
        raise ValueError("History timestamp is empty or oversized.")
    if "T" not in value and " " not in value:
        raise ValueError("History timestamp must include a time component.")
    try:
        datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("History timestamp is not valid ISO-8601.") from error
    return value


def _merge_metadata(
    metadata: Mapping[str, object] | None,
    additional_data: Mapping[str, object] | None,
) -> dict[str, object]:
    base = _copy_root_mapping(metadata, "metadata")
    extensions = _copy_root_mapping(additional_data, "additional_data")
    collisions = sorted(set(base).intersection(extensions))
    if collisions:
        names = ", ".join(collisions[:8])
        suffix = "" if len(collisions) <= 8 else ", ..."
        raise ValueError(
            "History metadata and additional_data contain duplicate keys: "
            f"{names}{suffix}."
        )
    base.update(extensions)
    normalized = _clone_json_value(base, _JsonBudget(), 0, set())
    if not isinstance(normalized, dict):
        raise TypeError("History metadata root must be an object.")
    return normalized


def _copy_root_mapping(
    value: Mapping[str, object] | None, field: str
) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError(f"History {field} must be a mapping or None.")
    try:
        if len(value) > MAX_HISTORY_METADATA_ITEMS:
            raise ValueError(f"History {field} exceeds the root item limit.")
        return dict(value.items())
    except (LookupError, RuntimeError, TypeError) as error:
        raise ValueError(f"History {field} changed during snapshot.") from error


def _serialize_metadata(value: dict[str, object]) -> str | None:
    if not value:
        return None
    try:
        payload = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:
        raise ValueError("History metadata is not JSON serializable.") from error
    try:
        encoded = payload.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("History metadata must be valid UTF-8 text.") from error
    if len(encoded) > MAX_HISTORY_METADATA_BYTES:
        raise ValueError("History metadata exceeds the encoded byte limit.")
    return payload


def _decode_metadata(value: object) -> dict[str, object]:
    if value is None or value == "":
        return {}
    if not isinstance(value, str):
        raise TypeError("Persisted history metadata must be text or null.")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("Persisted history metadata is not valid UTF-8 text.") from error
    if len(encoded) > MAX_HISTORY_METADATA_BYTES:
        raise ValueError("Persisted history metadata exceeds the byte limit.")
    try:
        decoded = json.loads(
            value,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, RecursionError, ValueError) as error:
        raise ValueError("Persisted history metadata is invalid JSON.") from error
    normalized = _clone_json_value(decoded, _JsonBudget(), 0, set())
    if not isinstance(normalized, dict):
        raise TypeError("Persisted history metadata root must be an object.")
    return normalized


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate history metadata key: {key!r}.")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"Non-standard JSON number is forbidden: {value}.")


def _clone_json_value(
    value: object,
    budget: _JsonBudget,
    depth: int,
    active_containers: set[int],
) -> object:
    budget.consume()
    if depth > MAX_HISTORY_METADATA_DEPTH:
        raise ValueError("History metadata exceeds the nesting-depth limit.")
    if value is None or isinstance(value, (bool, str)):
        if isinstance(value, str) and len(value) > MAX_HISTORY_METADATA_STRING_CHARS:
            raise ValueError("History metadata string exceeds the character limit.")
        return value
    if isinstance(value, int):
        if abs(value) > MAX_HISTORY_INTEGER_ABS:
            raise ValueError("History metadata integer exceeds signed 64-bit bounds.")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("History metadata contains a non-finite number.")
        return value
    if isinstance(value, Mapping):
        return _clone_mapping(value, budget, depth, active_containers)
    if isinstance(value, (list, tuple)):
        return _clone_sequence(value, budget, depth, active_containers)
    raise TypeError(
        f"History metadata contains unsupported type: {type(value).__name__}."
    )


def _clone_mapping(
    value: Mapping[object, object],
    budget: _JsonBudget,
    depth: int,
    active_containers: set[int],
) -> dict[str, object]:
    if len(value) > MAX_HISTORY_METADATA_ITEMS:
        raise ValueError("History metadata object exceeds the item limit.")
    identity = id(value)
    if identity in active_containers:
        raise ValueError("History metadata contains a reference cycle.")
    active_containers.add(identity)
    try:
        expected_length = len(value)
        items = tuple(value.items())
        if len(items) != expected_length:
            raise ValueError("History metadata changed during snapshot.")
        result: dict[str, object] = {}
        for key, item in items:
            normalized_key = _validate_metadata_key(key)
            if normalized_key in result:
                raise ValueError(f"Duplicate history metadata key: {normalized_key!r}.")
            result[normalized_key] = _clone_json_value(
                item, budget, depth + 1, active_containers
            )
        return result
    except RuntimeError as error:
        raise ValueError("History metadata changed during snapshot.") from error
    finally:
        active_containers.remove(identity)


def _clone_sequence(
    value: list[object] | tuple[object, ...],
    budget: _JsonBudget,
    depth: int,
    active_containers: set[int],
) -> list[object]:
    if len(value) > MAX_HISTORY_METADATA_ITEMS:
        raise ValueError("History metadata array exceeds the item limit.")
    identity = id(value)
    if identity in active_containers:
        raise ValueError("History metadata contains a reference cycle.")
    active_containers.add(identity)
    try:
        expected_length = len(value)
        snapshot = tuple(value)
        if len(snapshot) != expected_length:
            raise ValueError("History metadata changed during snapshot.")
        return [
            _clone_json_value(item, budget, depth + 1, active_containers)
            for item in snapshot
        ]
    finally:
        active_containers.remove(identity)


def _validate_metadata_key(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("History metadata keys must be strings.")
    if not value or len(value) > MAX_HISTORY_METADATA_KEY_CHARS:
        raise ValueError("History metadata key is empty or oversized.")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("History metadata keys must not contain control characters.")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("History metadata keys must be valid UTF-8 text.") from error
    return value
