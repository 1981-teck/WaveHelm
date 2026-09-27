from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import re
import sqlite3
from typing import Protocol

from src.model.component_database.db_primitives import SqlExecutor
from src.model.component_database import playlist_identity, playlist_position_schema
from src.utils.exceptions import DatabaseError

CORE_SCHEMA_VERSION = 3
_MAX_SQLITE_USER_VERSION = 2_147_483_647
_MAX_SCHEMA_TABLES = 4096
_MAX_INDEXES_PER_TABLE = 64
_MAX_INDEX_COLUMNS = 32
_MAX_FOREIGN_KEYS_PER_TABLE = 32
_MAX_SCHEMA_NAME_CHARS = 255
_MAX_TABLE_SQL_CHARS = 16_384

class CoreSchemaError(DatabaseError):
    """Raised when the core database schema is absent, corrupt, or unsupported."""

class SchemaTransaction(SqlExecutor, Protocol):
    """SQL executor whose transaction lifecycle is owned by its caller."""

    @property
    def in_transaction(self) -> bool:
        """Return whether the owner-provided transaction is still active."""

@dataclass(frozen=True, slots=True)
class CoreMigration:
    """One forward-only migration owned by the outer database transaction."""

    source_version: int
    target_version: int
    apply: Callable[[SqlExecutor], None]

_CORE_TABLE_SQL = (
    """CREATE TABLE library_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        path TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL,
        media_type TEXT NOT NULL,
        duration REAL NOT NULL,
        metadata TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE playlists (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        description TEXT,
        cover_art TEXT,
        creation_date TEXT NOT NULL,
        last_modified TEXT NOT NULL
    )""",
    """CREATE TABLE playlist_items (
        playlist_id INTEGER NOT NULL,
        media_path TEXT NOT NULL,
        position INTEGER NOT NULL,
        added_at TEXT NOT NULL,
        PRIMARY KEY (playlist_id, media_path),
        FOREIGN KEY (playlist_id) REFERENCES playlists (id) ON DELETE CASCADE
    )""",
    """CREATE TABLE history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        path TEXT NOT NULL,
        media_type TEXT NOT NULL,
        duration REAL NOT NULL,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        metadata TEXT
    )""",
    """CREATE TABLE favorites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        path TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL,
        media_type TEXT NOT NULL,
        duration REAL NOT NULL,
        metadata TEXT,
        added_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE custom_eq_presets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        settings TEXT NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE custom_ambient_presets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        settings TEXT NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE user_profile (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_name TEXT NOT NULL,
        avatar_path TEXT,
        stats_data TEXT,
        eq_settings_data TEXT,
        effects_settings_data TEXT,
        ambient_settings_data TEXT,
        created_at TEXT NOT NULL,
        last_updated TEXT NOT NULL
    )""",
    """CREATE TABLE app_settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )""",
)

_CORE_TABLE_NAMES = frozenset(
    {
        "app_settings",
        "custom_ambient_presets",
        "custom_eq_presets",
        "favorites",
        "history",
        "library_items",
        "playlist_items",
        "playlists",
        "user_profile",
    }
)

_EXPECTED_UNIQUE_KEYS = {
    "app_settings": frozenset({("key",)}),
    "custom_ambient_presets": frozenset({("name",)}),
    "custom_eq_presets": frozenset({("name",)}),
    "favorites": frozenset({("path",)}),
    "history": frozenset(),
    "library_items": frozenset({("path",)}),
    "playlist_items": frozenset({("playlist_id", "media_path")}),
    "playlists": frozenset({("name",)}),
    "user_profile": frozenset(),
}

_EXPECTED_FOREIGN_KEYS = {
    "playlist_items": frozenset(
        {("playlist_id", "playlists", "id", "NO ACTION", "CASCADE", "NONE")}
    )
}
_SQL_SPACE = re.compile(r"\s+")

def _normalize_sql(statement: str) -> str:
    return _SQL_SPACE.sub(" ", statement.strip()).upper()

def _quote_identifier(name: str) -> str:
    has_control = any(ord(character) < 32 or ord(character) == 127 for character in name)
    if not name or len(name) > _MAX_SCHEMA_NAME_CHARS or has_control:
        raise CoreSchemaError("Database schema contains an invalid identifier.")
    return '"' + name.replace('"', '""') + '"'

