from __future__ import annotations

import re
import sqlite3

from src.model.component_database.db_primitives import SqlExecutor
from src.model.component_database.playlist_position import (
    MAX_PLAYLIST_ADDED_AT_CHARS,
    MAX_PLAYLIST_ITEMS_PER_DATABASE,
    MAX_PLAYLIST_ITEMS_PER_PLAYLIST,
    MAX_PLAYLIST_MEDIA_PATH_CHARS,
    PLAYLIST_POSITION_INDEX,
)
from src.utils.exceptions import DatabaseError


_BACKUP_TABLE = "playlist_items_v2_backup"
_MAX_INDEXES = 64
_MAX_INDEX_COLUMNS = 16
_MAX_FOREIGN_KEYS = 16
_MAX_TRIGGERS = 8
_MAX_DEPENDENCY_OBJECTS = 4_096
_MAX_STORED_SQL_CHARS = 16_384
_POSITION_CHECK_SQL = "typeof(position) = 'integer' AND position >= 1"
_PLAYLIST_ITEMS_TABLE_SQL = f"""
CREATE TABLE playlist_items (
    playlist_id INTEGER NOT NULL,
    media_path TEXT NOT NULL,
    position INTEGER NOT NULL CHECK({_POSITION_CHECK_SQL}),
    added_at TEXT NOT NULL,
    PRIMARY KEY (playlist_id, media_path),
    FOREIGN KEY (playlist_id) REFERENCES playlists (id) ON DELETE CASCADE
)
"""
_POSITION_INDEX_SQL = (
    f"CREATE UNIQUE INDEX {PLAYLIST_POSITION_INDEX} "
    "ON playlist_items(playlist_id, position)"
)
_EXPECTED_COLUMNS = (
    ("playlist_id", "INTEGER", 1, None, 1),
    ("media_path", "TEXT", 1, None, 2),
    ("position", "INTEGER", 1, None, 0),
    ("added_at", "TEXT", 1, None, 0),
)
_EXPECTED_UNIQUE_KEYS = frozenset(
    {("playlist_id", "media_path"), ("playlist_id", "position")}
)
_EXPECTED_FOREIGN_KEYS = frozenset(
    {("playlist_id", "playlists", "id", "NO ACTION", "CASCADE", "NONE")}
)
_SQL_SPACE = re.compile(r"\s+")


class PlaylistPositionSchemaError(DatabaseError):
    """Raised when playlist position data cannot migrate or validate safely."""


def _normalize_sql(statement: str) -> str:
    return _SQL_SPACE.sub(" ", statement.strip()).upper()


def _row_values(row: object, label: str) -> tuple[object, ...]:
    if isinstance(row, sqlite3.Row):
        return tuple(row)
    if isinstance(row, tuple):
        return row
    raise PlaylistPositionSchemaError(f"{label} returned a malformed row.")


def _read_non_negative_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PlaylistPositionSchemaError(f"{label} is not a non-negative integer.")
    return value


def _migration_query(table_name: str) -> str:
    if table_name not in {"playlist_items", _BACKUP_TABLE}:
        raise PlaylistPositionSchemaError("Playlist migration table is not authorized.")
    return (
        f'SELECT playlist_id, media_path, position, added_at FROM "{table_name}" '
        "ORDER BY playlist_id, position, added_at, media_path"
    )


def _validated_migration_values(row: object) -> tuple[int, str, int, str]:
    values = _row_values(row, "playlist item migration inventory")
    if len(values) != 4:
        raise PlaylistPositionSchemaError("Playlist item row has an invalid shape.")
    playlist_id, media_path, stored_position, added_at = values
    valid_id = (
        not isinstance(playlist_id, bool)
        and isinstance(playlist_id, int)
        and playlist_id > 0
    )
    valid_path = (
        isinstance(media_path, str)
        and media_path == media_path.strip()
        and 0 < len(media_path) <= MAX_PLAYLIST_MEDIA_PATH_CHARS
        and "\x00" not in media_path
    )
    valid_timestamp = (
        isinstance(added_at, str)
        and added_at == added_at.strip()
        and 0 < len(added_at) <= MAX_PLAYLIST_ADDED_AT_CHARS
        and not any(
            ord(character) < 32 or ord(character) == 127 for character in added_at
        )
    )
    if not valid_id or not valid_path:
        raise PlaylistPositionSchemaError("Playlist item contains invalid identity data.")
    if isinstance(stored_position, bool) or not isinstance(stored_position, int):
        raise PlaylistPositionSchemaError("Playlist item position is not an integer.")
    if not valid_timestamp:
        raise PlaylistPositionSchemaError("Playlist item timestamp is invalid.")
    return playlist_id, media_path, stored_position, added_at


