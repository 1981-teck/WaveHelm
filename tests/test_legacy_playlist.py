from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.model.playlist import LegacyPlaylistContractError, Playlist
from src.model.song import Song


def _song(identifier: str, *, seconds: float = 61.0) -> Song:
    return Song(
        id=identifier,
        title=f"Title {identifier}",
        artist="Artist",
        album="Album",
        duration=timedelta(seconds=seconds),
        file_path=f"/music/{identifier}.mp3",
        genre="Rock",
        year=2026,
        track_number=1,
        artwork_url="https://example.invalid/cover.png",
        tags=["one", "two"],
    )


def _playlist(*songs: Song, playlist_id: str = "playlist-id") -> Playlist:
    created = datetime(2026, 1, 2, 3, 4, 5)
    return Playlist(
        id=playlist_id,
        name="Legacy Mix",
        songs=list(songs),
        creation_date=created,
        last_modified=created,
        description="Compatibility playlist",
        cover_art="/covers/legacy.png",
    )


def test_constructor_and_getters_detach_caller_owned_song_state() -> None:
    source = _song("song-1")
    playlist = _playlist(source)

    source.title = "Changed outside"
    assert source.tags is not None
    source.tags.append("outside")

    stored = playlist.get_song_by_id("song-1")
    assert stored is not None
    assert stored.title == "Title song-1"
    assert stored.tags == ["one", "two"]

    stored.title = "Changed snapshot"
    assert stored.tags is not None
    stored.tags.append("snapshot")
    assert playlist.get_song_by_id("song-1") == _song("song-1")


def test_add_remove_and_move_enforce_exact_state_transitions() -> None:
    playlist = _playlist(_song("one"), _song("two"))
    original_modified = playlist.last_modified

    assert playlist.add_song(_song("three")) is True
    assert playlist.add_song(_song("three")) is False
    assert [song.id for song in playlist.songs] == ["one", "two", "three"]
    assert playlist.last_modified >= original_modified

    assert playlist.move_song("three", 0) is True
    assert playlist.move_song("three", 0) is False
    assert playlist.move_song("missing", 99) is False
    assert [song.id for song in playlist.songs] == ["three", "one", "two"]

    assert playlist.remove_song("one") is True
    unchanged = playlist.last_modified
    assert playlist.remove_song("missing") is False
    assert playlist.last_modified == unchanged
    assert [song.id for song in playlist.songs] == ["three", "two"]


@pytest.mark.parametrize("position", [-1, 2, True, 1.5, "1"])
def test_move_rejects_invalid_positions(position: object) -> None:
    playlist = _playlist(_song("one"), _song("two"))
    with pytest.raises((TypeError, ValueError)):
        playlist.move_song("one", position)  # type: ignore[arg-type]
    assert [song.id for song in playlist.songs] == ["one", "two"]


def test_aware_timestamps_remain_aware_after_mutation() -> None:
    created = datetime(2026, 1, 2, tzinfo=timezone.utc)
    playlist = Playlist(
        id="aware",
        name="Aware",
        songs=[_song("one")],
        creation_date=created,
        last_modified=created,
    )

    assert playlist.add_song(_song("two")) is True
    assert playlist.last_modified.tzinfo is timezone.utc
    assert playlist.last_modified >= created


def test_to_dict_and_from_dict_round_trip_are_detached() -> None:
    playlist = _playlist(_song("one"), _song("two"))
    document = playlist.to_dict()
    restored = Playlist.from_dict(document)

    assert restored.to_dict() == document
    songs = document["songs"]
    assert isinstance(songs, list)
    first = songs[0]
    assert isinstance(first, dict)
    tags = first["tags"]
    assert isinstance(tags, list)
    tags.append("mutated")
    assert playlist.to_dict() != document


def test_from_dict_rejects_unknown_missing_and_duplicate_song_ids() -> None:
    document = _playlist(_song("one")).to_dict()
    with_unknown = dict(document)
    with_unknown["unexpected"] = True
    with pytest.raises(LegacyPlaylistContractError, match="unexpected"):
        Playlist.from_dict(with_unknown)

    missing = dict(document)
    del missing["name"]
    with pytest.raises(LegacyPlaylistContractError, match="missing"):
        Playlist.from_dict(missing)

    duplicate = dict(document)
    songs = list(document["songs"])  # type: ignore[arg-type]
    songs.append(dict(songs[0]))
    duplicate["songs"] = songs
    with pytest.raises(LegacyPlaylistContractError, match="unique"):
        Playlist.from_dict(duplicate)


def test_from_dict_rejects_invalid_numeric_and_timeline_values() -> None:
    document = _playlist(_song("one")).to_dict()
    invalid_duration = dict(document)
    songs = [dict(document["songs"][0])]  # type: ignore[index]
    songs[0]["duration"] = float("nan")
    invalid_duration["songs"] = songs
    with pytest.raises(LegacyPlaylistContractError, match="finite"):
        Playlist.from_dict(invalid_duration)

    invalid_timeline = dict(document)
    invalid_timeline["creation_date"] = "2026-01-02T00:00:00"
    invalid_timeline["last_modified"] = "2026-01-01T00:00:00"
    with pytest.raises(LegacyPlaylistContractError, match="precede"):
        Playlist.from_dict(invalid_timeline)


def test_save_and_load_round_trip_uses_safe_deterministic_filename(tmp_path: Path) -> None:
    playlist = _playlist(_song("one"), playlist_id="../not-a-path-component")

    result = playlist.save_to_file(tmp_path / "playlists")
    assert result.path.parent == (tmp_path / "playlists").resolve()
    assert result.path.name == Playlist.storage_filename(playlist.id)
    assert ".." not in result.path.name
    assert "not-a-path-component" not in result.path.name
    assert result.path.is_file()

    restored = Playlist.load_from_file(result.path)
    assert restored.to_dict() == playlist.to_dict()


def test_serialization_detects_direct_external_field_corruption(tmp_path: Path) -> None:
    playlist = _playlist(_song("one"))
    result = playlist.save_to_file(tmp_path)
    original = result.path.read_bytes()

    playlist.name = ""
    with pytest.raises(LegacyPlaylistContractError):
        playlist.save_to_file(tmp_path)
    assert result.path.read_bytes() == original


def test_logging_handler_failure_cannot_change_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    playlist = _playlist(_song("one"))

    def fail_log(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("logging boundary failed")

    monkeypatch.setattr("src.model.playlist.logger.log", fail_log)
    result = playlist.save_to_file(tmp_path)
    restored = Playlist.load_from_file(result.path)
    assert restored.to_dict() == playlist.to_dict()