_EXPECTED_TABLE_SQL = {
    statement.split(None, 3)[2]: _normalize_sql(statement)
    for statement in _CORE_TABLE_SQL
}

def _row_values(row: object, label: str) -> tuple[object, ...]:
    if isinstance(row, sqlite3.Row):
        return tuple(row)
    if isinstance(row, tuple):
        return row
    raise CoreSchemaError(f"{label} returned a malformed row.")

def _read_single_integer(row: object, label: str) -> int:
    if row is None:
        raise CoreSchemaError(f"{label} did not return a value.")
    values = _row_values(row, label)
    value = values[0] if len(values) == 1 else None
    if isinstance(value, bool) or not isinstance(value, int):
        raise CoreSchemaError(f"{label} is not a stored integer.")
    return value

def _read_user_version(connection: SqlExecutor) -> int:
    version = _read_single_integer(
        connection.execute("PRAGMA user_version").fetchone(), "core schema version"
    )
    if version < 0 or version > _MAX_SQLITE_USER_VERSION:
        raise CoreSchemaError("Core schema version is outside SQLite bounds.")
    return version

def _set_user_version(connection: SqlExecutor, version: int) -> None:
    if version < 0 or version > _MAX_SQLITE_USER_VERSION:
        raise CoreSchemaError("Refusing to store an invalid core schema version.")
    connection.execute(f"PRAGMA user_version = {version}")
    if _read_user_version(connection) != version:
        raise CoreSchemaError("Core schema version did not persist in the transaction.")

def _user_table_names(connection: SqlExecutor) -> frozenset[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name LIMIT ?",
        (_MAX_SCHEMA_TABLES + 1,),
    ).fetchall()
    if len(rows) > _MAX_SCHEMA_TABLES:
        raise CoreSchemaError("Database table inventory exceeds its hard cap.")
    names: set[str] = set()
    for row in rows:
        values = _row_values(row, "database table inventory")
        value = values[0] if len(values) == 1 else None
        if not isinstance(value, str):
            raise CoreSchemaError("Database table inventory contains an invalid name.")
        _quote_identifier(value)
        names.add(value)
    return frozenset(names)

def _has_user_schema_objects(connection: SqlExecutor) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' LIMIT 1"
    ).fetchone()
    return row is not None

def _table_sql(connection: SqlExecutor, table_name: str) -> str:
    row = connection.execute(
        "SELECT substr(sql, 1, ?) FROM sqlite_master "
        "WHERE type = 'table' AND name = ?",
        (_MAX_TABLE_SQL_CHARS + 1, table_name),
    ).fetchone()
    if row is None:
        raise CoreSchemaError(f"Core schema is missing table: {table_name}.")
    values = _row_values(row, f"SQL definition for {table_name}")
    statement = values[0] if len(values) == 1 else None
    if not isinstance(statement, str) or not statement:
        raise CoreSchemaError(f"Core table has no valid SQL definition: {table_name}.")
    if len(statement) > _MAX_TABLE_SQL_CHARS:
        raise CoreSchemaError(f"Core table SQL exceeds its hard cap: {table_name}.")
    return statement


