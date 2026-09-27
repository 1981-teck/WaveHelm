from __future__ import annotations

from contextlib import closing
import random
import shutil
import sqlite3
import threading
from pathlib import Path

import pytest

import src.model.component_database.db_core as db_core_module
import src.model.component_database.playlist_manager as manager_module
import src.model.component_database.playlist_position as position_module
from src.model.component_database.db_core import DbCore
from src.model.component_database.playlist_manager import PlaylistManager
from src.model.component_database.playlist_position import (
    PlaylistPositionInvariantError,
)
from src.utils.exceptions import DatabaseError


RUNTIME_ROOT = Path(__file__).resolve().parent / "_playlist_position_runtime"


class DummyLocalization:
    def get_text(self, key: str, **kwargs: object) -> str:
        del kwargs
        return key


@pytest.fixture(autouse=True)
def reset_db_core_singleton():
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
    monkeypatch: pytest.MonkeyPatch, name: str
) -> tuple[PlaylistManager, DbCore, Path]:
    runtime_dir = RUNTIME_ROOT / name
    shutil.rmtree(runtime_dir, ignore_errors=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_core_module, "get_user_data_dir", lambda: runtime_dir)
    core = DbCore(db_path="playlist.db", localization_manager=DummyLocalization())
    return PlaylistManager(core), core, runtime_dir / "playlist.db"


def _sequence(
    manager: PlaylistManager, playlist_id: int
) -> tuple[list[str], list[int]]:
    rows = manager.get_playlist_items(playlist_id)
    return (
        [str(row["media_path"]) for row in rows],
        [int(row["position"]) for row in rows],
    )


def test_randomized_mutations_match_a_reference_list(monkeypatch: pytest.MonkeyPatch) -> None:
    manager, core, _ = _make_manager(monkeypatch, "randomized_model")
    playlist_id = manager.create_playlist("Randomized")
    expected: list[str] = []
    randomizer = random.Random(0x5B2026)
    next_item = 0

    for operation_index in range(500):
        choice = randomizer.randrange(3) if expected else 0
        if choice == 0:
            path = f"track-{next_item:04d}.wav"
            next_item += 1
            position = randomizer.randint(1, len(expected) + 1)
            manager.add_playlist_item(playlist_id, path, position)
            expected.insert(position - 1, path)
        elif choice == 1:
            source_index = randomizer.randrange(len(expected))
            path = expected.pop(source_index)
            manager.remove_playlist_item(playlist_id, path)
        else:
            source_index = randomizer.randrange(len(expected))
            target = randomizer.randint(1, len(expected))
            path = expected.pop(source_index)
            expected.insert(target - 1, path)
            manager.reorder_playlist_item(playlist_id, path, target)

        if operation_index % 10 == 0 or operation_index == 499:
            paths, positions = _sequence(manager, playlist_id)
            assert paths == expected
            assert positions == list(range(1, len(expected) + 1))

    core.close()


def test_concurrent_appends_produce_one_exact_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, core, _ = _make_manager(monkeypatch, "concurrent_append")
    playlist_id = manager.create_playlist("Concurrent Append")
    barrier = threading.Barrier(32)
    errors: list[Exception] = []

    def append(index: int) -> None:
        try:
            barrier.wait(timeout=10.0)
            manager.add_playlist_item(playlist_id, f"append-{index}.wav")
        except Exception as error:
            errors.append(error)

    threads = [threading.Thread(target=append, args=(index,)) for index in range(32)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20.0)

    assert errors == []
    assert all(not thread.is_alive() for thread in threads)
    paths, positions = _sequence(manager, playlist_id)
    assert set(paths) == {f"append-{index}.wav" for index in range(32)}
    assert positions == list(range(1, 33))
    core.close()


def test_concurrent_front_inserts_remain_unique_and_contiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, core, _ = _make_manager(monkeypatch, "concurrent_front")
    playlist_id = manager.create_playlist("Concurrent Front")
    barrier = threading.Barrier(16)
    errors: list[Exception] = []

    def insert(index: int) -> None:
        try:
            barrier.wait(timeout=10.0)
            manager.add_playlist_item(playlist_id, f"front-{index}.wav", 1)
        except Exception as error:
            errors.append(error)

    threads = [threading.Thread(target=insert, args=(index,)) for index in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20.0)

    assert errors == []
    assert all(not thread.is_alive() for thread in threads)
    paths, positions = _sequence(manager, playlist_id)
    assert set(paths) == {f"front-{index}.wav" for index in range(16)}
    assert positions == list(range(1, 17))
    core.close()


def test_shift_rowcount_drift_rolls_back_the_complete_insert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, core, _ = _make_manager(monkeypatch, "rowcount_drift")
    playlist_id = manager.create_playlist("Fault")
    for path in ("one.wav", "two.wav", "three.wav"):
        manager.add_playlist_item(playlist_id, path)
    assert core.conn is not None
    core.conn.execute(
        "CREATE TRIGGER ignore_one_position_update "
        "BEFORE UPDATE OF position ON playlist_items "
        "WHEN OLD.media_path = 'two.wav' BEGIN SELECT RAISE(IGNORE); END"
    )
    core.conn.commit()

    with pytest.raises(PlaylistPositionInvariantError, match="affected"):
        manager.add_playlist_item(playlist_id, "candidate.wav", 1)

    paths, positions = _sequence(manager, playlist_id)
    assert paths == ["one.wav", "two.wav", "three.wav"]
    assert positions == [1, 2, 3]
    core.close()


