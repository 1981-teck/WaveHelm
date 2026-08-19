from __future__ import annotations

import pytest

from src.controller import playlist_controller_tracks as tracks_mod
from src.model.media_file import MediaFile, MediaType


class DummyDB:
    def __init__(self, fail_add: bool = False, fail_remove: bool = False) -> None:
        self.fail_add = fail_add
        self.fail_remove = fail_remove
        self.added: list[tuple[int, str, int]] = []
        self.removed: list[tuple[int, str]] = []

    def add_playlist_item(self, playlist_id: int, path: str, position: int) -> None:
        if self.fail_add:
            raise RuntimeError("add fail")
        self.added.append((playlist_id, path, position))

    def remove_playlist_item(self, playlist_id: int, path: str) -> None:
        if self.fail_remove:
            raise RuntimeError("remove fail")
        self.removed.append((playlist_id, path))

    def get_playlist_items(self, _playlist_id: int) -> list[dict[str, object]]:
        return []


class DummyEventBus:
    def __init__(self) -> None:
        self.calls: list[tuple[object, object]] = []

    def publish(self, event_type: object, payload: object) -> None:
        self.calls.append((event_type, payload))


class DummyLibraryController:
    def __init__(self, media: MediaFile | None = None, fail_add: bool = False) -> None:
        self.media = media
        self.fail_add = fail_add
        self.add_calls: list[list[str]] = []

    def get_media_by_path(self, _path: str) -> MediaFile | None:
        return self.media

    def add_media_files(self, paths: list[str]) -> None:
        self.add_calls.append(paths)
        if self.fail_add:
            raise RuntimeError("library add fail")


class DummyController:
    def __init__(self) -> None:
        self._current_playlist_id = 1
        self._playlists_cache: dict[int, dict[str, str]] = {1: {"name": "One"}}
        self._playlist_items_cache: dict[int, list[tuple[int, str]]] = {1: []}
        self.database_manager = DummyDB()
        self.event_bus = DummyEventBus()
        self.library_controller = DummyLibraryController(
            media=MediaFile(
                path="C:/song.mp3",
                title="Song",
                media_type=MediaType.AUDIO,
            )
        )
        self.feedback: list[tuple[str, dict[str, object]]] = []
        self.updated: list[int] = []
        self.synced: list[int] = []
        self.handled: list[tuple[str, str]] = []

    def _ensure_cache_valid(self) -> None:
        return None

    def _notify_feedback(self, key: str, **kwargs: object) -> None:
        self.feedback.append((key, kwargs))

    def _sync_playlist_file(self, playlist_id: int) -> None:
        self.synced.append(playlist_id)

    def _notify_playlist_updated(self, playlist_id: int) -> None:
        self.updated.append(playlist_id)

    def _handle_error(self, error: object, key: str) -> None:
        self.handled.append((str(error), key))


for _name in [
    "add_files_to_current_playlist",
    "_ensure_media_in_library",
    "_add_media_to_playlist",
    "remove_from_playlist",
    "remove_many_from_playlist",
    "get_tracks_in_playlist",
    "get_playlist_track_count",
    "_build_media_from_playlist_row",
]:
    setattr(DummyController, _name, getattr(tracks_mod, _name))


def test_add_files_to_current_playlist_counts_failures_without_crashing() -> None:
    controller = DummyController()
    controller.library_controller = DummyLibraryController(media=None, fail_add=True)

    controller.add_files_to_current_playlist(["C:/missing.mp3"])

    assert controller.feedback[-1][0] == "some_tracks_failed_to_add"


def test_add_media_to_playlist_returns_false_on_db_failure() -> None:
    controller = DummyController()
    controller.database_manager = DummyDB(fail_add=True)

    result = controller._add_media_to_playlist(
        1,
        MediaFile(
            path="C:/song.mp3",
            title="Song",
            media_type=MediaType.AUDIO,
        ),
    )

    assert result is False


def test_remove_from_playlist_handles_db_failure() -> None:
    controller = DummyController()
    controller.database_manager = DummyDB(fail_remove=True)
    controller._playlist_items_cache[1] = [(1, "C:/song.mp3")]

    result = controller.remove_from_playlist(1, "C:/song.mp3")

    assert result is False
    assert controller.handled == [("remove fail", "error_removing_from_playlist")]


def test_build_media_from_playlist_row_uses_strict_typed_boundary() -> None:
    controller = DummyController()
    row: dict[str, object] = {
        "title": "Recovered",
        "media_type": "audio",
        "duration": 9.5,
        "metadata": {"artist": "Artist"},
        "database_only_column": 7,
    }

    media = controller._build_media_from_playlist_row("C:/recovered.mp3", row)

    assert media is not None
    assert media.title == "Recovered"
    assert media.media_type is MediaType.AUDIO
    assert media.duration == 9.5
    assert media.metadata == {"artist": "Artist"}


def test_build_media_from_playlist_row_rejects_complete_corrupt_row(
    caplog: pytest.LogCaptureFixture,
) -> None:
    controller = DummyController()

    assert controller._build_media_from_playlist_row(
        "C:/broken.mp3",
        {"media_type": "not-a-type", "duration": "invalid", "metadata": []},
    ) is None
    assert "Invalid playlist media row" in caplog.text


def test_build_media_from_playlist_row_distinguishes_missing_from_malformed_fields() -> None:
    controller = DummyController()

    recovered = controller._build_media_from_playlist_row(
        "C:/orphan.mp3",
        {"title": None, "media_type": None, "duration": None, "metadata": None},
    )

    assert recovered is not None
    assert recovered.title == "orphan"
    assert recovered.media_type is MediaType.AUDIO
    assert recovered.duration == 0.0
    assert recovered.metadata == {}

    fallback = controller._build_media_from_playlist_row("C:/fallback.mp3", None)
    assert fallback is not None
    assert fallback.media_type is MediaType.AUDIO

    assert controller._build_media_from_playlist_row(
        "C:/invalid.mp3",
        {"title": "", "media_type": "", "duration": False, "metadata": {}},
    ) is None
    assert controller._build_media_from_playlist_row("", None) is None


def test_get_tracks_in_playlist_skips_invalid_persisted_record() -> None:
    controller = DummyController()
    controller.library_controller = DummyLibraryController(media=None)
    controller._playlist_items_cache[1] = [(1, "C:/broken.mp3")]
    controller.database_manager.get_playlist_items = lambda _playlist_id: [
        {
            "media_path": "C:/broken.mp3",
            "title": "Broken",
            "media_type": "audio",
            "duration": -1.0,
            "metadata": {},
        }
    ]

    assert controller.get_tracks_in_playlist(1) == []
