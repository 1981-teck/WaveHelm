from __future__ import annotations

import json
import os
from pathlib import Path
import threading

import pytest

import src.controller.library_catalog_store as catalog_module
import src.controller.library_controller as controller_module
from src.audio.audio_events import AudioEventType
from src.controller.library_catalog_store import (
    CatalogCommitStatus,
    LibraryCatalogConflictError,
    LibraryCatalogStore,
    LibraryCatalogWriteBlockedError,
    LibraryCatalogWriteError,
)
from src.controller.library_controller import LibraryController
from src.controller.library_mirror import LibraryMirrorStatus
from src.model.media_file import MediaFile, MediaType
from src.utils.exceptions import NotFoundError


class RecordingBus:
    def __init__(self) -> None:
        self.calls: list[tuple[AudioEventType, dict[str, object]]] = []
        self.failure: Exception | None = None
        self._lock = threading.Lock()

    def publish(self, event_type: AudioEventType, payload: dict[str, object]) -> None:
        if self.failure is not None:
            raise self.failure
        with self._lock:
            self.calls.append((event_type, dict(payload)))


class MirrorDatabase:
    def __init__(self, rows: list[dict[str, object]] | None = None) -> None:
        self.rows = {str(row["path"]): dict(row) for row in rows or []}
        self.failures: dict[str, Exception] = {}
        self.operations: list[tuple[str, str | None]] = []
        self._lock = threading.Lock()

    def _raise_if_failed(self, operation: str) -> None:
        error = self.failures.get(operation)
        if error is not None:
            raise error

    def get_all_library_items(self) -> list[dict[str, object]]:
        self._raise_if_failed("inventory")
        with self._lock:
            self.operations.append(("inventory", None))
            return [dict(row) for row in self.rows.values()]

    def add_library_item(self, **values: object) -> None:
        self._raise_if_failed("upsert")
        path = values.get("path")
        assert isinstance(path, str)
        with self._lock:
            self.rows[path] = dict(values)
            self.operations.append(("upsert", path))

    def remove_library_item(self, path: str) -> None:
        self._raise_if_failed("remove")
        with self._lock:
            self.operations.append(("remove", path))
            if path not in self.rows:
                raise NotFoundError(f"missing: {path}")
            del self.rows[path]

    def is_favorite(self, path: str) -> bool:
        return False

    def add_favorite(self, **values: object) -> None:
        return None

def _media(root: Path, name: str, title: str | None = None) -> MediaFile:
    return MediaFile(
        path=str(root / name),
        title=title or name,
        media_type=MediaType.AUDIO,
        duration=1.0,
        metadata={"artist": "Wave"},
    )

def _make_controller(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    *,
    database: MirrorDatabase | None = None,
    bus: RecordingBus | None = None,
    loader: object | None = None,
) -> tuple[LibraryController, RecordingBus, MirrorDatabase]:
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        controller_module,
        "get_app_data_path",
        lambda *parts, create=True: root.joinpath(*parts) if parts else root,
    )
    if loader is not None:
        monkeypatch.setattr(controller_module, "load_library_media_files", loader)
    selected_bus = bus or RecordingBus()
    selected_database = database or MirrorDatabase()
    return (
        LibraryController(selected_bus, selected_database),
        selected_bus,
        selected_database,
    )

def test_catalog_store_commit_and_directory_sync_degradation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "library.json"
    store = LibraryCatalogStore(target)
    assert store.load().paths == ()

    def committed_without_directory_sync(path: Path, payload: bytes) -> bool:
        path.write_bytes(payload)
        return False

    monkeypatch.setattr(
        catalog_module,
        "write_bytes_atomic_durable",
        committed_without_directory_sync,
    )
    status = store.commit(["C:/Music/one.wav"])

    assert status is CatalogCommitStatus.COMMITTED_WITHOUT_DIRECTORY_SYNC
    assert json.loads(target.read_text(encoding="utf-8")) == ["C:/Music/one.wav"]
    sync_calls: list[Path] = []
    monkeypatch.setattr(catalog_module, "sync_parent_directory", sync_calls.append)
    assert store.commit(["C:/Music/one.wav"]) is CatalogCommitStatus.DURABLE
    assert sync_calls == [tmp_path]
    reloaded = LibraryCatalogStore(target).load()
    assert reloaded.paths == ("C:/Music/one.wav",)
    assert reloaded.write_blocked is False

