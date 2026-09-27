from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import logging
import math
from pathlib import Path
import shutil
import threading

import pytest

import src.model.component_database.db_core as db_core_module
from src.model.component_database.db_core import DbCore
from src.model.component_database.history_contract import (
    MAX_HISTORY_METADATA_ITEMS,
    MAX_HISTORY_QUERY_LIMIT,
    HistoryCapabilityError,
    HistoryDataError,
)
from src.model.component_database.history_manager import HistoryManager
from src.utils.exceptions import DatabaseError


RUNTIME_ROOT = Path(__file__).resolve().parent / "_history_manager_runtime"


class DummyLocalization:
    def get_text(self, key: str, **kwargs: object) -> str:
        return key


@pytest.fixture(autouse=True)
def reset_db_core_singleton() -> None:
    _reset_core()
    yield
    _reset_core()


def _reset_core() -> None:
    instance = getattr(DbCore, "_instance", None)
    if instance is not None and getattr(instance, "conn", None) is not None:
        instance.close()
    DbCore._instance = None


def _make_manager(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    *,
    max_entries: int = 10_000,
) -> tuple[HistoryManager, DbCore, Path]:
    runtime = RUNTIME_ROOT / name
    shutil.rmtree(runtime, ignore_errors=True)
    runtime.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_core_module, "get_user_data_dir", lambda: runtime)
    core = DbCore(db_path="history.db", localization_manager=DummyLocalization())
    return HistoryManager(core, max_entries=max_entries), core, runtime


def test_add_history_entry_uses_path_first_contract_and_snapshots_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _core, _runtime = _make_manager(monkeypatch, "add_and_read")
    metadata: dict[str, object] = {"artist": "Alpha", "nested": [1]}
    additional = {"source": "library"}

    record_id = manager.add_history_entry(
        "C:/media/song.wav",
        "Song",
        "audio",
        12,
        metadata,
        additional,
    )
    metadata["artist"] = "Mutated"
    nested = metadata["nested"]
    assert isinstance(nested, list)
    nested.append(2)

    items = manager.get_history()
    assert record_id == items[0]["id"]
    assert items[0]["path"] == "C:/media/song.wav"
    assert items[0]["title"] == "Song"
    assert items[0]["duration"] == 12.0
    assert items[0]["metadata"] == {
        "artist": "Alpha",
        "nested": [1],
        "source": "library",
    }
    assert datetime.fromisoformat(items[0]["timestamp"]).utcoffset() is not None


def test_legacy_add_history_item_preserves_title_path_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _core, _runtime = _make_manager(monkeypatch, "legacy_alias")

    manager.add_history_item("Legacy title", "legacy.wav", "audio", 3.0)

    item = manager.get_history()[0]
    assert item["title"] == "Legacy title"
    assert item["path"] == "legacy.wav"


@pytest.mark.parametrize("value", [0, -1, MAX_HISTORY_QUERY_LIMIT + 1])
def test_get_history_rejects_out_of_range_limits(
    monkeypatch: pytest.MonkeyPatch, value: int
) -> None:
    manager, _core, _runtime = _make_manager(monkeypatch, f"limit_{value}")
    manager.add_history_entry("one.wav", "One", "audio", 1.0)

    with pytest.raises(ValueError, match="between"):
        manager.get_history(limit=value)


@pytest.mark.parametrize("value", [False, 1.5, "1"])
def test_get_history_rejects_ambiguous_limit_types(
    monkeypatch: pytest.MonkeyPatch, value: object
) -> None:
    manager, _core, _runtime = _make_manager(monkeypatch, "limit_types")

    with pytest.raises(TypeError, match="integer"):
        manager.get_history(limit=value)  # type: ignore[arg-type]


def test_none_limit_resolves_to_bounded_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, core, _runtime = _make_manager(monkeypatch, "limit_default")
    with core.shared_transaction(mode="IMMEDIATE") as transaction:
        for index in range(205):
            transaction.execute(
                "INSERT INTO history "
                "(title, path, media_type, duration, metadata, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    f"Track {index}",
                    f"track-{index}.wav",
                    "audio",
                    1.0,
                    None,
                    "2026-09-03T00:00:00+00:00",
                ),
            )

    assert len(manager.get_history(limit=None)) == 200
    count = core._execute_query("SELECT COUNT(*) FROM history", fetch_one=True)
    assert count is not None and count[0] == 205


