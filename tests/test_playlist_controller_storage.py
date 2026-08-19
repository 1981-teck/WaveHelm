from __future__ import annotations

import errno
import json
import os
from pathlib import Path

import pytest

import src.controller.playlist_controller_storage as playlist_storage
from src.utils.exceptions import DatabaseError


class GoodTrack:
    def __init__(self, path: str) -> None:
        self.path = path

    def to_dict(self) -> dict[str, object]:
        return {"path": self.path}


class BadTrack:
    def to_dict(self) -> dict[str, object]:
        return {"payload": object()}


class DummyController:
    def __init__(self, tmp_path: Path) -> None:
        self._playlists_storage_dir = tmp_path / "playlists"
        self._playlists_cache: dict[int, dict[str, object]] = {
            1: {
                "name": "Road Trip",
                "description": "desc",
                "creation_date": "2024-01-01",
                "last_modified": "2024-01-02",
            }
        }
        self.tracks: list[object] = []

    def get_tracks_in_playlist(self, playlist_id: int) -> list[object]:
        assert playlist_id in self._playlists_cache
        return list(self.tracks)


playlist_storage.attach_playlist_controller_storage_behavior(DummyController)


def _temporary_files(controller: DummyController) -> list[Path]:
    if not controller._playlists_storage_dir.exists():
        return []
    return list(controller._playlists_storage_dir.glob(".*.tmp"))


def _write_initial_snapshot(controller: DummyController, path: str = "old.mp3") -> Path:
    controller.tracks = [GoodTrack(path)]
    return controller._sync_playlist_file(1)


def test_sync_playlist_file_writes_complete_snapshot_atomically(tmp_path: Path) -> None:
    controller = DummyController(tmp_path)
    controller.tracks = [GoodTrack("a.mp3")]

    target = controller._sync_playlist_file(1)

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["name"] == "Road Trip"
    assert payload["tracks"] == [{"path": "a.mp3"}]
    assert _temporary_files(controller) == []


def test_serialization_failure_preserves_previous_target(tmp_path: Path) -> None:
    controller = DummyController(tmp_path)
    target = _write_initial_snapshot(controller)
    previous_bytes = target.read_bytes()
    controller.tracks = [BadTrack()]

    with pytest.raises(playlist_storage.PlaylistSerializationError) as captured:
        controller._sync_playlist_file(1)

    assert captured.value.committed is False
    assert target.read_bytes() == previous_bytes
    assert _temporary_files(controller) == []


def test_snapshot_construction_failure_is_typed_and_preserves_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = DummyController(tmp_path)
    target = _write_initial_snapshot(controller)
    previous_bytes = target.read_bytes()

    def fail_snapshot(_playlist_id: int) -> list[object]:
        raise DatabaseError("playlist query failed")

    monkeypatch.setattr(controller, "get_tracks_in_playlist", fail_snapshot)
    with pytest.raises(playlist_storage.PlaylistSnapshotError) as captured:
        controller._sync_playlist_file(1)

    assert captured.value.committed is False
    assert target.read_bytes() == previous_bytes
    assert _temporary_files(controller) == []


def test_permission_failure_preserves_previous_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = DummyController(tmp_path)
    target = _write_initial_snapshot(controller)
    previous_bytes = target.read_bytes()
    controller.tracks = [GoodTrack("new.mp3")]

    def deny_temporary_file(*_args: object, **_kwargs: object) -> tuple[int, str]:
        raise PermissionError(errno.EACCES, "permission denied")

    monkeypatch.setattr(playlist_storage.tempfile, "mkstemp", deny_temporary_file)
    with pytest.raises(playlist_storage.PlaylistWriteError) as captured:
        controller._sync_playlist_file(1)

    assert captured.value.committed is False
    assert target.read_bytes() == previous_bytes
    assert _temporary_files(controller) == []


def test_disk_full_preserves_target_and_removes_partial_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = DummyController(tmp_path)
    target = _write_initial_snapshot(controller)
    previous_bytes = target.read_bytes()
    controller.tracks = [GoodTrack("new.mp3")]
    real_write = playlist_storage.os.write

    def fail_after_partial_write(file_descriptor: int, payload: bytes) -> None:
        real_write(file_descriptor, payload[:16])
        raise OSError(errno.ENOSPC, "no space left on device")

    monkeypatch.setattr(playlist_storage, "_write_all", fail_after_partial_write)
    with pytest.raises(playlist_storage.PlaylistWriteError) as captured:
        controller._sync_playlist_file(1)

    assert captured.value.committed is False
    assert target.read_bytes() == previous_bytes
    assert _temporary_files(controller) == []


