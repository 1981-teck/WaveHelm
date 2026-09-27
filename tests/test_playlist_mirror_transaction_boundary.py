from __future__ import annotations

import ast
import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

import src.model.component_database.db_core as db_core_module
import src.model.component_database.playlist_mirror_maintenance as maintenance_module
from src.model.component_database.db_core import DbCore
from src.model.component_database.playlist_manager import PlaylistManager
from src.model.component_database.playlist_mirror_journal import (
    MAX_MIRROR_ATTEMPTS,
    PlaylistMirrorJournalCorruptionError,
    PlaylistMirrorJournalError,
)
from src.model.component_database.playlist_mirror_maintenance import (
    PlaylistMirrorMaintenanceCorruptionError,
    PlaylistMirrorRepairRejectedError,
)
from src.model.component_database.playlist_mirror_schema import (
    PLAYLIST_MIRROR_REPAIR_TABLE,
)
from src.utils.exceptions import DatabaseError, IntegrityError


class DummyLocalization:
    def get_text(self, key: str, **_kwargs: object) -> str:
        return key


class InjectedDomainError(RuntimeError):
    """Test-only domain failure used to verify rollback semantics."""


class CommitFailureConnection:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    @property
    def in_transaction(self) -> bool:
        return self._connection.in_transaction

    def execute(
        self, query: str, params: tuple[object, ...] | None = None
    ) -> sqlite3.Cursor:
        if params is None:
            return self._connection.execute(query)
        return self._connection.execute(query, params)

    def commit(self) -> None:
        raise sqlite3.OperationalError("injected commit failure")

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()


class RollbackFailureConnection(CommitFailureConnection):
    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        raise sqlite3.OperationalError("injected rollback failure")


@pytest.fixture(autouse=True)
def reset_db_core_singleton() -> Iterator[None]:
    instance = getattr(DbCore, "_instance", None)
    if instance is not None and getattr(instance, "conn", None) is not None:
        instance.close()
    DbCore._instance = None
    yield
    instance = getattr(DbCore, "_instance", None)
    if instance is not None and getattr(instance, "conn", None) is not None:
        instance.close()
    DbCore._instance = None


def _make_core(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> DbCore:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_core_module, "get_user_data_dir", lambda: data_dir)
    return DbCore(db_path="boundary.db", localization_manager=DummyLocalization())