def test_retention_keeps_only_newest_entries_and_survives_reopen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, core, runtime = _make_manager(
        monkeypatch, "retention", max_entries=3
    )
    for index in range(6):
        manager.add_history_entry(
            f"track-{index}.wav", f"Track {index}", "audio", float(index)
        )

    assert [item["title"] for item in manager.get_history(limit=10)] == [
        "Track 5",
        "Track 4",
        "Track 3",
    ]
    core.close()
    DbCore._instance = None
    monkeypatch.setattr(db_core_module, "get_user_data_dir", lambda: runtime)
    reopened_core = DbCore(db_path="history.db", localization_manager=DummyLocalization())
    reopened = HistoryManager(reopened_core, max_entries=3)
    assert [item["title"] for item in reopened.get_history(limit=10)] == [
        "Track 5",
        "Track 4",
        "Track 3",
    ]


def test_retention_failure_rolls_back_insert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, core, _runtime = _make_manager(monkeypatch, "retention_rollback")

    def fail_retention(_transaction: object) -> None:
        raise DatabaseError("injected retention failure")

    monkeypatch.setattr(manager, "_enforce_retention", fail_retention)
    with pytest.raises(DatabaseError, match="adding history"):
        manager.add_history_entry("failed.wav", "Failed", "audio", 1.0)

    row = core._execute_query("SELECT COUNT(*) FROM history", fetch_one=True)
    assert row is not None and row[0] == 0


def test_metadata_validation_fails_before_any_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, core, _runtime = _make_manager(monkeypatch, "metadata_validation")
    cyclic: list[object] = []
    cyclic.append(cyclic)

    invalid_values: list[dict[str, object]] = [
        {"value": math.nan},
        {"cycle": cyclic},
        {"unsupported": {1, 2}},
        {"oversized": list(range(MAX_HISTORY_METADATA_ITEMS + 1))},
        {"invalid_unicode": "bad\ud800"},
    ]
    for metadata in invalid_values:
        with pytest.raises((TypeError, ValueError)):
            manager.add_history_entry("bad.wav", "Bad", "audio", 1.0, metadata)

    row = core._execute_query("SELECT COUNT(*) FROM history", fetch_one=True)
    assert row is not None and row[0] == 0


def test_additional_data_cannot_overwrite_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, core, _runtime = _make_manager(monkeypatch, "metadata_collision")

    with pytest.raises(ValueError, match="duplicate keys"):
        manager.add_history_entry(
            "bad.wav",
            "Bad",
            "audio",
            1.0,
            {"source": "metadata"},
            {"source": "extension"},
        )

    row = core._execute_query("SELECT COUNT(*) FROM history", fetch_one=True)
    assert row is not None and row[0] == 0


@pytest.mark.parametrize(
    ("duration", "error_type"),
    [
        (-1.0, ValueError),
        (math.inf, ValueError),
        (math.nan, ValueError),
        (True, TypeError),
        pytest.param(10**10_000, ValueError, id="overflowing-integer"),
    ],
)
def test_duration_contract_rejects_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
    duration: object,
    error_type: type[Exception],
) -> None:
    manager, _core, _runtime = _make_manager(monkeypatch, "invalid_duration")

    with pytest.raises(error_type):
        manager.add_history_entry(
            "bad.wav", "Bad", "audio", duration  # type: ignore[arg-type]
        )




@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("path", "bad\npath.wav"),
        ("path", "\ntrack.wav"),
        ("title", "bad\ttitle"),
        ("media_type", "audio\x7f"),
        ("title", "bad\ud800"),
    ],
)
def test_required_text_rejects_controls_and_invalid_unicode(
    monkeypatch: pytest.MonkeyPatch, field: str, value: str
) -> None:
    manager, core, _runtime = _make_manager(monkeypatch, "invalid_required_text")
    values = {
        "path": "track.wav",
        "title": "Track",
        "media_type": "audio",
    }
    values[field] = value

    with pytest.raises(ValueError):
        manager.add_history_entry(
            values["path"], values["title"], values["media_type"], 1.0
        )

    count = core._execute_query("SELECT COUNT(*) FROM history", fetch_one=True)
    assert count is not None and count[0] == 0


