"""Typed, bounded ffprobe subprocess boundary.

Boundary edge cases and mitigations:
- hung processes are terminated after a finite wall-clock timeout, then killed after a bounded grace period;
- stdout/stderr are drained concurrently but retained only up to hard byte caps, with overflow causing fail-closed termination;
- malformed UTF-8/JSON, non-finite durations, excessive stream counts, invalid paths, and non-zero exits become typed errors.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Final, TypedDict, cast

DEFAULT_TIMEOUT_SECONDS: Final = 8.0
STDOUT_LIMIT_BYTES: Final = 512 * 1024
STDERR_LIMIT_BYTES: Final = 64 * 1024
_READ_CHUNK_BYTES: Final = 8 * 1024
_PROCESS_POLL_SECONDS: Final = 0.01
_PROCESS_STOP_GRACE_SECONDS: Final = 0.5
_MAX_AUDIO_STREAMS: Final = 256
_MAX_SHORT_TEXT_CHARS: Final = 64
_MAX_TITLE_CHARS: Final = 256
_AUDIO_CODEC_COMPATIBILITY_ORDER: Final = {
    "aac": 0, "mp3": 1, "ac3": 2, "eac3": 3, "pcm_s16le": 4,
    "pcm_s24le": 5, "flac": 6, "vorbis": 7, "opus": 8,
    "truehd": 20, "dts": 21, "dtshd": 22,
}


class AudioTrackMetadata(TypedDict):
    stream_index: int
    track_index: int
    language: str
    title: str
    codec_name: str
    codec_long_name: str
    channels: int
    channel_layout: str
    is_default: bool
    is_forced: bool
    label: str


class FfprobeError(RuntimeError):
    """Base class for deterministic ffprobe boundary failures."""


class FfprobeUnavailableError(FfprobeError): pass
class FfprobeInputError(FfprobeError): pass
class FfprobeTimeoutError(FfprobeError): pass
class FfprobeOutputLimitError(FfprobeError): pass
class FfprobeExecutionError(FfprobeError): pass
class FfprobeProtocolError(FfprobeError): pass

@dataclass
class _BoundedCapture:
    stream: BinaryIO
    stream_name: str
    limit_bytes: int
    buffer: bytearray = field(default_factory=bytearray)
    overflowed: bool = False
    read_error: OSError | ValueError | None = None

    def consume(self, stop_signal: threading.Event) -> None:
        """Drain a pipe without retaining more than its hard cap."""
        try:
            while True:
                chunk = self.stream.read(_READ_CHUNK_BYTES)
                if not chunk:
                    break
                remaining = self.limit_bytes - len(self.buffer)
                if remaining > 0:
                    self.buffer.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    self.overflowed = True
                    stop_signal.set()
        except (OSError, ValueError) as error:
            self.read_error = error
            stop_signal.set()
        finally:
            try:
                self.stream.close()
            except (OSError, ValueError) as error:
                if self.read_error is None:
                    self.read_error = error


def probe_duration(file_path: str, *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> float:
    """Return a positive finite duration or raise a typed boundary error."""
    normalized_path = _normalize_local_file(file_path)
    payload = _run_ffprobe(
        ("-v", "error", "-show_entries", "format=duration", "-of", "json", normalized_path),
        timeout_seconds=timeout_seconds,
    )
    root = _load_json_mapping(payload)
    format_data = _as_string_mapping(root.get("format"))
    duration_value = format_data.get("duration") if format_data is not None else None
    duration = _positive_finite_float(duration_value)
    if duration is None:
        raise FfprobeProtocolError("ffprobe returned no positive finite duration")
    return duration


def probe_audio_tracks(
    file_path: str,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[AudioTrackMetadata]:
    """Return normalized audio streams with bounded count and field sizes."""
    normalized_path = _normalize_local_file(file_path)
    payload = _run_ffprobe(
        (
            "-v", "error", "-show_entries",
            "stream=index,codec_type,codec_name,codec_long_name,channels,channel_layout:stream_tags=language,title:stream_disposition=default,forced",
            "-of", "json", normalized_path,
        ),
        timeout_seconds=timeout_seconds,
    )
    root = _load_json_mapping(payload)
    streams = _as_object_list(root.get("streams"))
    if streams is None:
        return []
    if len(streams) > _MAX_AUDIO_STREAMS:
        raise FfprobeProtocolError("ffprobe returned too many stream records")
    return _normalize_audio_tracks(streams)


def build_audio_track_candidates(audio_tracks: Sequence[AudioTrackMetadata]) -> list[int]:
    """Return a stable compatibility-first stream selection order."""
    ordered: list[tuple[tuple[int, int, int, int], int]] = []
    seen: set[int] = set()
    for track in audio_tracks:
        stream_index = track["stream_index"]
        if stream_index < 0 or stream_index in seen:
            continue
        seen.add(stream_index)
        ordered.append((_audio_track_sort_key(track), stream_index))
    ordered.sort(key=lambda item: item[0])
    return [stream_index for _, stream_index in ordered]


def _run_ffprobe(arguments: Sequence[str], *, timeout_seconds: float) -> bytes:
    """Run ffprobe with bounded concurrent capture and deterministic shutdown."""
    executable = _resolve_executable()
    timeout = _validate_timeout(timeout_seconds)
    command = (executable, *arguments)
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, ValueError) as error:
        raise FfprobeExecutionError(f"unable to start ffprobe: {error}") from error
    stdout_stream, stderr_stream = process.stdout, process.stderr
    if stdout_stream is None or stderr_stream is None:
        _stop_process(process)
        raise FfprobeExecutionError("ffprobe pipes were not created")

    stop_signal = threading.Event()
    stdout_capture = _BoundedCapture(stdout_stream, "stdout", STDOUT_LIMIT_BYTES)
    stderr_capture = _BoundedCapture(stderr_stream, "stderr", STDERR_LIMIT_BYTES)
    threads = (
        threading.Thread(target=stdout_capture.consume, args=(stop_signal,), daemon=True),
        threading.Thread(target=stderr_capture.consume, args=(stop_signal,), daemon=True),
    )
    try:
        for thread in threads:
            thread.start()
    except RuntimeError as error:
        _stop_process(process)
        for thread in threads:
            if thread.ident is not None:
                thread.join(_PROCESS_STOP_GRACE_SECONDS)
        raise FfprobeExecutionError(f"unable to start pipe reader: {error}") from error

    timed_out = _wait_for_process(process, stop_signal, timeout)
    if process.poll() is None:
        _stop_process(process)
    returncode = _reap_process(process)
    _join_capture_threads(threads, stdout_capture, stderr_capture)
    stdout = bytes(stdout_capture.buffer)
    stderr = bytes(stderr_capture.buffer)
    if timed_out:
        raise FfprobeTimeoutError(f"ffprobe exceeded {timeout:.3f} seconds")
    _raise_capture_error(stdout_capture)
    _raise_capture_error(stderr_capture)
    _raise_output_limit(stdout_capture)
    _raise_output_limit(stderr_capture)
    if returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()[:2048]
        suffix = f": {detail}" if detail else ""
        raise FfprobeExecutionError(f"ffprobe exited with code {returncode}{suffix}")
    return stdout


def _wait_for_process(
    process: subprocess.Popen[bytes],
    stop_signal: threading.Event,
    timeout_seconds: float,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while process.poll() is None:
        remaining = deadline - time.monotonic()
        if remaining <= 0.0:
            return True
        if stop_signal.wait(min(_PROCESS_POLL_SECONDS, remaining)):
            return False
    return False


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
    except OSError as error:
        if process.poll() is None:
            raise FfprobeExecutionError(f"unable to terminate ffprobe: {error}") from error
        return
    try:
        process.wait(timeout=_PROCESS_STOP_GRACE_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        process.kill()
    except OSError as error:
        if process.poll() is None:
            raise FfprobeExecutionError(f"unable to kill ffprobe: {error}") from error
        return
    try:
        process.wait(timeout=_PROCESS_STOP_GRACE_SECONDS)
    except subprocess.TimeoutExpired as error:
        raise FfprobeExecutionError("ffprobe remained alive after kill") from error


def _reap_process(process: subprocess.Popen[bytes]) -> int:
    try:
        return process.wait(timeout=_PROCESS_STOP_GRACE_SECONDS)
    except subprocess.TimeoutExpired as error:
        _stop_process(process)
        raise FfprobeExecutionError("ffprobe could not be reaped") from error


def _join_capture_threads(
    threads: Sequence[threading.Thread],
    stdout_capture: _BoundedCapture,
    stderr_capture: _BoundedCapture,
) -> None:
    for thread in threads:
        thread.join(_PROCESS_STOP_GRACE_SECONDS)
    if all(not thread.is_alive() for thread in threads):
        return
    for capture in (stdout_capture, stderr_capture):
        try:
            capture.stream.close()
        except (OSError, ValueError):
            continue
    for thread in threads:
        thread.join(_PROCESS_STOP_GRACE_SECONDS)
    if any(thread.is_alive() for thread in threads):
        raise FfprobeExecutionError("ffprobe pipe readers did not terminate")


def _raise_capture_error(capture: _BoundedCapture) -> None:
    if capture.read_error is not None:
        raise FfprobeExecutionError(
            f"unable to read ffprobe {capture.stream_name}: {capture.read_error}"
        ) from capture.read_error


def _raise_output_limit(capture: _BoundedCapture) -> None:
    if capture.overflowed:
        raise FfprobeOutputLimitError(
            f"ffprobe {capture.stream_name} exceeded {capture.limit_bytes} bytes"
        )


def _resolve_executable() -> str:
    executable = shutil.which("ffprobe")
    if not executable:
        raise FfprobeUnavailableError("ffprobe is not available in PATH")
    return executable


def _normalize_local_file(file_path: str) -> str:
    if not file_path or "\x00" in file_path:
        raise FfprobeInputError("ffprobe requires a non-empty local file path")
    try:
        path = Path(file_path).expanduser()
        is_file = path.is_file()
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise FfprobeInputError(f"unable to validate ffprobe input: {error}") from error
    if not is_file:
        raise FfprobeInputError("ffprobe input is not an accessible local file")
    return os.path.abspath(os.fspath(path))


def _validate_timeout(timeout_seconds: float) -> float:
    try:
        timeout = float(timeout_seconds)
    except (TypeError, ValueError) as error:
        raise FfprobeInputError("ffprobe timeout must be numeric") from error
    if not math.isfinite(timeout) or timeout <= 0.0:
        raise FfprobeInputError("ffprobe timeout must be positive and finite")
    return timeout


def _load_json_mapping(payload: bytes) -> Mapping[str, object]:
    text = _decode_bounded_text(payload)
    if not text.strip():
        raise FfprobeProtocolError("ffprobe returned empty JSON output")
    try:
        decoded: object = json.loads(text)
    except (RecursionError, ValueError) as error:
        raise FfprobeProtocolError(f"ffprobe returned malformed JSON: {error}") from error
    root = _as_string_mapping(decoded)
    if root is None:
        raise FfprobeProtocolError("ffprobe JSON root must be an object")
    return root


def _decode_bounded_text(payload: bytes) -> str:
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise FfprobeProtocolError("ffprobe output is not valid UTF-8") from error


def _as_string_mapping(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        return None
    return cast(Mapping[str, object], value)


def _as_object_list(value: object) -> list[object] | None:
    return cast(list[object], value) if isinstance(value, list) else None


def _positive_finite_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0.0 else None


def _normalize_audio_tracks(streams: Sequence[object]) -> list[AudioTrackMetadata]:
    tracks: list[AudioTrackMetadata] = []
    seen_indexes: set[int] = set()
    for stream_value in streams:
        stream = _as_string_mapping(stream_value)
        if stream is None or stream.get("codec_type") != "audio":
            continue
        track = _build_audio_track(stream, len(tracks))
        if track is None or track["stream_index"] in seen_indexes:
            continue
        seen_indexes.add(track["stream_index"])
        tracks.append(track)
    tracks.sort(key=lambda item: (item["stream_index"], item["track_index"]))
    return tracks


def _build_audio_track(
    stream: Mapping[str, object],
    ordinal: int,
) -> AudioTrackMetadata | None:
    stream_index = _coerce_int(stream.get("index"), default=-1)
    if stream_index < 0 or stream_index > 2_147_483_647:
        return None
    tags = _as_string_mapping(stream.get("tags")) or {}
    disposition = _as_string_mapping(stream.get("disposition")) or {}
    codec_name = _bounded_text(stream.get("codec_name"), _MAX_SHORT_TEXT_CHARS).lower()
    language = _bounded_text(tags.get("language"), _MAX_SHORT_TEXT_CHARS).lower()
    title = _bounded_text(tags.get("title"), _MAX_TITLE_CHARS)
    channel_layout = _bounded_text(stream.get("channel_layout"), _MAX_SHORT_TEXT_CHARS)
    channels = min(1024, max(0, _coerce_int(stream.get("channels"), default=0)))
    return {
        "stream_index": stream_index,
        "track_index": ordinal,
        "language": language,
        "title": title,
        "codec_name": codec_name,
        "codec_long_name": _bounded_text(stream.get("codec_long_name"), _MAX_TITLE_CHARS),
        "channels": channels,
        "channel_layout": channel_layout,
        "is_default": bool(_coerce_int(disposition.get("default"), default=0)),
        "is_forced": bool(_coerce_int(disposition.get("forced"), default=0)),
        "label": _format_audio_track_label(
            ordinal, language, title, codec_name, channel_layout, channels
        ),
    }


def _bounded_text(value: object, max_chars: int) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return ""
    return str(value).strip()[:max_chars]


def _coerce_int(value: object, *, default: int) -> int:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, float) and (not math.isfinite(value) or not value.is_integer()):
        return default
    if not isinstance(value, (int, float, str)):
        return default
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _format_audio_track_label(
    ordinal: int,
    language: str,
    title: str,
    codec_name: str,
    channel_layout: str,
    channels: int,
) -> str:
    parts = [f"Track {ordinal + 1}"]
    parts.extend(value for value in (language, title, codec_name.upper()) if value)
    if channel_layout:
        parts.append(channel_layout)
    elif channels > 0:
        parts.append("1 ch" if channels == 1 else f"{channels} ch")
    return " — ".join(parts)


def _audio_track_sort_key(track: AudioTrackMetadata) -> tuple[int, int, int, int]:
    compatibility_rank = _AUDIO_CODEC_COMPATIBILITY_ORDER.get(track["codec_name"], 10)
    default_rank = 0 if track["is_default"] else 1
    forced_rank = 0 if track["is_forced"] else 1
    return (compatibility_rank, default_rank, forced_rank, track["stream_index"])
