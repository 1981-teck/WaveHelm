from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import src.model.component_database.db_core as db_core_module
from src.model.component_database.db_core import DbCore
from src.model.component_database.playlist_manager import PlaylistManager
from src.utils.exceptions import IntegrityError


RUNTIME_ROOT = Path(__file__).resolve().parent / "_playlist_persistence_runtime"


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


def test_playlist_roundtrip_persists_across_reopen(monkeypatch):
    runtime_dir = _runtime_dir('roundtrip')
    monkeypatch.setattr(db_core_module, 'get_user_data_dir', lambda: runtime_dir)

    core = DbCore(db_path='persist.db', localization_manager=DummyLocalization())
    manager = PlaylistManager(core)
    playlist_id = manager.create_playlist('Roadtrip')
    manager.add_playlist_item(playlist_id, 'a.wav')
    manager.add_playlist_item(playlist_id, 'b.wav')
    manager.rename_playlist(playlist_id, 'Roadtrip Updated')
    core.close()

    DbCore._instance = None
    reopened_core = DbCore(db_path='persist.db', localization_manager=DummyLocalization())
    reopened_manager = PlaylistManager(reopened_core)

    playlist = reopened_manager.get_playlist_by_id(playlist_id)
    items = reopened_manager.get_playlist_items(playlist_id)

    assert playlist['name'] == 'Roadtrip Updated'
    assert [item['media_path'] for item in items] == ['a.wav', 'b.wav']
    assert [item['position'] for item in items] == [1, 2]

    reopened_core.close()


def test_duplicate_add_rolls_back_without_corrupting_playlist_order(monkeypatch):
    runtime_dir = _runtime_dir('duplicate_rollback')
    monkeypatch.setattr(db_core_module, 'get_user_data_dir', lambda: runtime_dir)

    core = DbCore(db_path='persist.db', localization_manager=DummyLocalization())
    manager = PlaylistManager(core)
    playlist_id = manager.create_playlist('Integrity')
    manager.add_playlist_item(playlist_id, 'one.wav')
    manager.add_playlist_item(playlist_id, 'two.wav')

    with pytest.raises(IntegrityError):
        manager.add_playlist_item(playlist_id, 'one.wav')

    items = manager.get_playlist_items(playlist_id)
    assert [item['media_path'] for item in items] == ['one.wav', 'two.wav']
    assert [item['position'] for item in items] == [1, 2]
    core.close()
