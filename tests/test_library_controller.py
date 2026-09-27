from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

import src.controller.library_controller as library_module
import src.controller.library_media_loader as loader_module
from src.audio.audio_events import AudioEventType
from src.controller.library_catalog_store import (
    LibraryCatalogConflictError,
    LibraryCatalogStore,
    LibraryCatalogWriteBlockedError,
    LibraryCatalogWriteError,
)
from src.controller.library_controller import LibraryController, _canon_path_win
from src.model.media_file import MediaFile, MediaType
from src.utils.media_metadata import AudioTagMetadata


RUNTIME_ROOT = Path(__file__).resolve().parent / "_library_controller_runtime"


def _runtime_dir(name: str) -> Path:
    target = RUNTIME_ROOT / name
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    return target


class DummyEventBus:
    def __init__(self, fail_publish: bool = False) -> None:
        self.fail_publish = fail_publish
        self.calls: list[tuple[AudioEventType, dict[str, object]]] = []

    def publish(self, event_type: AudioEventType, payload: dict[str, object]) -> None:
        if self.fail_publish:
            raise ValueError("publish fail")
        self.calls.append((event_type, payload))


class DummyDatabaseManager:
    def __init__(self) -> None:
        self.library_items: list[dict[str, object]] = []
        self.removed: list[str] = []
        self.favorites: set[str] = set()
        self.favorite_items: list[dict[str, object]] = []
        self.fail_on: dict[str, Exception] = {}

    def add_library_item(self, **kwargs: object) -> None:
        error = self.fail_on.get("add_library_item")
        if error:
            raise error
        path = kwargs.get("path")
        assert isinstance(path, str)
        self.library_items = [item for item in self.library_items if item.get("path") != path]
        self.library_items.append(dict(kwargs))

    def remove_library_item(self, path: str) -> None:
        error = self.fail_on.get("remove_library_item")
        if error:
            raise error
        self.library_items = [item for item in self.library_items if item.get("path") != path]
        self.removed.append(path)

    def get_all_library_items(self) -> list[dict[str, object]]:
        error = self.fail_on.get("get_all_library_items")
        if error:
            raise error
        return [dict(item) for item in self.library_items]

    def is_favorite(self, path: str) -> bool:
        error = self.fail_on.get("is_favorite")
        if error:
            raise error
        return path in self.favorites

    def add_favorite(self, **kwargs: object) -> None:
        error = self.fail_on.get("add_favorite")
        if error:
            raise error
        path = kwargs["path"]
        assert isinstance(path, str)
        self.favorite_items.append(kwargs)
        self.favorites.add(path)


def _make_controller(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    *,
    event_bus: DummyEventBus | None = None,
    database_manager: DummyDatabaseManager | None = None,
) -> tuple[LibraryController, DummyEventBus, DummyDatabaseManager, Path]:
    runtime_dir = _runtime_dir(name)
    monkeypatch.setattr(
        library_module,
        "get_app_data_path",
        lambda *parts, create=True: runtime_dir.joinpath(*parts) if parts else runtime_dir,
    )
    selected_event_bus = event_bus or DummyEventBus()
    selected_database = database_manager or DummyDatabaseManager()
    controller = LibraryController(
        event_bus=selected_event_bus,
        database_manager=selected_database,
    )
    return controller, selected_event_bus, selected_database, runtime_dir


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_catalog_parent_link_and_replacement_are_fail_closed(tmp_path: Path) -> None:
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    linked_store = LibraryCatalogStore(linked_parent / "library.json")
    assert linked_store.load().write_blocked is True
    with pytest.raises(LibraryCatalogWriteBlockedError):
        linked_store.commit(["one.wav"])
    assert not (real_parent / "library.json").exists()
    clean_store = LibraryCatalogStore(real_parent / "clean.json")
    assert clean_store.load().write_blocked is False
    with pytest.raises(LibraryCatalogWriteError, match="whitespace"):
        clean_store.commit(["   "])

    stable_parent = tmp_path / "stable-parent"
    stable_parent.mkdir()
    stable_store = LibraryCatalogStore(stable_parent / "library.json")
    assert stable_store.load().write_blocked is False
    stable_parent.rename(tmp_path / "old-parent")
    stable_parent.mkdir()
    with pytest.raises(LibraryCatalogConflictError, match="parent changed"):
        stable_store.commit(["one.wav"])
    assert not (stable_parent / "library.json").exists()