def test_catalog_store_blocks_partial_and_malformed_sources(tmp_path: Path) -> None:
    target = tmp_path / "library.json"
    original = json.dumps(["valid.wav", *([None] * 100)]).encode("utf-8")
    target.write_bytes(original)
    store = LibraryCatalogStore(target)

    loaded = store.load()
    assert loaded.paths == ("valid.wav",)
    assert loaded.write_blocked is True
    assert len(loaded.issues) == 65
    with pytest.raises(LibraryCatalogWriteBlockedError):
        store.commit(["replacement.wav"])
    assert target.read_bytes() == original

    target.write_text("{broken", encoding="utf-8")
    malformed = LibraryCatalogStore(target).load()
    assert malformed.paths == ()
    assert malformed.write_blocked is True

    target.write_bytes((b"[" * 10_000) + (b"]" * 10_000))
    deeply_nested = LibraryCatalogStore(target).load()
    assert deeply_nested.paths == ()
    assert deeply_nested.write_blocked is True
    assert "RecursionError" in deeply_nested.issues[0]


def test_catalog_store_detects_external_change(tmp_path: Path) -> None:
    target = tmp_path / "library.json"
    target.write_text(json.dumps(["one.wav"]), encoding="utf-8")
    store = LibraryCatalogStore(target)
    assert store.load().write_blocked is False
    target.write_text(json.dumps(["external.wav"]), encoding="utf-8")

    with pytest.raises(LibraryCatalogConflictError):
        store.commit(["one.wav", "two.wav"])
    assert json.loads(target.read_text(encoding="utf-8")) == ["external.wav"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_catalog_store_rejects_symbolic_link(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_text("[]", encoding="utf-8")
    link = tmp_path / "library.json"
    link.symlink_to(source)

    loaded = LibraryCatalogStore(link).load()
    assert loaded.write_blocked is True
    assert "symbolic link" in loaded.issues[0]


def test_catalog_store_rejects_oversize_and_control_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(catalog_module, "_MAX_CATALOG_BYTES", 16)
    target = tmp_path / "library.json"
    target.write_bytes(b"[\"12345678901234567890\"]")
    assert LibraryCatalogStore(target).load().write_blocked is True

    clean = LibraryCatalogStore(tmp_path / "clean.json")
    clean.load()
    with pytest.raises(LibraryCatalogWriteError, match="control character"):
        clean.commit(["bad\npath.wav"])


def test_state_preparation_failure_occurs_before_catalog_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, bus, database = _make_controller(monkeypatch, tmp_path)
    before_operations = list(database.operations)
    monkeypatch.setattr(
        controller_module,
        "_unique_paths",
        lambda paths: (_ for _ in ()).throw(MemoryError("injected")),
    )

    with pytest.raises(MemoryError, match="injected"):
        controller.add_media_files_from_objects([_media(tmp_path, "one.wav")])
    assert controller.get_all_media() == []
    assert database.operations == before_operations
    assert bus.calls == []
    assert not (tmp_path / "library.json").exists()


def test_catalog_failure_leaves_memory_database_and_events_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, bus, database = _make_controller(monkeypatch, tmp_path)
    media = _media(tmp_path, "one.wav")
    before_operations = list(database.operations)
    monkeypatch.setattr(
        controller._catalog_store,
        "commit",
        lambda paths: (_ for _ in ()).throw(LibraryCatalogWriteError("injected")),
    )

    with pytest.raises(LibraryCatalogWriteError, match="injected"):
        controller.add_media_files_from_objects([media])
    assert controller.get_all_media() == []
    assert controller.catalog_paths == ()
    assert database.operations == before_operations
    assert bus.calls == []
    assert not (tmp_path / "library.json").exists()


def test_mirror_failure_is_degraded_after_canonical_commit_and_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = MirrorDatabase()
    controller, bus, _ = _make_controller(monkeypatch, tmp_path, database=database)
    database.failures["upsert"] = RuntimeError("database offline")

    result = controller.add_media_files_from_objects([_media(tmp_path, "one.wav")])

    assert result.catalog_status is CatalogCommitStatus.DURABLE
    assert result.mirror_result.status is LibraryMirrorStatus.DEGRADED
    assert controller.get_media_by_path(str(tmp_path / "one.wav")) is not None
    assert json.loads((tmp_path / "library.json").read_text(encoding="utf-8")) == [
        str(tmp_path / "one.wav")
    ]
    assert bus.calls[0][1]["mirror_status"] == "degraded"
    assert bus.calls[-1][1]["color"] == "orange"

    database.failures.clear()
    recovered = controller.save_library()
    assert recovered.mirror_result.status is LibraryMirrorStatus.SYNCHRONIZED
    assert str(tmp_path / "one.wav") in database.rows


def test_hostile_database_error_is_contained_per_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class HostileError(RuntimeError):
        def __str__(self) -> str:
            raise RuntimeError("render failed")

    database = MirrorDatabase()
    original_add = database.add_library_item

    def selective_add(**values: object) -> None:
        if Path(str(values.get("path", ""))).name.startswith("hostile-"):
            raise HostileError()
        original_add(**values)

    monkeypatch.setattr(database, "add_library_item", selective_add)
    controller, _, _ = _make_controller(monkeypatch, tmp_path, database=database)
    hostile = [_media(tmp_path, f"hostile-{index}.wav") for index in range(140)]
    result = controller.add_media_files_from_objects(
        [*hostile, _media(tmp_path, "healthy.wav")],
        emit_event=False,
        emit_feedback=False,
    )
    assert result.mirror_result.status is LibraryMirrorStatus.DEGRADED
    assert result.mirror_result.failures[0].error_type == "HostileError"
    assert "unprintable" in result.mirror_result.failures[0].message
    assert len(result.mirror_result.failures) == 129
    assert result.mirror_result.failures[-1].operation == "failure_limit"
    assert str(tmp_path / "healthy.wav") in database.rows


def test_remove_write_failure_and_mirror_failure_have_distinct_outcomes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = MirrorDatabase()
    controller, bus, _ = _make_controller(monkeypatch, tmp_path, database=database)
    path = str(tmp_path / "one.wav")
    controller.add_media_files_from_objects([_media(tmp_path, "one.wav")])
    committed_bytes = (tmp_path / "library.json").read_bytes()
    bus.calls.clear()
    original_commit = controller._catalog_store.commit
    monkeypatch.setattr(
        controller._catalog_store,
        "commit",
        lambda paths: (_ for _ in ()).throw(LibraryCatalogWriteError("disk full")),
    )
    with pytest.raises(LibraryCatalogWriteError, match="disk full"):
        controller.remove_media(path)
    assert controller.get_media_by_path(path) is not None
    assert (tmp_path / "library.json").read_bytes() == committed_bytes
    assert path in database.rows
    assert bus.calls == []

    monkeypatch.setattr(controller._catalog_store, "commit", original_commit)
    database.failures["remove"] = RuntimeError("delete unavailable")
    assert controller.remove_media(path) is True
    assert controller.get_media_by_path(path) is None
    assert json.loads((tmp_path / "library.json").read_text(encoding="utf-8")) == []
    assert path in database.rows
    assert controller.last_persistence_result is not None
    assert controller.last_persistence_result.mirror_result.status is LibraryMirrorStatus.DEGRADED

    database.failures.clear()
    assert controller.save_library().mirror_result.is_synchronized is True
    assert path not in database.rows


def test_unavailable_canonical_path_survives_later_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = str(tmp_path / "offline.wav")
    available = str(tmp_path / "available.wav")
    (tmp_path / "library.json").write_text(json.dumps([missing]), encoding="utf-8")
    loader = lambda paths: []
    controller, _, _ = _make_controller(monkeypatch, tmp_path, loader=loader)
    assert controller.unresolved_catalog_paths == (missing,)

    controller.add_media_files_from_objects([_media(tmp_path, "available.wav")])
    persisted = json.loads((tmp_path / "library.json").read_text(encoding="utf-8"))
    assert persisted == [missing, available]
    assert controller.unresolved_catalog_paths == (missing,)


def test_untrusted_catalog_never_deletes_stale_mirror_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    valid = str(tmp_path / "valid.wav")
    stale = str(tmp_path / "stale.wav")
    (tmp_path / "library.json").write_text(json.dumps([valid, None]), encoding="utf-8")
    database = MirrorDatabase([{"path": stale, "title": "Stale"}])
    loader = lambda paths: [_media(tmp_path, "valid.wav")]
    controller, _, _ = _make_controller(
        monkeypatch,
        tmp_path,
        database=database,
        loader=loader,
    )

    assert controller.catalog_write_blocked is True
    assert stale in database.rows
    with pytest.raises(LibraryCatalogWriteBlockedError):
        controller.add_media_files_from_objects([_media(tmp_path, "new.wav")])
    assert stale in database.rows


def test_concurrent_additions_preserve_every_committed_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, _, database = _make_controller(monkeypatch, tmp_path)
    media = [_media(tmp_path, f"track-{index}.wav") for index in range(24)]
    errors: list[BaseException] = []

    def worker(item: MediaFile) -> None:
        try:
            controller.add_media_files_from_objects(
                [item],
                emit_event=False,
                emit_feedback=False,
            )
        except BaseException as error:  # test harness captures thread failures
            errors.append(error)

    threads = [threading.Thread(target=worker, args=(item,)) for item in media]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    assert all(not thread.is_alive() for thread in threads)
    expected = {item.path for item in media}
    assert set(controller.catalog_paths) == expected
    assert set(json.loads((tmp_path / "library.json").read_text(encoding="utf-8"))) == expected
    assert set(database.rows) == expected


def test_external_conflict_and_event_failure_do_not_corrupt_commit_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, bus, database = _make_controller(monkeypatch, tmp_path)
    first = _media(tmp_path, "one.wav")
    controller.add_media_files_from_objects([first])
    external = [str(tmp_path / "external.wav")]
    (tmp_path / "library.json").write_text(json.dumps(external), encoding="utf-8")
    before_operations = list(database.operations)

    with pytest.raises(LibraryCatalogConflictError):
        controller.add_media_files_from_objects([_media(tmp_path, "two.wav")])
    assert [media.path for media in controller.get_all_media()] == [first.path]
    assert database.operations == before_operations
    assert json.loads((tmp_path / "library.json").read_text(encoding="utf-8")) == external

    repaired_root = tmp_path / "event-boundary"
    failing_bus = RecordingBus()
    event_controller, _, event_database = _make_controller(
        monkeypatch,
        repaired_root,
        database=MirrorDatabase(),
        bus=failing_bus,
    )
    failing_bus.failure = KeyError("subscriber failure")
    result = event_controller.add_media_files_from_objects([_media(repaired_root, "ok.wav")])
    assert result.is_fully_synchronized is True
    assert str(repaired_root / "ok.wav") in event_database.rows
    assert (repaired_root / "library.json").is_file()


def test_input_media_is_detached_before_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, _, _ = _make_controller(monkeypatch, tmp_path)
    source = _media(tmp_path, "one.wav", "Original")
    controller.add_media_files_from_objects([source], emit_event=False, emit_feedback=False)
    source.title = "Mutated externally"
    assert controller.get_media_by_path(source.path).title == "Original"

    exposed = controller.get_all_media()[0]
    exposed.title = "Mutated through getter"
    found = controller.get_media_by_path(source.path)
    assert found is not None
    found.title = "Mutated through lookup"
    assert controller.get_media_by_path(source.path).title == "Original"
