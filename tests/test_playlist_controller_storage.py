from __future__ import annotations

import json
import shutil
from pathlib import Path

import src.controller.playlist_controller_storage as playlist_storage


class GoodTrack:
    def __init__(self, path: str):
        self.path = path

    def to_dict(self):
        return {'path': self.path}


class BadTrack:
    def to_dict(self):
        return {'payload': object()}


class DummyController:
    def __init__(self, tmp_path: Path):
        self._playlists_storage_dir = tmp_path / 'playlists'
        self._playlists_cache = {
            1: {
                'name': 'Road Trip',
                'description': 'desc',
                'creation_date': '2024-01-01',
                'last_modified': '2024-01-02',
            }
        }
        self.tracks = []

    def get_tracks_in_playlist(self, playlist_id: int):
        return list(self.tracks)


playlist_storage.attach_playlist_controller_storage_behavior(DummyController)

WORK_TMP_ROOT = Path(__file__).resolve().parent / '_playlist_storage_runtime'


def _fresh_tmp_dir(name: str) -> Path:
    target = WORK_TMP_ROOT / name
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    return target


def test_sync_playlist_file_writes_snapshot():
    tmp_path = _fresh_tmp_dir('writes_snapshot')
    controller = DummyController(tmp_path)
    controller.tracks = [GoodTrack('a.mp3')]

    controller._sync_playlist_file(1)

    files = list((tmp_path / 'playlists').glob('1_*.json'))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding='utf-8'))
    assert payload['name'] == 'Road Trip'
    assert payload['tracks'] == [{'path': 'a.mp3'}]


def test_sync_playlist_file_swallows_serialization_type_errors():
    tmp_path = _fresh_tmp_dir('serialization_errors')
    controller = DummyController(tmp_path)
    controller.tracks = [BadTrack()]

    controller._sync_playlist_file(1)


def test_delete_playlist_storage_ignores_oserror(monkeypatch):
    tmp_path = _fresh_tmp_dir('delete_oserror')
    controller = DummyController(tmp_path)
    controller.tracks = [GoodTrack('a.mp3')]
    controller._sync_playlist_file(1)
    target = controller._get_playlist_storage_file(1, 'Road Trip', create_parent=False)
    original_unlink = Path.unlink

    def failing_unlink(self, *args, **kwargs):
        if self == target:
            raise OSError('busy')
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', failing_unlink)
    controller._delete_playlist_storage(1, 'Road Trip')
    assert target.exists()
