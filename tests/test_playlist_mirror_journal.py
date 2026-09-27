from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

import src.controller.playlist_controller_storage as playlist_storage
import src.model.component_database.db_core as db_core_module
import src.model.component_database.playlist_manager as playlist_manager_module
import src.model.component_database.playlist_mirror_journal as journal_module
from src.model.component_database.db_core import DbCore
from src.model.component_database.playlist_manager import PlaylistManager
from src.model.component_database.playlist_mirror_journal import (
    MAX_MIRROR_ATTEMPTS,
    MAX_MIRROR_ERROR_CHARS,
    PlaylistMirrorJournal,
    PlaylistMirrorJournalCorruptionError,
    PlaylistMirrorJournalError,
)
from src.utils.exceptions import DatabaseError


class DummyLocalization:
    def get_text(self, key: str, **_kwargs: object) -> str:
        return key


class TrackSnapshot:
    def __init__(self, path: str) -> None:
        self.path = path

    def to_dict(self) -> dict[str, object]:
        return {"path": self.path}


class DatabaseFacade:
    def __init__(self, manager: PlaylistManager) -> None:
        self.playlists = manager


class RecoveryController:
    def __init__(self, root: Path, manager: PlaylistManager) -> None:
        self.database_manager = DatabaseFacade(manager)
        self._playlists_storage_dir = root / "mirrors"
        self._playlists_cache: dict[int, dict[str, object]] = {}
        self.tracks: dict[int, list[TrackSnapshot]] = {}

    def refresh_cache(self) -> None:
        cache: dict[int, dict[str, object]] = {}
        for playlist in self.database_manager.playlists.get_all_playlists():
            cache[int(playlist["id"])] = playlist
        self._playlists_cache = cache

    def get_tracks_in_playlist(self, playlist_id: int) -> list[TrackSnapshot]:
        return list(self.tracks.get(playlist_id, []))


playlist_storage.attach_playlist_controller_storage_behavior(RecoveryController)


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


def _make_manager(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[PlaylistManager, DbCore]:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_core_module, "get_user_data_dir", lambda: data_dir)
    core = DbCore(db_path="playlist.db", localization_manager=DummyLocalization())
    return PlaylistManager(core), core


def _connection(core: DbCore) -> sqlite3.Connection:
    connection = core.conn
    assert connection is not None
    return connection


def test_create_and_track_mutations_coalesce_one_sync_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, core = _make_manager(tmp_path, monkeypatch)
    playlist_id = manager.create_playlist("Alpha")

    job = manager.mirror_journal.get_job(playlist_id)
    assert job is not None
    assert job.operation == "sync"
    assert job.playlist_name == "Alpha"
    assert job.stale_names == ()

    manager.add_playlist_item(playlist_id, "one.wav")
    manager.add_playlist_item(playlist_id, "two.wav")
    manager.reorder_playlist_item(playlist_id, "two.wav", 1)
    manager.remove_playlist_item(playlist_id, "one.wav")

    row = _connection(core).execute(
        "SELECT COUNT(*) FROM playlist_mirror_outbox WHERE playlist_id = ?",
        (playlist_id,),
    ).fetchone()
    assert row is not None and int(row[0]) == 1
    assert manager.mirror_journal.get_job(playlist_id).attempt_count == 0


def test_journal_failure_rolls_back_the_canonical_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, core = _make_manager(tmp_path, monkeypatch)

    def fail_enqueue(*_args: object, **_kwargs: object) -> None:
        raise PlaylistMirrorJournalError("injected journal failure")

    monkeypatch.setattr(
        playlist_manager_module, "enqueue_playlist_mirror_sync", fail_enqueue
    )
    with pytest.raises(DatabaseError, match="injected journal failure"):
        manager.create_playlist("Atomic")

    connection = _connection(core)
    playlist_count = connection.execute("SELECT COUNT(*) FROM playlists").fetchone()
    outbox_count = connection.execute(
        "SELECT COUNT(*) FROM playlist_mirror_outbox"
    ).fetchone()
    assert playlist_count is not None and int(playlist_count[0]) == 0
    assert outbox_count is not None and int(outbox_count[0]) == 0


