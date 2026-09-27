"""Media discovery and metadata extraction for library ingestion.

This module owns recursive discovery, media classification, and metadata probes.
``LibraryController`` remains responsible for state, persistence, database
mirroring, and event publication.

Edge cases handled:
- empty, non-text, NUL-bearing, or failing ``PathLike`` values are ignored;
- missing paths and unsupported files do not create media records;
- corrupt or unavailable audio/video metadata providers degrade deterministically;
- invalid or non-finite durations never reach the ``MediaFile`` boundary;
- capture release failures are contained after the primary probe completes;
- traversal, metadata count, elapsed time, and cancellation share one scan budget.

Complexity:
- discovery is O(I + D + E), bounded by input, directory, and entry limits;
- metadata work is O(M), bounded by the accepted media-file limit.
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Sequence

try:
    import cv2
except ImportError:
    cv2 = None  # type: ignore[assignment]

from src.controller.library_scan import (
    LibraryMediaScanResult,
    LibraryPathScanResult,
    LibraryScanCancellation,
    LibraryScanLimits,
    LibraryScanSession,
    bounded_scan_error_text,
    safe_scan_log,
    validate_scan_path_text,
)
from src.controller.library_scan_traversal import (
    discover_supported_paths,
    scan_supported_paths,
    verify_media_file_identity,
)
from src.model.media_file import MediaFile, MediaType
from src.utils import ffprobe_service
from src.utils.helpers import is_audio_file, is_video_file
from src.utils.media_metadata import (
    AudioMetadataDependencyUnavailableError,
    AudioMetadataError,
    read_audio_basic_metadata,
)

logger = logging.getLogger(__name__)

PATH_BOUNDARY_EXCEPTIONS = (AttributeError, OSError, RuntimeError, TypeError, ValueError)
AUDIO_METADATA_EXCEPTIONS = (
    AttributeError,
    AudioMetadataDependencyUnavailableError,
    AudioMetadataError,
    FileNotFoundError,
    OSError,
    OverflowError,
    RuntimeError,
    TypeError,
    ValueError,
)
VIDEO_METADATA_EXCEPTIONS = (
    AttributeError,
    OSError,
    OverflowError,
    RuntimeError,
    TypeError,
    ValueError,
)
VIDEO_TRACK_METADATA_EXCEPTIONS = (AttributeError, KeyError, TypeError, ValueError)

def _log(level: int, message: str, *args: object) -> None:
    safe_scan_log(logger, level, message, *args)


def scan_library_media_paths(
    path_values: Sequence[object],
    *,
    limits: LibraryScanLimits | None = None,
    cancellation: LibraryScanCancellation | None = None,
) -> LibraryPathScanResult:
    """Return a complete bounded discovery result or raise on interruption.

    Edge cases: scalar plural input preserves the legacy empty result; traversal
    failures, cancellation, and resource ceilings never expose partial paths.
    """
    session = LibraryScanSession(limits, cancellation)
    return scan_supported_paths(
        path_values,
        accept_path=_is_supported_media_path,
        coerce_path=lambda value: _coerce_media_path(value, context="discovery"),
        session=session,
    )


def discover_library_media_paths(
    path_values: Sequence[object],
    *,
    limits: LibraryScanLimits | None = None,
    cancellation: LibraryScanCancellation | None = None,
) -> list[str]:
    """Return supported paths only when the bounded scan completes."""
    return list(
        scan_library_media_paths(
            path_values,
            limits=limits,
            cancellation=cancellation,
        ).paths
    )


def scan_library_media_files(
    path_values: Sequence[object],
    *,
    limits: LibraryScanLimits | None = None,
    cancellation: LibraryScanCancellation | None = None,
) -> LibraryMediaScanResult:
    """Discover and load media under one fail-closed scan budget.

    Cancellation is cooperative around in-process metadata providers. The shared
    ffprobe boundary receives the remaining deadline as its subprocess timeout.
    """
    session = LibraryScanSession(limits, cancellation)
    paths = discover_supported_paths(
        path_values,
        accept_path=_is_supported_media_path,
        coerce_path=lambda value: _coerce_media_path(value, context="discovery"),
        session=session,
    )
    media_files: list[MediaFile] = []
    for path in paths:
        session.claim_metadata_probe()
        media = _load_library_media_file(path, session=session)
        session.checkpoint()
        if media is not None:
            media_files.append(media)
            session.record_media_loaded()
    return LibraryMediaScanResult(tuple(media_files), session.complete_report())


def load_library_media_files(
    path_values: Sequence[object],
    *,
    limits: LibraryScanLimits | None = None,
    cancellation: LibraryScanCancellation | None = None,
) -> list[MediaFile]:
    """Return media only after bounded discovery and loading complete."""
    return list(
        scan_library_media_files(
            path_values,
            limits=limits,
            cancellation=cancellation,
        ).media_files
    )


def load_library_media_file(path_value: object) -> MediaFile | None:
    """Build one media record outside a multi-file scan session."""
    return _load_library_media_file(path_value, session=None)


def _load_library_media_file(
    path_value: object,
    *,
    session: LibraryScanSession | None,
) -> MediaFile | None:
    """Build one library media record under an optional cooperative budget."""
    _checkpoint(session)
    path = _coerce_media_path(path_value, context="media load")
    if path is None:
        return None
    expected_identity = None
    if session is not None:
        discovered_identity = session.expected_media_identity(path)
        expected_identity = verify_media_file_identity(
            path, session=session, expected=discovered_identity
        )
    media_type = _detect_library_media_type(path)
    if media_type is None:
        return None

    title = os.path.basename(path)
    duration = 0.0
    metadata: dict[str, object] = {}
    if media_type is MediaType.AUDIO:
        title, duration, audio_metadata = _read_library_audio_metadata(
            path, title, session=session
        )
        metadata = dict(audio_metadata)
    else:
        duration = _read_library_video_duration(path, session=session)
        metadata = _read_library_video_metadata(path, session=session)
    if session is not None and expected_identity is not None:
        verify_media_file_identity(path, session=session, expected=expected_identity)
    _checkpoint(session)
    return MediaFile(
        path=path,
        title=title,
        media_type=media_type,
        duration=duration,
        metadata=metadata,
    )


def _coerce_media_path(value: object, *, context: str) -> str | None:
    """Return a safe text path without touching the filesystem on invalid data.

    Edge cases: non-path objects and byte paths are rejected; empty or NUL-bearing
    text is rejected; failing ``PathLike`` implementations are contained.
    """
    if not isinstance(value, (str, os.PathLike)):
        _log(
            logging.WARNING,
            "[Library] Ignoring invalid media path value of type %s.",
            type(value).__name__,
        )
        return None
    try:
        path = os.fspath(value)
        if not isinstance(path, str):
            raise TypeError("media paths must resolve to text")
        if "\x00" in path:
            raise ValueError("media paths must not contain NUL characters")
        if not validate_scan_path_text(path):
            raise ValueError("media path exceeds text limits or is not valid UTF-8")
        if not path.strip():
            _log(logging.WARNING, "[Library] Ignoring invalid media path value: empty text.")
            return None
        return path
    except PATH_BOUNDARY_EXCEPTIONS as error:
        _log(
            logging.WARNING,
            "[Library] Media path rejected during %s (%s): %s",
            context,
            type(error).__name__,
            bounded_scan_error_text(error),
        )
        return None


def _is_supported_media_path(path: str) -> bool:
    """Return whether either established extension policy accepts ``path``."""
    return is_audio_file(path) or is_video_file(path)


def _detect_library_media_type(path: str) -> MediaType | None:
    """Apply audio-first classification to preserve the previous behavior."""
    if is_audio_file(path):
        return MediaType.AUDIO
    if is_video_file(path):
        return MediaType.VIDEO
    return None


def _read_library_audio_metadata(
    path: str,
    fallback_title: str,
    *,
    session: LibraryScanSession | None,
) -> tuple[str, float, dict[str, str]]:
    """Return normalized audio metadata without hard-failing library imports.

    Edge cases: unavailable or corrupt tags; invalid provider shapes; blank text
    fields; negative, overflowing, or non-finite durations.
    """
    _checkpoint(session)
    try:
        tag_data = read_audio_basic_metadata(path)
        title = (
            tag_data.title
            if isinstance(tag_data.title, str) and tag_data.title
            else fallback_title
        )
        metadata = _collect_audio_text_metadata(tag_data.artist, tag_data.album)
        duration = _positive_finite_float(tag_data.duration)
        result = (title, duration, metadata)
    except AUDIO_METADATA_EXCEPTIONS as error:
        _log(logging.WARNING, "[Library] Audio metadata error for '%s': %s", path, error)
        result = (fallback_title, 0.0, {})
    _checkpoint(session)
    return result


def _collect_audio_text_metadata(artist: object, album: object) -> dict[str, str]:
    """Return supported text tags while ignoring invalid optional values.

    Edge cases: non-string artist/album values are ignored; empty strings are
    omitted; valid values retain the established keys without transformation.
    """
    metadata: dict[str, str] = {}
    if isinstance(artist, str) and artist:
        metadata["artist"] = artist
    if isinstance(album, str) and album:
        metadata["album"] = album
    return metadata


def _read_library_video_duration(
    path: str,
    *,
    session: LibraryScanSession | None,
) -> float:
    """Return a finite duration using OpenCV and then bounded ffprobe.

    Edge cases: unavailable or failing OpenCV; invalid rates/counts; typed
    ffprobe failures; capture cleanup failures.
    """
    duration = _read_opencv_video_duration(path, session=session)
    if duration > 0.0:
        return duration
    try:
        if session is None:
            probe_value = ffprobe_service.probe_duration(path)
        else:
            timeout = _remaining_probe_timeout(session)
            probe_value = ffprobe_service.probe_duration(path, timeout_seconds=timeout)
        duration = _positive_finite_float(probe_value)
    except ffprobe_service.FfprobeError as error:
        _log(logging.DEBUG, "[Library] ffprobe duration failed for '%s': %s", path, error)
        duration = 0.0
    _checkpoint(session)
    return duration


def _read_opencv_video_duration(
    path: str,
    *,
    session: LibraryScanSession | None,
) -> float:
    """Return a finite OpenCV duration or zero when unavailable or invalid.

    Edge cases: falsey capture objects are still queried; non-finite and overflowed
    rates/counts degrade to zero; release failures do not replace the probe result.
    """
    _checkpoint(session)
    if cv2 is None:
        return 0.0
    capture = None
    try:
        capture = cv2.VideoCapture(path)
        if capture is not None and capture.isOpened():
            fps = _positive_finite_float(capture.get(cv2.CAP_PROP_FPS))
            frame_count = _positive_finite_float(capture.get(cv2.CAP_PROP_FRAME_COUNT))
            if fps > 0.0 and frame_count > 0.0:
                duration = _positive_finite_float(frame_count / fps)
                _checkpoint(session)
                return duration
    except VIDEO_METADATA_EXCEPTIONS as error:
        _log(logging.DEBUG, "[Library] OpenCV duration failed for '%s': %s", path, error)
    finally:
        if capture is not None:
            _release_video_capture(capture, path)
    _checkpoint(session)
    return 0.0


def _release_video_capture(capture: object, path: str) -> None:
    """Release a capture while containing provider-specific cleanup errors.

    Edge cases: missing release methods, provider runtime errors, and invalid
    capture proxies are logged without escaping the cleanup boundary.
    """
    try:
        capture.release()  # type: ignore[attr-defined]
    except VIDEO_METADATA_EXCEPTIONS as error:
        _log(logging.DEBUG, "[Library] VideoCapture release failed for '%s': %s", path, error)


def _positive_finite_float(value: object) -> float:
    """Return a positive finite float or zero for invalid duration values.

    Edge cases: booleans are not numeric durations; conversion failures return zero;
    non-positive, non-finite, and overflowed values return zero.
    """
    if isinstance(value, bool):
        return 0.0
    try:
        normalized = float(value)
    except (OverflowError, TypeError, ValueError):
        return 0.0
    if not math.isfinite(normalized) or normalized <= 0.0:
        return 0.0
    return normalized


def _read_library_video_metadata(
    path: str,
    *,
    session: LibraryScanSession | None,
) -> dict[str, object]:
    """Return JSON-safe audio-track metadata through the probe boundary.

    Edge cases: unavailable ffprobe; malformed streams; deterministic candidate
    ordering supplied by the shared ffprobe service.
    """
    _checkpoint(session)
    try:
        if session is None:
            audio_tracks = ffprobe_service.probe_audio_tracks(path)
        else:
            timeout = _remaining_probe_timeout(session)
            audio_tracks = ffprobe_service.probe_audio_tracks(
                path, timeout_seconds=timeout
            )
        candidates = ffprobe_service.build_audio_track_candidates(audio_tracks)
    except ffprobe_service.FfprobeError as error:
        _log(logging.DEBUG, "[Library] ffprobe audio tracks failed for '%s': %s", path, error)
        audio_tracks, candidates = [], []
    except VIDEO_TRACK_METADATA_EXCEPTIONS as error:
        _log(
            logging.WARNING,
            "[Library] Invalid ffprobe audio-track payload for '%s' (%s): %s",
            path,
            type(error).__name__,
            bounded_scan_error_text(error),
        )
        audio_tracks, candidates = [], []
    _checkpoint(session)
    return {
        "audio_tracks": audio_tracks,
        "audio_track_candidates": candidates,
    }


def _checkpoint(session: LibraryScanSession | None) -> None:
    if session is not None:
        session.checkpoint()


def _remaining_probe_timeout(session: LibraryScanSession | None) -> float:
    if session is None:
        return ffprobe_service.DEFAULT_TIMEOUT_SECONDS
    return session.remaining_seconds(ffprobe_service.DEFAULT_TIMEOUT_SECONDS)


__all__ = [
    "discover_library_media_paths",
    "load_library_media_file",
    "load_library_media_files",
    "scan_library_media_files",
    "scan_library_media_paths",
]