def test_canon_path_win_falls_back_on_bad_input() -> None:
    class BadPath:
        def __fspath__(self) -> str:
            raise TypeError("bad path")

    assert isinstance(_canon_path_win("demo.mp3"), str)
    bad = BadPath()
    assert _canon_path_win(bad) is bad


def test_add_media_files_from_objects_saves_syncs_and_deduplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, event_bus, database_manager, runtime_dir = _make_controller(
        monkeypatch,
        "add_objects",
    )
    media = MediaFile(
        path=str(runtime_dir / "song.wav"),
        title="Song",
        media_type=MediaType.AUDIO,
        duration=12.0,
        metadata={"artist": "A"},
    )
    duplicate = MediaFile(
        path=str(runtime_dir / "song.wav"),
        title="Song",
        media_type=MediaType.AUDIO,
        duration=12.0,
        metadata={},
    )

    result = controller.add_media_files_from_objects([media, duplicate])

    assert result.is_fully_synchronized is True
    assert len(controller.get_all_media()) == 1
    assert database_manager.library_items[-1]["title"] == "Song"
    assert controller._library_path.is_file()
    assert json.loads(controller._library_path.read_text(encoding="utf-8")) == [
        str(runtime_dir / "song.wav")
    ]
    assert [call[0] for call in event_bus.calls] == [
        AudioEventType.LIBRARY_UPDATED,
        AudioEventType.FEEDBACK_MESSAGE,
    ]


def test_change_event_uses_committed_result_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, event_bus, _, runtime_dir = _make_controller(monkeypatch, "event_snapshot")
    first = MediaFile(str(runtime_dir / "one.wav"), "One", MediaType.AUDIO, 1.0, {})
    second = MediaFile(str(runtime_dir / "two.wav"), "Two", MediaType.AUDIO, 1.0, {})
    first_result = controller.add_media_files_from_objects(
        [first], emit_event=False, emit_feedback=False
    )
    controller.add_media_files_from_objects([second], emit_event=False, emit_feedback=False)

    controller._announce_change(
        first_result, emit_event=True, emit_feedback=False, action_message="Committed"
    )
    assert event_bus.calls[-1][1]["count"] == 1
    assert event_bus.calls[-1][1]["revision"] == first_result.revision
    with controller._commit_gate.transaction():
        with pytest.raises(RuntimeError, match="reentrant"):
            controller.load_persistent_library()


def test_path_import_uses_extracted_loader_without_changing_feedback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, event_bus, database_manager, runtime_dir = _make_controller(
        monkeypatch,
        "loader_delegation",
    )
    expected_path = str(runtime_dir / "delegated.wav")
    media = MediaFile(expected_path, "Delegated", MediaType.AUDIO, 1.0, {})
    observed: list[list[str]] = []

    def fake_load(paths: object) -> list[MediaFile]:
        assert isinstance(paths, list)
        observed.append(list(paths))
        return [media]

    monkeypatch.setattr(library_module, "load_library_media_files", fake_load)
    controller.add_media_files([expected_path])

    assert observed == [[expected_path]]
    assert database_manager.library_items[-1]["path"] == expected_path
    assert [call[0] for call in event_bus.calls] == [
        AudioEventType.LIBRARY_UPDATED,
        AudioEventType.FEEDBACK_MESSAGE,
    ]