@pytest.mark.parametrize(
    "stored_metadata",
    ["{broken", "[]", '{"duplicate":1,"duplicate":2}', '{"value":NaN}'],
)
def test_corrupt_metadata_raises_instead_of_returning_empty_object(
    monkeypatch: pytest.MonkeyPatch, stored_metadata: str
) -> None:
    manager, core, _runtime = _make_manager(monkeypatch, "corrupt_metadata")
    core._execute_write(
        "INSERT INTO history "
        "(title, path, media_type, duration, metadata, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            "Broken",
            "broken.wav",
            "audio",
            1.0,
            stored_metadata,
            "2026-09-03T00:00:00",
        ),
    )

    with pytest.raises(HistoryDataError, match="supported contract"):
        manager.get_history()


def test_persisted_timestamp_requires_a_time_component(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, core, _runtime = _make_manager(monkeypatch, "invalid_timestamp")
    core._execute_write(
        "INSERT INTO history "
        "(title, path, media_type, duration, metadata, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("Broken", "broken.wav", "audio", 1.0, None, "2026-09-03"),
    )

    with pytest.raises(HistoryDataError, match="supported contract"):
        manager.get_history()


def test_completed_downloads_is_explicitly_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, core, _runtime = _make_manager(monkeypatch, "downloads_capability")
    manager.add_history_entry("playback.wav", "Playback", "audio", 1.0)

    with pytest.raises(HistoryCapabilityError, match="without download status"):
        manager.get_completed_downloads()

    row = core._execute_query("SELECT COUNT(*) FROM history", fetch_one=True)
    assert row is not None and row[0] == 1


def test_clear_history_returns_exact_count_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _core, _runtime = _make_manager(monkeypatch, "clear")
    for index in range(3):
        manager.add_history_entry(
            f"track-{index}.wav", f"Track {index}", "audio", 1.0
        )

    assert manager.clear_history() == 3
    assert manager.clear_history() == 0
    assert manager.get_history() == []


def test_concurrent_writers_preserve_unique_ids_and_retention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, core, _runtime = _make_manager(
        monkeypatch, "concurrent", max_entries=10
    )
    workers = 24
    barrier = threading.Barrier(workers + 1)
    result_lock = threading.Lock()
    record_ids: list[int] = []
    errors: list[str] = []

    def write(index: int) -> None:
        barrier.wait(timeout=5.0)
        try:
            record_id = manager.add_history_entry(
                f"track-{index}.wav", f"Track {index}", "audio", 1.0
            )
            with result_lock:
                record_ids.append(record_id)
        except Exception as error:
            with result_lock:
                errors.append(f"{type(error).__name__}: {error}")

    threads = [threading.Thread(target=write, args=(index,)) for index in range(workers)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=5.0)
    for thread in threads:
        thread.join(timeout=10.0)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert len(record_ids) == workers
    assert len(set(record_ids)) == workers
    count = core._execute_query("SELECT COUNT(*) FROM history", fetch_one=True)
    assert count is not None and count[0] == 10
    assert len(manager.get_history(limit=10)) == 10


class _RaisingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        raise RuntimeError("injected logging failure")


def test_success_does_not_depend_on_external_logging_handlers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _core, _runtime = _make_manager(monkeypatch, "logging_boundary")
    logger = logging.getLogger("src.model.component_database.history_manager")
    handler = _RaisingHandler()
    previous_propagate = logger.propagate
    logger.addHandler(handler)
    logger.propagate = False
    try:
        record_id = manager.add_history_entry(
            "logged.wav", "Logged", "audio", 1.0
        )
        assert record_id > 0
        assert manager.clear_history() == 1
    finally:
        logger.removeHandler(handler)
        logger.propagate = previous_propagate


def test_history_retention_configuration_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _manager, core, _runtime = _make_manager(monkeypatch, "retention_config")

    with pytest.raises(TypeError):
        HistoryManager(core, max_entries=True)
    with pytest.raises(ValueError):
        HistoryManager(core, max_entries=0)
