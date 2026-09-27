"""Controller-boundary tests for bounded and cancellable library imports."""

from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path

import pytest

import src.controller.library_controller as controller_module
import src.controller.library_media_loader as loader_module
import src.controller.library_scan_traversal as traversal_module
from src.audio.audio_event_models import AudioEventType
from src.controller.library_controller import LibraryController
from src.controller.library_scan import (
    LibraryScanCancellation,
    LibraryScanCancelledError,
    LibraryScanLimitError,
    LibraryScanLimits,
    LibraryScanTraversalError,
)
from src.utils.media_metadata import AudioTagMetadata

RUNTIME_ROOT = Path(__file__).resolve().parent / "_library_scan_controller_runtime"


def _runtime_dir(name: str) -> Path:
    target = RUNTIME_ROOT / name
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    return target


class DummyEventBus:
    def __init__(self) -> None:
        self.calls: list[tuple[AudioEventType, dict[str, object]]] = []

    def publish(self, event_type: AudioEventType, payload: dict[str, object]) -> None:
        self.calls.append((event_type, dict(payload)))


class DummyDatabaseManager:
    def __init__(self) -> None:
        self.library_items: list[dict[str, object]] = []
        self.removed: list[str] = []

    def add_library_item(self, **kwargs: object) -> None:
        path = kwargs.get("path")
        assert isinstance(path, str)
        self.library_items = [item for item in self.library_items if item.get("path") != path]
        self.library_items.append(dict(kwargs))

    def remove_library_item(self, path: str) -> None:
        self.library_items = [item for item in self.library_items if item.get("path") != path]
        self.removed.append(path)

    def get_all_library_items(self) -> list[dict[str, object]]:
        return [dict(item) for item in self.library_items]


def _make_controller(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> tuple[LibraryController, DummyEventBus, DummyDatabaseManager, Path]:
    runtime_dir = _runtime_dir(name)
    monkeypatch.setattr(
        controller_module,
        "get_app_data_path",
        lambda *parts, create=True: runtime_dir.joinpath(*parts) if parts else runtime_dir,
    )
    event_bus = DummyEventBus()
    database = DummyDatabaseManager()
    controller = LibraryController(event_bus, database)  # type: ignore[arg-type]
    return controller, event_bus, database, runtime_dir


def _patch_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loader_module, "is_audio_file", lambda path: str(path).endswith(".mp3"))
    monkeypatch.setattr(loader_module, "is_video_file", lambda path: False)
    monkeypatch.setattr(
        loader_module,
        "read_audio_basic_metadata",
        lambda path: AudioTagMetadata(title=Path(path).stem, duration=1.0),
    )


def _assert_no_import_side_effects(
    controller: LibraryController,
    event_bus: DummyEventBus,
    database: DummyDatabaseManager,
) -> None:
    assert controller.get_all_media() == []
    assert controller.catalog_paths == ()
    assert database.library_items == []
    assert event_bus.calls == []
    assert not controller._library_path.exists()


def test_controller_commits_only_after_a_complete_bounded_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, event_bus, database, runtime_dir = _make_controller(monkeypatch, "success")
    media_path = runtime_dir / "one.mp3"
    ignored_path = runtime_dir / "notes.txt"
    media_path.write_text("x", encoding="utf-8")
    ignored_path.write_text("x", encoding="utf-8")
    _patch_audio(monkeypatch)

    result = controller.add_media_files(
        [runtime_dir],
        scan_limits=LibraryScanLimits(
            max_input_paths=1,
            max_directories=1,
            max_entries=2,
            max_media_files=1,
            max_elapsed_seconds=30.0,
        ),
    )

    assert result.added_count == 1
    assert [media.path for media in controller.get_all_media()] == [str(media_path)]
    assert [item["path"] for item in database.library_items] == [str(media_path)]
    assert json.loads(controller._library_path.read_text(encoding="utf-8")) == [str(media_path)]
    assert [event for event, _ in event_bus.calls] == [
        AudioEventType.LIBRARY_UPDATED,
        AudioEventType.FEEDBACK_MESSAGE,
    ]


def test_scan_limit_does_not_commit_partial_controller_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, event_bus, database, runtime_dir = _make_controller(monkeypatch, "limit")
    for name in ("one.mp3", "two.mp3"):
        (runtime_dir / name).write_text("x", encoding="utf-8")
    _patch_audio(monkeypatch)

    with pytest.raises(LibraryScanLimitError):
        controller.add_media_files(
            [runtime_dir],
            scan_limits=LibraryScanLimits(max_media_files=1),
        )

    _assert_no_import_side_effects(controller, event_bus, database)


def test_cross_thread_cancellation_reaches_controller_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, event_bus, database, runtime_dir = _make_controller(monkeypatch, "cancel")
    media_path = runtime_dir / "one.mp3"
    media_path.write_text("x", encoding="utf-8")
    token = LibraryScanCancellation()
    provider_entered = threading.Event()
    provider_release = threading.Event()
    errors: list[Exception] = []
    monkeypatch.setattr(loader_module, "is_audio_file", lambda path: True)
    monkeypatch.setattr(loader_module, "is_video_file", lambda path: False)

    def blocking_metadata(path: str) -> AudioTagMetadata:
        provider_entered.set()
        assert provider_release.wait(2.0)
        return AudioTagMetadata(duration=1.0)

    def worker() -> None:
        try:
            controller.add_media_files([media_path], cancellation=token)
        except Exception as error:  # explicit worker boundary for test evidence
            errors.append(error)

    monkeypatch.setattr(loader_module, "read_audio_basic_metadata", blocking_metadata)
    thread = threading.Thread(target=worker)
    thread.start()
    assert provider_entered.wait(2.0)
    token.cancel()
    provider_release.set()
    thread.join(2.0)

    assert not thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], LibraryScanCancelledError)
    _assert_no_import_side_effects(controller, event_bus, database)


def test_traversal_error_after_discovery_started_does_not_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, event_bus, database, runtime_dir = _make_controller(monkeypatch, "traversal")
    (runtime_dir / "one.mp3").write_text("x", encoding="utf-8")
    blocked = runtime_dir / "blocked"
    blocked.mkdir()
    _patch_audio(monkeypatch)
    original_scandir = traversal_module.os.scandir

    def failing_scandir(path: object):
        if str(path) == str(blocked):
            raise PermissionError("blocked descendant")
        return original_scandir(path)

    monkeypatch.setattr(traversal_module.os, "scandir", failing_scandir)
    with pytest.raises(LibraryScanTraversalError, match="blocked descendant"):
        controller.add_media_files([runtime_dir])

    _assert_no_import_side_effects(controller, event_bus, database)


def test_direct_add_honors_a_pre_cancelled_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, event_bus, database, runtime_dir = _make_controller(monkeypatch, "direct")
    media_path = runtime_dir / "one.mp3"
    media_path.write_text("x", encoding="utf-8")
    token = LibraryScanCancellation()
    token.cancel()
    _patch_audio(monkeypatch)

    with pytest.raises(LibraryScanCancelledError):
        controller.add_media(str(media_path), cancellation=token)

    _assert_no_import_side_effects(controller, event_bus, database)