def test_capacity_failure_rolls_back_only_the_new_playlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, core = _make_manager(tmp_path, monkeypatch)
    monkeypatch.setattr(journal_module, "MAX_PENDING_MIRROR_JOBS", 1)
    first_id = manager.create_playlist("First")

    with pytest.raises(DatabaseError, match="journal is full"):
        manager.create_playlist("Second")

    playlists = manager.get_all_playlists()
    assert [(int(item["id"]), item["name"]) for item in playlists] == [
        (first_id, "First")
    ]
    row = _connection(core).execute(
        "SELECT COUNT(*) FROM playlist_mirror_outbox"
    ).fetchone()
    assert row is not None and int(row[0]) == 1


def test_rename_and_delete_coalesce_all_stale_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, _core = _make_manager(tmp_path, monkeypatch)
    playlist_id = manager.create_playlist("Alpha")
    manager.rename_playlist(playlist_id, "Beta")
    manager.rename_playlist(playlist_id, "Gamma")

    renamed = manager.mirror_journal.get_job(playlist_id)
    assert renamed is not None
    assert renamed.operation == "sync"
    assert renamed.playlist_name == "Gamma"
    assert renamed.stale_names == ("Alpha", "Beta")

    manager.delete_playlist(playlist_id)
    deleted = manager.mirror_journal.get_job(playlist_id)
    assert deleted is not None
    assert deleted.operation == "delete"
    assert deleted.playlist_name == "Gamma"
    assert deleted.stale_names == ("Alpha", "Beta")


def test_retry_backoff_is_bounded_and_exhaustion_is_observable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, _core = _make_manager(tmp_path, monkeypatch)
    playlist_id = manager.create_playlist("Retry")
    journal = manager.mirror_journal

    assert journal.record_failure(playlist_id, RuntimeError("first"), now=100.0)
    first = journal.get_job(playlist_id)
    assert first is not None
    assert first.attempt_count == 1
    assert first.next_attempt_at == 101.0
    assert journal.fetch_due(now=100.999) == ()
    assert [job.playlist_id for job in journal.fetch_due(now=101.0)] == [playlist_id]

    for attempt in range(2, MAX_MIRROR_ATTEMPTS + 1):
        assert journal.record_failure(
            playlist_id, RuntimeError(f"failure-{attempt}"), now=200.0 + attempt
        )
    exhausted = journal.get_job(playlist_id)
    assert exhausted is not None
    assert exhausted.attempt_count == MAX_MIRROR_ATTEMPTS
    assert journal.exhausted_count() == 1
    assert journal.fetch_due(now=10_000.0) == ()


def test_journal_validation_rejects_invalid_limits_and_corrupt_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, _core = _make_manager(tmp_path, monkeypatch)
    journal = manager.mirror_journal

    with pytest.raises(PlaylistMirrorJournalError, match="batch limit"):
        journal.fetch_due(limit=0)
    with pytest.raises(PlaylistMirrorJournalError, match="finite"):
        journal.fetch_due(now=float("nan"))
    with pytest.raises(PlaylistMirrorJournalError, match="positive integer"):
        journal.get_job(0)
    assert journal.record_failure(999, RuntimeError("missing"), now=0.0) is False

    corrupt_rows: tuple[tuple[object, ...], ...] = (
        (1, "unknown", "Alpha", "[]", 0, 0.0, ""),
        (1, "sync", "Alpha", "{}", 0, 0.0, ""),
        (1, "sync", "Alpha", "[]", -1, 0.0, ""),
        (1, "sync", "Alpha", "[]", 0, float("inf"), ""),
        (1, "sync", "Alpha", "[]", 0, 0.0, "x" * (MAX_MIRROR_ERROR_CHARS + 1)),
    )
    for row in corrupt_rows:
        with pytest.raises(PlaylistMirrorJournalCorruptionError):
            PlaylistMirrorJournal._decode_job(row)


def test_controller_rejects_a_present_but_unwired_database_manager(
    tmp_path: Path,
) -> None:
    class UnwiredDatabaseManager:
        playlists = object()

    class UnwiredController:
        database_manager = UnwiredDatabaseManager()
        _playlists_cache: dict[int, dict[str, object]] = {}
        _playlists_storage_dir = tmp_path / "mirrors"

    with pytest.raises(
        playlist_storage.PlaylistReconciliationError, match="binding is missing"
    ):
        playlist_storage._sync_all_playlist_files(UnwiredController())


