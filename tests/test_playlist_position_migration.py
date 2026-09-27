from __future__ import annotations

from contextlib import closing
import sqlite3

import pytest

import src.model.component_database.core_schema as core_schema_module
import src.model.component_database.playlist_position_schema as position_schema
from src.model.component_database.core_schema import (
    CORE_SCHEMA_VERSION,
    CoreSchemaError,
    ensure_core_schema,
)
from src.model.component_database.playlist_identity import (
    canonicalize_playlist_name,
    migrate_playlist_identity_v2,
)
from src.model.component_database.playlist_position import PLAYLIST_POSITION_INDEX


class RecordingTransaction:
    """Record schema statements and optionally fail one matching statement."""

    def __init__(
        self, connection: sqlite3.Connection, blocked_sql: str | None = None
    ) -> None:
        self._connection = connection
        self._blocked_sql = blocked_sql.upper() if blocked_sql else None
        self.statements: list[str] = []

    @property
    def in_transaction(self) -> bool:
        return self._connection.in_transaction

    def execute(
        self, statement: str, parameters: tuple[object, ...] = ()
    ) -> sqlite3.Cursor:
        normalized = " ".join(statement.upper().split())
        self.statements.append(normalized)
        if self._blocked_sql is not None and self._blocked_sql in normalized:
            raise sqlite3.OperationalError("injected playlist position migration failure")
        return self._connection.execute(statement, parameters)


def _create_v2_schema(connection: sqlite3.Connection) -> None:
    for statement in core_schema_module._CORE_TABLE_SQL:
        connection.execute(statement)
    connection.execute("PRAGMA user_version = 1")
    connection.execute("BEGIN IMMEDIATE")
    migrate_playlist_identity_v2(connection)
    connection.execute("PRAGMA user_version = 2")
    connection.commit()


def _insert_playlist(
    connection: sqlite3.Connection, playlist_id: int, name: str
) -> None:
    connection.execute(
        "INSERT INTO playlists "
        "(id, name, canonical_name, creation_date, last_modified) "
        "VALUES (?, ?, ?, '2026-09-02', '2026-09-02')",
        (playlist_id, name, canonicalize_playlist_name(name)),
    )


def _insert_item(
    connection: sqlite3.Connection,
    playlist_id: int,
    media_path: object,
    position: object,
    added_at: object = "2026-09-02T00:00:00",
) -> None:
    connection.execute(
        "INSERT INTO playlist_items "
        "(playlist_id, media_path, position, added_at) VALUES (?, ?, ?, ?)",
        (playlist_id, media_path, position, added_at),
    )


def _migrate(connection: sqlite3.Connection) -> int:
    connection.execute("BEGIN IMMEDIATE")
    version = ensure_core_schema(connection)
    connection.commit()
    return version


def _item_rows(connection: sqlite3.Connection) -> list[tuple[object, ...]]:
    return connection.execute(
        "SELECT playlist_id, media_path, position, added_at "
        "FROM playlist_items ORDER BY playlist_id, position"
    ).fetchall()


def _position_index_count(connection: sqlite3.Connection) -> int:
    return int(
        connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'index' AND name = ?",
            (PLAYLIST_POSITION_INDEX,),
        ).fetchone()[0]
    )


def test_v2_to_v3_deterministically_renumbers_without_losing_rows() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        _create_v2_schema(connection)
        _insert_playlist(connection, 1, "First")
        _insert_playlist(connection, 2, "Second")
        for item in (
            (1, "late.wav", 100, "2026-09-02T03:00:00"),
            (1, "tie-b.wav", 0, "2026-09-02T02:00:00"),
            (1, "tie-a.wav", 0, "2026-09-02T02:00:00"),
            (2, "negative.wav", -8, "2026-09-02T01:00:00"),
            (2, "one.wav", 1, "2026-09-02T00:00:00"),
        ):
            _insert_item(connection, *item)
        connection.commit()

        assert _migrate(connection) == CORE_SCHEMA_VERSION == 3

        assert _item_rows(connection) == [
            (1, "tie-a.wav", 1, "2026-09-02T02:00:00"),
            (1, "tie-b.wav", 2, "2026-09-02T02:00:00"),
            (1, "late.wav", 3, "2026-09-02T03:00:00"),
            (2, "negative.wav", 1, "2026-09-02T01:00:00"),
            (2, "one.wav", 2, "2026-09-02T00:00:00"),
        ]
        assert _position_index_count(connection) == 1
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'playlist_items'"
        ).fetchone()[0]
        assert "typeof(position) = 'integer'" in table_sql
        assert "position >= 1" in table_sql


