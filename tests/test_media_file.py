from __future__ import annotations

import os
from pathlib import Path

import pytest

import src.model.media_file as media_file_module
from src.model.media_file import MediaFile, MediaFileValidationError, MediaType


class _BrokenPath:
    def __fspath__(self) -> str:
        raise TypeError("broken path protocol")


def test_constructor_derives_stable_local_state(tmp_path: Path) -> None:
    media_path = tmp_path / "nested" / ".." / "track.mp3"

    media = MediaFile(path=media_path, metadata={"title": "  Metadata title  "})

    assert media.path == os.path.normpath(str(media_path))
    assert media.title == "Metadata title"
    assert media.media_type is MediaType.AUDIO
    assert media.duration == 0.0
    assert media.metadata == {"title": "  Metadata title  "}


def test_constructor_preserves_protocol_paths_and_detects_supported_types() -> None:
    cases = [
        ("HTTPS://example.com/live", MediaType.STREAM),
        ("cdda://1", MediaType.CD_TRACK),
        ("movie.mkv", MediaType.VIDEO),
        ("track.cda", MediaType.CD_TRACK),
        ("episode.bin", MediaType.PODCAST),
        ("unknown.bin", MediaType.UNKNOWN),
    ]

    for path, expected_type in cases:
        metadata = {"media_type": "podcast"} if expected_type is MediaType.PODCAST else {}
        media = MediaFile(path=path, metadata=metadata)
        assert media.media_type is expected_type

    assert MediaFile(path=cases[0][0]).path == cases[0][0]
    assert MediaFile(path=cases[1][0]).is_local_file() is False


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"path": ""}, "must not be empty"),
        ({"path": "bad\x00path.mp3"}, "NUL"),
        ({"path": 7}, "must be text"),
        ({"path": _BrokenPath()}, "could not be resolved"),
        ({"path": "song.mp3", "duration": -0.1}, "must not be negative"),
        ({"path": "song.mp3", "duration": float("nan")}, "must be finite"),
        ({"path": "song.mp3", "duration": True}, "real number"),
        ({"path": "song.mp3", "duration": 10**10_000}, "represented safely"),
        ({"path": "song.mp3", "metadata": ["bad"]}, "must be a mapping"),
        ({"path": "song.mp3", "metadata": {1: "bad"}}, "keys must be text"),
        ({"path": "song.mp3", "media_type": "audio"}, "MediaType"),
        ({"path": "song.mp3", "media_type": MediaType.ALL}, "filter"),
        ({"path": "song.mp3", "title": 7}, "title must be text"),
    ],
)
def test_constructor_rejects_invalid_state(
    kwargs: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(MediaFileValidationError, match=message):
        MediaFile(**kwargs)


def test_failed_assignments_preserve_state_and_valid_updates_remain_supported() -> None:
    media = MediaFile(
        path="song.mp3",
        title="Song",
        media_type=MediaType.AUDIO,
        duration=12.0,
        metadata={"artist": "Artist"},
    )

    invalid_assignments = [
        ("duration", -1.0),
        ("path", "other.mp3"),
        ("media_type", MediaType.VIDEO),
        ("metadata", []),
        ("title", "   "),
    ]
    for field_name, value in invalid_assignments:
        with pytest.raises(MediaFileValidationError):
            setattr(media, field_name, value)

    assert media.to_dict() == {
        "path": "song.mp3",
        "title": "Song",
        "media_type": "audio",
        "duration": 12.0,
        "metadata": {"artist": "Artist"},
    }

    media.title = "Updated"
    media.duration = 13
    media.metadata = {"album": "Album"}
    assert (media.title, media.duration, media.metadata) == ("Updated", 13.0, {"album": "Album"})


def test_mapping_boundary_is_strict_and_compatibility_boundary_drops_invalid(
    caplog: pytest.LogCaptureFixture,
) -> None:
    valid: dict[str, object] = {
        "id": 9,
        "path": "https://example.com/video",
        "title": "Video",
        "media_type": "STREAM",
        "duration": 3,
        "metadata": None,
    }

    media = MediaFile.from_mapping(valid)

    assert media.path == valid["path"]
    assert media.media_type is MediaType.STREAM
    assert media.duration == 3.0
    assert media.metadata == {}

    with pytest.raises(MediaFileValidationError, match="must be a mapping"):
        MediaFile.from_mapping([])  # type: ignore[arg-type]
    with pytest.raises(MediaFileValidationError, match="missing path"):
        MediaFile.from_mapping({})
    with pytest.raises(MediaFileValidationError, match="must be text"):
        MediaFile.from_mapping({"path": "song.mp3", "media_type": 4})

    invalid = {"path": "https://example.com/video", "media_type": "weird"}
    with pytest.raises(MediaFileValidationError, match="Unsupported media_type"):
        MediaFile.from_mapping(invalid)

    assert MediaFile.from_mapping({"path": "song.bin", "media_type": " "}).media_type is MediaType.UNKNOWN
    assert MediaFile.from_mapping({"path": "song.bin", "media_type": None}).media_type is MediaType.UNKNOWN
    assert MediaFile.from_mapping({"path": "episode.bin", "media_type": MediaType.PODCAST}).media_type is MediaType.PODCAST
    assert MediaFile.from_dict(invalid) is None
    assert MediaFile.from_dict([]) is None
    assert "Invalid MediaFile record" in caplog.text


def test_metadata_is_detached_on_construction_assignment_access_and_export() -> None:
    original: dict[str, object] = {
        "artist": "Artist",
        "tracks": [{"index": 1}],
        "binary": bytearray(b"abc"),
    }
    media = MediaFile(path="song.mp3", metadata=original)

    original["artist"] = "Changed"
    original["tracks"][0]["index"] = 99
    exposed = media.metadata
    exposed["artist"] = "Getter mutation"
    exposed["tracks"][0]["index"] = 88
    exported = media.to_dict()
    exported["metadata"]["artist"] = "Export mutation"
    exported["metadata"]["tracks"][0]["index"] = 77

    assert media.metadata == {
        "artist": "Artist",
        "tracks": [{"index": 1}],
        "binary": b"abc",
    }

    replacement = {"album": "Album"}
    media.metadata = replacement
    replacement["album"] = "Changed"
    assert media.metadata == {"album": "Album"}


def test_metadata_boundary_rejects_recursive_deep_and_unsupported_values() -> None:
    recursive: dict[str, object] = {}
    recursive["self"] = recursive
    too_deep: object = "leaf"
    for _ in range(14):
        too_deep = [too_deep]

    invalid_values: list[dict[str, object]] = [
        recursive,
        {"nested": too_deep},
        {"object": object()},
        {"number": float("inf")},
        {"integer": 1 << 256},
        {"items": list(range(8_193))},
        {"text": "\ud800"},
        {"bad-key-\ud800": "value"},
    ]

    for metadata in invalid_values:
        with pytest.raises(MediaFileValidationError):
            MediaFile(path="song.mp3", metadata=metadata)


def test_metadata_accepts_supported_scalars_and_repeated_non_recursive_containers() -> None:
    shared = [None, True, 3, 2.5, "text", b"bytes", (1, 2)]
    media = MediaFile(path="song.mp3", metadata={"first": shared, "second": shared})

    snapshot = media.metadata
    assert snapshot["first"] == shared
    assert snapshot["second"] == shared
    assert snapshot["first"] is not snapshot["second"]


@pytest.mark.parametrize(
    ("constant_name", "limit", "metadata", "message"),
    [
        ("_MAX_METADATA_DEPTH", 1, {"a": {"b": {}}}, "depth limit"),
        ("_MAX_METADATA_ITEMS", 4, {"items": [1, 2, 3]}, "item limit"),
        ("_MAX_METADATA_TEXT_CHARS", 3, {"text": "1234"}, "text exceeds"),
        ("_MAX_METADATA_BINARY_BYTES", 3, {"binary": b"1234"}, "binary value"),
        ("_MAX_METADATA_KEY_CHARS", 3, {"abcd": 1}, "key exceeds"),
        ("_MAX_METADATA_INTEGER_BITS", 3, {"number": 8}, "integer exceeds"),
        ("_MAX_METADATA_SCALAR_BYTES", 4, {"a": "1234"}, "scalar byte budget"),
    ],
)
def test_metadata_budgets_are_enforced(
    monkeypatch: pytest.MonkeyPatch,
    constant_name: str,
    limit: int,
    metadata: dict[str, object],
    message: str,
) -> None:
    monkeypatch.setattr(media_file_module, constant_name, limit)

    with pytest.raises(MediaFileValidationError, match=message):
        MediaFile(path="song.mp3", metadata=metadata)


def test_invalid_metadata_replacement_preserves_previous_snapshot() -> None:
    media = MediaFile(path="song.mp3", metadata={"artist": "Artist"})
    recursive: dict[str, object] = {}
    recursive["self"] = recursive

    with pytest.raises(MediaFileValidationError, match="recursive"):
        media.metadata = recursive

    assert media.metadata == {"artist": "Artist"}


def test_known_metadata_fields_are_validated() -> None:
    with pytest.raises(MediaFileValidationError, match="metadata.title"):
        MediaFile(path="song.mp3", metadata={"title": 4})
    with pytest.raises(MediaFileValidationError, match="metadata.media_type"):
        MediaFile(path="song.bin", metadata={"media_type": "invalid"})


def test_duration_display_title_and_file_properties(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media_path = tmp_path / "track.flac"
    media_path.write_bytes(b"12345")
    media = MediaFile(
        path=media_path,
        title="Track",
        duration=3661.9,
        metadata={"artist": "Artist", "album": "Album"},
    )

    assert media.get_formatted_duration() == "01:01:01"
    assert media.get_display_title() == "Track - Artist (Album)"
    assert MediaFile(path="a.mp3", title="A", metadata={"artist": "Artist"}).get_display_title() == "Artist - A"
    assert MediaFile(path="a.mp3", title="A", metadata={"album": "Album"}).get_display_title() == "A (Album)"
    assert MediaFile(path="a.mp3", title="A").get_display_title() == "A"
    assert MediaFile(path="a.mp3", duration=61).get_formatted_duration() == "01:01"
    assert media.file_extension == ".flac"
    assert media.exists() is True
    assert media.file_size == 5

    monkeypatch.setattr(media_file_module.os.path, "getsize", lambda _path: (_ for _ in ()).throw(OSError("denied")))
    assert media.file_size == 0

    stream = MediaFile(path="https://example.com/live", title="Live")
    assert stream.exists() is False
    assert stream.file_size == 0


def test_identity_assignment_equality_and_repr_contracts() -> None:
    first = MediaFile(path="nested/../song.wav", title="Song", media_type=MediaType.AUDIO)
    second = MediaFile(path="song.wav", title="Song", media_type=MediaType.AUDIO)

    first.path = "song.wav"
    first.media_type = MediaType.AUDIO

    assert first == second
    assert "path='song.wav'" in repr(first)
    assert "media_type=<MediaType.AUDIO" in repr(first)