def test_corrupt_persisted_job_is_rejected_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, core = _make_manager(tmp_path, monkeypatch)
    playlist_id = manager.create_playlist("Corrupt")
    connection = _connection(core)
    connection.execute(
        "UPDATE playlist_mirror_outbox SET stale_names = ? WHERE playlist_id = ?",
        ("{broken", playlist_id),
    )
    connection.commit()

    with pytest.raises(PlaylistMirrorJournalCorruptionError, match="stale-name JSON"):
        manager.mirror_journal.fetch_due(now=10_000.0)


def test_recovery_rebuilds_renames_tracks_and_deletions_idempotently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, _core = _make_manager(tmp_path, monkeypatch)
    controller = RecoveryController(tmp_path, manager)
    playlist_id = manager.create_playlist("Alpha")
    controller.refresh_cache()

    assert controller._sync_all_playlist_files() == 1
    alpha_target = controller._get_playlist_storage_file(
        playlist_id, "Alpha", create_parent=False
    )
    assert alpha_target.exists()
    assert manager.mirror_journal.get_job(playlist_id) is None

    manager.rename_playlist(playlist_id, "Beta")
    manager.add_playlist_item(playlist_id, "track.wav")
    controller.tracks[playlist_id] = [TrackSnapshot("track.wav")]
    controller.refresh_cache()
    assert controller._sync_all_playlist_files() == 1

    beta_target = controller._get_playlist_storage_file(
        playlist_id, "Beta", create_parent=False
    )
    payload = json.loads(beta_target.read_text(encoding="utf-8"))
    assert payload["name"] == "Beta"
    assert payload["tracks"] == [{"path": "track.wav"}]
    assert not alpha_target.exists()
    assert manager.mirror_journal.get_job(playlist_id) is None

    manager.delete_playlist(playlist_id)
    controller.refresh_cache()
    assert controller._sync_all_playlist_files() == 1
    assert not beta_target.exists()
    assert manager.mirror_journal.get_job(playlist_id) is None
    assert controller._sync_all_playlist_files() == 0


def test_failed_recovery_records_backoff_and_never_reports_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, core = _make_manager(tmp_path, monkeypatch)
    controller = RecoveryController(tmp_path, manager)
    playlist_id = manager.create_playlist("Blocked")
    controller.refresh_cache()

    with monkeypatch.context() as patch:
        def fail_replace(_source: object, _target: object) -> None:
            raise PermissionError("replace blocked")

        patch.setattr(playlist_storage, "_replace_file_durable", fail_replace)
        with pytest.raises(playlist_storage.PlaylistReconciliationError, match="failed"):
            controller._sync_all_playlist_files()
        first = manager.mirror_journal.get_job(playlist_id)
        assert first is not None and first.attempt_count == 1
        with pytest.raises(playlist_storage.PlaylistReconciliationError, match="pending"):
            controller._sync_all_playlist_files()
        assert manager.mirror_journal.get_job(playlist_id).attempt_count == 1

    connection = _connection(core)
    connection.execute(
        "UPDATE playlist_mirror_outbox SET next_attempt_at = 0 WHERE playlist_id = ?",
        (playlist_id,),
    )
    connection.commit()
    assert controller._sync_all_playlist_files() == 1
    assert manager.mirror_journal.get_job(playlist_id) is None


def test_recovery_batch_is_bounded_and_drains_on_a_later_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, _core = _make_manager(tmp_path, monkeypatch)
    controller = RecoveryController(tmp_path, manager)
    monkeypatch.setattr(playlist_storage, "MAX_RECOVERY_BATCH", 2)
    playlist_ids = [manager.create_playlist(f"Batch {index}") for index in range(3)]
    controller.refresh_cache()

    with pytest.raises(playlist_storage.PlaylistReconciliationError, match="pending"):
        controller._sync_all_playlist_files()
    remaining = manager.mirror_journal.pending_playlist_ids()
    assert len(remaining) == 1
    assert len(list(controller._playlists_storage_dir.glob("*.json"))) == 2

    assert controller._sync_all_playlist_files() == 3
    assert manager.mirror_journal.pending_playlist_ids() == frozenset()
    assert len(list(controller._playlists_storage_dir.glob("*.json"))) == len(playlist_ids)
