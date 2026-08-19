from __future__ import annotations

import math
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from src.model.component_database.db_core import DbCore
from src.model.component_database.playlist_mirror_journal import (
    MAX_MIRROR_ATTEMPTS,
    MAX_MIRROR_ERROR_CHARS,
    PlaylistMirrorJob,
    PlaylistMirrorJournalError,
    decode_playlist_mirror_job,
)
from src.model.component_database.playlist_mirror_schema import (
    PLAYLIST_MIRROR_OUTBOX_TABLE,
    PLAYLIST_MIRROR_REPAIR_TABLE,
    PlaylistMirrorSchemaError,
    read_playlist_mirror_schema_version,
)

MAX_MAINTENANCE_BATCH = 64
MAX_REPAIR_AUDIT_EVENTS = 4096
MAX_REPAIR_ACTOR_CHARS = 128
MAX_REPAIR_REASON_CHARS = 1024

class PlaylistMirrorMaintenanceError(RuntimeError):
    """Base error for playlist mirror maintenance operations."""
class PlaylistMirrorRepairRejectedError(PlaylistMirrorMaintenanceError):
    """Raised when an administrative repair request is unsafe."""
class PlaylistMirrorMaintenanceCorruptionError(PlaylistMirrorMaintenanceError):
    """Raised when persisted maintenance state violates its schema."""

@dataclass(frozen=True, slots=True)
class PlaylistMirrorInspection:
    """Bounded operational view of one pending journal job."""
    job: PlaylistMirrorJob
    created_at: str
    updated_at: str

    @property
    def exhausted(self) -> bool:
        return self.job.attempt_count >= MAX_MIRROR_ATTEMPTS

@dataclass(frozen=True, slots=True)
class PlaylistMirrorRepairEvent:
    """One persisted administrative rearm event."""
    event_id: int
    playlist_id: int
    previous_attempt_count: int
    previous_error: str
    actor: str
    reason: str
    created_at: str

@dataclass(frozen=True, slots=True)
class PlaylistMirrorRepairResult:
    """Outcome of one all-or-nothing bounded rearm request."""
    playlist_ids: tuple[int, ...]
    evicted_audit_events: int

def _validate_limit(limit: int) -> int:
    invalid = isinstance(limit, bool) or not isinstance(limit, int)
    invalid = invalid or not 1 <= limit <= MAX_MAINTENANCE_BATCH
    if invalid:
        raise PlaylistMirrorMaintenanceError("maintenance batch limit is outside the allowed range")
    return limit

