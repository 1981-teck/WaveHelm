from __future__ import annotations

import logging
import sqlite3
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.model.component_database.db_core as db_core_module
from src.model.component_database.db_core import DbCore


RUNTIME_ROOT = Path(__file__).resolve().parent / "_db_core_runtime"


class DummyLocalization:
    def get_text(self, key: str, **kwargs):
        return key


@pytest.fixture(autouse=True)
def reset_db_core_singleton():
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


def test_db_core_context_execute_query_and_integrity(monkeypatch):
    runtime_dir = _runtime_dir('query_and_integrity')
    monkeypatch.setattr(db_core_module, 'get_user_data_dir', lambda: runtime_dir)

    core = DbCore(db_path='unit.db', localization_manager=DummyLocalization())
    assert Path(core.db_path) == runtime_dir / 'unit.db'
    assert core.conn is not None

    with core as entered:
        entered._execute_query(
            "INSERT INTO playlists (name, description, cover_art, creation_date, last_modified) VALUES (?, ?, ?, ?, ?)",
            ('Alpha', None, None, '2024-01-01', '2024-01-01'),
        )
        row = entered._execute_query(
            "SELECT name FROM playlists WHERE name = ?",
            ('Alpha',),
            fetch_one=True,
        )
        assert row['name'] == 'Alpha'
        assert entered._last_changes() >= 1

        with pytest.raises(sqlite3.IntegrityError):
            entered._execute_query(
                "INSERT INTO playlists (name, description, cover_art, creation_date, last_modified) VALUES (?, ?, ?, ?, ?)",
                ('Alpha', None, None, '2024-01-01', '2024-01-01'),
            )

        count_row = entered._execute_query(
            "SELECT COUNT(*) AS total FROM playlists WHERE name = ?",
            ('Alpha',),
            fetch_one=True,
        )
        assert count_row['total'] == 1

    assert core.conn is None


def test_db_core_uses_wavehelm_db_by_default(monkeypatch):
    runtime_dir = _runtime_dir('default_db')
    monkeypatch.setattr(db_core_module, 'get_user_data_dir', lambda: runtime_dir)

    core = DbCore(localization_manager=DummyLocalization())
    assert Path(core.db_path) == runtime_dir / 'wavehelm.db'
    assert Path(core.db_path).exists()
    assert sorted(path.name for path in runtime_dir.glob('*.db')) == ['wavehelm.db']


def test_configure_connection_logs_when_mmap_pragma_is_unavailable(caplog):
    caplog.set_level(logging.DEBUG, logger=db_core_module.logger.name)

    class FakeCursor:
        def execute(self, query):
            if 'mmap_size' in query:
                raise sqlite3.OperationalError('unsupported')
            return None

    core = object.__new__(DbCore)
    core.conn = type('FakeConn', (), {'cursor': lambda self: FakeCursor()})()

    DbCore._configure_connection(core)

    assert 'SQLite mmap_size PRAGMA skipped' in caplog.text