def _playlist_item_total(connection: SqlExecutor, table_name: str) -> int:
    if table_name not in {"playlist_items", _BACKUP_TABLE}:
        raise PlaylistPositionSchemaError("Playlist migration table is not authorized.")
    row = connection.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()
    values = _row_values(row, "playlist item count") if row is not None else ()
    count = _read_non_negative_integer(values[0] if len(values) == 1 else None, "item count")
    if count > MAX_PLAYLIST_ITEMS_PER_DATABASE:
        raise PlaylistPositionSchemaError("Playlist item inventory exceeds its hard cap.")
    return count


def _scan_migration_items(connection: SqlExecutor) -> int:
    expected_total = _playlist_item_total(connection, "playlist_items")
    cursor = connection.execute(_migration_query("playlist_items"))
    observed_total = 0
    current_playlist: int | None = None
    playlist_count = 0
    while observed_total < expected_total:
        row = cursor.fetchone()
        if row is None:
            raise PlaylistPositionSchemaError("Playlist item inventory ended unexpectedly.")
        playlist_id, _, _, _ = _validated_migration_values(row)
        observed_total += 1
        if playlist_id != current_playlist:
            current_playlist = playlist_id
            playlist_count = 0
        playlist_count += 1
        if playlist_count > MAX_PLAYLIST_ITEMS_PER_PLAYLIST:
            raise PlaylistPositionSchemaError("Playlist item count exceeds its hard cap.")
    if cursor.fetchone() is not None:
        raise PlaylistPositionSchemaError("Playlist item inventory changed during preflight.")
    return observed_total


def _copy_migration_items(connection: SqlExecutor, expected_total: int) -> None:
    if _playlist_item_total(connection, _BACKUP_TABLE) != expected_total:
        raise PlaylistPositionSchemaError("Playlist item inventory changed before migration copy.")
    cursor = connection.execute(_migration_query(_BACKUP_TABLE))
    current_playlist: int | None = None
    normalized_position = 0
    copied = 0
    while copied < expected_total:
        row = cursor.fetchone()
        if row is None:
            raise PlaylistPositionSchemaError("Playlist item migration copy ended unexpectedly.")
        playlist_id, media_path, _, added_at = _validated_migration_values(row)
        if playlist_id != current_playlist:
            current_playlist = playlist_id
            normalized_position = 0
        normalized_position += 1
        inserted = connection.execute(
            "INSERT INTO playlist_items "
            "(playlist_id, media_path, position, added_at) VALUES (?, ?, ?, ?)",
            (playlist_id, media_path, normalized_position, added_at),
        )
        if inserted.rowcount != 1:
            raise PlaylistPositionSchemaError("Playlist item migration insert was not exact.")
        copied += 1
    if cursor.fetchone() is not None:
        raise PlaylistPositionSchemaError("Playlist item migration copy exceeded its preflight.")


def _preserved_nonunique_indexes(connection: SqlExecutor) -> tuple[str, ...]:
    rows = connection.execute(
        "SELECT name, substr(sql, 1, ?) FROM sqlite_master "
        "WHERE type = 'index' AND tbl_name = 'playlist_items' AND sql IS NOT NULL "
        "ORDER BY name LIMIT ?",
        (_MAX_STORED_SQL_CHARS + 1, _MAX_INDEXES + 1),
    ).fetchall()
    if len(rows) > _MAX_INDEXES:
        raise PlaylistPositionSchemaError("Playlist item index inventory exceeds its hard cap.")
    statements: list[str] = []
    for row in rows:
        values = _row_values(row, "playlist item migration indexes")
        name = values[0] if len(values) == 2 else None
        statement = values[1] if len(values) == 2 else None
        valid = (
            isinstance(name, str)
            and name != PLAYLIST_POSITION_INDEX
            and isinstance(statement, str)
            and len(statement) <= _MAX_STORED_SQL_CHARS
            and _normalize_sql(statement).startswith("CREATE INDEX ")
        )
        if not valid:
            raise PlaylistPositionSchemaError("Playlist item index cannot be preserved safely.")
        statements.append(statement)
    return tuple(statements)


