from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

COMPONENT_SCHEMA_TABLE = "wavehelm_component_schema"
PLAYLIST_MIRROR_COMPONENT = "playlist_mirror"
PLAYLIST_MIRROR_SCHEMA_VERSION = 2
LEGACY_PLAYLIST_MIRROR_SCHEMA_VERSION = 1
PLAYLIST_MIRROR_OUTBOX_TABLE = "playlist_mirror_outbox"
PLAYLIST_MIRROR_REPAIR_TABLE = "playlist_mirror_repair_log"

_COMPONENT_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {COMPONENT_SCHEMA_TABLE} (
    component TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL CHECK (schema_version > 0),
    updated_at TEXT NOT NULL
)
"""
_PLAYLIST_MIRROR_OUTBOX_SQL = f"""
CREATE TABLE IF NOT EXISTS {PLAYLIST_MIRROR_OUTBOX_TABLE} (
    playlist_id INTEGER PRIMARY KEY CHECK (playlist_id > 0),
    operation TEXT NOT NULL CHECK (operation IN ('sync', 'delete')),
    playlist_name TEXT NOT NULL,
    stale_names TEXT NOT NULL DEFAULT '[]',
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 8),
    next_attempt_at REAL NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""
_PLAYLIST_MIRROR_DUE_INDEX_SQL = f"""
CREATE INDEX IF NOT EXISTS idx_playlist_mirror_outbox_due
ON {PLAYLIST_MIRROR_OUTBOX_TABLE} (next_attempt_at, attempt_count, playlist_id)
"""
_PLAYLIST_MIRROR_REPAIR_SQL = f"""
CREATE TABLE IF NOT EXISTS {PLAYLIST_MIRROR_REPAIR_TABLE} (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    playlist_id INTEGER NOT NULL CHECK (playlist_id > 0),
    previous_attempt_count INTEGER NOT NULL CHECK (
        previous_attempt_count BETWEEN 0 AND 8
    ),
    previous_error TEXT NOT NULL CHECK (length(previous_error) <= 1024),
    actor TEXT NOT NULL CHECK (length(actor) BETWEEN 1 AND 128),
    reason TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 1024),
    created_at TEXT NOT NULL
)
"""
_PLAYLIST_MIRROR_REPAIR_INDEX_SQL = f"""
CREATE INDEX IF NOT EXISTS idx_playlist_mirror_repair_playlist
ON {PLAYLIST_MIRROR_REPAIR_TABLE} (playlist_id, event_id DESC)
"""

_OUTBOX_COLUMNS = frozenset(
    {
        "playlist_id",
        "operation",
        "playlist_name",
        "stale_names",
        "attempt_count",
        "next_attempt_at",
        "last_error",
        "created_at",
        "updated_at",
    }
)
_REPAIR_COLUMNS = frozenset(
    {
        "event_id",
        "playlist_id",
        "previous_attempt_count",
        "previous_error",
        "actor",
        "reason",
        "created_at",
    }
)
_COMPONENT_SCHEMA_COLUMNS = frozenset({"component", "schema_version", "updated_at"})


class PlaylistMirrorSchemaError(RuntimeError):
    """Raised when the mirror schema is absent, corrupt, or unsupported."""


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _table_columns(connection: sqlite3.Connection, table_name: str) -> frozenset[str]:
    rows = connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()
    return frozenset(str(row[1]) for row in rows)


def _require_exact_columns(
    connection: sqlite3.Connection,
    table_name: str,
    expected_columns: frozenset[str],
) -> None:
    actual_columns = _table_columns(connection, table_name)
    missing = sorted(expected_columns - actual_columns)
    unexpected = sorted(actual_columns - expected_columns)
    if not missing and not unexpected:
        return
    details: list[str] = []
    if missing:
        details.append("missing columns: " + ", ".join(missing))
    if unexpected:
        details.append("unexpected columns: " + ", ".join(unexpected))
    raise PlaylistMirrorSchemaError(
        f"playlist mirror table {table_name} has an incompatible shape ("
        + "; ".join(details)
        + ")"
    )


def _read_recorded_version(connection: sqlite3.Connection) -> int | None:
    row = connection.execute(
        f"SELECT schema_version FROM {COMPONENT_SCHEMA_TABLE} WHERE component = ?",
        (PLAYLIST_MIRROR_COMPONENT,),
    ).fetchone()
    if row is None:
        return None
    value = row[0]
    if isinstance(value, bool) or not isinstance(value, int):
        raise PlaylistMirrorSchemaError(
            "playlist mirror schema version is not an integer"
        )
    if value < 1:
        raise PlaylistMirrorSchemaError(
            "playlist mirror schema version must be positive"
        )
    return value


def _record_current_version(connection: sqlite3.Connection) -> None:
    connection.execute(
        f"""
        INSERT INTO {COMPONENT_SCHEMA_TABLE} (component, schema_version, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(component) DO UPDATE SET
            schema_version = excluded.schema_version,
            updated_at = excluded.updated_at
        """,
        (
            PLAYLIST_MIRROR_COMPONENT,
            PLAYLIST_MIRROR_SCHEMA_VERSION,
            _utc_timestamp(),
        ),
    )


def _create_current_schema(connection: sqlite3.Connection) -> None:
    connection.execute(_PLAYLIST_MIRROR_OUTBOX_SQL)
    connection.execute(_PLAYLIST_MIRROR_DUE_INDEX_SQL)
    connection.execute(_PLAYLIST_MIRROR_REPAIR_SQL)
    connection.execute(_PLAYLIST_MIRROR_REPAIR_INDEX_SQL)
    _validate_current_tables(connection)


