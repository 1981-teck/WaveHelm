from __future__ import annotations

import shutil
from contextlib import contextmanager
from pathlib import Path

import pytest

import src.model.component_database.db_core as db_core_module
from src.model.component_database.db_core import DbCore
from src.model.component_database.playlist_manager import PlaylistManager
from src.utils.exceptions import DatabaseError, IntegrityError, NotFoundError


RUNTIME_ROOT = Path(__file__).resolve().parent / "_playlist_manager_runtime"


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


def _make_manager(monkeypatch, name: str):
    runtime_dir = _runtime_dir(name)
    monkeypatch.setattr(db_core_module, 'get_user_data_dir', lambda: runtime_dir)
    core = DbCore(db_path='playlist.db', localization_manager=DummyLocalization())
    return PlaylistManager(core), core


def test_create_rename_and_duplicate_validation(monkeypatch):
    manager, core = _make_manager(monkeypatch, 'create_rename')

    alpha_id = manager.create_playlist('Alpha')
    beta_id = manager.create_playlist('Beta')
    assert alpha_id != beta_id

    manager.rename_playlist(alpha_id, 'Gamma')
    assert manager.get_playlist_by_id(alpha_id)['name'] == 'Gamma'

    with pytest.raises(IntegrityError):
        manager.rename_playlist(beta_id, 'gamma')

    with pytest.raises(ValueError):
        manager.rename_playlist(alpha_id, '   ')

    with pytest.raises(IntegrityError):
        manager.create_playlist('Gamma')

    core.close()


def test_playlist_items_add_remove_reorder_and_delete(monkeypatch):
    manager, core = _make_manager(monkeypatch, 'items_flow')
    playlist_id = manager.create_playlist('Flow')

    manager.add_playlist_item(playlist_id, 'track_1.wav')
    manager.add_playlist_item(playlist_id, 'track_2.wav')
    manager.add_playlist_item(playlist_id, 'track_0.wav', position=1)

    items = manager.get_playlist_items(playlist_id)
    assert [item['media_path'] for item in items] == ['track_0.wav', 'track_1.wav', 'track_2.wav']
    assert [item['position'] for item in items] == [1, 2, 3]

    manager.reorder_playlist_item(playlist_id, 'track_2.wav', 1)
    items = manager.get_playlist_items(playlist_id)
    assert [item['media_path'] for item in items] == ['track_2.wav', 'track_0.wav', 'track_1.wav']
    assert [item['position'] for item in items] == [1, 2, 3]

    manager.remove_playlist_item(playlist_id, 'track_0.wav')
    items = manager.get_playlist_items(playlist_id)
    assert [item['media_path'] for item in items] == ['track_2.wav', 'track_1.wav']
    assert [item['position'] for item in items] == [1, 2]

    with pytest.raises(IntegrityError):
        manager.add_playlist_item(playlist_id, 'track_1.wav')

    manager.delete_playlist(playlist_id)
    assert manager.get_playlist_by_id(playlist_id) is None

    with pytest.raises(NotFoundError):
        manager.delete_playlist(playlist_id)

    core.close()


def test_get_playlist_items_recovers_invalid_metadata_json(monkeypatch):
    manager, core = _make_manager(monkeypatch, 'invalid_metadata')
    playlist_id = manager.create_playlist('Json')

    core._execute_query(
        "INSERT INTO library_items (path, title, media_type, duration, metadata) VALUES (?, ?, ?, ?, ?)",
        ('track_bad.wav', 'Bad', 'audio', 12.0, '{broken'),
    )
    core._execute_query(
        "INSERT INTO playlist_items (playlist_id, media_path, position, added_at) VALUES (?, ?, ?, ?)",
        (playlist_id, 'track_bad.wav', 1, '2024-01-01T00:00:00'),
    )

    items = manager.get_playlist_items(playlist_id)
    assert items[0]['metadata'] == {}

    core.close()


def test_playlist_mutations_run_on_full_synchronous_connections(monkeypatch):
    manager, core = _make_manager(monkeypatch, 'durable_mutations')
    observed_modes: list[int] = []
    real_durable_connection = core.durable_write_connection

    @contextmanager
    def tracked_durable_connection():
        with real_durable_connection() as connection:
            observed_modes.append(
                int(connection.execute("PRAGMA synchronous").fetchone()[0])
            )
            yield connection

    monkeypatch.setattr(core, 'durable_write_connection', tracked_durable_connection)

    playlist_id = manager.create_playlist('Durable')
    manager.add_playlist_item(playlist_id, 'track.wav')
    manager.reorder_playlist_item(playlist_id, 'track.wav', 0)
    manager.remove_playlist_item(playlist_id, 'track.wav')
    manager.rename_playlist(playlist_id, 'Durable Renamed')
    manager.delete_playlist(playlist_id)

    assert observed_modes == [2, 2, 2, 2, 2, 2]
    assert core.conn is not None
    assert int(core.conn.execute("PRAGMA synchronous").fetchone()[0]) == 1
