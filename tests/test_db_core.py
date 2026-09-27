from __future__ import annotations

from collections.abc import Iterator
import logging
import sqlite3
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.model.component_database.db_core as db_core_module
from src.model.component_database.db_core import DbCore
from src.model.component_database.db_primitives import DbWriteResult
from src.utils.exceptions import DatabaseError, IntegrityError


RUNTIME_ROOT = Path(__file__).resolve().parent / "_db_core_runtime"


class DummyLocalization:
    def get_text(self, key: str, **kwargs: object) -> str:
        return key


@pytest.fixture(autouse=True)
def reset_db_core_singleton() -> Iterator[None]:
    instance = getattr(DbCore, '_instance', None)
    if instance is not None and getattr(instance, 'conn', None) is not None:
        instance.close()
    DbCore._instance = None
    yield
    instance = getattr(DbCore, '_instance', None)
    if instance is not None and getattr(instance, 'conn', None) is not None:
        instance.close()
    DbCore._instance = None


def _runtime_dir(name: str) -> Path:
    target = RUNTIME_ROOT / name
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    return target


def test_db_core_context_execute_query_and_integrity(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_dir = _runtime_dir('query_and_integrity')
    monkeypatch.setattr(db_core_module, 'get_user_data_dir', lambda: runtime_dir)

    core = DbCore(db_path='unit.db', localization_manager=DummyLocalization())
    assert Path(core.db_path) == runtime_dir / 'unit.db'
    assert core.conn is not None

    with core as entered:
        write_result = entered._execute_write(
            "INSERT INTO playlists "
            "(name, canonical_name, description, cover_art, creation_date, last_modified) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ('Alpha', 'alpha', None, None, '2024-01-01', '2024-01-01'),
        )
        assert isinstance(write_result, DbWriteResult)
        assert write_result.rowcount == 1
        assert write_result.lastrowid is not None
        row = entered._execute_query(
            "SELECT name FROM playlists WHERE name = ?",
            ('Alpha',),
            fetch_one=True,
        )
        assert row['name'] == 'Alpha'

        with pytest.raises(IntegrityError) as captured:
            entered._execute_query(
                "INSERT INTO playlists "
                "(name, canonical_name, description, cover_art, creation_date, last_modified) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ('Alpha', 'alpha', None, None, '2024-01-01', '2024-01-01'),
            )
        assert not isinstance(captured.value, sqlite3.IntegrityError)

        count_row = entered._execute_query(
            "SELECT COUNT(*) AS total FROM playlists WHERE name = ?",
            ('Alpha',),
            fetch_one=True,
        )
        assert count_row['total'] == 1

    assert core.conn is None


def test_execute_write_returns_exact_cursor_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_dir = _runtime_dir('write_result')
    monkeypatch.setattr(db_core_module, 'get_user_data_dir', lambda: runtime_dir)
    core = DbCore(db_path='unit.db', localization_manager=DummyLocalization())

    inserted = core._execute_write(
        "INSERT INTO custom_eq_presets (name, settings, created_at) VALUES (?, ?, ?)",
        ('Rock', '{}', '2026-09-02T00:00:00'),
    )
    assert isinstance(inserted, DbWriteResult)
    assert inserted.rowcount == 1
    assert inserted.lastrowid is not None and inserted.lastrowid > 0

    missing = core._execute_write(
        "DELETE FROM custom_eq_presets WHERE id = ?",
        (999_999,),
    )
    assert missing == DbWriteResult(rowcount=0, lastrowid=None)
    multiline = core._execute_write(
        "INSERT\nINTO custom_eq_presets (name, settings, created_at) VALUES (?, ?, ?)",
        ('Jazz', '{}', '2026-09-02T00:00:01'),
    )
    assert multiline.lastrowid is not None

    updated = core._execute_write(
        "UPDATE custom_eq_presets SET settings = ? WHERE name = ?",
        ('{\"gain\": 1}', 'Rock'),
    )
    selected = core._execute_query(
        "SELECT settings FROM custom_eq_presets WHERE name = ?",
        ('Rock',),
        fetch_one=True,
    )
    assert selected is not None and selected['settings'] == '{"gain": 1}'
    assert updated == DbWriteResult(rowcount=1, lastrowid=None)
    assert inserted.rowcount == 1
    assert not hasattr(core, '_legacy_write_state')
    assert not hasattr(core, '_last_changes')
    assert not hasattr(core, '_clear_legacy_write_result')


def test_execute_query_rejects_conflicting_fetch_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_dir = _runtime_dir('fetch_modes')
    monkeypatch.setattr(db_core_module, 'get_user_data_dir', lambda: runtime_dir)
    core = DbCore(db_path='unit.db', localization_manager=DummyLocalization())

    with pytest.raises(ValueError, match='mutually exclusive'):
        core._execute_query('SELECT 1', fetch_one=True, fetch_all=True)


def test_falsey_localization_manager_is_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_dir = _runtime_dir('falsey_localization')
    monkeypatch.setattr(db_core_module, 'get_user_data_dir', lambda: runtime_dir)

    class FalseyLocalization(DummyLocalization):
        def __bool__(self) -> bool:
            return False

    localization = FalseyLocalization()
    core = DbCore(db_path='unit.db', localization_manager=localization)
    assert core.localization_manager is localization


def test_failed_initialization_allows_singleton_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_dir = _runtime_dir('initialization_retry')
    monkeypatch.setattr(db_core_module, 'get_user_data_dir', lambda: runtime_dir)
    real_open = DbCore._open_connection

    def fail_open(self: DbCore, *, synchronous: str) -> sqlite3.Connection:
        raise sqlite3.OperationalError(f'injected open failure: {synchronous}')

    monkeypatch.setattr(DbCore, '_open_connection', fail_open)
    with pytest.raises(DatabaseError, match='connection error'):
        DbCore(db_path='unit.db', localization_manager=DummyLocalization())
    failed_instance = DbCore._instance
    assert failed_instance is not None
    assert not failed_instance._initialized

    monkeypatch.setattr(DbCore, '_open_connection', real_open)
    core = DbCore(db_path='unit.db', localization_manager=DummyLocalization())
    assert core is failed_instance
    assert core.conn is not None


def test_database_path_is_lexically_canonical(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_dir = _runtime_dir('canonical_path')
    monkeypatch.setattr(db_core_module, 'get_user_data_dir', lambda: runtime_dir)
    localization = DummyLocalization()

    core = DbCore(db_path='nested/../unit.db', localization_manager=localization)
    assert Path(core.db_path) == runtime_dir / 'unit.db'
    assert DbCore(db_path='unit.db', localization_manager=localization) is core


def test_db_core_uses_wavehelm_db_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_dir = _runtime_dir('default_db')
    monkeypatch.setattr(db_core_module, 'get_user_data_dir', lambda: runtime_dir)

    core = DbCore(localization_manager=DummyLocalization())
    assert Path(core.db_path) == runtime_dir / 'wavehelm.db'
    assert Path(core.db_path).exists()
    assert sorted(path.name for path in runtime_dir.glob('*.db')) == ['wavehelm.db']


def test_configure_connection_logs_when_mmap_pragma_is_unavailable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger=db_core_module.logger.name)

    class FakeCursor:
        def execute(self, query: str) -> None:
            if 'mmap_size' in query:
                raise sqlite3.OperationalError('unsupported')
            return None

    core = object.__new__(DbCore)
    core.conn = type('FakeConn', (), {'cursor': lambda self: FakeCursor()})()

    DbCore._configure_connection(core)

    assert 'SQLite mmap_size PRAGMA skipped' in caplog.text


def test_durable_write_connection_uses_wal_full_without_changing_shared_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_dir = _runtime_dir('durable_connection')
    monkeypatch.setattr(db_core_module, 'get_user_data_dir', lambda: runtime_dir)
    core = DbCore(db_path='unit.db', localization_manager=DummyLocalization())
    assert core.conn is not None
    assert int(core.conn.execute("PRAGMA synchronous").fetchone()[0]) == 1

    with core.durable_write_connection() as durable:
        assert durable is not core.conn
        assert str(durable.execute("PRAGMA journal_mode").fetchone()[0]).lower() == 'wal'
        assert int(durable.execute("PRAGMA synchronous").fetchone()[0]) == 2
        durable.execute("BEGIN IMMEDIATE")
        durable.execute(
            "INSERT INTO playlists "
            "(name, canonical_name, description, cover_art, creation_date, last_modified) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ('Uncommitted', 'uncommitted', None, None, '2024-01-01', '2024-01-01'),
        )

    assert int(core.conn.execute("PRAGMA synchronous").fetchone()[0]) == 1
    row = core.conn.execute(
        "SELECT COUNT(*) FROM playlists WHERE name = 'Uncommitted'"
    ).fetchone()
    assert int(row[0]) == 0


def test_durable_write_connection_rejects_unverified_safety_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_dir = _runtime_dir('durable_reject')
    monkeypatch.setattr(db_core_module, 'get_user_data_dir', lambda: runtime_dir)
    core = DbCore(db_path='unit.db', localization_manager=DummyLocalization())
    real_open = core._open_connection

    def open_normal_instead(*, synchronous: str) -> sqlite3.Connection:
        assert synchronous == 'FULL'
        return real_open(synchronous='NORMAL')

    monkeypatch.setattr(core, '_open_connection', open_normal_instead)
    with pytest.raises(db_core_module.DatabaseError, match='WAL/FULL'):
        with core.durable_write_connection():
            pass


def test_read_and_write_sqlite_failures_use_application_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_dir = _runtime_dir('typed_sqlite_failures')
    monkeypatch.setattr(db_core_module, 'get_user_data_dir', lambda: runtime_dir)
    core = DbCore(db_path='unit.db', localization_manager=DummyLocalization())

    with pytest.raises(DatabaseError) as read_error:
        core._execute_query('SELECT * FROM missing_table', fetch_all=True)
    assert not isinstance(read_error.value, sqlite3.Error)

    with pytest.raises(DatabaseError) as write_error:
        core._execute_write('DELETE FROM missing_table')
    assert not isinstance(write_error.value, sqlite3.Error)

    recovered = core._execute_write(
        'INSERT INTO custom_eq_presets (name, settings, created_at) VALUES (?, ?, ?)',
        ('Recovered', '{}', '2026-09-02T00:00:00'),
    )
    assert recovered.rowcount == 1


def test_database_path_primitive_rejects_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_dir = _runtime_dir('invalid_paths')
    monkeypatch.setattr(db_core_module, 'get_user_data_dir', lambda: runtime_dir)

    with pytest.raises(ValueError, match='non-empty'):
        DbCore(db_path='', localization_manager=DummyLocalization())
    with pytest.raises(ValueError, match='NUL'):
        DbCore(db_path='bad\x00path.db', localization_manager=DummyLocalization())
    with pytest.raises(TypeError, match='string or None'):
        DbCore(
            db_path=Path('unit.db'),  # type: ignore[arg-type]
            localization_manager=DummyLocalization(),
        )


class _CloseFailureConnection:
    def close(self) -> None:
        raise sqlite3.OperationalError('injected close failure')


def test_shared_close_failure_is_typed_and_connection_is_detached() -> None:
    core = DbCore.__new__(DbCore)
    core._db_lock = db_core_module.SerializedConnectionGate()
    core.conn = _CloseFailureConnection()  # type: ignore[assignment]

    with pytest.raises(DatabaseError, match='closing database connection'):
        core.close()

    assert core.conn is None