def test_replace_failure_preserves_target_and_old_rename_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = DummyController(tmp_path)
    controller._playlists_cache[1]["name"] = "Old Name"
    old_target = _write_initial_snapshot(controller)
    previous_bytes = old_target.read_bytes()
    controller._playlists_cache[1]["name"] = "New Name"
    controller.tracks = [GoodTrack("new.mp3")]

    def fail_replace(_source: object, _target: object) -> None:
        raise OSError(errno.EIO, "replace failed")

    monkeypatch.setattr(playlist_storage, "_replace_file_durable", fail_replace)
    with pytest.raises(playlist_storage.PlaylistWriteError) as captured:
        controller._sync_playlist_file(1, previous_name="Old Name")

    new_target = controller._get_playlist_storage_file(1, "New Name", create_parent=False)
    assert captured.value.committed is False
    assert old_target.read_bytes() == previous_bytes
    assert not new_target.exists()
    assert _temporary_files(controller) == []


def test_post_commit_directory_sync_failure_reports_committed_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = DummyController(tmp_path)
    target = _write_initial_snapshot(controller)
    controller.tracks = [GoodTrack("committed.mp3")]

    def fail_directory_sync(_parent: Path) -> None:
        raise OSError(errno.EIO, "directory fsync failed")

    monkeypatch.setattr(playlist_storage, "_sync_parent_directory", fail_directory_sync)
    with pytest.raises(playlist_storage.PlaylistWriteError) as captured:
        controller._sync_playlist_file(1)

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert captured.value.committed is True
    assert payload["tracks"] == [{"path": "committed.mp3"}]
    assert _temporary_files(controller) == []


def test_successful_rename_commits_new_file_before_removing_old(tmp_path: Path) -> None:
    controller = DummyController(tmp_path)
    controller._playlists_cache[1]["name"] = "Old Name"
    old_target = _write_initial_snapshot(controller)
    controller._playlists_cache[1]["name"] = "New Name"
    controller.tracks = [GoodTrack("new.mp3")]

    new_target = controller._sync_playlist_file(1, previous_name="Old Name")

    assert new_target.exists()
    assert not old_target.exists()
    assert _temporary_files(controller) == []


def test_delete_playlist_storage_raises_typed_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = DummyController(tmp_path)
    target = _write_initial_snapshot(controller)
    original_unlink = Path.unlink

    def failing_unlink(self: Path, *args: object, **kwargs: object) -> None:
        if self == target:
            raise OSError(errno.EBUSY, "busy")
        original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", failing_unlink)
    with pytest.raises(playlist_storage.PlaylistDeleteError) as captured:
        controller._delete_playlist_storage(1, "Road Trip")

    assert captured.value.committed is False
    assert target.exists()


def test_delete_playlist_storage_unlinks_broken_symlink(tmp_path: Path) -> None:
    controller = DummyController(tmp_path)
    target = controller._get_playlist_storage_file(1, "Road Trip")
    missing_target = tmp_path / "missing-target.json"
    try:
        target.symlink_to(missing_target)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"symlink creation unavailable: {error}")

    assert os.path.lexists(target)
    controller._delete_playlist_storage(1, "Road Trip")

    assert not os.path.lexists(target)
    assert not missing_target.exists()


def test_missing_or_unknown_playlist_is_rejected(tmp_path: Path) -> None:
    controller = DummyController(tmp_path)

    with pytest.raises(playlist_storage.PlaylistWriteError):
        controller._sync_playlist_file(None)
    with pytest.raises(playlist_storage.PlaylistWriteError):
        controller._sync_playlist_file(999)


def test_playlist_snapshot_size_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = DummyController(tmp_path)
    controller.tracks = [GoodTrack("x" * 512)]
    monkeypatch.setattr(playlist_storage, "PLAYLIST_FILE_MAX_BYTES", 128)

    with pytest.raises(playlist_storage.PlaylistSerializationError):
        controller._sync_playlist_file(1)

    assert _temporary_files(controller) == []


def test_sync_all_playlist_files_returns_committed_count(tmp_path: Path) -> None:
    controller = DummyController(tmp_path)
    controller._playlists_cache[2] = {"name": "Second"}

    assert controller._sync_all_playlist_files() == 2
    assert len(list(controller._playlists_storage_dir.glob("*.json"))) == 2


def test_playlist_storage_filename_handles_reserved_and_invalid_names(
    tmp_path: Path,
) -> None:
    controller = DummyController(tmp_path)

    reserved = controller._get_playlist_storage_file(7, "CON", create_parent=False)
    invalid = controller._get_playlist_storage_file(8, "***", create_parent=False)

    assert reserved.name == "7__CON.json"
    assert invalid.name == "8_playlist_8.json"


def test_playlist_storage_filename_respects_windows_utf16_component_limit(
    tmp_path: Path,
) -> None:
    controller = DummyController(tmp_path)

    target = controller._get_playlist_storage_file(
        42,
        "😀" * 300,
        create_parent=False,
    )

    assert len(target.name.encode("utf-16-le")) // 2 <= 255
    assert target.name.startswith("42_")
    assert target.name.endswith(".json")
