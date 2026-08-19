from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.model.media_loader as media_loader_module
from src.model.media_file import MediaType
from src.model.media_loader import MediaLoader
from src.utils import ffprobe_service
from src.utils.media_metadata import AudioMetadataError, AudioTagMetadata


class DummyLocalization:
    def get_text(self, key, **kwargs):
        return key


class DummyVideoCapture:
    def __init__(self, path, *, opened=True, frame_count=300.0, fps=30.0):
        self.path = path
        self._opened = opened
        self._frame_count = frame_count
        self._fps = fps
        self.released = False

    def isOpened(self):
        return self._opened

    def get(self, prop):
        if prop == media_loader_module.cv2.CAP_PROP_FRAME_COUNT:
            return self._frame_count
        if prop == media_loader_module.cv2.CAP_PROP_FPS:
            return self._fps
        return 0.0

    def release(self):
        self.released = True


class FakeProcess:
    def __init__(
        self,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
        running: bool = False,
        ignore_terminate: bool = False,
    ) -> None:
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self._returncode = None if running else returncode
        self._final_returncode = returncode
        self.ignore_terminate = ignore_terminate
        self.terminated = False
        self.killed = False

    def poll(self):
        return self._returncode

    def wait(self, timeout=None):
        if self._returncode is None:
            raise subprocess.TimeoutExpired("ffprobe", timeout)
        return self._returncode

    def terminate(self):
        self.terminated = True
        if not self.ignore_terminate:
            self._returncode = -15

    def kill(self):
        self.killed = True
        self._returncode = -9


def _install_fake_process(monkeypatch, process: FakeProcess) -> list[tuple[object, object]]:
    calls: list[tuple[object, object]] = []
    monkeypatch.setattr(ffprobe_service.shutil, "which", lambda executable: "/fake/ffprobe")

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return process

    monkeypatch.setattr(ffprobe_service.subprocess, "Popen", fake_popen)
    return calls


def test_ffprobe_duration_boundary_is_typed_and_bounded(tmp_path, monkeypatch):
    media_path = tmp_path / "track.mp3"
    media_path.write_bytes(b"x")
    success = FakeProcess(stdout=json.dumps({"format": {"duration": "12.5"}}).encode())
    calls = _install_fake_process(monkeypatch, success)

    assert ffprobe_service.probe_duration(str(media_path)) == 12.5
    command, kwargs = calls[0]
    assert command[0] == "/fake/ffprobe"
    assert command[1:3] == ("-v", "error")
    assert kwargs["stdin"] == subprocess.DEVNULL

    malformed = FakeProcess(stdout=b"{broken")
    _install_fake_process(monkeypatch, malformed)
    with pytest.raises(ffprobe_service.FfprobeProtocolError):
        ffprobe_service.probe_duration(str(media_path))

    failed = FakeProcess(stderr=b"decoder failure", returncode=3)
    _install_fake_process(monkeypatch, failed)
    with pytest.raises(ffprobe_service.FfprobeExecutionError, match="code 3"):
        ffprobe_service.probe_duration(str(media_path))


def test_ffprobe_boundary_terminates_timeout_and_output_overflow(tmp_path, monkeypatch):
    media_path = tmp_path / "clip.mkv"
    media_path.write_bytes(b"x")
    timed_out = FakeProcess(running=True)
    _install_fake_process(monkeypatch, timed_out)

    with pytest.raises(ffprobe_service.FfprobeTimeoutError):
        ffprobe_service.probe_duration(str(media_path), timeout_seconds=0.02)
    assert timed_out.terminated is True

    oversized = FakeProcess(
        stdout=b"x" * (ffprobe_service.STDOUT_LIMIT_BYTES + 1),
        running=True,
    )
    _install_fake_process(monkeypatch, oversized)
    with pytest.raises(ffprobe_service.FfprobeOutputLimitError, match="stdout"):
        ffprobe_service.probe_duration(str(media_path))
    assert oversized.terminated is True