def test_mirror_failure_rolls_back_reorder_and_positions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, core, _ = _make_manager(monkeypatch, "mirror_rollback")
    playlist_id = manager.create_playlist("Mirror")
    for path in ("one.wav", "two.wav", "three.wav"):
        manager.add_playlist_item(playlist_id, path)

    def fail_mirror(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise manager_module.PlaylistMirrorJournalError("injected mirror failure")

    monkeypatch.setattr(manager_module, "enqueue_playlist_mirror_sync", fail_mirror)
    with pytest.raises(DatabaseError, match="reordering"):
        manager.reorder_playlist_item(playlist_id, "three.wav", 1)

    assert _sequence(manager, playlist_id) == (
        ["one.wav", "two.wav", "three.wav"],
        [1, 2, 3],
    )
    core.close()


def test_global_capacity_is_enforced_before_a_new_insert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, core, _ = _make_manager(monkeypatch, "global_capacity")
    first_id = manager.create_playlist("First")
    second_id = manager.create_playlist("Second")
    manager.add_playlist_item(first_id, "one.wav")
    manager.add_playlist_item(second_id, "two.wav")
    monkeypatch.setattr(position_module, "MAX_PLAYLIST_ITEMS_PER_DATABASE", 2)

    with pytest.raises(PlaylistPositionInvariantError, match="capacity"):
        manager.add_playlist_item(first_id, "three.wav")

    assert _sequence(manager, first_id) == (["one.wav"], [1])
    assert _sequence(manager, second_id) == (["two.wav"], [1])
    core.close()


def test_per_playlist_capacity_is_enforced_without_sparse_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, core, _ = _make_manager(monkeypatch, "playlist_capacity")
    playlist_id = manager.create_playlist("Capacity")
    manager.add_playlist_item(playlist_id, "one.wav")
    manager.add_playlist_item(playlist_id, "two.wav")
    monkeypatch.setattr(position_module, "MAX_PLAYLIST_ITEMS_PER_PLAYLIST", 2)

    with pytest.raises(PlaylistPositionInvariantError, match="capacity"):
        manager.add_playlist_item(playlist_id, "three.wav")

    assert _sequence(manager, playlist_id) == (["one.wav", "two.wav"], [1, 2])
    core.close()


def test_media_path_hard_cap_fails_before_database_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, core, _ = _make_manager(monkeypatch, "path_capacity")
    playlist_id = manager.create_playlist("Path")

    with pytest.raises(ValueError, match="hard cap"):
        manager.add_playlist_item(playlist_id, "x" * 32_769)

    assert _sequence(manager, playlist_id) == ([], [])
    core.close()


def test_readers_never_observe_transient_shift_positions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, core, db_path = _make_manager(monkeypatch, "reader_visibility")
    playlist_id = manager.create_playlist("Visibility")
    for index in range(12):
        manager.add_playlist_item(playlist_id, f"track-{index}.wav")
    errors: list[str] = []
    writer_done = threading.Event()

    def writer() -> None:
        try:
            for index in range(120):
                path = f"track-{index % 12}.wav"
                target = index % 12 + 1
                manager.reorder_playlist_item(playlist_id, path, target)
        finally:
            writer_done.set()

    def reader() -> None:
        with closing(sqlite3.connect(db_path, timeout=10.0)) as connection:
            while not writer_done.is_set():
                row = connection.execute(
                    "SELECT COUNT(*), MIN(position), MAX(position), COUNT(DISTINCT position) "
                    "FROM playlist_items WHERE playlist_id = ?",
                    (playlist_id,),
                ).fetchone()
                if row != (12, 1, 12, 12):
                    errors.append(repr(row))
                    return

    writer_thread = threading.Thread(target=writer)
    reader_thread = threading.Thread(target=reader)
    reader_thread.start()
    writer_thread.start()
    writer_thread.join(timeout=30.0)
    reader_thread.join(timeout=30.0)

    assert not writer_thread.is_alive()
    assert not reader_thread.is_alive()
    assert errors == []
    assert _sequence(manager, playlist_id)[1] == list(range(1, 13))
    core.close()


def test_runtime_validation_rejects_duplicate_positions_without_unique_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, core, _ = _make_manager(monkeypatch, "duplicate_position_drift")
    playlist_id = manager.create_playlist("Duplicate Drift")
    for path in ("one.wav", "two.wav", "three.wav"):
        manager.add_playlist_item(playlist_id, path)
    assert core.conn is not None
    core.conn.execute("DROP INDEX ux_playlist_items_position")
    core.conn.execute(
        "UPDATE playlist_items SET position = 1 WHERE media_path = 'two.wav'"
    )
    core.conn.commit()

    with pytest.raises(PlaylistPositionInvariantError, match="contiguous"):
        manager.get_playlist_items(playlist_id)

    core.close()