def _validate_time(value: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise PlaylistMirrorMaintenanceError("maintenance timestamp is not numeric") from error
    if not math.isfinite(result) or result < 0:
        raise PlaylistMirrorMaintenanceError("maintenance timestamp must be finite and non-negative")
    return result

def _validate_text(value: str, label: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise PlaylistMirrorMaintenanceError(f"{label} must be text")
    normalized = value.strip()
    if not normalized:
        raise PlaylistMirrorMaintenanceError(f"{label} cannot be empty")
    if len(normalized) > maximum:
        raise PlaylistMirrorMaintenanceError(f"{label} exceeds its hard cap")
    if "\x00" in normalized:
        raise PlaylistMirrorMaintenanceError(f"{label} contains a NUL character")
    try:
        normalized.encode("utf-8", "strict")
    except UnicodeEncodeError as error:
        raise PlaylistMirrorMaintenanceError(f"{label} is not valid UTF-8 text") from error
    return normalized

def _validate_playlist_ids(playlist_ids: tuple[int, ...]) -> tuple[int, ...]:
    if not isinstance(playlist_ids, tuple):
        raise PlaylistMirrorMaintenanceError("playlist IDs must be supplied as a tuple")
    if not 1 <= len(playlist_ids) <= MAX_MAINTENANCE_BATCH:
        raise PlaylistMirrorMaintenanceError("playlist repair batch is outside the allowed range")
    normalized: list[int] = []
    seen: set[int] = set()
    for playlist_id in playlist_ids:
        invalid = (
            isinstance(playlist_id, bool)
            or not isinstance(playlist_id, int)
            or playlist_id < 1
        )
        if invalid:
            raise PlaylistMirrorMaintenanceError("playlist repair IDs must be positive integers")
        if playlist_id in seen:
            raise PlaylistMirrorMaintenanceError("playlist repair IDs must not contain duplicates")
        normalized.append(playlist_id)
        seen.add(playlist_id)
    return tuple(sorted(normalized))

def _validate_stored_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PlaylistMirrorMaintenanceCorruptionError(f"{label} must be stored as an integer")
    return value

def _validate_iso_timestamp(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise PlaylistMirrorMaintenanceCorruptionError(f"{label} must be stored as text")
    if not value or len(value) > 64:
        raise PlaylistMirrorMaintenanceCorruptionError(
            f"{label} is missing or exceeds its hard cap"
        )
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise PlaylistMirrorMaintenanceCorruptionError(
            f"{label} is not an ISO-8601 timestamp"
        ) from error
    if parsed.tzinfo is None:
        raise PlaylistMirrorMaintenanceCorruptionError(f"{label} must include a timezone")
    return value

def _decode_inspection(
    row: sqlite3.Row | tuple[object, ...],
) -> PlaylistMirrorInspection:
    values = tuple(row)
    if len(values) != 9:
        raise PlaylistMirrorMaintenanceCorruptionError(
            "playlist mirror inspection row has an invalid shape"
        )
    try:
        job = decode_playlist_mirror_job(values[:7])
    except PlaylistMirrorJournalError as error:
        raise PlaylistMirrorMaintenanceCorruptionError(str(error)) from error
    return PlaylistMirrorInspection(
        job=job,
        created_at=_validate_iso_timestamp(values[7], "journal creation time"),
        updated_at=_validate_iso_timestamp(values[8], "journal update time"),
    )

def _decode_repair_event(
    row: sqlite3.Row | tuple[object, ...],
) -> PlaylistMirrorRepairEvent:
    values = tuple(row)
    if len(values) != 7:
        raise PlaylistMirrorMaintenanceCorruptionError(
            "playlist mirror repair event has an invalid shape"
        )
    event_id = _validate_stored_int(values[0], "repair event ID")
    playlist_id = _validate_stored_int(values[1], "repair playlist ID")
    attempt_count = _validate_stored_int(values[2], "prior attempt count")
    if event_id < 1 or playlist_id < 1:
        raise PlaylistMirrorMaintenanceCorruptionError(
            "playlist mirror repair event IDs must be positive"
        )
    if attempt_count != MAX_MIRROR_ATTEMPTS:
        raise PlaylistMirrorMaintenanceCorruptionError(
            "playlist mirror repair event has an invalid prior attempt count"
        )
    previous_error = values[3]
    if not isinstance(previous_error, str):
        raise PlaylistMirrorMaintenanceCorruptionError(
            "playlist mirror repair event error must be stored as text"
        )
    if len(previous_error) > MAX_MIRROR_ERROR_CHARS:
        raise PlaylistMirrorMaintenanceCorruptionError(
            "playlist mirror repair event error exceeds its hard cap"
        )
    try:
        actor = _validate_text(values[4], "repair actor", MAX_REPAIR_ACTOR_CHARS)
        reason = _validate_text(values[5], "repair reason", MAX_REPAIR_REASON_CHARS)
    except PlaylistMirrorMaintenanceError as error:
        raise PlaylistMirrorMaintenanceCorruptionError(str(error)) from error
    return PlaylistMirrorRepairEvent(
        event_id=event_id,
        playlist_id=playlist_id,
        previous_attempt_count=attempt_count,
        previous_error=previous_error,
        actor=actor,
        reason=reason,
        created_at=_validate_iso_timestamp(values[6], "repair event time"),
    )

def _prepare_repair_request(
    playlist_ids: tuple[int, ...],
    actor: str,
    reason: str,
    now: float | None,
) -> tuple[tuple[int, ...], str, str, float, str]:
    identifiers = _validate_playlist_ids(playlist_ids)
    actor = _validate_text(actor, "repair actor", MAX_REPAIR_ACTOR_CHARS)
    reason = _validate_text(reason, "repair reason", MAX_REPAIR_REASON_CHARS)
    current_time = _validate_time(time.time() if now is None else now)
    try:
        timestamp = datetime.fromtimestamp(current_time, timezone.utc).isoformat()
    except (OSError, OverflowError, ValueError) as error:
        raise PlaylistMirrorMaintenanceError(
            "maintenance timestamp is outside the supported datetime range"
        ) from error
    return identifiers, actor, reason, current_time, timestamp

def _load_repair_targets(
    connection: sqlite3.Connection,
    playlist_ids: tuple[int, ...],
) -> dict[int, PlaylistMirrorInspection]:
    placeholders = ",".join("?" for _value in playlist_ids)
    rows = connection.execute(
        f"""
        SELECT playlist_id, operation, playlist_name, stale_names,
               attempt_count, next_attempt_at, last_error, created_at, updated_at
        FROM {PLAYLIST_MIRROR_OUTBOX_TABLE}
        WHERE playlist_id IN ({placeholders})
        """,
        playlist_ids,
    ).fetchall()
    inspections = tuple(_decode_inspection(row) for row in rows)
    return {inspection.job.playlist_id: inspection for inspection in inspections}

def _require_exhausted_targets(
    playlist_ids: tuple[int, ...],
    targets: dict[int, PlaylistMirrorInspection],
) -> None:
    missing = tuple(value for value in playlist_ids if value not in targets)
    if missing:
        raise PlaylistMirrorRepairRejectedError(
            f"playlist mirror repair jobs are missing: {missing}"
        )
    active = tuple(value for value in playlist_ids if not targets[value].exhausted)
    if active:
        raise PlaylistMirrorRepairRejectedError(
            f"playlist mirror jobs are not exhausted: {active}"
        )

def _rotate_repair_audit(
    connection: sqlite3.Connection,
    incoming_events: int,
) -> int:
    row = connection.execute(
        f"SELECT COUNT(*) FROM {PLAYLIST_MIRROR_REPAIR_TABLE}"
    ).fetchone()
    if row is None or isinstance(row[0], bool) or not isinstance(row[0], int):
        raise PlaylistMirrorMaintenanceCorruptionError(
            "playlist mirror repair audit count is invalid"
        )
    overflow = max(row[0] + incoming_events - MAX_REPAIR_AUDIT_EVENTS, 0)
    if not overflow:
        return 0
    cursor = connection.execute(
        f"""
        DELETE FROM {PLAYLIST_MIRROR_REPAIR_TABLE}
        WHERE event_id IN (
            SELECT event_id FROM {PLAYLIST_MIRROR_REPAIR_TABLE}
            ORDER BY event_id ASC LIMIT ?
        )
        """,
        (overflow,),
    )
    if cursor.rowcount != overflow:
        raise PlaylistMirrorMaintenanceCorruptionError(
            "playlist mirror repair audit rotation was incomplete"
        )
    return overflow

def _record_rearm(
    connection: sqlite3.Connection,
    inspection: PlaylistMirrorInspection,
    actor: str,
    reason: str,
    current_time: float,
    timestamp: str,
) -> None:
    job = inspection.job
    connection.execute(
        f"""
        INSERT INTO {PLAYLIST_MIRROR_REPAIR_TABLE} (
            playlist_id, previous_attempt_count, previous_error,
            actor, reason, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (job.playlist_id, job.attempt_count, job.last_error, actor, reason, timestamp),
    )
    cursor = connection.execute(
        f"""
        UPDATE {PLAYLIST_MIRROR_OUTBOX_TABLE}
        SET attempt_count = 0, next_attempt_at = ?, last_error = '', updated_at = ?
        WHERE playlist_id = ? AND attempt_count >= ?
        """,
        (current_time, timestamp, job.playlist_id, MAX_MIRROR_ATTEMPTS),
    )
    if cursor.rowcount != 1:
        raise PlaylistMirrorMaintenanceCorruptionError(
            f"playlist mirror job changed during repair: {job.playlist_id}"
        )

class PlaylistMirrorMaintenance:
    """Explicit, bounded inspection and rearm gate for exhausted mirror jobs."""

    def __init__(self, db_core: DbCore) -> None:
        self._db_core = db_core

    def _connection(self) -> sqlite3.Connection:
        connection = self._db_core.conn
        if connection is None:
            self._db_core.connect()
            connection = self._db_core.conn
        if connection is None:
            raise PlaylistMirrorMaintenanceError(
                "playlist mirror maintenance database is unavailable"
            )
        return connection

    def schema_version(self) -> int:
        with self._db_core._db_lock:
            try:
                return read_playlist_mirror_schema_version(self._connection())
            except PlaylistMirrorSchemaError as error:
                raise PlaylistMirrorMaintenanceError(str(error)) from error

    def inspect_jobs(
        self, *, limit: int = 32, exhausted_only: bool = False
    ) -> tuple[PlaylistMirrorInspection, ...]:
        """Return a bounded snapshot; corrupt rows fail the entire inspection."""
        limit = _validate_limit(limit)
        if not isinstance(exhausted_only, bool):
            raise PlaylistMirrorMaintenanceError("exhausted_only must be boolean")
        where_clause = "WHERE attempt_count >= ?" if exhausted_only else ""
        parameters = (MAX_MIRROR_ATTEMPTS, limit) if exhausted_only else (limit,)
        with self._db_core._db_lock:
            try:
                connection = self._connection()
                read_playlist_mirror_schema_version(connection)
                rows = connection.execute(
                    f"""
                    SELECT playlist_id, operation, playlist_name, stale_names,
                           attempt_count, next_attempt_at, last_error,
                           created_at, updated_at
                    FROM {PLAYLIST_MIRROR_OUTBOX_TABLE}
                    {where_clause}
                    ORDER BY attempt_count DESC, updated_at, playlist_id LIMIT ?
                    """,
                    parameters,
                ).fetchall()
            except (PlaylistMirrorSchemaError, sqlite3.Error) as error:
                raise PlaylistMirrorMaintenanceError(
                    f"playlist mirror inspection failed: {error}"
                ) from error
        return tuple(_decode_inspection(row) for row in rows)

    def inspect_repair_events(
        self, *, limit: int = 32, playlist_id: int | None = None
    ) -> tuple[PlaylistMirrorRepairEvent, ...]:
        limit = _validate_limit(limit)
        if playlist_id is not None:
            playlist_id = _validate_playlist_ids((playlist_id,))[0]
        where_clause = "WHERE playlist_id = ?" if playlist_id is not None else ""
        parameters = (playlist_id, limit) if playlist_id is not None else (limit,)
        with self._db_core._db_lock:
            try:
                connection = self._connection()
                read_playlist_mirror_schema_version(connection)
                rows = connection.execute(
                    f"""
                    SELECT event_id, playlist_id, previous_attempt_count,
                           previous_error, actor, reason, created_at
                    FROM {PLAYLIST_MIRROR_REPAIR_TABLE}
                    {where_clause}
                    ORDER BY event_id DESC LIMIT ?
                    """,
                    parameters,
                ).fetchall()
            except (PlaylistMirrorSchemaError, sqlite3.Error) as error:
                raise PlaylistMirrorMaintenanceError(
                    f"playlist mirror repair audit lookup failed: {error}"
                ) from error
        return tuple(_decode_repair_event(row) for row in rows)

    def rearm_exhausted(
        self,
        playlist_ids: tuple[int, ...],
        *,
        actor: str,
        reason: str,
        now: float | None = None,
    ) -> PlaylistMirrorRepairResult:
        """Rearm exhausted jobs in one bounded, audited transaction.

        Edge cases:
            1. Missing or non-exhausted IDs reject the complete request.
            2. Audit rotation is bounded and reported to the caller.
            3. Any write failure rolls back both retry state and audit rows.
        """
        request = _prepare_repair_request(playlist_ids, actor, reason, now)
        identifiers, actor, reason, current_time, timestamp = request
        with self._db_core._db_lock:
            connection = self._connection()
            if connection.in_transaction:
                raise PlaylistMirrorMaintenanceError(
                    "playlist mirror repair requires an idle connection"
                )
            try:
                connection.execute("BEGIN IMMEDIATE")
                read_playlist_mirror_schema_version(connection)
                targets = _load_repair_targets(connection, identifiers)
                _require_exhausted_targets(identifiers, targets)
                overflow = _rotate_repair_audit(connection, len(identifiers))
                for playlist_id in identifiers:
                    _record_rearm(
                        connection,
                        targets[playlist_id],
                        actor,
                        reason,
                        current_time,
                        timestamp,
                    )
                connection.commit()
                return PlaylistMirrorRepairResult(identifiers, overflow)
            except PlaylistMirrorMaintenanceError:
                connection.rollback()
                raise
            except (PlaylistMirrorSchemaError, PlaylistMirrorJournalError) as error:
                connection.rollback()
                raise PlaylistMirrorMaintenanceCorruptionError(str(error)) from error
            except sqlite3.Error as error:
                connection.rollback()
                raise PlaylistMirrorMaintenanceError(
                    f"playlist mirror repair transaction failed: {error}"
                ) from error