def _validate_external_dependencies(connection: SqlExecutor) -> None:
    object_count = connection.execute(
        "SELECT (SELECT COUNT(*) FROM sqlite_master "
        "WHERE type IN ('view', 'trigger', 'table') AND name NOT LIKE 'sqlite_%') + "
        "(SELECT COUNT(*) FROM sqlite_temp_master "
        "WHERE type IN ('view', 'trigger', 'table') AND name NOT LIKE 'sqlite_%')"
    ).fetchone()
    values = _row_values(object_count, "playlist dependency object count")
    count = _read_non_negative_integer(
        values[0] if len(values) == 1 else None, "object count"
    )
    if count > _MAX_DEPENDENCY_OBJECTS:
        raise PlaylistPositionSchemaError("Playlist dependency inventory exceeds its hard cap.")
    catalog = (
        "SELECT type, name, sql FROM sqlite_master WHERE type IN ('view', 'trigger') "
        "UNION ALL SELECT type, name, sql FROM sqlite_temp_master "
        "WHERE type IN ('view', 'trigger')"
    )
    oversized = connection.execute(
        f"SELECT type, name FROM ({catalog}) "
        "WHERE length(COALESCE(sql, '')) > ? LIMIT 1",
        (_MAX_STORED_SQL_CHARS,),
    ).fetchone()
    if oversized is not None:
        raise PlaylistPositionSchemaError("Playlist dependency SQL exceeds its hard cap.")
    dependent = connection.execute(
        f"SELECT type, name FROM ({catalog}) "
        "WHERE instr(lower(COALESCE(sql, '')), 'playlist_items') > 0 "
        "ORDER BY type, name LIMIT 1"
    ).fetchone()
    if dependent is not None:
        raise PlaylistPositionSchemaError(
            "Playlist item migration has an external view or trigger dependency."
        )
    foreign_key = connection.execute(
        "SELECT schema_object.name FROM sqlite_master AS schema_object "
        "JOIN pragma_foreign_key_list(schema_object.name) AS foreign_key "
        "WHERE schema_object.type = 'table' "
        "AND foreign_key.\"table\" = 'playlist_items' "
        "ORDER BY schema_object.name LIMIT 1"
    ).fetchone()
    if foreign_key is not None:
        raise PlaylistPositionSchemaError(
            "Playlist item migration has an external foreign-key dependency."
        )


def _validate_migration_preconditions(connection: SqlExecutor) -> tuple[str, ...]:
    _validate_external_dependencies(connection)
    conflict = connection.execute(
        "SELECT name FROM sqlite_master WHERE name IN (?, ?) LIMIT 1",
        (_BACKUP_TABLE, PLAYLIST_POSITION_INDEX),
    ).fetchone()
    if conflict is not None:
        raise PlaylistPositionSchemaError("Playlist position migration object already exists.")
    orphan = connection.execute(
        "SELECT pi.playlist_id FROM playlist_items pi "
        "LEFT JOIN playlists p ON p.id = pi.playlist_id "
        "WHERE p.id IS NULL LIMIT 1"
    ).fetchone()
    if orphan is not None:
        raise PlaylistPositionSchemaError("Playlist item inventory contains an orphan row.")
    return _preserved_nonunique_indexes(connection)


