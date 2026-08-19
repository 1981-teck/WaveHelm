from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest

import src.controller.playlist_controller_storage as playlist_storage
import src.model.component_database.db_core as db_core_module
import src.model.component_database.playlist_mirror_maintenance as maintenance_module
from src.model.component_database.db_core import DbCore
from src.model.component_database.playlist_manager import PlaylistManager
from src.model.component_database.playlist_mirror_journal import MAX_MIRROR_ATTEMPTS
from src.model.component_database.playlist_mirror_maintenance import (
    PlaylistMirrorMaintenanceCorruptionError,
    PlaylistMirrorMaintenanceError,
    PlaylistMirrorRepairRejectedError,
)
from src.model.component_database.playlist_mirror_schema import (
    COMPONENT_SCHEMA_TABLE,
    PLAYLIST_MIRROR_COMPONENT,
    PLAYLIST_MIRROR_REPAIR_TABLE,
    PLAYLIST_MIRROR_SCHEMA_VERSION,
    PlaylistMirrorSchemaError,
    ensure_playlist_mirror_schema,
    read_playlist_mirror_schema_version,
)


class DummyLocalization:
    def get_text(self, key: str, **_kwargs: object) -> str:
        return key


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


def _exhaust(manager: PlaylistManager, playlist_id: int) -> None:
    for attempt in range(MAX_MIRROR_ATTEMPTS):
        recorded = manager.mirror_journal.record_failure(
            playlist_id,
            RuntimeError(f"failure-{attempt + 1}"),
            now=float(attempt + 1),
        )
        assert recorded is True


