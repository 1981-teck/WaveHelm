from __future__ import annotations

from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
import sqlite3
import threading

import pytest

import src.model.component_database.core_schema as core_schema_module
import src.model.component_database.db_core as db_core_module
from src.model.component_database.core_schema import (
    CORE_SCHEMA_VERSION,
    CoreSchemaError,
    ensure_core_schema,
)
from src.model.component_database.db_core import DbCore
from src.model.component_database.playlist_manager import PlaylistManager


CORE_TABLES = frozenset(
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


class DummyLocalization:
    def get_text(self, key: str, **kwargs: object) -> str:
        return key


@pytest.fixture(autouse=True)
def reset_db_core_singleton() -> Iterator[None]:
    _reset_singleton()
    yield
    _reset_singleton()


def _reset_singleton() -> None:
    instance = getattr(DbCore, "_instance", None)
    if instance is not None and getattr(instance, "conn", None) is not None:
        instance.close()
    DbCore._instance = None


def _configure_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(db_core_module, "get_user_data_dir", lambda: tmp_path)
    return tmp_path / "core.db"


def _create_current_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, DbCore]:
    db_path = _configure_runtime(tmp_path, monkeypatch)
    core = DbCore(db_path="core.db", localization_manager=DummyLocalization())
    return db_path, core


def _user_tables(connection: sqlite3.Connection) -> frozenset[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return frozenset(str(row[0]) for row in rows)


def _schema_snapshot(connection: sqlite3.Connection) -> tuple[tuple[object, ...], ...]:
    rows = connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall()
    return tuple(tuple(row) for row in rows)


def test_new_database_is_created_atomically_at_current_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _db_path, core = _create_current_database(tmp_path, monkeypatch)
    assert core.conn is not None
    assert core.conn.execute("PRAGMA user_version").fetchone()[0] == CORE_SCHEMA_VERSION
    assert _user_tables(core.conn) == CORE_TABLES


def test_current_schema_reopen_is_idempotent_and_preserves_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path, core = _create_current_database(tmp_path, monkeypatch)
    core._execute_write("INSERT INTO app_settings (key, value) VALUES (?, ?)", ("k", "v"))
    assert core.conn is not None
    before = _schema_snapshot(core.conn)
    core.close()
    _reset_singleton()

    reopened = DbCore(db_path="core.db", localization_manager=DummyLocalization())
    assert reopened.conn is not None
    assert _schema_snapshot(reopened.conn) == before
    assert reopened.conn.execute("SELECT value FROM app_settings").fetchone()[0] == "v"
    assert Path(reopened.db_path) == db_path


def test_complete_unversioned_legacy_schema_is_adopted_and_migrated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = _configure_runtime(tmp_path, monkeypatch)
    with closing(sqlite3.connect(db_path)) as connection:
        for statement in core_schema_module._CORE_TABLE_SQL:
            connection.execute(statement)
        connection.execute("INSERT INTO app_settings VALUES ('legacy', 'kept')")
        connection.execute(
            "INSERT INTO playlists "
            "(name, description, cover_art, creation_date, last_modified) "
            "VALUES ('Straße', NULL, NULL, '2024-01-01', '2024-01-01')"
        )
        connection.commit()

    reopened = DbCore(db_path="core.db", localization_manager=DummyLocalization())
    assert reopened.conn is not None
    assert reopened.conn.execute("SELECT value FROM app_settings").fetchone()[0] == "kept"
    row = reopened.conn.execute(
        "SELECT name, canonical_name FROM playlists"
    ).fetchone()
    assert tuple(row) == ("Straße", "strasse")
    assert reopened.conn.execute("PRAGMA user_version").fetchone()[0] == CORE_SCHEMA_VERSION


def test_partial_unversioned_schema_fails_without_filling_missing_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path, core = _create_current_database(tmp_path, monkeypatch)
    core.close()
    _reset_singleton()
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute("DROP TABLE history")
        connection.execute("PRAGMA user_version = 0")

    with pytest.raises(CoreSchemaError, match="partial"):
        DbCore(db_path="core.db", localization_manager=DummyLocalization())
    with closing(sqlite3.connect(db_path)) as connection:
        assert "history" not in _user_tables(connection)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0


def test_unrelated_unversioned_database_is_not_claimed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = _configure_runtime(tmp_path, monkeypatch)
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute("CREATE TABLE foreign_app (value TEXT)")

    with pytest.raises(CoreSchemaError, match="non-core schema objects"):
        DbCore(db_path="core.db", localization_manager=DummyLocalization())
    with closing(sqlite3.connect(db_path)) as connection:
        assert _user_tables(connection) == frozenset({"foreign_app"})
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0




def test_unrelated_unversioned_view_is_not_claimed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = _configure_runtime(tmp_path, monkeypatch)
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute("CREATE VIEW foreign_view AS SELECT 1 AS value")

    with pytest.raises(CoreSchemaError, match="non-core schema objects"):
        DbCore(db_path="core.db", localization_manager=DummyLocalization())
    with closing(sqlite3.connect(db_path)) as connection:
        row = connection.execute(
            "SELECT type FROM sqlite_master WHERE name = 'foreign_view'"
        ).fetchone()
        assert row == ("view",)
        assert _user_tables(connection) == frozenset()


def test_schema_identifier_controls_are_rejected_before_adoption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = _configure_runtime(tmp_path, monkeypatch)
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute('CREATE TABLE "foreign\nname" (value TEXT)')

    with pytest.raises(CoreSchemaError, match="invalid identifier"):
        DbCore(db_path="core.db", localization_manager=DummyLocalization())
    with closing(sqlite3.connect(db_path)) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'"
        ).fetchone()[0] == 1


