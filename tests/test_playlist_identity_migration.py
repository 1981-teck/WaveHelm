from __future__ import annotations

from contextlib import closing
import sqlite3

import pytest

import src.model.component_database.core_schema as core_schema_module
import src.model.component_database.playlist_identity as identity_module
from src.model.component_database.core_schema import (
    CORE_SCHEMA_VERSION,
    CoreSchemaError,
    ensure_core_schema,
)
from src.model.component_database.playlist_identity import (
    PlaylistIdentitySchemaError,
    canonicalize_playlist_name,
    normalize_playlist_name,
    validate_playlist_identity_v2,
)


class FaultingTransaction:
    """Delegate SQL except for one injected migration statement."""

    def __init__(self, connection: sqlite3.Connection, blocked_sql: str) -> None:
        self._connection = connection
        self._blocked_sql = blocked_sql.upper()

    @property
    def in_transaction(self) -> bool:
        return self._connection.in_transaction

    def execute(
        self, statement: str, parameters: tuple[object, ...] = ()
    ) -> sqlite3.Cursor:
        if self._blocked_sql in statement.upper():
            raise sqlite3.OperationalError("injected migration failure")
        return self._connection.execute(statement, parameters)


def _create_v1_schema(connection: sqlite3.Connection) -> None:
    for statement in core_schema_module._CORE_TABLE_SQL:
        connection.execute(statement)
    connection.execute("PRAGMA user_version = 1")
    connection.commit()


def _insert_v1_playlist(
    connection: sqlite3.Connection, playlist_id: int, name: str
) -> None:
    connection.execute(
        "INSERT INTO playlists "
        "(id, name, description, cover_art, creation_date, last_modified) "
        "VALUES (?, ?, NULL, NULL, '2024-01-01', '2024-01-01')",
        (playlist_id, name),
    )


def _migrate(connection: sqlite3.Connection) -> int:
    connection.execute("BEGIN IMMEDIATE")
    version = ensure_core_schema(connection)
    connection.commit()
    return version


def _playlist_columns(connection: sqlite3.Connection) -> tuple[str, ...]:
    return tuple(str(row[1]) for row in connection.execute("PRAGMA table_info(playlists)"))


def test_unicode_identity_policy_covers_compatibility_and_casefold() -> None:
    assert canonicalize_playlist_name("Straße") == canonicalize_playlist_name("STRASSE")
    assert canonicalize_playlist_name("é") == canonicalize_playlist_name("e\u0301")
    assert canonicalize_playlist_name("Ｆｏｏ") == canonicalize_playlist_name("foo")
    assert canonicalize_playlist_name("  Mix  ") == "mix"
    assert canonicalize_playlist_name("῟") == canonicalize_playlist_name(" ̔͂")
    assert canonicalize_playlist_name("I") == "i"
    assert canonicalize_playlist_name("İ") != canonicalize_playlist_name("I")
    assert canonicalize_playlist_name("ı") != canonicalize_playlist_name("I")


@pytest.mark.parametrize(
    "value, error_type",
    [
        ("", ValueError),
        ("   ", ValueError),
        ("x" * 101, ValueError),
        ("x\nname", ValueError),
        ("x\ud800", ValueError),
        (None, TypeError),
        (123, TypeError),
    ],
)
def test_playlist_name_policy_rejects_invalid_values(
    value: object, error_type: type[Exception]
) -> None:
    with pytest.raises(error_type):
        normalize_playlist_name(value)  # type: ignore[arg-type]


def test_v1_to_v2_preserves_ids_items_and_autoincrement() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        _create_v1_schema(connection)
        _insert_v1_playlist(connection, 5, "Straße")
        _insert_v1_playlist(connection, 9, "Road")
        connection.execute(
            "INSERT INTO playlist_items VALUES (?, ?, ?, ?)",
            (5, "track.wav", 1, "2024-01-01"),
        )
        connection.commit()

        assert _migrate(connection) == CORE_SCHEMA_VERSION
        rows = connection.execute(
            "SELECT id, name, canonical_name FROM playlists ORDER BY id"
        ).fetchall()
        assert rows == [(5, "Straße", "strasse"), (9, "Road", "road")]
        assert connection.execute("SELECT * FROM playlist_items").fetchone() == (
            5,
            "track.wav",
            1,
            "2024-01-01",
        )
        cursor = connection.execute(
            "INSERT INTO playlists "
            "(name, canonical_name, creation_date, last_modified) VALUES (?, ?, ?, ?)",
            ("Next", "next", "2024-01-01", "2024-01-01"),
        )
        assert cursor.lastrowid == 10


