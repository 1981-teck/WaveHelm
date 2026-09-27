from __future__ import annotations

import math
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.controller.library_media_loader as loader_module
import src.controller.library_scan_traversal as traversal_module
from src.controller.library_media_loader import (
    discover_library_media_paths,
    load_library_media_file,
    load_library_media_files,
)
from src.controller.library_scan import LibraryScanTraversalError
from src.model.media_file import MediaType
from src.utils import ffprobe_service
from src.utils.media_metadata import AudioMetadataError, AudioTagMetadata


RUNTIME_ROOT = Path(__file__).resolve().parent / "_library_media_loader_runtime"


def _runtime_dir(name: str) -> Path:
    target = RUNTIME_ROOT / name
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    return target


def test_discover_library_media_paths_expands_and_filters_supported_files() -> None:
    runtime_dir = _runtime_dir("discover")
    nested = runtime_dir / "nested"
    nested.mkdir()
    audio_path = runtime_dir / "song.mp3"
    video_path = nested / "movie.mkv"
    ignored_path = nested / "notes.txt"
    for path in (audio_path, video_path, ignored_path):
        path.write_text("x", encoding="utf-8")

    discovered = discover_library_media_paths([runtime_dir])

    assert set(discovered) == {str(audio_path), str(video_path)}
    assert str(ignored_path) not in discovered


def test_discovery_fails_closed_on_directory_scan_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_dir = _runtime_dir("blocked")
    original_scandir = traversal_module.os.scandir

    def failing_scandir(path: object):
        if str(path) == str(runtime_dir):
            raise PermissionError("access denied")
        return original_scandir(path)

    monkeypatch.setattr(traversal_module.os, "scandir", failing_scandir)

    with pytest.raises(LibraryScanTraversalError, match="access denied") as captured:
        discover_library_media_paths([runtime_dir])
    assert captured.value.report.completed is False
    assert captured.value.report.directory_errors == 1


def test_discovery_rejects_non_path_values_before_filesystem_access(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class BrokenPath:
        def __fspath__(self) -> str:
            raise RuntimeError("should not escape")

    class MissingPathState:
        def __fspath__(self) -> str:
            raise AttributeError("missing state")

    values: list[object] = [
        None,
        7,
        b"bytes",
        "",
        BrokenPath(),
        MissingPathState(),
        "bad\x00path",
    ]
    assert discover_library_media_paths(values) == []
    assert "Ignoring invalid media path value" in caplog.text
    assert "must not contain NUL" in caplog.text


def test_discovery_accepts_text_pathlike_and_preserves_duplicate_file_entries() -> None:
    runtime_dir = _runtime_dir("pathlike")
    audio_path = runtime_dir / "song.wav"
    audio_path.write_text("x", encoding="utf-8")

    discovered = discover_library_media_paths([audio_path, audio_path])

    assert discovered == [str(audio_path), str(audio_path)]


def test_load_audio_uses_basic_metadata_and_preserves_library_title_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loader_module, "is_audio_file", lambda path: True)
    monkeypatch.setattr(loader_module, "is_video_file", lambda path: False)
    monkeypatch.setattr(
        loader_module,
        "read_audio_basic_metadata",
        lambda path: AudioTagMetadata(
            title="Real Title",
            artist="Artist",
            album="Album",
            duration=91.0,
        ),
    )

    media = load_library_media_file("folder/track.mp3")

    assert media is not None
    assert media.title == "Real Title"
    assert media.media_type is MediaType.AUDIO
    assert media.duration == 91.0
    assert media.metadata == {"artist": "Artist", "album": "Album"}


