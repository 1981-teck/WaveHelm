from __future__ import annotations

from dataclasses import dataclass
import re
import sqlite3
import unicodedata

from src.model.component_database.db_primitives import SqlExecutor
from src.utils.exceptions import DatabaseError


MAX_PLAYLIST_NAME_CODEPOINTS = 100
MAX_PLAYLIST_NAME_INPUT_CODEPOINTS = 256
MAX_PLAYLIST_CANONICAL_CODEPOINTS = 400
MAX_PLAYLIST_CANONICAL_UTF8_BYTES = 1_600
MAX_PLAYLISTS_PER_DATABASE = 100_000
PLAYLIST_CANONICAL_INDEX = "ux_playlists_canonical_name"

_CANONICAL_COLUMN_SQL = "canonical_name TEXT NOT NULL DEFAULT ''"
_CANONICAL_INDEX_SQL = (
    f"CREATE UNIQUE INDEX {PLAYLIST_CANONICAL_INDEX} "
    "ON playlists(canonical_name)"
)
_INSERT_GUARD_NAME = "trg_playlists_canonical_insert_guard"
_UPDATE_GUARD_NAME = "trg_playlists_canonical_update_guard"
_INSERT_GUARD_SQL = f"""
CREATE TRIGGER {_INSERT_GUARD_NAME}
BEFORE INSERT ON playlists
WHEN length(NEW.canonical_name) NOT BETWEEN 1 AND {MAX_PLAYLIST_CANONICAL_CODEPOINTS}
   OR length(CAST(NEW.canonical_name AS BLOB)) > {MAX_PLAYLIST_CANONICAL_UTF8_BYTES}
BEGIN
    SELECT RAISE(ABORT, 'playlist canonical name required');
END
"""
_UPDATE_GUARD_SQL = f"""
CREATE TRIGGER {_UPDATE_GUARD_NAME}
BEFORE UPDATE OF canonical_name ON playlists
WHEN length(NEW.canonical_name) NOT BETWEEN 1 AND {MAX_PLAYLIST_CANONICAL_CODEPOINTS}
   OR length(CAST(NEW.canonical_name AS BLOB)) > {MAX_PLAYLIST_CANONICAL_UTF8_BYTES}
BEGIN
    SELECT RAISE(ABORT, 'playlist canonical name required');
END
"""
_EXPECTED_TRIGGER_SQL = {
    _INSERT_GUARD_NAME: _INSERT_GUARD_SQL,
    _UPDATE_GUARD_NAME: _UPDATE_GUARD_SQL,
}
_EXPECTED_COLUMNS = (
    ("id", "INTEGER", 0, None, 1),
    ("name", "TEXT", 1, None, 0),
    ("description", "TEXT", 0, None, 0),
    ("cover_art", "TEXT", 0, None, 0),
    ("creation_date", "TEXT", 1, None, 0),
    ("last_modified", "TEXT", 1, None, 0),
    ("canonical_name", "TEXT", 1, "''", 0),
)
_EXPECTED_UNIQUE_KEYS = frozenset({("name",), ("canonical_name",)})
_EXPECTED_PLAYLIST_TABLE_SQL = """
CREATE TABLE playlists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    cover_art TEXT,
    creation_date TEXT NOT NULL,
    last_modified TEXT NOT NULL
, canonical_name TEXT NOT NULL DEFAULT '')
"""
_SQL_SPACE = re.compile(r"\s+")
_MAX_PLAYLIST_INDEXES = 64
_MAX_INDEX_COLUMNS = 16
_MAX_PLAYLIST_TRIGGERS = 8
_MAX_STORED_SQL_CHARS = 16_384
_MAX_COLLISION_GROUPS_IN_ERROR = 32
_MAX_COLLISION_ROWS_IN_ERROR = 64


class PlaylistIdentitySchemaError(DatabaseError):
    """Raised when playlist identity data or schema cannot migrate safely."""


@dataclass(frozen=True, slots=True)
class PlaylistName:
    """Validated display name and its persistent Unicode-equivalence key."""

    display: str
    canonical: str


@dataclass(frozen=True, slots=True)
class _StoredPlaylistName:
    playlist_id: int
    display: str
    canonical: str