@pytest.mark.parametrize(
    "blocked_sql",
    [
        "ALTER TABLE PLAYLIST_ITEMS",
        "CREATE TABLE PLAYLIST_ITEMS",
        "INSERT INTO PLAYLIST_ITEMS",
        "CREATE UNIQUE INDEX UX_PLAYLIST_ITEMS_POSITION",
        "DROP TABLE PLAYLIST_ITEMS_V2_BACKUP",
    ],
)
def test_each_migration_failure_rolls_back_rows_schema_and_version(
    blocked_sql: str,
) -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        _create_v2_schema(connection)
        _insert_playlist(connection, 1, "Rollback")
        _insert_item(connection, 1, "one.wav", 50)
        _insert_item(connection, 1, "two.wav", 0)
        connection.commit()
        before = _item_rows(connection)
        proxy = RecordingTransaction(connection, blocked_sql)

        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(CoreSchemaError, match="migration failed"):
            ensure_core_schema(proxy)
        connection.rollback()

        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert _item_rows(connection) == before
        assert _position_index_count(connection) == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = 'playlist_items_v2_backup'"
        ).fetchone()[0] == 0


@pytest.mark.parametrize(
    ("media_path", "position", "added_at", "message"),
    [
        (" ", 1, "2026-09-02", "identity data"),
        ("bad\x00path", 1, "2026-09-02", "identity data"),
        ("x" * 32_769, 1, "2026-09-02", "identity data"),
        ("track.wav", "invalid", "2026-09-02", "not an integer"),
        ("track.wav", 1, "", "timestamp"),
        ("track.wav", 1, "bad\ntimestamp", "timestamp"),
        ("track.wav", 1, "x" * 129, "timestamp"),
    ],
    ids=["blank-path", "nul-path", "overlong-path", "noninteger-position",
         "empty-timestamp", "newline-timestamp", "overlong-timestamp"],
)
def test_invalid_v2_inventory_fails_before_first_schema_statement(
    media_path: object, position: object, added_at: object, message: str
) -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        _create_v2_schema(connection)
        _insert_playlist(connection, 1, "Invalid")
        _insert_item(connection, 1, media_path, position, added_at)
        connection.commit()
        proxy = RecordingTransaction(connection)

        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(CoreSchemaError, match=message):
            ensure_core_schema(proxy)
        connection.rollback()

        mutation_prefixes = ("ALTER TABLE", "CREATE TABLE", "DROP TABLE")
        assert not any(
            statement.startswith(mutation_prefixes) for statement in proxy.statements
        )
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2


def test_orphan_inventory_fails_before_first_schema_statement() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        _create_v2_schema(connection)
        _insert_item(connection, 999, "orphan.wav", 1)
        connection.commit()
        proxy = RecordingTransaction(connection)

        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(CoreSchemaError, match="orphan"):
            ensure_core_schema(proxy)
        connection.rollback()

        assert not any(statement.startswith("ALTER TABLE") for statement in proxy.statements)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2


def test_capacity_overflow_fails_before_schema_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        _create_v2_schema(connection)
        _insert_playlist(connection, 1, "Capacity")
        for index in range(3):
            _insert_item(connection, 1, f"{index}.wav", index + 1)
        connection.commit()
        monkeypatch.setattr(position_schema, "MAX_PLAYLIST_ITEMS_PER_PLAYLIST", 2)
        proxy = RecordingTransaction(connection)

        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(CoreSchemaError, match="hard cap"):
            ensure_core_schema(proxy)
        connection.rollback()

        assert not any(statement.startswith("ALTER TABLE") for statement in proxy.statements)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2


def test_v3_validator_rejects_sparse_sequence_without_repair() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        _create_v2_schema(connection)
        _insert_playlist(connection, 1, "Sparse")
        _insert_item(connection, 1, "one.wav", 1)
        _insert_item(connection, 1, "two.wav", 2)
        connection.commit()
        _migrate(connection)
        connection.execute(
            "UPDATE playlist_items SET position = 20 WHERE media_path = 'two.wav'"
        )
        connection.commit()

        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(CoreSchemaError, match="contiguous"):
            ensure_core_schema(connection)
        connection.rollback()

        assert _item_rows(connection)[-1][2] == 20
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3


def test_v3_validator_rejects_redundant_unique_index_and_trigger() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        _create_v2_schema(connection)
        _insert_playlist(connection, 1, "Drift")
        connection.commit()
        _migrate(connection)
        connection.execute(
            "CREATE UNIQUE INDEX redundant_position_index "
            "ON playlist_items(playlist_id, position)"
        )

        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(CoreSchemaError, match="index inventory"):
            ensure_core_schema(connection)
        connection.rollback()
        connection.execute("DROP INDEX redundant_position_index")
        connection.execute(
            "CREATE TRIGGER unexpected_position_trigger "
            "AFTER INSERT ON playlist_items BEGIN SELECT 1; END"
        )

        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(CoreSchemaError, match="unexpected trigger"):
            ensure_core_schema(connection)
        connection.rollback()


