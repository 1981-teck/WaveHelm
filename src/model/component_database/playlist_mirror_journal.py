from __future__ import annotations

import json
import math
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from src.model.component_database.db_core import DbCore
from src.model.component_database.playlist_mirror_schema import (
    PlaylistMirrorSchemaError,
    ensure_playlist_mirror_schema,
)

MirrorOperation = Literal["sync", "delete"]

MAX_PENDING_MIRROR_JOBS = 4096
MAX_STALE_MIRROR_NAMES = 64
MAX_MIRROR_NAME_CHARS = 1024
MAX_MIRROR_ATTEMPTS = 8
MAX_MIRROR_ERROR_CHARS = 1024
MAX_RECOVERY_BATCH = 256
RETRY_BASE_SECONDS = 1.0
RETRY_MAX_SECONDS = 300.0


class PlaylistMirrorJournalError(RuntimeError):
    """Base error for durable playlist mirror journal failures."""


class PlaylistMirrorJournalCorruptionError(PlaylistMirrorJournalError):
    """Raised when a persisted outbox row violates the typed journal schema."""


class PlaylistMirrorJournalCapacityError(PlaylistMirrorJournalError):
    """Raised when bounded journal capacity would be exceeded."""


@dataclass(frozen=True, slots=True)
class PlaylistMirrorJob:
    """One coalesced and retryable playlist mirror operation."""

    playlist_id: int
    operation: MirrorOperation
    playlist_name: str
    stale_names: tuple[str, ...]
    attempt_count: int
    next_attempt_at: float
    last_error: str


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_playlist_id(playlist_id: int) -> int:
    if isinstance(playlist_id, bool) or not isinstance(playlist_id, int) or playlist_id < 1:
        raise PlaylistMirrorJournalError("playlist ID must be a positive integer")
    return playlist_id