def test_ffprobe_audio_tracks_normalize_deduplicate_and_rank(tmp_path, monkeypatch):
    media_path = tmp_path / "movie.mkv"
    media_path.write_bytes(b"x")
    payload = {
        "streams": [
            {"codec_type": "video", "index": 0},
            {
                "codec_type": "audio",
                "index": "4",
                "codec_name": "truehd",
                "codec_long_name": "TrueHD",
                "channels": "8",
                "channel_layout": "7.1",
                "tags": {"language": "ENG", "title": "Atmos"},
                "disposition": {"default": 1, "forced": 0},
            },
            {
                "codec_type": "audio",
                "index": "1",
                "codec_name": "ac3",
                "channels": "6",
                "channel_layout": "5.1",
                "tags": {"language": "ita", "title": "Dub"},
            },
            {"codec_type": "audio", "index": "1", "codec_name": "aac"},
            {"codec_type": "audio", "index": "bad", "codec_name": "aac"},
            {"codec_type": "audio", "index": 1.5, "codec_name": "aac"},
            {"codec_type": "audio", "index": 2_147_483_648, "codec_name": "aac"},
        ]
    }
    _install_fake_process(monkeypatch, FakeProcess(stdout=json.dumps(payload).encode()))

    tracks = ffprobe_service.probe_audio_tracks(str(media_path))

    assert [track["stream_index"] for track in tracks] == [1, 4]
    assert tracks[0]["label"] == "Track 2 — ita — Dub — AC3 — 5.1"
    assert tracks[1]["language"] == "eng"
    assert ffprobe_service.build_audio_track_candidates(tracks) == [1, 4]


def test_load_audio_uses_audio_metadata_reader_and_falls_back_to_basic_media(
    tmp_path,
    monkeypatch,
):
    loader = MediaLoader(DummyLocalization())
    audio_path = tmp_path / "song.mp3"
    audio_path.write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        media_loader_module,
        "read_audio_rich_metadata",
        lambda path: AudioTagMetadata(duration=0.0, cover_image=b"img", lyrics="hello"),
    )
    monkeypatch.setattr(ffprobe_service, "probe_duration", lambda path: 33.0)

    media = loader.load_audio(str(audio_path))
    assert media is not None
    assert media.media_type == MediaType.AUDIO
    assert media.duration == 33.0
    assert media.metadata["cover_image"] == b"img"
    assert media.metadata["lyrics"] == "hello"

    monkeypatch.setattr(
        media_loader_module,
        "read_audio_rich_metadata",
        lambda path: (_ for _ in ()).throw(AudioMetadataError("bad audio")),
    )
    fallback = loader.load_audio(str(audio_path))
    assert fallback is not None
    assert fallback.media_type == MediaType.AUDIO
    assert fallback.duration == 0.0


def test_load_video_uses_opencv_and_ffprobe_fallbacks(tmp_path, monkeypatch):
    loader = MediaLoader(DummyLocalization())
    video_path = tmp_path / "clip.mp4"
    video_path.write_text("x", encoding="utf-8")
    monkeypatch.setattr(media_loader_module, "OPENCV_AVAILABLE", True)
    monkeypatch.setattr(ffprobe_service, "probe_audio_tracks", lambda path: [])
    fake_cv2 = SimpleNamespace(
        CAP_PROP_FRAME_COUNT=1,
        CAP_PROP_FPS=2,
        VideoCapture=lambda path: DummyVideoCapture(
            path, opened=True, frame_count=600.0, fps=60.0
        ),
    )
    monkeypatch.setattr(media_loader_module, "cv2", fake_cv2)

    media = loader.load_video(str(video_path))
    assert media is not None
    assert media.media_type == MediaType.VIDEO
    assert media.duration == 10.0

    failing_cv2 = SimpleNamespace(
        CAP_PROP_FRAME_COUNT=1,
        CAP_PROP_FPS=2,
        VideoCapture=lambda path: (_ for _ in ()).throw(RuntimeError("opencv fail")),
    )
    monkeypatch.setattr(media_loader_module, "cv2", failing_cv2)
    monkeypatch.setattr(ffprobe_service, "probe_duration", lambda path: 44.0)
    media_ffprobe = loader.load_video(str(video_path))
    assert media_ffprobe is not None
    assert media_ffprobe.duration == 44.0

    nonfinite_cv2 = SimpleNamespace(
        CAP_PROP_FRAME_COUNT=1,
        CAP_PROP_FPS=2,
        VideoCapture=lambda path: DummyVideoCapture(
            path, opened=True, frame_count=float("inf"), fps=60.0
        ),
    )
    monkeypatch.setattr(media_loader_module, "cv2", nonfinite_cv2)
    media_nonfinite = loader.load_video(str(video_path))
    assert media_nonfinite is not None
    assert media_nonfinite.duration == 44.0