def _make_manager(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[PlaylistManager, DbCore]:
    core = _make_core(tmp_path, monkeypatch)
    return PlaylistManager(core), core


def _connection(core: DbCore) -> sqlite3.Connection:
    connection = core.conn
    assert isinstance(connection, sqlite3.Connection)
    return connection


def _count(connection: sqlite3.Connection, table: str) -> int:
    row = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
    assert row is not None and isinstance(row[0], int)
    return row[0]


def _exhaust(manager: PlaylistManager, playlist_id: int) -> None:
    for attempt in range(MAX_MIRROR_ATTEMPTS):
        assert manager.mirror_journal.record_failure(
            playlist_id,
            RuntimeError(f"failure-{attempt + 1}"),
            now=float(attempt + 1),
        )


def test_shared_boundaries_commit_rollback_and_revoke_capabilities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    core = _make_core(tmp_path, monkeypatch)
    core._execute_write("CREATE TABLE boundary_items (value TEXT UNIQUE)")

    with core.shared_transaction() as transaction:
        assert not hasattr(transaction, "commit")
        assert not hasattr(transaction, "rollback")
        retained_cursor = transaction.execute(
            "INSERT INTO boundary_items VALUES (?)", ("committed",)
        )
        retained_transaction = transaction

    with pytest.raises(DatabaseError, match="no longer active"):
        retained_transaction.execute("INSERT INTO boundary_items VALUES ('late')")
    with pytest.raises(DatabaseError, match="no longer active"):
        retained_cursor.fetchone()

    with pytest.raises(InjectedDomainError):
        with core.shared_transaction() as transaction:
            transaction.execute("INSERT INTO boundary_items VALUES (?)", ("rolled-back",))
            raise InjectedDomainError("abort")

    with core.shared_connection() as lease:
        retained_read_cursor = lease.execute("SELECT COUNT(*) FROM boundary_items")
        assert retained_read_cursor.fetchone()[0] == 1
        retained_lease = lease

    with pytest.raises(DatabaseError, match="no longer active"):
        retained_lease.execute("SELECT 1")
    with pytest.raises(DatabaseError, match="no longer active"):
        retained_read_cursor.fetchone()


def test_transaction_capability_rejects_cross_thread_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    core = _make_core(tmp_path, monkeypatch)
    core._execute_write("CREATE TABLE thread_items (value INTEGER)")
    errors: list[BaseException] = []

    with core.shared_transaction() as transaction:
        cursor = transaction.execute("SELECT 1")

        def cross_thread_write() -> None:
            for action in (
                lambda: transaction.execute("INSERT INTO thread_items VALUES (1)"),
                cursor.fetchone,
            ):
                try:
                    action()
                except Exception as error:
                    errors.append(error)

        worker = threading.Thread(target=cross_thread_write)
        worker.start()
        worker.join(timeout=5)
        assert not worker.is_alive()
        transaction.execute("INSERT INTO thread_items VALUES (2)")

    assert len(errors) == 2
    assert all(isinstance(error, DatabaseError) for error in errors)
    assert all("cross thread" in str(error) for error in errors)
    assert _count(_connection(core), "thread_items") == 1


def test_shared_connection_rolls_back_a_leaked_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    core = _make_core(tmp_path, monkeypatch)
    core._execute_write("CREATE TABLE leaked_items (value INTEGER)")

    with pytest.raises(DatabaseError, match="leaked an active transaction"):
        with core.shared_connection() as lease:
            lease.execute("BEGIN IMMEDIATE")
            lease.execute("INSERT INTO leaked_items VALUES (1)")

    assert _count(_connection(core), "leaked_items") == 0


def test_unowned_transaction_is_rolled_back_and_rejected_on_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    core = _make_core(tmp_path, monkeypatch)
    core._execute_write("CREATE TABLE unowned_items (value INTEGER)")
    connection = _connection(core)
    connection.execute("BEGIN IMMEDIATE")
    connection.execute("INSERT INTO unowned_items VALUES (1)")

    with pytest.raises(DatabaseError, match="unowned active transaction"):
        with core.shared_connection():
            raise AssertionError("boundary body must not run")

    assert _count(connection, "unowned_items") == 0


def test_transaction_maps_integrity_errors_and_rejects_invalid_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    core = _make_core(tmp_path, monkeypatch)
    core._execute_write("CREATE TABLE unique_items (value TEXT UNIQUE)")
    core._execute_write("INSERT INTO unique_items VALUES (?)", ("one",))

    with pytest.raises(IntegrityError, match="integrity constraint"):
        with core.shared_transaction() as transaction:
            transaction.execute("INSERT INTO unique_items VALUES (?)", ("one",))

    with core.shared_transaction() as transaction:
        for statement in (
            "/* owner bypass */ COMMIT",
            "; /* owner bypass */ COMMIT",
            "\ufeffCOMMIT",
        ):
            with pytest.raises(DatabaseError, match="boundary-owned"):
                transaction.execute(statement)
        with pytest.raises(DatabaseError, match="Standalone database read"):
            core._execute_query("SELECT 1", fetch_one=True)
        with pytest.raises(DatabaseError, match="Standalone database write"):
            core._execute_write("INSERT INTO unique_items VALUES ('bypass')")
        with pytest.raises(DatabaseError, match="Cannot close"):
            core.close()
        transaction.execute("INSERT INTO unique_items VALUES (?)", ("two",))

    with pytest.raises(ValueError, match="unsupported"):
        core.shared_transaction("READ WRITE")  # type: ignore[arg-type]
    assert _count(_connection(core), "unique_items") == 2


def test_boundary_rejects_cross_thread_exit_without_committing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    core = _make_core(tmp_path, monkeypatch)
    core._execute_write("CREATE TABLE exit_items (value INTEGER)")
    boundary = core.shared_transaction()
    transaction = boundary.__enter__()
    transaction.execute("INSERT INTO exit_items VALUES (1)")
    errors: list[BaseException] = []

    def wrong_thread_exit() -> None:
        try:
            boundary.__exit__(None, None, None)
        except Exception as error:
            errors.append(error)

    worker = threading.Thread(target=wrong_thread_exit)
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], DatabaseError)
    assert "another thread" in str(errors[0])
    assert _count(_connection(core), "exit_items") == 1
    boundary.__exit__(InjectedDomainError, InjectedDomainError("abort"), None)
    assert _count(_connection(core), "exit_items") == 0


def test_rollback_failure_invalidates_the_shared_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    core = _make_core(tmp_path, monkeypatch)
    core._execute_write("CREATE TABLE rollback_items (value INTEGER)")
    real_connection = _connection(core)
    core.conn = RollbackFailureConnection(real_connection)  # type: ignore[assignment]

    with pytest.raises(DatabaseError, match="rollback failed"):
        with core.shared_transaction() as transaction:
            transaction.execute("INSERT INTO rollback_items VALUES (1)")
            raise InjectedDomainError("force rollback")

    assert core.conn is None
    core.connect()
    assert _count(_connection(core), "rollback_items") == 0


