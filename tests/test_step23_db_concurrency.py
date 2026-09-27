from __future__ import annotations

from collections.abc import Iterator
import logging
import os
import sqlite3
import shutil
import threading
import time
from pathlib import Path

import pytest

import src.model.component_database.db_core as db_core_module
from src.model.component_database.db_core import DbCore
from src.model.component_database.db_primitives import DbWriteResult
from src.model.component_database.eq_preset_manager import EqPresetManager
from src.utils.exceptions import DatabaseError, NotFoundError

RUNTIME_ROOT = Path(__file__).resolve().parent / "_step23_db_runtime"


class DummyLocalization:
    def get_text(self, key: str, **kwargs: object) -> str:
        return key


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


def _runtime_dir(name: str) -> Path:
    target = RUNTIME_ROOT / name
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    return target


def _core(monkeypatch: pytest.MonkeyPatch, name: str) -> DbCore:
    runtime_dir = _runtime_dir(name)
    monkeypatch.setattr(db_core_module, "get_user_data_dir", lambda: runtime_dir)
    return DbCore(db_path="unit.db", localization_manager=DummyLocalization())


def test_manager_uses_same_call_rowcount_during_concurrent_insert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later insert must not turn a zero-row manager delete into success."""
    core = _core(monkeypatch, "rowcount_race")
    manager = EqPresetManager(core)
    delete_committed = threading.Event()
    insert_committed = threading.Event()
    original_write = core._execute_write
    outcome: dict[str, object] = {}

    def hooked_write(
        query: str,
        params: tuple[object, ...] | None = None,
    ) -> DbWriteResult:
        result = original_write(query, params)
        if query.lstrip().upper().startswith("DELETE FROM CUSTOM_EQ_PRESETS"):
            delete_committed.set()
            if not insert_committed.wait(3.0):
                raise RuntimeError("rowcount race test timed out")
        return result

    core._execute_write = hooked_write  # type: ignore[method-assign]

    def delete_missing() -> None:
        try:
            manager.delete_custom_eq_preset(999_999)
        except (NotFoundError, DatabaseError) as error:
            outcome["error"] = error

    worker = threading.Thread(target=delete_missing)
    worker.start()
    assert delete_committed.wait(3.0)
    original_write(
        "INSERT INTO custom_eq_presets (name, settings, created_at) VALUES (?, ?, ?)",
        ("Concurrent", "{}", "2026-09-02T00:00:00"),
    )
    insert_committed.set()
    worker.join(3.0)

    assert not worker.is_alive()
    assert isinstance(outcome.get("error"), NotFoundError)


def test_two_durable_connections_can_be_open_concurrently(monkeypatch: pytest.MonkeyPatch) -> None:
    """An isolated durable context must not retain the shared connection gate."""
    core = _core(monkeypatch, "durable_concurrency")
    first_entered = threading.Event()
    second_entered = threading.Event()
    release_first = threading.Event()
    errors: list[Exception] = []

    def first() -> None:
        try:
            with core.durable_write_connection():
                first_entered.set()
                release_first.wait(3.0)
        except DatabaseError as error:
            errors.append(error)

    def second() -> None:
        try:
            with core.durable_write_connection():
                second_entered.set()
        except DatabaseError as error:
            errors.append(error)

    first_thread = threading.Thread(target=first)
    second_thread = threading.Thread(target=second)
    first_thread.start()
    assert first_entered.wait(3.0)
    second_thread.start()
    assert second_entered.wait(3.0)
    release_first.set()
    first_thread.join(3.0)
    second_thread.join(3.0)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert errors == []


def test_incompatible_singleton_reinitialization_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_dir = _runtime_dir("singleton_path")
    monkeypatch.setattr(db_core_module, "get_user_data_dir", lambda: runtime_dir)
    localization = DummyLocalization()
    core = DbCore(db_path="first.db", localization_manager=localization)

    assert DbCore(db_path="first.db", localization_manager=localization) is core
    with pytest.raises(DatabaseError, match="different database path"):
        DbCore(db_path="second.db", localization_manager=localization)
    with pytest.raises(DatabaseError, match="different localization manager"):
        DbCore(db_path="first.db", localization_manager=DummyLocalization())
    assert core.db_path == os.path.normcase(str((runtime_dir / "first.db").absolute()))
    assert Path(core.db_path).samefile(runtime_dir / "first.db")


class _PragmaRow:
    def __init__(self, value: object) -> None:
        self._value = value

    def fetchone(self) -> tuple[object]:
        return (self._value,)


class _CleanupFailureConnection:
    in_transaction = True

    def execute(self, query: str) -> _PragmaRow:
        if "journal_mode" in query:
            return _PragmaRow("wal")
        return _PragmaRow(2)

    def rollback(self) -> None:
        raise sqlite3.OperationalError("injected rollback failure")

    def close(self) -> None:
        raise sqlite3.OperationalError("injected close failure")


def test_durable_cleanup_failures_are_not_silenced(monkeypatch: pytest.MonkeyPatch) -> None:
    core = _core(monkeypatch, "cleanup_failure")
    fake = _CleanupFailureConnection()
    monkeypatch.setattr(core, "_open_connection", lambda *, synchronous: fake)

    with pytest.raises(DatabaseError) as captured:
        with core.durable_write_connection():
            pass

    message = str(captured.value)
    assert "rollback failure" in message
    assert "close failure" in message


def test_durable_cleanup_does_not_mask_body_exception(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    core = _core(monkeypatch, "cleanup_body_exception")
    fake = _CleanupFailureConnection()
    monkeypatch.setattr(core, "_open_connection", lambda *, synchronous: fake)
    caplog.set_level(logging.ERROR, logger=db_core_module.logger.name)

    with pytest.raises(RuntimeError, match="body failure"):
        with core.durable_write_connection():
            raise RuntimeError("body failure")

    assert "rollback failure" in caplog.text
    assert "close failure" in caplog.text


class _FailingCursor:
    rowcount = 0
    lastrowid = None

    def execute(
        self, query: str, params: tuple[object, ...] | None = None
    ) -> None:
        raise sqlite3.OperationalError("injected statement failure")


class _SharedRollbackFailureConnection:
    def cursor(self) -> _FailingCursor:
        return _FailingCursor()

    def rollback(self) -> None:
        raise sqlite3.OperationalError("injected shared rollback failure")

    def close(self) -> None:
        return None


def test_shared_rollback_failure_invalidates_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    core = _core(monkeypatch, "shared_rollback_failure")
    fake = _SharedRollbackFailureConnection()
    original = core.conn
    assert original is not None
    original.close()
    core.conn = fake  # type: ignore[assignment]

    with pytest.raises(DatabaseError, match="rollback failed"):
        core._execute_write("DELETE FROM missing")

    assert core.conn is None


def test_shared_gate_releases_waiter_after_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    core = _core(monkeypatch, "gate_release")
    first_entered = threading.Event()
    second_entered = threading.Event()

    def first() -> None:
        try:
            with core._db_lock:
                first_entered.set()
                raise RuntimeError("injected")
        except RuntimeError:
            return

    def second() -> None:
        assert first_entered.wait(3.0)
        with core._db_lock:
            second_entered.set()

    first_thread = threading.Thread(target=first)
    second_thread = threading.Thread(target=second)
    first_thread.start()
    second_thread.start()
    first_thread.join(3.0)
    second_thread.join(3.0)

    assert second_entered.is_set()
    assert not first_thread.is_alive()
    assert not second_thread.is_alive()

def test_shared_gate_supports_bounded_same_thread_reentry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core = _core(monkeypatch, "gate_reentry")

    with core._db_lock:
        with core._db_lock:
            row = core._execute_query("SELECT 1 AS value", fetch_one=True)

    assert row is not None and row["value"] == 1


class _ArgumentFailureCursor:
    rowcount = 0
    lastrowid = None

    def execute(
        self, query: str, params: tuple[object, ...] | None = None
    ) -> None:
        raise TypeError("injected argument failure")


class _ArgumentRollbackFailureConnection(_SharedRollbackFailureConnection):
    def cursor(self) -> _ArgumentFailureCursor:
        return _ArgumentFailureCursor()


def test_argument_error_is_preserved_when_rollback_also_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core = _core(monkeypatch, "argument_rollback_failure")
    fake = _ArgumentRollbackFailureConnection()
    original = core.conn
    assert original is not None
    original.close()
    core.conn = fake  # type: ignore[assignment]

    with pytest.raises(DatabaseError) as captured:
        core._execute_write("DELETE FROM missing", (object(),))

    assert "rollback failed" in str(captured.value)
    assert "injected argument failure" in str(captured.value)
    assert core.conn is None


def test_connection_gate_does_not_hold_its_condition_mutex_during_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core = _core(monkeypatch, "gate_mutex_scope")
    acquired = threading.Event()

    def inspect_condition_mutex() -> None:
        if core._db_lock._condition.acquire(blocking=False):
            acquired.set()
            core._db_lock._condition.release()

    with core._db_lock:
        observer = threading.Thread(target=inspect_condition_mutex)
        observer.start()
        observer.join(3.0)

    assert not observer.is_alive()
    assert acquired.is_set()


def test_shared_connection_serializes_sixty_four_concurrent_writers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core = _core(monkeypatch, "shared_writer_stress")
    start = threading.Event()
    result_lock = threading.Lock()
    outcomes: list[tuple[int, int]] = []
    errors: list[Exception] = []

    def write(index: int) -> None:
        try:
            if not start.wait(3.0):
                raise RuntimeError("shared writer stress start timed out")
            result = core._execute_write(
                "INSERT INTO custom_eq_presets (name, settings, created_at) "
                "VALUES (?, ?, ?)",
                (f"Preset-{index}", "{}", "2026-09-02T00:00:00"),
            )
            with result_lock:
                outcomes.append((result.rowcount, int(result.lastrowid or 0)))
        except (DatabaseError, RuntimeError) as error:
            with result_lock:
                errors.append(error)

    threads = [threading.Thread(target=write, args=(index,)) for index in range(64)]
    for thread in threads:
        thread.start()
    start.set()
    for thread in threads:
        thread.join(10.0)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert len(outcomes) == 64
    assert all(rowcount == 1 for rowcount, _ in outcomes)
    assert len({lastrowid for _, lastrowid in outcomes}) == 64
    count = core._execute_query(
        "SELECT COUNT(*) AS total FROM custom_eq_presets", fetch_one=True
    )
    assert count is not None and count["total"] == 64
    assert not hasattr(core, "_last_changes")


class _ArgumentRollbackSuccessConnection:
    def __init__(self) -> None:
        self.rolled_back = False

    def cursor(self) -> _ArgumentFailureCursor:
        return _ArgumentFailureCursor()

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        return None


def test_argument_error_is_reraised_after_successful_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core = _core(monkeypatch, "argument_rollback_success")
    fake = _ArgumentRollbackSuccessConnection()
    original = core.conn
    assert original is not None
    original.close()
    core.conn = fake  # type: ignore[assignment]

    with pytest.raises(TypeError, match="injected argument failure"):
        core._execute_write("DELETE FROM missing", (object(),))

    assert fake.rolled_back
    assert core.conn is fake
