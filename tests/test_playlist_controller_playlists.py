from __future__ import annotations

from pathlib import Path

import pytest

from src.controller import playlist_controller_playlists as playlists_mod
from src.controller.playlist_controller_storage import (
    PlaylistCleanupError,
    PlaylistDeleteError,
    PlaylistSnapshotError,
    PlaylistStorageError,
    PlaylistWriteError,
)
from src.utils.exceptions import DatabaseError, NotFoundError


class DummyDB:
    def __init__(
        self,
        fail_create: bool = False,
        fail_rename: bool = False,
        fail_delete: bool = False,
    ) -> None:
        self.fail_create = fail_create
        self.fail_rename = fail_rename
        self.fail_delete = fail_delete
        self.created: list[tuple[str, str]] = []
        self.renamed: list[tuple[int, str]] = []
        self.deleted: list[int] = []

    def create_playlist(self, name: str, description: str = "") -> int:
        if self.fail_create:
            raise RuntimeError("create fail")
        self.created.append((name, description))
        return 7

    def update_playlist_name(self, playlist_id: int, new_name: str) -> None:
        if self.fail_rename:
            raise RuntimeError("rename fail")
        self.renamed.append((playlist_id, new_name))

    def delete_playlist(self, playlist_id: int) -> None:
        if self.fail_delete:
            raise RuntimeError("delete fail")
        self.deleted.append(playlist_id)


class DummyEventBus:
    def __init__(self) -> None:
        self.calls: list[tuple[object, object]] = []

    def publish(self, event_type: object, payload: object) -> None:
        self.calls.append((event_type, payload))


class DummyController:
    def __init__(self) -> None:
        self.database_manager = DummyDB()
        self.event_bus = DummyEventBus()
        self._playlists_cache: dict[int, dict[str, object]] = {
            1: {"name": "Old", "description": ""}
        }
        self._playlist_items_cache: dict[int, list[object]] = {1: []}
        self._current_playlist_id: int | None = None
        self._currently_playing: int | None = None
        self._cache_valid = True
        self.feedback: list[tuple[str, dict[str, object]]] = []
        self.updated: list[int] = []
        self.synced: list[tuple[int, str | None]] = []
        self.deleted_storage: list[tuple[int, str]] = []
        self.handled: list[tuple[str, str, dict[str, object]]] = []
        self.duplicate_name: str | None = None
        self.sync_error: PlaylistStorageError | None = None
        self.delete_error: PlaylistStorageError | None = None

    def _validate_playlist_name(self, name: str) -> bool:
        return bool(name)

    def _playlist_name_exists(self, name: str, exclude_id: int | None = None) -> bool:
        del exclude_id
        return name == self.duplicate_name

    def _notify_feedback(self, key: str, **kwargs: object) -> None:
        self.feedback.append((key, kwargs))

    def _notify_playlist_updated(self, playlist_id: int) -> None:
        self.updated.append(playlist_id)

    def _sync_playlist_file(
        self, playlist_id: int, previous_name: str | None = None
    ) -> Path:
        self.synced.append((playlist_id, previous_name))
        if self.sync_error is not None:
            raise self.sync_error
        return Path(f"{playlist_id}.json")

    def _delete_playlist_storage(self, playlist_id: int, playlist_name: str) -> None:
        self.deleted_storage.append((playlist_id, playlist_name))
        if self.delete_error is not None:
            raise self.delete_error

    def _ensure_cache_valid(self) -> None:
        return None

    def _handle_error(self, error: Exception, key: str, **kwargs: object) -> None:
        self.handled.append((str(error), key, kwargs))


for _name in [
    "create_playlist",
    "rename_playlist",
    "delete_playlist",
    "get_all_playlists",
    "get_playlist_by_id",
    "search_playlists",
    "set_current_playlist",
    "set_currently_playing",
    "get_currently_playing",
]:
    setattr(DummyController, _name, getattr(playlists_mod, _name))
DummyController.current_playlist_id = playlists_mod.current_playlist_id


def test_create_playlist_handles_database_failure() -> None:
    controller = DummyController()
    controller.database_manager = DummyDB(fail_create=True)

    result = controller.create_playlist("Demo")

    assert result is None
    assert controller.handled == [
        ("create fail", "error_creating_playlist", {"name": "Demo"})
    ]


def test_create_playlist_handles_real_database_error() -> None:
    controller = DummyController()

    def fail_create(_name: str, _description: str = "") -> int:
        raise DatabaseError("database unavailable")

    controller.database_manager.create_playlist = fail_create

    assert controller.create_playlist("Demo") is None
    assert controller.handled[0][1] == "error_creating_playlist"


def test_rename_playlist_handles_database_failure() -> None:
    controller = DummyController()
    controller.database_manager = DummyDB(fail_rename=True)

    controller.rename_playlist(1, "Renamed")

    assert controller.handled == [
        ("rename fail", "error_renaming_playlist", {"name": "Renamed"})
    ]


def test_rename_playlist_handles_real_not_found_error() -> None:
    controller = DummyController()

    def fail_rename(_playlist_id: int, _new_name: str) -> None:
        raise NotFoundError("playlist disappeared")

    controller.database_manager.update_playlist_name = fail_rename
    controller.rename_playlist(1, "Renamed")

    assert controller.handled[0][1] == "error_renaming_playlist"
    assert controller._playlists_cache[1]["name"] == "Old"