def normalize_playlist_name(name: str) -> PlaylistName:
    """Validate a playlist name and derive its NFKC/casefold identity.

    Edge cases:
        1. Raw or compatibility-introduced edge whitespace cannot create an identity.
        2. Control characters and unpaired surrogates are rejected before SQLite I/O.
        3. Unicode normalization expansion remains bounded in code points and bytes.
    """
    if not isinstance(name, str):
        raise TypeError("Playlist name must be text")
    if len(name) > MAX_PLAYLIST_NAME_INPUT_CODEPOINTS:
        raise ValueError("Playlist name input exceeds its length limit")
    if any(
        unicodedata.category(character) in {"Cc", "Cs", "Zl", "Zp"}
        for character in name
    ):
        raise ValueError("Playlist name contains a control character")
    display = name.strip()
    if not display:
        raise ValueError("Playlist name required")
    if len(display) > MAX_PLAYLIST_NAME_CODEPOINTS:
        raise ValueError("Playlist name exceeds its length limit")
    try:
        display.encode("utf-8", "strict")
        canonical = unicodedata.normalize("NFKC", display).casefold().strip()
        encoded_canonical = canonical.encode("utf-8", "strict")
    except UnicodeError as error:
        raise ValueError("Playlist name is not valid Unicode text") from error
    if not canonical:
        raise ValueError("Playlist canonical name is empty")
    if len(canonical) > MAX_PLAYLIST_CANONICAL_CODEPOINTS:
        raise ValueError("Playlist canonical name exceeds its length limit")
    if len(encoded_canonical) > MAX_PLAYLIST_CANONICAL_UTF8_BYTES:
        raise ValueError("Playlist canonical name exceeds its byte limit")
    return PlaylistName(display=display, canonical=canonical)


def canonicalize_playlist_name(name: str) -> str:
    """Return the canonical key used by create, rename, lookup, and migration."""
    return normalize_playlist_name(name).canonical


def _row_values(row: object, label: str) -> tuple[object, ...]:
    if isinstance(row, sqlite3.Row):
        return tuple(row)
    if isinstance(row, tuple):
        return row
    raise PlaylistIdentitySchemaError(f"{label} returned a malformed row.")


def _read_stored_names(
    connection: SqlExecutor, *, include_canonical: bool
) -> tuple[_StoredPlaylistName, ...]:
    columns = "id, name, canonical_name" if include_canonical else "id, name"
    rows = connection.execute(
        f"SELECT {columns} FROM playlists ORDER BY id LIMIT ?",
        (MAX_PLAYLISTS_PER_DATABASE + 1,),
    ).fetchall()
    if len(rows) > MAX_PLAYLISTS_PER_DATABASE:
        raise PlaylistIdentitySchemaError("Playlist inventory exceeds its hard cap.")
    records: list[_StoredPlaylistName] = []
    for row in rows:
        values = _row_values(row, "playlist identity inventory")
        expected_size = 3 if include_canonical else 2
        if len(values) != expected_size:
            raise PlaylistIdentitySchemaError("Playlist identity row has an invalid shape.")
        playlist_id, raw_name = values[0], values[1]
        if isinstance(playlist_id, bool) or not isinstance(playlist_id, int):
            raise PlaylistIdentitySchemaError("Playlist identity contains an invalid id.")
        if playlist_id <= 0 or not isinstance(raw_name, str):
            raise PlaylistIdentitySchemaError("Playlist identity contains invalid data.")
        try:
            normalized = normalize_playlist_name(raw_name)
        except (TypeError, ValueError) as error:
            raise PlaylistIdentitySchemaError(
                f"Playlist {playlist_id} has an invalid stored name.", details=str(error)
            ) from error
        stored_canonical = normalized.canonical
        if include_canonical:
            raw_canonical = values[2]
            if not isinstance(raw_canonical, str):
                raise PlaylistIdentitySchemaError(
                    f"Playlist {playlist_id} has an invalid canonical key."
                )
            stored_canonical = raw_canonical
            if stored_canonical != normalized.canonical:
                raise PlaylistIdentitySchemaError(
                    f"Playlist {playlist_id} canonical key does not match its name."
                )
        records.append(_StoredPlaylistName(playlist_id, raw_name, stored_canonical))
    return tuple(records)