def test_get_video_subtitles_reads_sidecar_file(tmp_path):
    loader = MediaLoader(DummyLocalization())
    video_path = tmp_path / "movie.mp4"
    subtitle_path = tmp_path / "movie.srt"
    video_path.write_text("x", encoding="utf-8")
    subtitle_path.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello",
        encoding="utf-8",
    )

    subtitles = loader._get_video_subtitles(str(video_path))
    assert subtitles == [
        {"lang": "und", "text": "1\n00:00:00,000 --> 00:00:01,000\nHello"}
    ]


@pytest.mark.parametrize(
    ("suffix", "payload", "expected"),
    [
        (".vtt", b"\xef\xbb\xbfWEBVTT\r\n\r\nHello", "WEBVTT\n\nHello"),
        (".ass", "[Events]\r\nDialogue: Hello".encode("utf-16"), "[Events]\nDialogue: Hello"),
    ],
)
def test_get_video_subtitles_accepts_only_deterministic_bom_encodings(
    tmp_path,
    suffix,
    payload,
    expected,
):
    loader = MediaLoader(DummyLocalization())
    video_path = tmp_path / "movie.mp4"
    video_path.write_bytes(b"video")
    video_path.with_suffix(suffix).write_bytes(payload)

    assert loader._get_video_subtitles(str(video_path)) == [
        {"lang": "und", "text": expected}
    ]


def test_get_video_subtitles_rejects_invalid_encoding_and_uses_next_sidecar(
    tmp_path,
    caplog,
):
    loader = MediaLoader(DummyLocalization())
    video_path = tmp_path / "movie.mp4"
    video_path.write_bytes(b"video")
    video_path.with_suffix(".srt").write_bytes(b"\xff\xfe\x00")
    video_path.with_suffix(".vtt").write_text("WEBVTT\n\nFallback", encoding="utf-8")

    assert loader._get_video_subtitles(str(video_path)) == [
        {"lang": "und", "text": "WEBVTT\n\nFallback"}
    ]
    assert "SubtitleEncodingError" in caplog.text


def test_subtitle_reader_stops_after_limit_detection_byte(monkeypatch):
    monkeypatch.setattr(media_loader_module, "SUBTITLE_MAX_BYTES", 32)
    monkeypatch.setattr(media_loader_module, "SUBTITLE_READ_CHUNK_BYTES", 8)
    stream = io.BytesIO(b"x" * 4096)

    with pytest.raises(media_loader_module.SubtitleSizeLimitError):
        MediaLoader._read_bounded_subtitle_bytes(stream)

    assert stream.tell() == 33


def test_get_video_subtitles_rejects_oversized_sidecar(tmp_path, monkeypatch):
    monkeypatch.setattr(media_loader_module, "SUBTITLE_MAX_BYTES", 32)
    loader = MediaLoader(DummyLocalization())
    video_path = tmp_path / "movie.mp4"
    video_path.write_bytes(b"video")
    video_path.with_suffix(".srt").write_bytes(b"x" * 33)

    assert loader._get_video_subtitles(str(video_path)) is None


def test_get_video_subtitles_rejects_link_sidecar(tmp_path):
    loader = MediaLoader(DummyLocalization())
    video_path = tmp_path / "movie.mp4"
    target_path = tmp_path / "external.txt"
    link_path = video_path.with_suffix(".srt")
    video_path.write_bytes(b"video")
    target_path.write_text("Sensitive external text", encoding="utf-8")
    try:
        link_path.symlink_to(target_path)
    except (NotImplementedError, OSError):
        pytest.skip("symbolic links are unavailable in this environment")

    assert loader._get_video_subtitles(str(video_path)) is None