def test_load_audio_degrades_on_metadata_failure_and_non_finite_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loader_module, "is_audio_file", lambda path: True)
    monkeypatch.setattr(loader_module, "is_video_file", lambda path: False)

    def raise_metadata_error(path: str) -> AudioTagMetadata:
        raise AudioMetadataError("broken")

    monkeypatch.setattr(loader_module, "read_audio_basic_metadata", raise_metadata_error)
    fallback = load_library_media_file("folder/track.mp3")
    assert fallback is not None
    assert fallback.title == "track.mp3"
    assert fallback.duration == 0.0
    assert fallback.metadata == {}

    monkeypatch.setattr(
        loader_module,
        "read_audio_basic_metadata",
        lambda path: AudioTagMetadata(title="Track", duration=math.inf),
    )
    non_finite = load_library_media_file("folder/track.mp3")
    assert non_finite is not None
    assert non_finite.duration == 0.0

    monkeypatch.setattr(loader_module, "read_audio_basic_metadata", lambda path: object())
    invalid_shape = load_library_media_file("folder/track.mp3")
    assert invalid_shape is not None
    assert invalid_shape.title == "track.mp3"
    assert invalid_shape.duration == 0.0
    assert invalid_shape.metadata == {}


def test_load_video_uses_opencv_duration_and_shared_track_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_calls: list[str] = []
    tracks = [
        {
            "stream_index": 4,
            "track_index": 0,
            "language": "eng",
            "title": "Atmos",
            "codec_name": "truehd",
            "codec_long_name": "TrueHD",
            "channels": 8,
            "channel_layout": "7.1",
            "is_default": True,
            "is_forced": False,
            "label": "Track 1 — eng — Atmos — TRUEHD — 7.1",
        },
        {
            "stream_index": 1,
            "track_index": 1,
            "language": "ita",
            "title": "Dub",
            "codec_name": "ac3",
            "codec_long_name": "AC-3",
            "channels": 6,
            "channel_layout": "5.1",
            "is_default": False,
            "is_forced": False,
            "label": "Track 2 — ita — Dub — AC3 — 5.1",
        },
    ]
    fake_cv2 = SimpleNamespace(
        CAP_PROP_FPS=1,
        CAP_PROP_FRAME_COUNT=2,
        VideoCapture=lambda path: SimpleNamespace(
            isOpened=lambda: True,
            get=lambda prop: 25.0 if prop == 1 else 250.0,
            release=lambda: release_calls.append(path),
        ),
    )
    monkeypatch.setattr(loader_module, "is_audio_file", lambda path: False)
    monkeypatch.setattr(loader_module, "is_video_file", lambda path: True)
    monkeypatch.setattr(loader_module, "cv2", fake_cv2)
    monkeypatch.setattr(ffprobe_service, "probe_audio_tracks", lambda path: tracks)

    media = load_library_media_file("movie.mkv")

    assert media is not None
    assert media.media_type is MediaType.VIDEO
    assert media.title == "movie.mkv"
    assert media.duration == 10.0
    assert release_calls == ["movie.mkv"]
    assert media.metadata["audio_tracks"] == tracks
    assert media.metadata["audio_track_candidates"] == [1, 4]


def test_video_probe_does_not_depend_on_capture_truthiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FalseyCapture:
        def __bool__(self) -> bool:
            return False

        def isOpened(self) -> bool:
            return True

        def get(self, prop: int) -> float:
            return 20.0 if prop == 1 else 200.0

        def release(self) -> None:
            return None

    fake_cv2 = SimpleNamespace(
        CAP_PROP_FPS=1,
        CAP_PROP_FRAME_COUNT=2,
        VideoCapture=lambda path: FalseyCapture(),
    )
    monkeypatch.setattr(loader_module, "is_audio_file", lambda path: False)
    monkeypatch.setattr(loader_module, "is_video_file", lambda path: True)
    monkeypatch.setattr(loader_module, "cv2", fake_cv2)
    monkeypatch.setattr(ffprobe_service, "probe_audio_tracks", lambda path: [])

    media = load_library_media_file("falsey.mkv")

    assert media is not None
    assert media.duration == 10.0