def _collision_details(records: tuple[_StoredPlaylistName, ...]) -> str | None:
    grouped: dict[str, list[_StoredPlaylistName]] = {}
    for record in records:
        grouped.setdefault(record.canonical, []).append(record)
    collisions = [items for items in grouped.values() if len(items) > 1]
    if not collisions:
        return None
    collisions.sort(key=lambda items: (items[0].canonical, items[0].playlist_id))
    rendered: list[str] = []
    rendered_rows = 0
    for items in collisions[:_MAX_COLLISION_GROUPS_IN_ERROR]:
        available = _MAX_COLLISION_ROWS_IN_ERROR - rendered_rows
        if available <= 0:
            break
        selected = items[:available]
        rows = ", ".join(f"{item.playlist_id}:{item.display!r}" for item in selected)
        rendered.append(f"{items[0].canonical!r} => [{rows}]")
        rendered_rows += len(selected)
    collision_rows = sum(map(len, collisions))
    suffix = ""
    if len(collisions) > len(rendered) or collision_rows > rendered_rows:
        suffix = (
            f"; truncated collision report: groups={len(collisions)}, "
            f"rows={collision_rows}"
        )
    return "; ".join(rendered) + suffix


def migrate_playlist_identity_v2(connection: SqlExecutor) -> None:
    """Add and populate the canonical playlist identity under an outer transaction.

    Edge cases:
        1. Canonically colliding legacy rows abort before any schema statement.
        2. Every row update must affect exactly the preflighted playlist record.
        3. Index or trigger creation failures roll back column and data changes.
    """
    records = _read_stored_names(connection, include_canonical=False)
    collision_details = _collision_details(records)
    if collision_details is not None:
        raise PlaylistIdentitySchemaError(
            "Canonical playlist name collisions block schema migration.",
            details=collision_details,
        )
    connection.execute(f"ALTER TABLE playlists ADD COLUMN {_CANONICAL_COLUMN_SQL}")
    for record in records:
        cursor = connection.execute(
            "UPDATE playlists SET canonical_name = ? WHERE id = ? AND name = ?",
            (record.canonical, record.playlist_id, record.display),
        )
        if cursor.rowcount != 1:
            raise PlaylistIdentitySchemaError(
                f"Playlist identity changed during migration: {record.playlist_id}."
            )
    connection.execute(_CANONICAL_INDEX_SQL)
    connection.execute(_INSERT_GUARD_SQL)
    connection.execute(_UPDATE_GUARD_SQL)


def _validate_table_sql(connection: SqlExecutor) -> None:
    row = connection.execute(
        "SELECT substr(sql, 1, ?) FROM sqlite_master "
        "WHERE type = 'table' AND name = 'playlists'",
        (_MAX_STORED_SQL_CHARS + 1,),
    ).fetchone()
    values = _row_values(row, "playlist table SQL") if row is not None else ()
    statement = values[0] if len(values) == 1 else None
    if not isinstance(statement, str) or len(statement) > _MAX_STORED_SQL_CHARS:
        raise PlaylistIdentitySchemaError("Playlist table SQL is missing or invalid.")
    if _normalize_sql(statement) != _normalize_sql(_EXPECTED_PLAYLIST_TABLE_SQL):
        raise PlaylistIdentitySchemaError("Playlist table has an incompatible v2 definition.")


def _table_columns(connection: SqlExecutor) -> tuple[tuple[object, ...], ...]:
    rows = connection.execute('PRAGMA table_info("playlists")').fetchall()
    return tuple(_row_values(row, "playlist table columns") for row in rows)


def _validate_columns(connection: SqlExecutor) -> None:
    rows = _table_columns(connection)
    actual: list[tuple[object, ...]] = []
    for row in rows:
        if len(row) < 6:
            raise PlaylistIdentitySchemaError("Playlist column inventory is malformed.")
        actual.append((row[1], row[2], row[3], row[4], row[5]))
    if tuple(actual) != _EXPECTED_COLUMNS:
        raise PlaylistIdentitySchemaError("Playlist table has an incompatible v2 shape.")