def _create_legacy_outbox(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE playlist_mirror_outbox (
            playlist_id INTEGER PRIMARY KEY CHECK (playlist_id > 0),
            operation TEXT NOT NULL CHECK (operation IN ('sync', 'delete')),
            playlist_name TEXT NOT NULL,
            stale_names TEXT NOT NULL DEFAULT '[]',
            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (
                attempt_count BETWEEN 0 AND 8
            ),
            next_attempt_at REAL NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def test_fresh_schema_is_versioned_and_exposed_by_playlist_manager(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, core = _make_manager(tmp_path, monkeypatch)

    assert manager.mirror_maintenance.schema_version() == PLAYLIST_MIRROR_SCHEMA_VERSION
    connection = _connection(core)
    version_row = connection.execute(
        f"SELECT schema_version FROM {COMPONENT_SCHEMA_TABLE} WHERE component = ?",
        (PLAYLIST_MIRROR_COMPONENT,),
    ).fetchone()
    repair_table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (PLAYLIST_MIRROR_REPAIR_TABLE,),
    ).fetchone()

    assert version_row is not None and int(version_row[0]) == 2
    assert repair_table is not None


def test_legacy_outbox_migrates_without_losing_pending_jobs() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        _create_legacy_outbox(connection)
        connection.execute(
            """
            INSERT INTO playlist_mirror_outbox (
                playlist_id, operation, playlist_name, stale_names,
                attempt_count, next_attempt_at, last_error, created_at, updated_at
            ) VALUES (1, 'sync', 'Legacy', '[]', 8, 300, 'blocked', ?, ?)
            """,
            ("2026-08-19T00:00:00+00:00", "2026-08-19T00:00:00+00:00"),
        )
        connection.commit()

        assert ensure_playlist_mirror_schema(connection) == 2
        row = connection.execute(
            "SELECT playlist_name, attempt_count, last_error "
            "FROM playlist_mirror_outbox WHERE playlist_id = 1"
        ).fetchone()

        assert row == ("Legacy", 8, "blocked")
        assert read_playlist_mirror_schema_version(connection) == 2
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (PLAYLIST_MIRROR_REPAIR_TABLE,),
        ).fetchone() is not None


def test_schema_rejects_future_versions_and_incomplete_legacy_tables() -> None:
    with closing(sqlite3.connect(":memory:")) as future:
        assert ensure_playlist_mirror_schema(future) == 2
        future.execute(
            f"UPDATE {COMPONENT_SCHEMA_TABLE} SET schema_version = 99 "
            "WHERE component = ?",
            (PLAYLIST_MIRROR_COMPONENT,),
        )
        future.commit()
        with pytest.raises(PlaylistMirrorSchemaError, match="newer"):
            ensure_playlist_mirror_schema(future)

    with closing(sqlite3.connect(":memory:")) as incomplete:
        incomplete.execute(
            "CREATE TABLE playlist_mirror_outbox (playlist_id INTEGER PRIMARY KEY)"
        )
        incomplete.commit()
        with pytest.raises(PlaylistMirrorSchemaError, match="missing columns"):
            ensure_playlist_mirror_schema(incomplete)


def test_bounded_inspection_reports_only_exhausted_jobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, _core = _make_manager(tmp_path, monkeypatch)
    exhausted_id = manager.create_playlist("Exhausted")
    pending_id = manager.create_playlist("Pending")
    _exhaust(manager, exhausted_id)

    inspections = manager.mirror_maintenance.inspect_jobs(
        exhausted_only=True,
        limit=maintenance_module.MAX_MAINTENANCE_BATCH,
    )

    assert [item.job.playlist_id for item in inspections] == [exhausted_id]
    assert inspections[0].exhausted is True
    assert manager.mirror_maintenance.inspect_jobs(limit=1)[0].job.playlist_id in {
        exhausted_id,
        pending_id,
    }
    with pytest.raises(PlaylistMirrorMaintenanceError, match="batch limit"):
        manager.mirror_maintenance.inspect_jobs(limit=0)


def test_rearm_is_all_or_nothing_and_requires_exhausted_jobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, _core = _make_manager(tmp_path, monkeypatch)
    exhausted_id = manager.create_playlist("Exhausted")
    pending_id = manager.create_playlist("Pending")
    _exhaust(manager, exhausted_id)

    with pytest.raises(PlaylistMirrorRepairRejectedError, match="not exhausted"):
        manager.mirror_maintenance.rearm_exhausted(
            (exhausted_id, pending_id),
            actor="operator",
            reason="verified storage permissions",
            now=1000.0,
        )

    exhausted = manager.mirror_journal.get_job(exhausted_id)
    assert exhausted is not None and exhausted.attempt_count == MAX_MIRROR_ATTEMPTS
    assert manager.mirror_maintenance.inspect_repair_events() == ()

    with pytest.raises(PlaylistMirrorRepairRejectedError, match="missing"):
        manager.mirror_maintenance.rearm_exhausted(
            (999,), actor="operator", reason="manual recovery", now=1000.0
        )


def test_rearm_records_audit_and_makes_the_job_due_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, _core = _make_manager(tmp_path, monkeypatch)
    playlist_id = manager.create_playlist("Repair")
    _exhaust(manager, playlist_id)
    exhausted = manager.mirror_journal.get_job(playlist_id)
    assert exhausted is not None

    result = manager.mirror_maintenance.rearm_exhausted(
        (playlist_id,),
        actor="local-admin",
        reason="filesystem issue resolved",
        now=2000.0,
    )
    repaired = manager.mirror_journal.get_job(playlist_id)
    events = manager.mirror_maintenance.inspect_repair_events(playlist_id=playlist_id)

    assert result.playlist_ids == (playlist_id,)
    assert result.evicted_audit_events == 0
    assert repaired is not None
    assert repaired.attempt_count == 0
    assert repaired.next_attempt_at == 2000.0
    assert repaired.last_error == ""
    assert [job.playlist_id for job in manager.mirror_journal.fetch_due(now=2000.0)] == [
        playlist_id
    ]
    assert len(events) == 1
    assert events[0].previous_attempt_count == MAX_MIRROR_ATTEMPTS
    assert events[0].previous_error == exhausted.last_error
    assert events[0].actor == "local-admin"
    assert events[0].reason == "filesystem issue resolved"


def test_repair_audit_rotation_is_bounded_and_reported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _core = _make_manager(tmp_path, monkeypatch)
    monkeypatch.setattr(maintenance_module, "MAX_REPAIR_AUDIT_EVENTS", 2)
    playlist_ids = [manager.create_playlist(f"Repair {index}") for index in range(3)]
    results = []

    for index, playlist_id in enumerate(playlist_ids):
        _exhaust(manager, playlist_id)
        results.append(
            manager.mirror_maintenance.rearm_exhausted(
                (playlist_id,),
                actor="operator",
                reason=f"repair-{index}",
                now=3000.0 + index,
            )
        )

    events = manager.mirror_maintenance.inspect_repair_events(limit=2)
    assert [result.evicted_audit_events for result in results] == [0, 0, 1]
    assert [event.playlist_id for event in events] == playlist_ids[:0:-1]


def test_repair_history_survives_database_reopen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, core = _make_manager(tmp_path, monkeypatch)
    playlist_id = manager.create_playlist("Persistent")
    _exhaust(manager, playlist_id)
    manager.mirror_maintenance.rearm_exhausted(
        (playlist_id,), actor="operator", reason="persistent audit", now=4000.0
    )
    core.close()
    DbCore._instance = None

    reopened_core = DbCore(db_path="playlist.db", localization_manager=DummyLocalization())
    reopened = PlaylistManager(reopened_core)
    events = reopened.mirror_maintenance.inspect_repair_events(playlist_id=playlist_id)

    assert reopened.mirror_maintenance.schema_version() == 2
    assert len(events) == 1
    assert events[0].reason == "persistent audit"


def test_corrupt_repair_audit_rows_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, core = _make_manager(tmp_path, monkeypatch)
    playlist_id = manager.create_playlist("Corrupt Audit")
    _exhaust(manager, playlist_id)
    manager.mirror_maintenance.rearm_exhausted(
        (playlist_id,), actor="operator", reason="initial repair", now=5000.0
    )
    connection = _connection(core)
    connection.execute(
        f"UPDATE {PLAYLIST_MIRROR_REPAIR_TABLE} SET previous_attempt_count = 1"
    )
    connection.commit()

    with pytest.raises(
        PlaylistMirrorMaintenanceCorruptionError, match="prior attempt count"
    ):
        manager.mirror_maintenance.inspect_repair_events()



def test_schema_rejects_unexpected_columns_and_missing_v2_tables() -> None:
    with closing(sqlite3.connect(":memory:")) as incompatible:
        _create_legacy_outbox(incompatible)
        incompatible.execute("ALTER TABLE playlist_mirror_outbox ADD COLUMN future TEXT")
        incompatible.commit()
        with pytest.raises(PlaylistMirrorSchemaError, match="unexpected columns"):
            ensure_playlist_mirror_schema(incompatible)

    with closing(sqlite3.connect(":memory:")) as incomplete_v2:
        assert ensure_playlist_mirror_schema(incomplete_v2) == 2
        incomplete_v2.execute(f"DROP TABLE {PLAYLIST_MIRROR_REPAIR_TABLE}")
        incomplete_v2.commit()
        with pytest.raises(PlaylistMirrorSchemaError, match="missing table"):
            ensure_playlist_mirror_schema(incomplete_v2)
        with pytest.raises(PlaylistMirrorSchemaError, match="missing table"):
            read_playlist_mirror_schema_version(incomplete_v2)


def test_unversioned_complete_v2_schema_is_adopted_without_data_loss() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        assert ensure_playlist_mirror_schema(connection) == 2
        connection.execute(
            """
            INSERT INTO playlist_mirror_outbox (
                playlist_id, operation, playlist_name, stale_names,
                attempt_count, next_attempt_at, last_error, created_at, updated_at
            ) VALUES (1, 'sync', 'Current', '[]', 8, 10, 'blocked', ?, ?)
            """,
            ("2026-08-19T00:00:00+00:00", "2026-08-19T00:00:00+00:00"),
        )
        connection.execute(
            f"DELETE FROM {COMPONENT_SCHEMA_TABLE} WHERE component = ?",
            (PLAYLIST_MIRROR_COMPONENT,),
        )
        connection.commit()

        assert ensure_playlist_mirror_schema(connection) == 2
        row = connection.execute(
            "SELECT playlist_name, attempt_count FROM playlist_mirror_outbox"
        ).fetchone()
        assert row == ("Current", 8)


def test_inspection_rejects_non_text_sqlite_payloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, core = _make_manager(tmp_path, monkeypatch)
    playlist_id = manager.create_playlist("Typed")
    connection = _connection(core)
    connection.execute(
        "UPDATE playlist_mirror_outbox SET playlist_name = ? WHERE playlist_id = ?",
        (sqlite3.Binary(b"not-text"), playlist_id),
    )
    connection.commit()

    with pytest.raises(
        PlaylistMirrorMaintenanceCorruptionError, match="text fields"
    ):
        manager.mirror_maintenance.inspect_jobs()


def test_stale_inspection_cannot_rearm_a_job_that_changed_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, _core = _make_manager(tmp_path, monkeypatch)
    playlist_id = manager.create_playlist("Stale")
    _exhaust(manager, playlist_id)
    assert manager.mirror_maintenance.inspect_jobs(exhausted_only=True)
    manager.rename_playlist(playlist_id, "Stale Changed")

    with pytest.raises(PlaylistMirrorRepairRejectedError, match="not exhausted"):
        manager.mirror_maintenance.rearm_exhausted(
            (playlist_id,), actor="operator", reason="stale console view", now=6000.0
        )
    with pytest.raises(PlaylistMirrorMaintenanceError, match="outside the allowed"):
        manager.mirror_maintenance.rearm_exhausted(
            tuple(range(1, maintenance_module.MAX_MAINTENANCE_BATCH + 2)),
            actor="operator", reason="oversized batch", now=6000.0
        )


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows file locking")
def test_windows_locked_target_preserves_previous_playlist_mirror(
    tmp_path: Path,
) -> None:
    import ctypes
    from ctypes import wintypes

    target = tmp_path / "locked.json"
    target.write_bytes(b"old")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.CreateFileW(str(target), 0xC0000000, 0, None, 3, 0x80, None)
    assert handle not in (None, ctypes.c_void_p(-1).value)
    try:
        with pytest.raises(playlist_storage.PlaylistWriteError):
            playlist_storage._write_playlist_payload_atomic(target, b"new", 1)
    finally:
        assert kernel32.CloseHandle(handle)

    assert target.read_bytes() == b"old"
    assert list(tmp_path.glob(f".{target.name}.*.tmp")) == []


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows file locking")
def test_windows_unlocked_target_is_replaced_without_temporary_residue(
    tmp_path: Path,
) -> None:
    target = tmp_path / "replace.json"
    target.write_bytes(b"old")

    result = playlist_storage._write_playlist_payload_atomic(target, b"new", 2)

    assert result == target
    assert target.read_bytes() == b"new"
    assert list(tmp_path.glob(f".{target.name}.*.tmp")) == []