def test_schema_table_inventory_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = _configure_runtime(tmp_path, monkeypatch)
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute("CREATE TABLE first_table (value TEXT)")
        connection.execute("CREATE TABLE second_table (value TEXT)")
    monkeypatch.setattr(core_schema_module, "_MAX_SCHEMA_TABLES", 1)

    with pytest.raises(CoreSchemaError, match="inventory exceeds"):
        DbCore(db_path="core.db", localization_manager=DummyLocalization())
    with closing(sqlite3.connect(db_path)) as connection:
        assert _user_tables(connection) == frozenset({"first_table", "second_table"})


def test_future_schema_version_fails_before_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = _configure_runtime(tmp_path, monkeypatch)
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute("CREATE TABLE future_state (value TEXT)")
        connection.execute("PRAGMA user_version = 99")

    with pytest.raises(CoreSchemaError, match="newer"):
        DbCore(db_path="core.db", localization_manager=DummyLocalization())
    with closing(sqlite3.connect(db_path)) as connection:
        assert _user_tables(connection) == frozenset({"future_state"})
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 99


def test_current_version_with_missing_table_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path, core = _create_current_database(tmp_path, monkeypatch)
    core.close()
    _reset_singleton()
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute("DROP TABLE history")

    with pytest.raises(CoreSchemaError, match="incomplete"):
        DbCore(db_path="core.db", localization_manager=DummyLocalization())
    with closing(sqlite3.connect(db_path)) as connection:
        assert "history" not in _user_tables(connection)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == CORE_SCHEMA_VERSION


def test_incompatible_core_table_shape_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path, core = _create_current_database(tmp_path, monkeypatch)
    core.close()
    _reset_singleton()
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute("ALTER TABLE history ADD COLUMN unexpected TEXT")

    with pytest.raises(CoreSchemaError, match="incompatible shape: history"):
        DbCore(db_path="core.db", localization_manager=DummyLocalization())


def test_unexpected_unique_constraint_and_trigger_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path, core = _create_current_database(tmp_path, monkeypatch)
    core.close()
    _reset_singleton()
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute('CREATE UNIQUE INDEX "history""path" ON history(path)')

    with pytest.raises(CoreSchemaError, match="unique constraints"):
        DbCore(db_path="core.db", localization_manager=DummyLocalization())
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute('DROP INDEX "history""path"')
        connection.execute(
            "CREATE TRIGGER history_shadow AFTER INSERT ON history BEGIN SELECT 1; END"
        )
    _reset_singleton()
    with pytest.raises(CoreSchemaError, match="unexpected trigger"):
        DbCore(db_path="core.db", localization_manager=DummyLocalization())


def test_auxiliary_component_tables_and_nonunique_indexes_are_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path, core = _create_current_database(tmp_path, monkeypatch)
    core.close()
    _reset_singleton()
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute("CREATE TABLE component_private (value TEXT)")
        connection.execute("CREATE INDEX history_path_lookup ON history(path)")

    reopened = DbCore(db_path="core.db", localization_manager=DummyLocalization())
    assert reopened.conn is not None
    assert "component_private" in _user_tables(reopened.conn)