def _validate_unique_keys(connection: SqlExecutor) -> None:
    rows = connection.execute('PRAGMA index_list("playlists")').fetchall()
    if len(rows) > _MAX_PLAYLIST_INDEXES:
        raise PlaylistIdentitySchemaError("Playlist index inventory exceeds its hard cap.")
    keys: set[tuple[str, ...]] = set()
    unique_index_count = 0
    found_named_index = False
    for row in rows:
        values = _row_values(row, "playlist index inventory")
        if len(values) < 5 or not isinstance(values[1], str):
            raise PlaylistIdentitySchemaError("Playlist index inventory is malformed.")
        index_name, is_unique, is_partial = values[1], values[2], values[4]
        if is_unique != 1:
            continue
        unique_index_count += 1
        if is_partial != 0:
            raise PlaylistIdentitySchemaError("Playlist unique index cannot be partial.")
        escaped_name = index_name.replace('"', '""')
        index_rows = connection.execute(
            f'PRAGMA index_info("{escaped_name}")'
        ).fetchall()
        if len(index_rows) > _MAX_INDEX_COLUMNS:
            raise PlaylistIdentitySchemaError("Playlist unique index exceeds its column cap.")
        columns: list[str] = []
        for index_row in index_rows:
            index_values = _row_values(index_row, "playlist index columns")
            column = index_values[2] if len(index_values) >= 3 else None
            if not isinstance(column, str) or not column:
                raise PlaylistIdentitySchemaError("Playlist unique index is malformed.")
            columns.append(column)
        key = tuple(columns)
        keys.add(key)
        if index_name == PLAYLIST_CANONICAL_INDEX and key == ("canonical_name",):
            found_named_index = True
    if unique_index_count != len(_EXPECTED_UNIQUE_KEYS):
        raise PlaylistIdentitySchemaError("Playlist unique index inventory is incompatible.")
    if frozenset(keys) != _EXPECTED_UNIQUE_KEYS or not found_named_index:
        raise PlaylistIdentitySchemaError("Playlist unique constraints are incompatible.")
    row = connection.execute(
        "SELECT substr(sql, 1, ?) FROM sqlite_master "
        "WHERE type = 'index' AND name = ?",
        (_MAX_STORED_SQL_CHARS + 1, PLAYLIST_CANONICAL_INDEX),
    ).fetchone()
    values = _row_values(row, "playlist canonical index SQL") if row is not None else ()
    statement = values[0] if len(values) == 1 else None
    if not isinstance(statement, str) or len(statement) > _MAX_STORED_SQL_CHARS:
        raise PlaylistIdentitySchemaError("Playlist canonical index SQL is invalid.")
    if _normalize_sql(statement) != _normalize_sql(_CANONICAL_INDEX_SQL):
        raise PlaylistIdentitySchemaError("Playlist canonical index definition is incompatible.")


def _normalize_sql(statement: str) -> str:
    return _SQL_SPACE.sub(" ", statement.strip()).upper()


def _validate_triggers(connection: SqlExecutor) -> None:
    rows = connection.execute(
        "SELECT name, substr(sql, 1, ?) FROM sqlite_master "
        "WHERE type = 'trigger' AND tbl_name = 'playlists' ORDER BY name LIMIT ?",
        (_MAX_STORED_SQL_CHARS + 1, _MAX_PLAYLIST_TRIGGERS + 1),
    ).fetchall()
    if len(rows) > _MAX_PLAYLIST_TRIGGERS:
        raise PlaylistIdentitySchemaError("Playlist trigger inventory exceeds its hard cap.")
    actual: dict[str, str] = {}
    for row in rows:
        values = _row_values(row, "playlist trigger inventory")
        name, statement = values if len(values) == 2 else (None, None)
        if not isinstance(name, str) or not isinstance(statement, str):
            raise PlaylistIdentitySchemaError("Playlist trigger inventory is malformed.")
        if len(statement) > _MAX_STORED_SQL_CHARS:
            raise PlaylistIdentitySchemaError("Playlist trigger SQL exceeds its hard cap.")
        actual[name] = _normalize_sql(statement)
    expected = {
        name: _normalize_sql(statement)
        for name, statement in _EXPECTED_TRIGGER_SQL.items()
    }
    if actual != expected:
        raise PlaylistIdentitySchemaError("Playlist identity triggers are incompatible.")


def validate_playlist_identity_v2(connection: SqlExecutor) -> None:
    """Validate v2 shape, constraints, guards, and every stored identity."""
    _validate_table_sql(connection)
    _validate_columns(connection)
    _validate_unique_keys(connection)
    _validate_triggers(connection)
    records = _read_stored_names(connection, include_canonical=True)
    collision_details = _collision_details(records)
    if collision_details is not None:
        raise PlaylistIdentitySchemaError(
            "Playlist canonical keys are not unique.", details=collision_details
        )