def migrate_playlist_positions_v3(connection: SqlExecutor) -> None:
    """Rebuild playlist items with deterministic contiguous positions and constraints.

    Edge cases:
        1. Duplicate or sparse legacy positions are renumbered without dropping rows.
        2. Invalid row types, orphan rows, or capacity overflow abort before the first DDL.
        3. Copy, index creation, backup removal, and the version marker share one transaction.
    """
    preserved_indexes = _validate_migration_preconditions(connection)
    item_count = _scan_migration_items(connection)
    connection.execute(f"ALTER TABLE playlist_items RENAME TO {_BACKUP_TABLE}")
    connection.execute(_PLAYLIST_ITEMS_TABLE_SQL)
    _copy_migration_items(connection, item_count)
    connection.execute(_POSITION_INDEX_SQL)
    connection.execute(f"DROP TABLE {_BACKUP_TABLE}")
    for statement in preserved_indexes:
        connection.execute(statement)


def _validate_table_shape(connection: SqlExecutor) -> None:
    row = connection.execute(
        "SELECT substr(sql, 1, ?) FROM sqlite_master "
        "WHERE type = 'table' AND name = 'playlist_items'",
        (_MAX_STORED_SQL_CHARS + 1,),
    ).fetchone()
    values = _row_values(row, "playlist item table SQL") if row is not None else ()
    statement = values[0] if len(values) == 1 else None
    if not isinstance(statement, str) or len(statement) > _MAX_STORED_SQL_CHARS:
        raise PlaylistPositionSchemaError("Playlist item table SQL is missing or invalid.")
    if _normalize_sql(statement) != _normalize_sql(_PLAYLIST_ITEMS_TABLE_SQL):
        raise PlaylistPositionSchemaError("Playlist item table has an incompatible v3 definition.")
    columns = connection.execute('PRAGMA table_info("playlist_items")').fetchall()
    actual: list[tuple[object, ...]] = []
    for column in columns:
        values = _row_values(column, "playlist item columns")
        if len(values) < 6:
            raise PlaylistPositionSchemaError("Playlist item column inventory is malformed.")
        actual.append((values[1], values[2], values[3], values[4], values[5]))
    if tuple(actual) != _EXPECTED_COLUMNS:
        raise PlaylistPositionSchemaError("Playlist item table has an incompatible v3 shape.")


def _validate_indexes(connection: SqlExecutor) -> None:
    rows = connection.execute('PRAGMA index_list("playlist_items")').fetchall()
    if len(rows) > _MAX_INDEXES:
        raise PlaylistPositionSchemaError("Playlist item index inventory exceeds its hard cap.")
    keys: set[tuple[str, ...]] = set()
    unique_count = 0
    named_position_index = False
    for row in rows:
        values = _row_values(row, "playlist item index inventory")
        if len(values) < 5 or not isinstance(values[1], str):
            raise PlaylistPositionSchemaError("Playlist item index inventory is malformed.")
        name, is_unique, is_partial = values[1], values[2], values[4]
        if is_unique != 1:
            continue
        unique_count += 1
        if is_partial != 0:
            raise PlaylistPositionSchemaError("Playlist item unique index cannot be partial.")
        escaped = name.replace('"', '""')
        index_rows = connection.execute(f'PRAGMA index_info("{escaped}")').fetchall()
        if len(index_rows) > _MAX_INDEX_COLUMNS:
            raise PlaylistPositionSchemaError("Playlist item unique index exceeds its cap.")
        columns: list[str] = []
        for index_row in index_rows:
            index_values = _row_values(index_row, "playlist item index columns")
            column = index_values[2] if len(index_values) >= 3 else None
            if not isinstance(column, str) or not column:
                raise PlaylistPositionSchemaError("Playlist item unique index is malformed.")
            columns.append(column)
        key = tuple(columns)
        keys.add(key)
        if name == PLAYLIST_POSITION_INDEX and key == ("playlist_id", "position"):
            named_position_index = True
    if unique_count != len(_EXPECTED_UNIQUE_KEYS):
        raise PlaylistPositionSchemaError("Playlist item unique index inventory is incompatible.")
    if frozenset(keys) != _EXPECTED_UNIQUE_KEYS or not named_position_index:
        raise PlaylistPositionSchemaError("Playlist item unique constraints are incompatible.")
    row = connection.execute(
        "SELECT substr(sql, 1, ?) FROM sqlite_master WHERE type = 'index' AND name = ?",
        (_MAX_STORED_SQL_CHARS + 1, PLAYLIST_POSITION_INDEX),
    ).fetchone()
    values = _row_values(row, "playlist position index SQL") if row is not None else ()
    statement = values[0] if len(values) == 1 else None
    if not isinstance(statement, str) or len(statement) > _MAX_STORED_SQL_CHARS:
        raise PlaylistPositionSchemaError("Playlist position index SQL is invalid.")
    if _normalize_sql(statement) != _normalize_sql(_POSITION_INDEX_SQL):
        raise PlaylistPositionSchemaError("Playlist position index definition is incompatible.")