def test_playlist_mirror_modules_use_only_public_boundaries() -> None:
    paths = (
        Path("src/model/component_database/playlist_mirror_journal.py"),
        Path("src/model/component_database/playlist_mirror_maintenance.py"),
    )
    boundary_calls: set[str] = set()
    forbidden_attributes: list[tuple[str, str, int]] = []
    forbidden_calls: list[tuple[str, str, int]] = []

    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                if node.attr in {"conn", "_db_lock", "_connection", "_cursor"}:
                    forbidden_attributes.append((str(path), node.attr, node.lineno))
                if node.attr in {"shared_connection", "shared_transaction"}:
                    boundary_calls.add(node.attr)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in {"commit", "rollback"}:
                    forbidden_calls.append((str(path), node.func.attr, node.lineno))

    assert forbidden_attributes == []
    assert forbidden_calls == []
    assert boundary_calls == {"shared_connection", "shared_transaction"}


def test_commit_failure_does_not_acknowledge_a_journal_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, core = _make_manager(tmp_path, monkeypatch)
    playlist_id = manager.create_playlist("Commit Failure")
    real_connection = _connection(core)
    failure_connection = CommitFailureConnection(real_connection)
    monkeypatch.setattr(core, "_connection", lambda: failure_connection)

    with pytest.raises(PlaylistMirrorJournalError, match="commit failure"):
        manager.mirror_journal.acknowledge(playlist_id)

    row = real_connection.execute(
        "SELECT 1 FROM playlist_mirror_outbox WHERE playlist_id = ?",
        (playlist_id,),
    ).fetchone()
    assert row is not None


def test_corrupt_existing_job_rolls_back_the_canonical_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, core = _make_manager(tmp_path, monkeypatch)
    playlist_id = manager.create_playlist("Original")
    connection = _connection(core)
    connection.execute(
        "UPDATE playlist_mirror_outbox SET playlist_name = ? WHERE playlist_id = ?",
        (sqlite3.Binary(b"not-text"), playlist_id),
    )
    connection.commit()

    with pytest.raises(DatabaseError, match="text fields"):
        manager.rename_playlist(playlist_id, "Changed")

    playlist = manager.get_playlist_by_id(playlist_id)
    assert playlist is not None and playlist["name"] == "Original"


def test_record_failure_rejects_non_integer_persisted_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, core = _make_manager(tmp_path, monkeypatch)
    playlist_id = manager.create_playlist("Typed Attempts")
    connection = _connection(core)
    connection.execute("PRAGMA ignore_check_constraints=ON")
    connection.execute(
        "UPDATE playlist_mirror_outbox SET attempt_count = ? WHERE playlist_id = ?",
        (sqlite3.Binary(b"1"), playlist_id),
    )
    connection.commit()

    with pytest.raises(
        PlaylistMirrorJournalCorruptionError, match="stored non-negative integer"
    ):
        manager.mirror_journal.record_failure(
            playlist_id, RuntimeError("retry"), now=10.0
        )


def test_rearm_rolls_back_when_the_audit_insert_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, core = _make_manager(tmp_path, monkeypatch)
    playlist_id = manager.create_playlist("Ignored Audit")
    _exhaust(manager, playlist_id)
    connection = _connection(core)
    connection.execute(
        f"""
        CREATE TRIGGER ignore_repair_insert
        BEFORE INSERT ON {PLAYLIST_MIRROR_REPAIR_TABLE}
        BEGIN
            SELECT RAISE(IGNORE);
        END
        """
    )
    connection.commit()

    with pytest.raises(
        PlaylistMirrorMaintenanceCorruptionError, match="audit was not recorded"
    ):
        manager.mirror_maintenance.rearm_exhausted(
            (playlist_id,), actor="operator", reason="fault injection", now=100.0
        )

    job = manager.mirror_journal.get_job(playlist_id)
    assert job is not None and job.attempt_count == MAX_MIRROR_ATTEMPTS
    assert manager.mirror_maintenance.inspect_repair_events() == ()


def test_repair_batch_larger_than_audit_capacity_is_rejected_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _core = _make_manager(tmp_path, monkeypatch)
    first = manager.create_playlist("First Exhausted")
    second = manager.create_playlist("Second Exhausted")
    _exhaust(manager, first)
    _exhaust(manager, second)
    monkeypatch.setattr(maintenance_module, "MAX_REPAIR_AUDIT_EVENTS", 1)

    with pytest.raises(PlaylistMirrorRepairRejectedError, match="audit capacity"):
        manager.mirror_maintenance.rearm_exhausted(
            (first, second), actor="operator", reason="oversized audit batch", now=200.0
        )

    assert manager.mirror_maintenance.inspect_repair_events() == ()
    assert manager.mirror_journal.get_job(first).attempt_count == MAX_MIRROR_ATTEMPTS
    assert manager.mirror_journal.get_job(second).attempt_count == MAX_MIRROR_ATTEMPTS