@pytest.mark.parametrize(
    "first, second",
    [
        ("Straße", "STRASSE"),
        ("é", "e\u0301"),
        ("Ｆｏｏ", "foo"),
        (" Mix ", "Mix"),
    ],
)
def test_collision_preflight_rolls_back_before_schema_mutation(
    first: str, second: str
) -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        _create_v1_schema(connection)
        _insert_v1_playlist(connection, 7, first)
        _insert_v1_playlist(connection, 11, second)
        connection.commit()
        before = connection.execute("SELECT id, name FROM playlists ORDER BY id").fetchall()

        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(CoreSchemaError, match="collisions") as captured:
            ensure_core_schema(connection)
        connection.rollback()

        assert "7:" in str(captured.value)
        assert "11:" in str(captured.value)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert "canonical_name" not in _playlist_columns(connection)
        assert connection.execute("SELECT id, name FROM playlists ORDER BY id").fetchall() == before


def test_source_v1_schema_is_validated_before_first_v2_statement() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        _create_v1_schema(connection)
        connection.execute("ALTER TABLE history ADD COLUMN unexpected TEXT")
        connection.commit()

        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(CoreSchemaError, match="incompatible shape: history"):
            ensure_core_schema(connection)
        connection.rollback()

        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert "canonical_name" not in _playlist_columns(connection)


def test_index_creation_failure_rolls_back_column_and_data() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        _create_v1_schema(connection)
        _insert_v1_playlist(connection, 1, "Alpha")
        connection.commit()
        proxy = FaultingTransaction(connection, "CREATE UNIQUE INDEX")

        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(CoreSchemaError, match="migration failed"):
            ensure_core_schema(proxy)
        connection.rollback()

        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert "canonical_name" not in _playlist_columns(connection)
        assert connection.execute("SELECT name FROM playlists").fetchone() == ("Alpha",)


def test_database_guards_reject_empty_canonical_identity() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        _create_v1_schema(connection)
        assert _migrate(connection) == CORE_SCHEMA_VERSION
        with pytest.raises(sqlite3.IntegrityError, match="canonical name required"):
            connection.execute(
                "INSERT INTO playlists "
                "(name, creation_date, last_modified) VALUES ('Alpha', 'd', 'd')"
            )
        connection.execute(
            "INSERT INTO playlists "
            "(name, canonical_name, creation_date, last_modified) "
            "VALUES ('Alpha', 'alpha', 'd', 'd')"
        )
        with pytest.raises(sqlite3.IntegrityError, match="canonical name required"):
            connection.execute(
                "UPDATE playlists SET canonical_name = '' WHERE name = 'Alpha'"
            )


def test_v2_validator_rejects_canonical_data_drift() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        _create_v1_schema(connection)
        _insert_v1_playlist(connection, 1, "Straße")
        connection.commit()
        _migrate(connection)
        connection.execute(
            "UPDATE playlists SET canonical_name = 'wrong' WHERE id = 1"
        )
        with pytest.raises(PlaylistIdentitySchemaError, match="does not match"):
            validate_playlist_identity_v2(connection)


def test_v2_validator_rejects_unexpected_playlist_trigger() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        _create_v1_schema(connection)
        _migrate(connection)
        connection.execute(
            "CREATE TRIGGER unexpected_playlist_trigger "
            "AFTER INSERT ON playlists BEGIN SELECT 1; END"
        )
        with pytest.raises(PlaylistIdentitySchemaError, match="triggers"):
            validate_playlist_identity_v2(connection)


def test_playlist_inventory_cap_fails_before_schema_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        _create_v1_schema(connection)
        _insert_v1_playlist(connection, 1, "Alpha")
        _insert_v1_playlist(connection, 2, "Beta")
        connection.commit()
        monkeypatch.setattr(identity_module, "MAX_PLAYLISTS_PER_DATABASE", 1)

        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(CoreSchemaError, match="hard cap"):
            ensure_core_schema(connection)
        connection.rollback()

        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert "canonical_name" not in _playlist_columns(connection)


def test_migration_preserves_display_text_and_nonunique_indexes() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        _create_v1_schema(connection)
        connection.execute(
            "CREATE INDEX idx_playlists_modified ON playlists(last_modified)"
        )
        _insert_v1_playlist(connection, 4, "  Mix  ")
        connection.commit()

        _migrate(connection)

        assert connection.execute(
            "SELECT name, canonical_name FROM playlists WHERE id = 4"
        ).fetchone() == ("  Mix  ", "mix")
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type = 'index' AND name = 'idx_playlists_modified'"
        ).fetchone()[0] == 1


def test_v2_validator_rejects_canonical_index_collation_drift() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        _create_v1_schema(connection)
        _migrate(connection)
        connection.execute("DROP INDEX ux_playlists_canonical_name")
        connection.execute(
            "CREATE UNIQUE INDEX ux_playlists_canonical_name "
            "ON playlists(canonical_name COLLATE NOCASE)"
        )
        with pytest.raises(PlaylistIdentitySchemaError, match="definition"):
            validate_playlist_identity_v2(connection)



def test_v2_validator_rejects_redundant_unique_identity_index() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        _create_v1_schema(connection)
        _migrate(connection)
        connection.execute(
            "CREATE UNIQUE INDEX redundant_playlist_identity "
            "ON playlists(canonical_name)"
        )
        with pytest.raises(PlaylistIdentitySchemaError, match="inventory"):
            validate_playlist_identity_v2(connection)