def _validate_foreign_keys_and_triggers(connection: SqlExecutor) -> None:
    rows = connection.execute('PRAGMA foreign_key_list("playlist_items")').fetchall()
    if len(rows) > _MAX_FOREIGN_KEYS:
        raise PlaylistPositionSchemaError("Playlist item foreign-key inventory exceeds its cap.")
    keys: set[tuple[str, str, str, str, str, str]] = set()
    for row in rows:
        values = _row_values(row, "playlist item foreign keys")
        if len(values) < 8 or not all(isinstance(value, str) for value in values[2:8]):
            raise PlaylistPositionSchemaError("Playlist item foreign-key inventory is malformed.")
        keys.add((values[3], values[2], values[4], values[5], values[6], values[7]))
    if frozenset(keys) != _EXPECTED_FOREIGN_KEYS:
        raise PlaylistPositionSchemaError("Playlist item foreign keys are incompatible.")
    triggers = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'trigger' "
        "AND tbl_name = 'playlist_items' ORDER BY name LIMIT ?",
        (_MAX_TRIGGERS + 1,),
    ).fetchall()
    if triggers:
        raise PlaylistPositionSchemaError("Playlist item table contains an unexpected trigger.")


def _validate_all_sequences(connection: SqlExecutor) -> None:
    row = connection.execute("SELECT COUNT(*) FROM playlist_items").fetchone()
    values = _row_values(row, "playlist item count") if row is not None else ()
    count = _read_non_negative_integer(values[0] if len(values) == 1 else None, "item count")
    if count > MAX_PLAYLIST_ITEMS_PER_DATABASE:
        raise PlaylistPositionSchemaError("Playlist item inventory exceeds its hard cap.")
    cursor = connection.execute(
        "SELECT playlist_id, COUNT(*), MIN(position), MAX(position), "
        "COALESCE(SUM(CASE WHEN typeof(position) = 'integer' THEN 1 ELSE 0 END), 0) "
        "FROM playlist_items GROUP BY playlist_id ORDER BY playlist_id"
    )
    observed = 0
    while observed < count:
        row = cursor.fetchone()
        if row is None:
            break
        values = _row_values(row, "playlist position groups")
        if len(values) != 5:
            raise PlaylistPositionSchemaError("Playlist position group is malformed.")
        playlist_id, group_count, minimum, maximum, typed_count = values
        valid_id = (
            not isinstance(playlist_id, bool)
            and isinstance(playlist_id, int)
            and playlist_id > 0
        )
        group_count = _read_non_negative_integer(group_count, "playlist group count")
        typed_count = _read_non_negative_integer(typed_count, "typed group count")
        if not valid_id or group_count > MAX_PLAYLIST_ITEMS_PER_PLAYLIST:
            raise PlaylistPositionSchemaError("Playlist position group exceeds its bounds.")
        if typed_count != group_count or minimum != 1 or maximum != group_count:
            raise PlaylistPositionSchemaError("Playlist positions are not contiguous 1..N.")
        observed += group_count
    if observed != count or cursor.fetchone() is not None:
        raise PlaylistPositionSchemaError("Playlist position group inventory is inconsistent.")


def validate_playlist_positions_v3(connection: SqlExecutor) -> None:
    """Validate v3 shape, constraints, and every persisted 1..N sequence."""
    _validate_table_shape(connection)
    _validate_indexes(connection)
    _validate_foreign_keys_and_triggers(connection)
    _validate_all_sequences(connection)