def test_subtitle_boundary_accepts_exact_limit_and_rejects_path_swap(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(media_loader_module, "SUBTITLE_MAX_BYTES", 32)
    assert MediaLoader._read_bounded_subtitle_bytes(io.BytesIO(b"x" * 32)) == b"x" * 32

    first_path = tmp_path / "first.srt"
    second_path = tmp_path / "second.srt"
    first_path.write_text("first", encoding="utf-8")
    second_path.write_text("second", encoding="utf-8")
    with pytest.raises(media_loader_module.SubtitleFileTypeError, match="changed"):
        MediaLoader._validate_subtitle_handle(
            first_path,
            first_path.stat(),
            second_path.stat(),
        )
    with pytest.raises(media_loader_module.SubtitleFileTypeError, match="regular"):
        MediaLoader._validate_subtitle_path(tmp_path, tmp_path.stat())


@pytest.mark.parametrize(
    "payload",
    [b"\xff\xfe\x00\x00A\x00\x00\x00", b"A\x00B"],
)
def test_subtitle_decoder_rejects_unsupported_or_ambiguous_payload(payload):
    with pytest.raises(media_loader_module.SubtitleEncodingError):
        MediaLoader._decode_subtitle_bytes(payload)


def test_ffprobe_rejects_invalid_input_unavailable_and_non_finite_duration(
    tmp_path,
    monkeypatch,
):
    missing = tmp_path / "missing.mp3"
    with pytest.raises(ffprobe_service.FfprobeInputError):
        ffprobe_service.probe_duration(str(missing))

    media_path = tmp_path / "track.mp3"
    media_path.write_bytes(b"x")
    monkeypatch.setattr(ffprobe_service.shutil, "which", lambda executable: None)
    with pytest.raises(ffprobe_service.FfprobeUnavailableError):
        ffprobe_service.probe_duration(str(media_path))

    _install_fake_process(
        monkeypatch,
        FakeProcess(stdout=json.dumps({"format": {"duration": "NaN"}}).encode()),
    )
    with pytest.raises(ffprobe_service.FfprobeProtocolError, match="positive finite"):
        ffprobe_service.probe_duration(str(media_path))


def test_ffprobe_kills_after_terminate_grace_and_bounds_diagnostics(tmp_path, monkeypatch):
    media_path = tmp_path / "clip.mkv"
    media_path.write_bytes(b"x")
    stubborn = FakeProcess(running=True, ignore_terminate=True)
    _install_fake_process(monkeypatch, stubborn)

    with pytest.raises(ffprobe_service.FfprobeTimeoutError):
        ffprobe_service.probe_duration(str(media_path), timeout_seconds=0.02)
    assert stubborn.terminated is True
    assert stubborn.killed is True

    invalid_utf8_stderr = FakeProcess(stderr=b"\xfffailure", returncode=4)
    _install_fake_process(monkeypatch, invalid_utf8_stderr)
    with pytest.raises(ffprobe_service.FfprobeExecutionError, match="code 4"):
        ffprobe_service.probe_duration(str(media_path))


def test_ffprobe_rejects_stderr_overflow_invalid_utf8_and_excessive_streams(
    tmp_path,
    monkeypatch,
):
    media_path = tmp_path / "clip.mkv"
    media_path.write_bytes(b"x")
    oversized_stderr = FakeProcess(
        stderr=b"e" * (ffprobe_service.STDERR_LIMIT_BYTES + 1),
        running=True,
    )
    _install_fake_process(monkeypatch, oversized_stderr)
    with pytest.raises(ffprobe_service.FfprobeOutputLimitError, match="stderr"):
        ffprobe_service.probe_duration(str(media_path))
    assert oversized_stderr.terminated is True

    _install_fake_process(monkeypatch, FakeProcess(stdout=b"\xff"))
    with pytest.raises(ffprobe_service.FfprobeProtocolError, match="UTF-8"):
        ffprobe_service.probe_duration(str(media_path))

    oversized_integer = b'{"format":{"duration":' + (b"9" * 5_000) + b"}}"
    _install_fake_process(monkeypatch, FakeProcess(stdout=oversized_integer))
    with pytest.raises(ffprobe_service.FfprobeProtocolError, match="malformed JSON"):
        ffprobe_service.probe_duration(str(media_path))

    excessive = {"streams": [{"codec_type": "audio", "index": index} for index in range(257)]}
    _install_fake_process(monkeypatch, FakeProcess(stdout=json.dumps(excessive).encode()))
    with pytest.raises(ffprobe_service.FfprobeProtocolError, match="too many"):
        ffprobe_service.probe_audio_tracks(str(media_path))