def _unique_keys(connection: SqlExecutor, table_name: str) -> frozenset[tuple[str, ...]]:
    index_count = _read_single_integer(
        connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'index' AND tbl_name = ?",
            (table_name,),
        ).fetchone(),
        f"index count for {table_name}",
    )
    if index_count > _MAX_INDEXES_PER_TABLE:
        raise CoreSchemaError(f"Index inventory exceeds its hard cap: {table_name}.")
    keys: set[tuple[str, ...]] = set()
    table_identifier = _quote_identifier(table_name)
    for row in connection.execute(f"PRAGMA index_list({table_identifier})").fetchall():
        values = _row_values(row, f"index inventory for {table_name}")
        if len(values) < 5:
            raise CoreSchemaError(f"Index inventory is malformed for table: {table_name}.")
        index_name, is_unique, is_partial = values[1], values[2], values[4]
        if not isinstance(index_name, str):
            raise CoreSchemaError(f"Index name is invalid for table: {table_name}.")
        index_identifier = _quote_identifier(index_name)
        if is_unique != 1:
            continue
        if is_partial != 0:
            raise CoreSchemaError(f"Core unique index is partial: {index_name}.")
        index_rows = connection.execute(
            f"PRAGMA index_info({index_identifier})"
        ).fetchall()
        if len(index_rows) > _MAX_INDEX_COLUMNS:
            raise CoreSchemaError(f"Unique index exceeds its column cap: {index_name}.")
        columns: list[str] = []
        for index_row in index_rows:
            index_values = _row_values(index_row, f"index columns for {index_name}")
            column = index_values[2] if len(index_values) >= 3 else None
            if not isinstance(column, str) or not column:
                raise CoreSchemaError(f"Unique index has an invalid column: {index_name}.")
            columns.append(column)
        keys.add(tuple(columns))
    return frozenset(keys)


def _foreign_keys(
    connection: SqlExecutor, table_name: str
) -> frozenset[tuple[str, str, str, str, str, str]]:
    identifier = _quote_identifier(table_name)
    rows = connection.execute(f"PRAGMA foreign_key_list({identifier})").fetchall()
    if len(rows) > _MAX_FOREIGN_KEYS_PER_TABLE:
        raise CoreSchemaError(f"Foreign-key inventory exceeds its hard cap: {table_name}.")
    keys: set[tuple[str, str, str, str, str, str]] = set()
    for row in rows:
        values = _row_values(row, f"foreign keys for {table_name}")
        if len(values) < 8 or not all(isinstance(value, str) for value in values[2:8]):
            raise CoreSchemaError(f"Foreign-key inventory is malformed: {table_name}.")
        keys.add((values[3], values[2], values[4], values[5], values[6], values[7]))
    return frozenset(keys)


def _validate_no_core_triggers(
    connection: SqlExecutor, excluded_tables: frozenset[str] = frozenset()
) -> None:
    inspected_tables = _CORE_TABLE_NAMES - excluded_tables
    if not inspected_tables:
        return
    placeholders = ",".join("?" for _ in inspected_tables)
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'trigger' "
        f"AND tbl_name IN ({placeholders}) ORDER BY name LIMIT 1",
        tuple(sorted(inspected_tables)),
    ).fetchone()
    if row is not None:
        values = _row_values(row, "core trigger inventory")
        name = values[0] if len(values) == 1 else None
        if not isinstance(name, str):
            raise CoreSchemaError("Core trigger inventory contains an invalid name.")
        _quote_identifier(name)
        raise CoreSchemaError(f"Core schema contains an unexpected trigger: {name}.")


def _validate_core_tables(
    connection: SqlExecutor, excluded_tables: frozenset[str] = frozenset()
) -> None:
    tables = _user_table_names(connection)
    missing = sorted(_CORE_TABLE_NAMES - tables)
    if missing:
        raise CoreSchemaError("Core schema is incomplete: " + ", ".join(missing) + ".")
    for table_name in sorted(_CORE_TABLE_NAMES - excluded_tables):
        actual_sql = _normalize_sql(_table_sql(connection, table_name))
        if actual_sql != _EXPECTED_TABLE_SQL[table_name]:
            raise CoreSchemaError(f"Core table has an incompatible shape: {table_name}.")
        if _unique_keys(connection, table_name) != _EXPECTED_UNIQUE_KEYS[table_name]:
            raise CoreSchemaError(f"Core unique constraints are incompatible: {table_name}.")
        expected_foreign_keys = _EXPECTED_FOREIGN_KEYS.get(table_name, frozenset())
        if _foreign_keys(connection, table_name) != expected_foreign_keys:
            raise CoreSchemaError(f"Core foreign keys are incompatible: {table_name}.")
    _validate_no_core_triggers(connection, excluded_tables)


def _validate_v1_schema(connection: SqlExecutor) -> None:
    _validate_core_tables(connection)


def _migrate_v1_to_v2(connection: SqlExecutor) -> None:
    playlist_identity.migrate_playlist_identity_v2(connection)