def test_delete_playlist_handles_database_failure() -> None:
    controller = DummyController()
    controller.database_manager = DummyDB(fail_delete=True)

    controller.delete_playlist(1)

    assert controller.handled == [
        ("delete fail", "error_deleting_playlist", {"name": "Old"})
    ]


def test_create_playlist_rolls_back_snapshot_construction_failure() -> None:
    controller = DummyController()
    controller.sync_error = PlaylistSnapshotError(
        "snapshot failed",
        playlist_id=7,
        committed=False,
    )

    with pytest.raises(PlaylistSnapshotError):
        controller.create_playlist("Demo")

    assert controller.database_manager.deleted == [7]
    assert 7 not in controller._playlists_cache
    assert controller.updated == []


def test_create_playlist_rolls_back_precommit_storage_failure() -> None:
    controller = DummyController()
    controller.sync_error = PlaylistWriteError(
        "write failed",
        playlist_id=7,
        committed=False,
    )

    with pytest.raises(PlaylistWriteError):
        controller.create_playlist("Demo")

    assert controller.database_manager.deleted == [7]
    assert 7 not in controller._playlists_cache
    assert 7 not in controller._playlist_items_cache
    assert controller._current_playlist_id is None
    assert controller.updated == []
    assert all(key != "playlist_created" for key, _ in controller.feedback)


def test_create_playlist_reports_rollback_failure_and_invalidates_cache() -> None:
    controller = DummyController()
    controller.database_manager = DummyDB(fail_delete=True)
    controller.sync_error = PlaylistWriteError(
        "write failed",
        playlist_id=7,
        committed=False,
    )

    with pytest.raises(playlists_mod.PlaylistPersistenceRollbackError):
        controller.create_playlist("Demo")

    assert controller._cache_valid is False
    assert controller.updated == []


def test_rename_playlist_restores_database_and_cache_before_commit() -> None:
    controller = DummyController()
    controller.sync_error = PlaylistWriteError(
        "replace failed",
        playlist_id=1,
        committed=False,
    )

    with pytest.raises(PlaylistWriteError):
        controller.rename_playlist(1, "Renamed")

    assert controller.database_manager.renamed == [(1, "Renamed"), (1, "Old")]
    assert controller._playlists_cache[1]["name"] == "Old"
    assert controller.updated == []
    assert all(key != "playlist_renamed" for key, _ in controller.feedback)


def test_rename_playlist_does_not_rollback_postcommit_cleanup_failure() -> None:
    controller = DummyController()
    controller.sync_error = PlaylistCleanupError(
        "old mirror cleanup failed",
        playlist_id=1,
        committed=True,
    )

    with pytest.raises(PlaylistCleanupError):
        controller.rename_playlist(1, "Renamed")

    assert controller.database_manager.renamed == [(1, "Renamed")]
    assert controller._playlists_cache[1]["name"] == "Renamed"
    assert controller.updated == []


def test_delete_playlist_propagates_storage_failure_without_success_event() -> None:
    controller = DummyController()
    controller._current_playlist_id = 1
    controller.delete_error = PlaylistDeleteError(
        "delete failed",
        playlist_id=1,
        committed=False,
    )

    with pytest.raises(PlaylistDeleteError):
        controller.delete_playlist(1)

    assert controller.database_manager.deleted == [1]
    assert 1 not in controller._playlists_cache
    assert controller.event_bus.calls == []
    assert all(key != "playlist_deleted" for key, _ in controller.feedback)


def test_create_playlist_success_notifies_only_after_mirror_commit() -> None:
    controller = DummyController()

    playlist_id = controller.create_playlist("Demo", "desc")

    assert playlist_id == 7
    assert controller.synced == [(7, None)]
    assert controller.updated == [7]
    assert controller.feedback[-1][0] == "playlist_created"
    assert controller._playlists_cache[7]["description"] == "desc"


def test_rename_playlist_success_updates_database_mirror_and_feedback() -> None:
    controller = DummyController()

    controller.rename_playlist(1, "Renamed")

    assert controller.database_manager.renamed == [(1, "Renamed")]
    assert controller.synced == [(1, "Old")]
    assert controller._playlists_cache[1]["name"] == "Renamed"
    assert controller.updated == [1]
    assert controller.feedback[-1][0] == "playlist_renamed"


def test_rename_playlist_rollback_failure_invalidates_cache() -> None:
    controller = DummyController()
    rename_calls = 0

    def fail_on_rollback(playlist_id: int, new_name: str) -> None:
        nonlocal rename_calls
        rename_calls += 1
        if rename_calls == 2:
            raise RuntimeError("rollback rename failed")
        controller.database_manager.renamed.append((playlist_id, new_name))

    controller.database_manager.update_playlist_name = fail_on_rollback
    controller.sync_error = PlaylistWriteError(
        "replace failed",
        playlist_id=1,
        committed=False,
    )

    with pytest.raises(playlists_mod.PlaylistPersistenceRollbackError):
        controller.rename_playlist(1, "Renamed")

    assert controller._cache_valid is False
    assert controller.synced == [(1, "Old")]


def test_delete_playlist_success_resets_state_and_publishes_update() -> None:
    controller = DummyController()
    controller._current_playlist_id = 1
    controller._currently_playing = 1

    controller.delete_playlist(1)

    assert controller.database_manager.deleted == [1]
    assert controller.deleted_storage == [(1, "Old")]
    assert controller._current_playlist_id is None
    assert controller._currently_playing is None
    assert controller.event_bus.calls
    assert controller.feedback[-1][0] == "playlist_deleted"
