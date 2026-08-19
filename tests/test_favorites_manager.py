from __future__ import annotations

import logging
import shutil
from pathlib import Path

import pytest

import src.model.component_database.db_core as db_core_module
import src.model.component_database.favorites_manager as favorites_manager_module
from src.model.component_database.db_core import DbCore
from src.model.component_database.favorites_manager import FavoritesManager
from src.utils.exceptions import IntegrityError, NotFoundError


RUNTIME_ROOT = Path(__file__).resolve().parent / "_favorites_manager_runtime"


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
    core = DbCore(db_path='favorites.db', localization_manager=DummyLocalization())
    return FavoritesManager(core), core


def test_add_remove_and_case_insensitive_path_matching(monkeypatch):
    manager, core = _make_manager(monkeypatch, 'favorites_flow')

    manager.add_favorite('C:/Temp/Song.wav', 'Song', 'audio', 12.0, {'artist': 'A'})
    assert manager.is_favorite('c:\\temp\\song.wav') is True

    with pytest.raises(IntegrityError):
        manager.add_favorite('c:\\temp\\song.wav', 'Song', 'audio', 12.0, None)

    manager.remove_favorite('c:\\temp\\song.wav')
    assert manager.is_favorite('C:/Temp/Song.wav') is False

    with pytest.raises(NotFoundError):
        manager.remove_favorite('C:/Temp/Song.wav')

    core.close()


def test_get_favorites_recovers_invalid_metadata_json(monkeypatch):
    manager, core = _make_manager(monkeypatch, 'favorites_invalid_json')

    core._execute_query(
        "INSERT INTO favorites (path, title, media_type, duration, metadata, added_at) VALUES (?, ?, ?, ?, ?, ?)",
        ('broken.wav', 'Broken', 'audio', 5.0, '{broken', '2024-01-01T00:00:00'),
    )

    items = manager.get_favorites()
    assert items[0]['metadata'] == {}

    core.close()


def test_normalize_storage_path_logs_on_bad_path(caplog):
    caplog.set_level(logging.DEBUG, logger=favorites_manager_module.logger.name)

    class BrokenPath:
        def startswith(self, *_args, **_kwargs):
            return False

        def strip(self):
            raise TypeError('boom')

    broken = BrokenPath()
    normalized = favorites_manager_module._normalize_storage_path(broken)

    assert normalized.startswith('<')
    assert 'Failed to normalize favorite storage path' in caplog.text