def _validate_time(value: float, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise PlaylistMirrorJournalError(f"{label} is not a valid timestamp") from error
    if not math.isfinite(result) or result < 0:
        raise PlaylistMirrorJournalError(f"{label} must be finite and non-negative")
    return result


def _validate_name(name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(name, str):
        raise PlaylistMirrorJournalError("playlist mirror name must be text")
    if not allow_empty and not name:
        raise PlaylistMirrorJournalError("playlist mirror name cannot be empty")
    if len(name) > MAX_MIRROR_NAME_CHARS:
        raise PlaylistMirrorJournalCapacityError("playlist mirror name exceeds the journal limit")
    if "\x00" in name:
        raise PlaylistMirrorJournalError("playlist mirror name contains a NUL character")
    return name


def _decode_stale_names(raw_value: str) -> tuple[str, ...]:
    try:
        decoded = json.loads(raw_value)
    except (json.JSONDecodeError, TypeError) as error:
        raise PlaylistMirrorJournalCorruptionError("invalid stale-name JSON") from error
    if not isinstance(decoded, list):
        raise PlaylistMirrorJournalCorruptionError("stale-name payload is not a list")
    if len(decoded) > MAX_STALE_MIRROR_NAMES:
        raise PlaylistMirrorJournalCorruptionError("stale-name payload exceeds its hard cap")

    names: list[str] = []
    seen: set[str] = set()
    for item in decoded:
        try:
            name = _validate_name(item)
        except PlaylistMirrorJournalError as error:
            raise PlaylistMirrorJournalCorruptionError(str(error)) from error
        if name not in seen:
            names.append(name)
            seen.add(name)
    return tuple(names)


def _encode_stale_names(names: tuple[str, ...]) -> str:
    return json.dumps(names, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _read_existing_job(
    connection: sqlite3.Connection, playlist_id: int
) -> tuple[MirrorOperation, str, tuple[str, ...]] | None:
    row = connection.execute(
        "SELECT operation, playlist_name, stale_names "
        "FROM playlist_mirror_outbox WHERE playlist_id = ?",
        (playlist_id,),
    ).fetchone()
    if row is None:
        return None
    operation = str(row[0])
    if operation not in ("sync", "delete"):
        raise PlaylistMirrorJournalCorruptionError("unknown playlist mirror operation")
    playlist_name = _validate_name(str(row[1]))
    return operation, playlist_name, _decode_stale_names(str(row[2]))


def _merge_stale_names(
    existing: tuple[str, ...], candidates: tuple[str | None, ...], current_name: str
) -> tuple[str, ...]:
    merged: list[str] = []
    seen: set[str] = {current_name}
    for candidate in (*existing, *candidates):
        if candidate is None:
            continue
        name = _validate_name(candidate)
        if name in seen:
            continue
        merged.append(name)
        seen.add(name)
        if len(merged) > MAX_STALE_MIRROR_NAMES:
            raise PlaylistMirrorJournalCapacityError("too many stale playlist mirror names")
    return tuple(merged)


def _upsert_job(
    connection: sqlite3.Connection,
    playlist_id: int,
    operation: MirrorOperation,
    playlist_name: str,
    stale_names: tuple[str, ...],
) -> None:
    existing_count = int(
        connection.execute("SELECT COUNT(*) FROM playlist_mirror_outbox").fetchone()[0]
    )
    existing_row = connection.execute(
        "SELECT 1 FROM playlist_mirror_outbox WHERE playlist_id = ?", (playlist_id,)
    ).fetchone()
    if existing_row is None and existing_count >= MAX_PENDING_MIRROR_JOBS:
        raise PlaylistMirrorJournalCapacityError("playlist mirror journal is full")

    timestamp = _utc_timestamp()
    connection.execute(
        """
        INSERT INTO playlist_mirror_outbox (
            playlist_id, operation, playlist_name, stale_names,
            attempt_count, next_attempt_at, last_error, created_at, updated_at
        ) VALUES (?, ?, ?, ?, 0, 0, '', ?, ?)
        ON CONFLICT(playlist_id) DO UPDATE SET
            operation = excluded.operation,
            playlist_name = excluded.playlist_name,
            stale_names = excluded.stale_names,
            attempt_count = 0,
            next_attempt_at = 0,
            last_error = '',
            updated_at = excluded.updated_at
        """,
        (
            playlist_id,
            operation,
            playlist_name,
            _encode_stale_names(stale_names),
            timestamp,
            timestamp,
        ),
    )


def enqueue_playlist_mirror_sync(
    connection: sqlite3.Connection,
    playlist_id: int,
    playlist_name: str,
    *,
    previous_name: str | None = None,
) -> None:
    """Coalesce a sync intent inside the caller's SQLite transaction.

    Edge cases:
        1. Consecutive renames retain every bounded stale filename for cleanup.
        2. A prior delete intent is superseded only by a new canonical playlist row.
        3. Capacity overflow aborts the surrounding database transaction fail-closed.
    """
    playlist_id = _validate_playlist_id(playlist_id)
    playlist_name = _validate_name(playlist_name)
    existing = _read_existing_job(connection, playlist_id)
    stale_names: tuple[str, ...] = ()
    previous_candidates: tuple[str | None, ...] = (previous_name,)
    if existing is not None:
        _operation, existing_name, stale_names = existing
        previous_candidates = (existing_name, previous_name)
    merged = _merge_stale_names(stale_names, previous_candidates, playlist_name)
    _upsert_job(connection, playlist_id, "sync", playlist_name, merged)


def enqueue_playlist_mirror_delete(
    connection: sqlite3.Connection, playlist_id: int, playlist_name: str
) -> None:
    """Coalesce a delete intent inside the caller's SQLite transaction."""
    playlist_id = _validate_playlist_id(playlist_id)
    playlist_name = _validate_name(playlist_name)
    existing = _read_existing_job(connection, playlist_id)
    stale_names: tuple[str, ...] = ()
    candidates: tuple[str | None, ...] = ()
    if existing is not None:
        _operation, existing_name, stale_names = existing
        candidates = (existing_name,)
    merged = _merge_stale_names(stale_names, candidates, playlist_name)
    _upsert_job(connection, playlist_id, "delete", playlist_name, merged)


def decode_playlist_mirror_job(
    row: sqlite3.Row | tuple[object, ...],
) -> PlaylistMirrorJob:
    """Decode one persisted job through the typed journal contract."""
    values = tuple(row)
    if len(values) != 7:
        raise PlaylistMirrorJournalCorruptionError(
            "playlist mirror row has an invalid shape"
        )
    playlist_id, operation, playlist_name, stale_raw, attempts, retry_at, error = values
    if not isinstance(operation, str) or operation not in ("sync", "delete"):
        raise PlaylistMirrorJournalCorruptionError(
            "unknown playlist mirror operation"
        )
    if isinstance(playlist_id, bool) or not isinstance(playlist_id, int):
        raise PlaylistMirrorJournalCorruptionError("playlist mirror ID is not an integer")
    if isinstance(attempts, bool) or not isinstance(attempts, int):
        raise PlaylistMirrorJournalCorruptionError(
            "playlist mirror attempt count is not an integer"
        )
    if isinstance(retry_at, bool) or not isinstance(retry_at, (int, float)):
        raise PlaylistMirrorJournalCorruptionError("playlist mirror retry time is not numeric")
    if not all(isinstance(value, str) for value in (playlist_name, stale_raw, error)):
        raise PlaylistMirrorJournalCorruptionError(
            "playlist mirror text fields have invalid storage types"
        )
    try:
        playlist_id = _validate_playlist_id(playlist_id)
        playlist_name = _validate_name(playlist_name)
        next_attempt_at = _validate_time(retry_at, "next retry time")
    except PlaylistMirrorJournalError as decode_error:
        raise PlaylistMirrorJournalCorruptionError(str(decode_error)) from decode_error
    if not 0 <= attempts <= MAX_MIRROR_ATTEMPTS:
        raise PlaylistMirrorJournalCorruptionError(
            "invalid playlist mirror attempt count"
        )
    if len(error) > MAX_MIRROR_ERROR_CHARS:
        raise PlaylistMirrorJournalCorruptionError(
            "playlist mirror error exceeds its hard cap"
        )
    return PlaylistMirrorJob(
        playlist_id=playlist_id,
        operation=operation,
        playlist_name=playlist_name,
        stale_names=_decode_stale_names(stale_raw),
        attempt_count=attempts,
        next_attempt_at=next_attempt_at,
        last_error=error,
    )


class PlaylistMirrorJournal:
    """Bounded SQLite outbox used to reconcile canonical playlists and JSON mirrors."""

    _decode_job = staticmethod(decode_playlist_mirror_job)

    def __init__(self, db_core: DbCore) -> None:
        self._db_core = db_core
        self.ensure_schema()

    def _connection(self) -> sqlite3.Connection:
        connection = self._db_core.conn
        if connection is None:
            self._db_core.connect()
            connection = self._db_core.conn
        if connection is None:
            raise PlaylistMirrorJournalError("playlist mirror journal database is unavailable")
        return connection

    def ensure_schema(self) -> None:
        with self._db_core._db_lock:
            connection = self._connection()
            try:
                ensure_playlist_mirror_schema(connection)
            except PlaylistMirrorSchemaError as error:
                raise PlaylistMirrorJournalError(
                    f"playlist mirror journal schema initialization failed: {error}"
                ) from error

    def fetch_due(
        self, *, limit: int = 32, now: float | None = None
    ) -> tuple[PlaylistMirrorJob, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_RECOVERY_BATCH:
            raise PlaylistMirrorJournalError("recovery batch limit is outside the allowed range")
        current_time = _validate_time(time.time() if now is None else now, "current time")
        with self._db_core._db_lock:
            connection = self._connection()
            try:
                rows = connection.execute(
                    """
                    SELECT playlist_id, operation, playlist_name, stale_names,
                           attempt_count, next_attempt_at, last_error
                    FROM playlist_mirror_outbox
                    WHERE attempt_count < ? AND next_attempt_at <= ?
                    ORDER BY updated_at, playlist_id
                    LIMIT ?
                    """,
                    (MAX_MIRROR_ATTEMPTS, current_time, limit),
                ).fetchall()
            except sqlite3.Error as error:
                raise PlaylistMirrorJournalError(
                    f"playlist mirror recovery query failed: {error}"
                ) from error
        return tuple(decode_playlist_mirror_job(row) for row in rows)

    def pending_playlist_ids(self) -> frozenset[int]:
        with self._db_core._db_lock:
            connection = self._connection()
            try:
                rows = connection.execute(
                    "SELECT playlist_id FROM playlist_mirror_outbox"
                ).fetchall()
            except sqlite3.Error as error:
                raise PlaylistMirrorJournalError(
                    f"playlist mirror pending query failed: {error}"
                ) from error
        try:
            return frozenset(_validate_playlist_id(int(row[0])) for row in rows)
        except (PlaylistMirrorJournalError, TypeError, ValueError, OverflowError) as error:
            raise PlaylistMirrorJournalCorruptionError(str(error)) from error

    def exhausted_count(self) -> int:
        with self._db_core._db_lock:
            connection = self._connection()
            try:
                row = connection.execute(
                    "SELECT COUNT(*) FROM playlist_mirror_outbox WHERE attempt_count >= ?",
                    (MAX_MIRROR_ATTEMPTS,),
                ).fetchone()
            except sqlite3.Error as error:
                raise PlaylistMirrorJournalError(
                    f"playlist mirror exhaustion query failed: {error}"
                ) from error
        return int(row[0])

    def acknowledge(self, playlist_id: int) -> None:
        playlist_id = _validate_playlist_id(playlist_id)
        with self._db_core._db_lock:
            connection = self._connection()
            try:
                connection.execute(
                    "DELETE FROM playlist_mirror_outbox WHERE playlist_id = ?", (playlist_id,)
                )
                connection.commit()
            except sqlite3.Error as error:
                connection.rollback()
                raise PlaylistMirrorJournalError(
                    f"playlist mirror acknowledgement failed: {error}"
                ) from error

    def record_failure(
        self, playlist_id: int, error: Exception, *, now: float | None = None
    ) -> bool:
        playlist_id = _validate_playlist_id(playlist_id)
        current_time = _validate_time(time.time() if now is None else now, "current time")
        raw_message = f"{type(error).__name__}: {error}"
        message = raw_message.encode("utf-8", "backslashreplace").decode("utf-8")
        message = message[:MAX_MIRROR_ERROR_CHARS]
        with self._db_core._db_lock:
            connection = self._connection()
            try:
                row = connection.execute(
                    "SELECT attempt_count FROM playlist_mirror_outbox WHERE playlist_id = ?",
                    (playlist_id,),
                ).fetchone()
                if row is None:
                    return False
                current_attempt = int(row[0])
                if not 0 <= current_attempt <= MAX_MIRROR_ATTEMPTS:
                    raise PlaylistMirrorJournalCorruptionError(
                        "invalid playlist mirror attempt count")
                attempt_count = min(current_attempt + 1, MAX_MIRROR_ATTEMPTS)
                delay = min(
                    RETRY_BASE_SECONDS * (2 ** max(attempt_count - 1, 0)),
                    RETRY_MAX_SECONDS,
                )
                next_attempt_at = _validate_time(
                    current_time + delay, "next retry time")
                connection.execute(
                    """
                    UPDATE playlist_mirror_outbox
                    SET attempt_count = ?, next_attempt_at = ?, last_error = ?, updated_at = ?
                    WHERE playlist_id = ?
                    """,
                    (
                        attempt_count,
                        next_attempt_at,
                        message,
                        _utc_timestamp(),
                        playlist_id,
                    ),
                )
                connection.commit()
            except sqlite3.Error as journal_error:
                connection.rollback()
                raise PlaylistMirrorJournalError(
                    f"playlist mirror failure state could not be persisted: {journal_error}"
                ) from journal_error
        return True

    def get_job(self, playlist_id: int) -> PlaylistMirrorJob | None:
        playlist_id = _validate_playlist_id(playlist_id)
        with self._db_core._db_lock:
            connection = self._connection()
            try:
                row = connection.execute(
                    """
                    SELECT playlist_id, operation, playlist_name, stale_names,
                           attempt_count, next_attempt_at, last_error
                    FROM playlist_mirror_outbox WHERE playlist_id = ?
                    """,
                    (playlist_id,),
                ).fetchone()
            except sqlite3.Error as error:
                raise PlaylistMirrorJournalError(
                    f"playlist mirror lookup failed: {error}"
                ) from error
        return None if row is None else decode_playlist_mirror_job(row)