def _validate_v2_schema(connection: SqlExecutor) -> None:
    _validate_core_tables(connection, frozenset({"playlists"}))
    playlist_identity.validate_playlist_identity_v2(connection)


def _migrate_v2_to_v3(connection: SqlExecutor) -> None:
    playlist_position_schema.migrate_playlist_positions_v3(connection)


def _validate_v3_schema(connection: SqlExecutor) -> None:
    _validate_core_tables(connection, frozenset({"playlists", "playlist_items"}))
    playlist_identity.validate_playlist_identity_v2(connection)
    playlist_position_schema.validate_playlist_positions_v3(connection)


def _migrate_unversioned_to_v1(connection: SqlExecutor) -> None:
    """Create or adopt the exact unversioned WaveHelm core schema.

    Edge cases:
        1. A partially created core schema fails without filling missing tables.
        2. An unrelated unversioned database is never claimed as WaveHelm-owned.
        3. A complete legacy schema is adopted without rewriting its stored rows.
    """
    tables = _user_table_names(connection)
    present_core = tables & _CORE_TABLE_NAMES
    if not present_core:
        if tables or _has_user_schema_objects(connection):
            raise CoreSchemaError(
                "Unversioned database contains non-core schema objects but no WaveHelm core schema."
            )
        for statement in _CORE_TABLE_SQL:
            connection.execute(statement)
        return
    if present_core != _CORE_TABLE_NAMES:
        missing = sorted(_CORE_TABLE_NAMES - present_core)
        raise CoreSchemaError(
            "Unversioned core schema is partial; missing: " + ", ".join(missing) + "."
        )
    _validate_v1_schema(connection)


_SCHEMA_VALIDATORS: dict[int, Callable[[SqlExecutor], None]] = {
    1: _validate_v1_schema,
    2: _validate_v2_schema,
    3: _validate_v3_schema,
}


def _validate_schema_version(connection: SqlExecutor, version: int) -> None:
    validator = _SCHEMA_VALIDATORS.get(version)
    if validator is None:
        raise CoreSchemaError(f"Core schema v{version} has no registered validator.")
    validator(connection)


_CORE_MIGRATIONS = (
    CoreMigration(0, 1, _migrate_unversioned_to_v1),
    CoreMigration(1, 2, _migrate_v1_to_v2),
    CoreMigration(2, 3, _migrate_v2_to_v3),
)


def _migration_from(version: int) -> CoreMigration:
    matches = tuple(item for item in _CORE_MIGRATIONS if item.source_version == version)
    if len(matches) != 1:
        raise CoreSchemaError(f"Core migration registry has no unique step from v{version}.")
    migration = matches[0]
    if migration.target_version != version + 1:
        raise CoreSchemaError("Core migrations must advance exactly one version.")
    return migration


def ensure_core_schema(connection: SchemaTransaction) -> int:
    """Migrate and validate the core schema inside an owner-provided transaction.

    Edge cases:
        1. Future versions fail before any migration statement is executed.
        2. Every step is validated before its version marker is advanced.
        3. SQLite failures become typed schema errors and remain rollback-safe.
    """
    try:
        if not connection.in_transaction:
            raise CoreSchemaError(
                "Core schema migration requires an active owner-provided transaction."
            )
        version = _read_user_version(connection)
        if version > CORE_SCHEMA_VERSION:
            raise CoreSchemaError(
                f"Core schema v{version} is newer than this WaveHelm build."
            )
        if version > 0:
            _validate_schema_version(connection, version)
        while version < CORE_SCHEMA_VERSION:
            migration = _migration_from(version)
            migration.apply(connection)
            _validate_schema_version(connection, migration.target_version)
            _set_user_version(connection, migration.target_version)
            version = migration.target_version
        if not connection.in_transaction:
            raise CoreSchemaError(
                "Core schema transaction ended before boundary completion."
            )
        return version
    except (
        playlist_identity.PlaylistIdentitySchemaError,
        playlist_position_schema.PlaylistPositionSchemaError,
    ) as error:
        raise CoreSchemaError(error.message, details=error.details) from error
    except CoreSchemaError:
        raise
    except sqlite3.Error as error:
        raise CoreSchemaError(
            "Core schema migration failed.", details=str(error)
        ) from error