def test_load_persistent_library_restores_entries_and_handles_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_dir = _runtime_dir("load_persistent")
    audio_file = runtime_dir / "track.wav"
    audio_file.write_text("x", encoding="utf-8")
    (runtime_dir / "library.json").write_text(
        json.dumps([str(audio_file)]),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        library_module,
        "get_app_data_path",
        lambda *parts, create=True: runtime_dir.joinpath(*parts) if parts else runtime_dir,
    )
    monkeypatch.setattr(loader_module, "is_audio_file", lambda path: str(path).endswith(".wav"))
    monkeypatch.setattr(loader_module, "is_video_file", lambda path: False)
    monkeypatch.setattr(
        loader_module,
        "read_audio_basic_metadata",
        lambda path: AudioTagMetadata(duration=0.0),
    )

    controller = LibraryController(DummyEventBus(), DummyDatabaseManager())
    assert [media.path for media in controller.get_all_media()] == [str(audio_file)]

    broken_dir = _runtime_dir("load_invalid")
    (broken_dir / "library.json").write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(
        library_module,
        "get_app_data_path",
        lambda *parts, create=True: broken_dir.joinpath(*parts) if parts else broken_dir,
    )
    broken = LibraryController(DummyEventBus(), DummyDatabaseManager())
    assert broken.get_all_media() == []
    assert broken.catalog_write_blocked is True


def test_load_persistent_library_ignores_invalid_path_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_dir = _runtime_dir("load_invalid_entries")
    audio_file = runtime_dir / "valid.wav"
    audio_file.write_text("x", encoding="utf-8")
    (runtime_dir / "library.json").write_text(
        json.dumps(
            [None, 7, {"path": "bad"}, "bad\x00path", str(audio_file), str(audio_file)]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        library_module,
        "get_app_data_path",
        lambda *parts, create=True: runtime_dir.joinpath(*parts) if parts else runtime_dir,
    )
    monkeypatch.setattr(loader_module, "is_audio_file", lambda path: str(path).endswith(".wav"))
    monkeypatch.setattr(loader_module, "is_video_file", lambda path: False)
    metadata_calls: list[str] = []

    def read_metadata(path: str) -> AudioTagMetadata:
        metadata_calls.append(path)
        return AudioTagMetadata(duration=0.0)

    monkeypatch.setattr(loader_module, "read_audio_basic_metadata", read_metadata)

    controller = LibraryController(DummyEventBus(), DummyDatabaseManager())

    assert [media.path for media in controller.get_all_media()] == [str(audio_file)]
    assert metadata_calls == [str(audio_file)]
    assert controller.catalog_write_blocked is True


def test_add_to_favorites_handles_duplicate_success_and_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, event_bus, database_manager, runtime_dir = _make_controller(
        monkeypatch,
        "favorites",
    )
    media = MediaFile(
        path=str(runtime_dir / "fav.wav"),
        title="Fav",
        media_type=MediaType.AUDIO,
        duration=5.0,
        metadata={},
    )

    assert controller.add_to_favorites(media) is True
    assert database_manager.favorite_items[-1]["path"] == media.path
    assert [call[0] for call in event_bus.calls[-2:]] == [
        AudioEventType.FAVORITE_CHANGED,
        AudioEventType.FEEDBACK_MESSAGE,
    ]

    assert controller.add_to_favorites(media) is False
    assert event_bus.calls[-1][1]["color"] == "orange"

    database_manager.fail_on["add_favorite"] = RuntimeError("db fail")
    other = MediaFile(
        path=str(runtime_dir / "other.wav"),
        title="Other",
        media_type=MediaType.AUDIO,
        duration=3.0,
        metadata={},
    )
    assert controller.add_to_favorites(other) is False
    assert event_bus.calls[-1][1]["color"] == "red"


def test_remove_media_and_search_preserve_controller_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, _, database_manager, runtime_dir = _make_controller(
        monkeypatch,
        "remove_search",
    )
    audio_path = str(runtime_dir / "track.mp3")
    video_path = str(runtime_dir / "clip.mp4")
    audio_media = MediaFile(
        audio_path,
        "Real Title",
        MediaType.AUDIO,
        91.0,
        {"artist": "Artist", "album": "Album"},
    )
    video_media = MediaFile(video_path, "clip.mp4", MediaType.VIDEO, 0.0, {})

    controller.add_media_files_from_objects(
        [audio_media, video_media],
        emit_event=False,
        emit_feedback=False,
    )

    assert [media.title for media in controller.search_media("artist")] == ["Real Title"]
    assert controller.get_media_by_path(audio_path).title == "Real Title"
    assert controller.remove_media(audio_path) is True
    assert database_manager.removed == [audio_path]
    assert controller.get_media_by_path(audio_path) is None
    assert controller.remove_media(audio_path) is False