def _migrate_legacy_schema(connection: sqlite3.Connection) -> None:
    _require_exact_columns(
        connection,
        PLAYLIST_MIRROR_OUTBOX_TABLE,
        _OUTBOX_COLUMNS,
    )
    if _table_exists(connection, PLAYLIST_MIRROR_REPAIR_TABLE):
        raise PlaylistMirrorSchemaError(
            "legacy playlist mirror schema unexpectedly contains a repair table"
        )
    connection.execute(_PLAYLIST_MIRROR_DUE_INDEX_SQL)
    connection.execute(_PLAYLIST_MIRROR_REPAIR_SQL)
    connection.execute(_PLAYLIST_MIRROR_REPAIR_INDEX_SQL)
    _require_exact_columns(
        connection,
        PLAYLIST_MIRROR_REPAIR_TABLE,
        _REPAIR_COLUMNS,
    )


def _validate_current_tables(connection: sqlite3.Connection) -> None:
    for table_name, columns in (
        (PLAYLIST_MIRROR_OUTBOX_TABLE, _OUTBOX_COLUMNS),
        (PLAYLIST_MIRROR_REPAIR_TABLE, _REPAIR_COLUMNS),
    ):
        if not _table_exists(connection, table_name):
            raise PlaylistMirrorSchemaError(
                f"playlist mirror schema v2 is missing table: {table_name}"
            )
        _require_exact_columns(connection, table_name, columns)


def _infer_unversioned_schema(connection: sqlite3.Connection) -> int:
    outbox_exists = _table_exists(connection, PLAYLIST_MIRROR_OUTBOX_TABLE)
    repair_exists = _table_exists(connection, PLAYLIST_MIRROR_REPAIR_TABLE)
    if not outbox_exists and not repair_exists:
        return 0
    if repair_exists and not outbox_exists:
        raise PlaylistMirrorSchemaError(
            "playlist mirror repair table exists without its outbox"
        )
    _require_exact_columns(connection, PLAYLIST_MIRROR_OUTBOX_TABLE, _OUTBOX_COLUMNS)
    if not repair_exists:
        return LEGACY_PLAYLIST_MIRROR_SCHEMA_VERSION
    _require_exact_columns(connection, PLAYLIST_MIRROR_REPAIR_TABLE, _REPAIR_COLUMNS)
    return PLAYLIST_MIRROR_SCHEMA_VERSION


def _validate_component_schema_table(connection: sqlite3.Connection) -> None:
    _require_exact_columns(
        connection,
        COMPONENT_SCHEMA_TABLE,
        _COMPONENT_SCHEMA_COLUMNS,
    )


def ensure_playlist_mirror_schema(connection: sqlite3.Connection) -> int:
    """Create or migrate the playlist mirror schema in one transaction.

    Edge cases:
        1. A legacy outbox without metadata migrates without losing pending jobs.
        2. Future or structurally incompatible schemas fail closed.
        3. Metadata and physical tables must agree before version 2 is accepted.
    """
    if connection.in_transaction:
        raise PlaylistMirrorSchemaError(
            "playlist mirror schema migration requires an idle connection"
        )
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(_COMPONENT_SCHEMA_SQL)
        _validate_component_schema_table(connection)
        recorded_version = _read_recorded_version(connection)
        if recorded_version is None:
            recorded_version = _infer_unversioned_schema(connection)
        if recorded_version > PLAYLIST_MIRROR_SCHEMA_VERSION:
            raise PlaylistMirrorSchemaError(
                "playlist mirror schema is newer than this WaveHelm build"
            )
        if recorded_version == 0:
            _create_current_schema(connection)
        elif recorded_version == LEGACY_PLAYLIST_MIRROR_SCHEMA_VERSION:
            if not _table_exists(connection, PLAYLIST_MIRROR_OUTBOX_TABLE):
                raise PlaylistMirrorSchemaError(
                    "legacy playlist mirror metadata exists without its outbox"
                )
            _migrate_legacy_schema(connection)
        elif recorded_version == PLAYLIST_MIRROR_SCHEMA_VERSION:
            _validate_current_tables(connection)
            connection.execute(_PLAYLIST_MIRROR_DUE_INDEX_SQL)
            connection.execute(_PLAYLIST_MIRROR_REPAIR_INDEX_SQL)
        else:
            raise PlaylistMirrorSchemaError(
                f"unsupported playlist mirror schema version: {recorded_version}"
            )
        _record_current_version(connection)
        connection.commit()
        return PLAYLIST_MIRROR_SCHEMA_VERSION
    except PlaylistMirrorSchemaError:
        connection.rollback()
        raise
    except sqlite3.Error as error:
        connection.rollback()
        raise PlaylistMirrorSchemaError(
            f"playlist mirror schema migration failed: {error}"
        ) from error


def read_playlist_mirror_schema_version(connection: sqlite3.Connection) -> int:
    """Read and validate both the recorded version and its physical tables."""
    try:
        if not _table_exists(connection, COMPONENT_SCHEMA_TABLE):
            raise PlaylistMirrorSchemaError(
                "playlist mirror schema metadata table is missing"
            )
        _validate_component_schema_table(connection)
        version = _read_recorded_version(connection)
        if version is None:
            raise PlaylistMirrorSchemaError(
                "playlist mirror schema version record is missing"
            )
        if version != PLAYLIST_MIRROR_SCHEMA_VERSION:
            raise PlaylistMirrorSchemaError(
                f"unsupported playlist mirror schema version: {version}"
            )
        _validate_current_tables(connection)
        return version
    except sqlite3.Error as error:
        raise PlaylistMirrorSchemaError(
            f"playlist mirror schema version lookup failed: {error}"
        ) from error