def test_v3_reopen_is_idempotent_and_keeps_exact_positions() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        _create_v2_schema(connection)
        _insert_playlist(connection, 1, "Stable")
        _insert_item(connection, 1, "first.wav", 10)
        _insert_item(connection, 1, "second.wav", 20)
        connection.commit()
        _migrate(connection)
        expected = _item_rows(connection)

        assert _migrate(connection) == 3
        assert _item_rows(connection) == expected
        assert _position_index_count(connection) == 1


def test_v2_nonunique_index_is_preserved_across_table_rebuild() -> None:
    index_sql = (
        "CREATE INDEX idx_playlist_items_added_position "
        "ON playlist_items(added_at, position) WHERE position >= 0"
    )
    with closing(sqlite3.connect(":memory:")) as connection:
        _create_v2_schema(connection)
        _insert_playlist(connection, 1, "Indexed")
        _insert_item(connection, 1, "one.wav", 20)
        connection.execute(index_sql)
        connection.commit()

        assert _migrate(connection) == 3

        stored = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
            ("idx_playlist_items_added_position",),
        ).fetchone()
        assert stored == (index_sql,)
        assert _item_rows(connection)[0][2] == 1


def test_preserved_index_recreation_failure_rolls_back_complete_migration() -> None:
    index_sql = (
        "CREATE INDEX idx_playlist_items_added_position "
        "ON playlist_items(added_at, position)"
    )
    with closing(sqlite3.connect(":memory:")) as connection:
        _create_v2_schema(connection)
        _insert_playlist(connection, 1, "Rollback Index")
        _insert_item(connection, 1, "one.wav", 50)
        connection.execute(index_sql)
        connection.commit()
        before = _item_rows(connection)
        proxy = RecordingTransaction(connection, "IDX_PLAYLIST_ITEMS_ADDED_POSITION")

        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(CoreSchemaError, match="migration failed"):
            ensure_core_schema(proxy)
        connection.rollback()

        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert _item_rows(connection) == before
        assert connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
            ("idx_playlist_items_added_position",),
        ).fetchone() == (index_sql,)
        assert _position_index_count(connection) == 0


@pytest.mark.parametrize(
    "dependency_kind", ["view", "temp_view", "trigger", "foreign_key"]
)
def test_external_playlist_item_dependency_fails_before_first_ddl(
    dependency_kind: str,
) -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        _create_v2_schema(connection)
        if dependency_kind in {"view", "temp_view"}:
            prefix = "TEMP " if dependency_kind == "temp_view" else ""
            connection.execute(
                f"CREATE {prefix}VIEW playlist_item_paths AS "
                "SELECT media_path FROM playlist_items"
            )
        elif dependency_kind == "trigger":
            connection.execute("CREATE TABLE external_events (id INTEGER PRIMARY KEY)")
            connection.execute(
                "CREATE TRIGGER external_playlist_item_probe "
                "AFTER INSERT ON external_events BEGIN "
                "SELECT COUNT(*) FROM playlist_items; END"
            )
        else:
            connection.execute(
                "CREATE TABLE playlist_item_notes ("
                "playlist_id INTEGER NOT NULL, media_path TEXT NOT NULL, "
                "FOREIGN KEY (playlist_id, media_path) "
                "REFERENCES playlist_items (playlist_id, media_path))"
            )
        connection.commit()
        proxy = RecordingTransaction(connection)

        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(CoreSchemaError, match="external"):
            ensure_core_schema(proxy)
        connection.rollback()

        mutation_prefixes = ("ALTER TABLE", "CREATE TABLE PLAYLIST_ITEMS")
        assert not any(
            statement.startswith(mutation_prefixes) for statement in proxy.statements
        )
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2


def test_dependency_inventory_cap_fails_before_first_ddl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        _create_v2_schema(connection)
        connection.commit()
        monkeypatch.setattr(position_schema, "_MAX_DEPENDENCY_OBJECTS", 8)
        proxy = RecordingTransaction(connection)

        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(CoreSchemaError, match="dependency inventory"):
            ensure_core_schema(proxy)
        connection.rollback()

        assert not any(statement.startswith("ALTER TABLE") for statement in proxy.statements)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2


def test_dependency_sql_cap_fails_before_first_ddl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        _create_v2_schema(connection)
        connection.execute(
            "CREATE VIEW oversized_schema_object AS SELECT '" + ("x" * 256) + "' AS value"
        )
        connection.commit()
        monkeypatch.setattr(position_schema, "_MAX_STORED_SQL_CHARS", 64)
        proxy = RecordingTransaction(connection)

        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(CoreSchemaError, match="SQL exceeds"):
            ensure_core_schema(proxy)
        connection.rollback()

        assert not any(statement.startswith("ALTER TABLE") for statement in proxy.statements)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