def test_load_video_falls_back_to_ffprobe_and_contains_typed_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_cv2 = SimpleNamespace(
        CAP_PROP_FPS=1,
        CAP_PROP_FRAME_COUNT=2,
        VideoCapture=lambda path: SimpleNamespace(
            isOpened=lambda: True,
            get=lambda prop: math.inf if prop == 1 else 600.0,
            release=lambda: None,
        ),
    )
    monkeypatch.setattr(loader_module, "is_audio_file", lambda path: False)
    monkeypatch.setattr(loader_module, "is_video_file", lambda path: True)
    monkeypatch.setattr(loader_module, "cv2", fake_cv2)
    monkeypatch.setattr(ffprobe_service, "probe_duration", lambda path: 123.4)

    def raise_timeout(path: str):
        raise ffprobe_service.FfprobeTimeoutError("timeout")

    monkeypatch.setattr(ffprobe_service, "probe_audio_tracks", raise_timeout)
    media = load_library_media_file("movie.mkv")
    assert media is not None
    assert media.duration == 123.4
    assert media.metadata == {
        "audio_tracks": [],
        "audio_track_candidates": [],
    }

    monkeypatch.setattr(ffprobe_service, "probe_duration", raise_timeout)
    failed = load_library_media_file("movie.mkv")
    assert failed is not None
    assert failed.duration == 0.0


def test_malformed_video_track_payload_degrades_to_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loader_module, "is_audio_file", lambda path: False)
    monkeypatch.setattr(loader_module, "is_video_file", lambda path: True)
    monkeypatch.setattr(loader_module, "cv2", None)
    monkeypatch.setattr(ffprobe_service, "probe_duration", lambda path: 1.0)
    monkeypatch.setattr(ffprobe_service, "probe_audio_tracks", lambda path: [{}])

    media = load_library_media_file("malformed.mkv")

    assert media is not None
    assert media.metadata == {
        "audio_tracks": [],
        "audio_track_candidates": [],
    }


def test_video_duration_overflow_falls_back_to_ffprobe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_cv2 = SimpleNamespace(
        CAP_PROP_FPS=1,
        CAP_PROP_FRAME_COUNT=2,
        VideoCapture=lambda path: SimpleNamespace(
            isOpened=lambda: True,
            get=lambda prop: 1e-308 if prop == 1 else 1e308,
            release=lambda: None,
        ),
    )
    monkeypatch.setattr(loader_module, "is_audio_file", lambda path: False)
    monkeypatch.setattr(loader_module, "is_video_file", lambda path: True)
    monkeypatch.setattr(loader_module, "cv2", fake_cv2)
    monkeypatch.setattr(ffprobe_service, "probe_duration", lambda path: 42.0)
    monkeypatch.setattr(ffprobe_service, "probe_audio_tracks", lambda path: [])

    media = load_library_media_file("overflow.mkv")

    assert media is not None
    assert media.duration == 42.0


def test_load_library_media_files_preserves_legacy_duplicate_probe_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_dir = _runtime_dir("duplicates")
    audio_path = runtime_dir / "song.wav"
    audio_path.write_text("x", encoding="utf-8")
    calls: list[str] = []
    monkeypatch.setattr(loader_module, "is_audio_file", lambda path: True)
    monkeypatch.setattr(loader_module, "is_video_file", lambda path: False)

    def read_metadata(path: str) -> AudioTagMetadata:
        calls.append(path)
        return AudioTagMetadata(title="Song", duration=1.0)

    monkeypatch.setattr(loader_module, "read_audio_basic_metadata", read_metadata)
    loaded = load_library_media_files([audio_path, audio_path])

    assert [media.path for media in loaded] == [str(audio_path), str(audio_path)]
    assert calls == [str(audio_path), str(audio_path)]


def test_load_library_media_file_rejects_unsupported_and_invalid_values() -> None:
    assert load_library_media_file("notes.txt") is None
    assert load_library_media_file(None) is None
    assert load_library_media_file(b"track.mp3") is None
    assert load_library_media_files("track.mp3") == []


@pytest.mark.parametrize("duration", [math.nan, math.inf, -math.inf])
def test_audio_duration_never_emits_non_finite_media(
    monkeypatch: pytest.MonkeyPatch,
    duration: float,
) -> None:
    monkeypatch.setattr(loader_module, "is_audio_file", lambda path: True)
    monkeypatch.setattr(loader_module, "is_video_file", lambda path: False)
    monkeypatch.setattr(
        loader_module,
        "read_audio_basic_metadata",
        lambda path: AudioTagMetadata(duration=duration),
    )

    media = load_library_media_file("track.wav")

    assert media is not None
    assert media.duration == 0.0