def test_playlist_mirror_component_coexists_with_core_versioning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _db_path, core = _create_current_database(tmp_path, monkeypatch)
    manager = PlaylistManager(core)
    assert manager.mirror_maintenance.schema_version() == 2
    assert core.conn is not None
    assert core.conn.execute("PRAGMA user_version").fetchone()[0] == CORE_SCHEMA_VERSION
    core.close()
    _reset_singleton()

    reopened = DbCore(db_path="core.db", localization_manager=DummyLocalization())
    assert PlaylistManager(reopened).mirror_maintenance.schema_version() == 2


def test_missing_target_validator_rolls_back_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = _configure_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(core_schema_module, "_SCHEMA_VALIDATORS", {})

    with pytest.raises(CoreSchemaError, match="no registered validator"):
        DbCore(db_path="core.db", localization_manager=DummyLocalization())
    with closing(sqlite3.connect(db_path)) as connection:
        assert _user_tables(connection) == frozenset()
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0


def test_migration_registry_gap_fails_without_schema_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = _configure_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(core_schema_module, "_CORE_MIGRATIONS", ())

    with pytest.raises(CoreSchemaError, match="no unique step"):
        DbCore(db_path="core.db", localization_manager=DummyLocalization())
    with closing(sqlite3.connect(db_path)) as connection:
        assert _user_tables(connection) == frozenset()
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0


def test_migration_failure_rolls_back_ddl_and_version_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = _configure_runtime(tmp_path, monkeypatch)
    original = core_schema_module._CORE_TABLE_SQL
    broken = original[:3] + ("CREATE TABLE broken (",) + original[3:]
    monkeypatch.setattr(core_schema_module, "_CORE_TABLE_SQL", broken)

    with pytest.raises(CoreSchemaError, match="migration failed"):
        DbCore(db_path="core.db", localization_manager=DummyLocalization())
    with closing(sqlite3.connect(db_path)) as connection:
        assert _user_tables(connection) == frozenset()
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0


def test_version_marker_failure_rolls_back_created_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = _configure_runtime(tmp_path, monkeypatch)

    def fail_marker(connection: object, version: int) -> None:
        del connection, version
        raise CoreSchemaError("injected marker failure")

    monkeypatch.setattr(core_schema_module, "_set_user_version", fail_marker)
    with pytest.raises(CoreSchemaError, match="marker failure"):
        DbCore(db_path="core.db", localization_manager=DummyLocalization())
    with closing(sqlite3.connect(db_path)) as connection:
        assert _user_tables(connection) == frozenset()
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0


def test_schema_runner_requires_owner_provided_transaction() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        with pytest.raises(CoreSchemaError, match="active owner-provided"):
            ensure_core_schema(connection)
        assert _user_tables(connection) == frozenset()


def test_caller_controls_schema_transaction_commit_and_rollback() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.execute("BEGIN IMMEDIATE")
        assert ensure_core_schema(connection) == CORE_SCHEMA_VERSION
        assert connection.in_transaction
        connection.rollback()
        assert _user_tables(connection) == frozenset()
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0

        connection.execute("BEGIN IMMEDIATE")
        assert ensure_core_schema(connection) == CORE_SCHEMA_VERSION
        connection.commit()
        assert _user_tables(connection) == CORE_TABLES
        assert connection.execute("PRAGMA user_version").fetchone()[0] == CORE_SCHEMA_VERSION


def test_concurrent_initialization_observes_one_complete_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_runtime(tmp_path, monkeypatch)
    cores: list[DbCore] = []
    errors: list[BaseException] = []

    def initialize() -> None:
        try:
            cores.append(DbCore(db_path="core.db", localization_manager=localization))
        except Exception as error:
            errors.append(error)

    localization = DummyLocalization()
    threads = [threading.Thread(target=initialize) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5.0)

    assert errors == []
    assert len(cores) == 16
    assert len({id(core) for core in cores}) == 1
    assert all(not thread.is_alive() for thread in threads)
    connection = cores[0].conn
    assert connection is not None
    assert _user_tables(connection) == CORE_TABLES
    assert connection.execute("PRAGMA user_version").fetchone()[0] == CORE_SCHEMA_VERSION
