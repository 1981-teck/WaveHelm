from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any, Mapping, Sequence

logger = logging.getLogger(__name__)

METADATA_READ_EXCEPTIONS = (AttributeError, OSError, RuntimeError, TypeError, UnicodeError, ValueError)


class AudioMetadataError(RuntimeError):
    """Raised when audio metadata cannot be parsed deterministically."""


class AudioMetadataDependencyUnavailableError(AudioMetadataError):
    """Raised when the TinyTag runtime dependency is not installed."""


@dataclass(frozen=True, slots=True)
class AudioTagMetadata:
    """Normalized audio tag payload used by the runtime metadata paths."""

    title: str | None = None
    artist: str | None = None
    album: str | None = None
    duration: float = 0.0
    cover_image: bytes | None = None
    cover_mime_type: str | None = None
    lyrics: str | None = None


@dataclass(frozen=True, slots=True)
class _TinyTagBindings:
    tag_class: Any
    exception_types: tuple[type[BaseException], ...]


def _resolve_tinytag_bindings() -> _TinyTagBindings:
    """Resolve TinyTag lazily so metadata imports stay deterministic.

    Edge cases handled:
    - TinyTag may be missing from the active runtime environment
    - packaging/import errors must surface as a deterministic dependency error
    - callers importing this module must not fail during metadata dependency checks
    """
    try:
        from tinytag import ParseError, TinyTag, TinyTagException, UnsupportedFormatError
    except ImportError as exc:
        raise AudioMetadataDependencyUnavailableError("TinyTag is not installed.") from exc

    return _TinyTagBindings(
        tag_class=TinyTag,
        exception_types=(ParseError, TinyTagException, UnsupportedFormatError),
    )


def _normalize_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return None


def _normalize_duration(value: object) -> float:
    try:
        duration = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return duration if duration > 0.0 else 0.0


def _first_other_value(other: Mapping[str, object], key: str) -> str | None:
    raw_value = other.get(key)
    if isinstance(raw_value, Sequence) and not isinstance(raw_value, (str, bytes, bytearray)):
        for item in raw_value:
            normalized = _normalize_text(item)
            if normalized:
                return normalized
        return None
    return _normalize_text(raw_value)


def _extract_cover_data(tag: Any) -> tuple[bytes | None, str | None]:
    image = getattr(getattr(tag, "images", None), "any", None)
    if image is None:
        return None, None

    image_data = getattr(image, "data", None)
    if not isinstance(image_data, bytes) or not image_data:
        return None, None

    mime_type = _normalize_text(getattr(image, "mime_type", None))
    return image_data, mime_type


def _read_tinytag(file_path: str, *, image: bool) -> Any:
    """Return the raw TinyTag object for an existing local audio file.

    Edge cases handled:
    - empty or missing paths fail fast before parser invocation
    - malformed or unsupported files are converted to a stable project error
    - image parsing stays opt-in to avoid unnecessary work on simple scans
    """
    if not file_path:
        raise AudioMetadataError("Audio metadata requires a non-empty file path.")

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(file_path)

    bindings = _resolve_tinytag_bindings()
    try:
        return bindings.tag_class.get(str(path), image=image)
    except bindings.exception_types + METADATA_READ_EXCEPTIONS as exc:
        raise AudioMetadataError(f"Failed to read metadata for '{file_path}'.") from exc


def read_audio_basic_metadata(file_path: str) -> AudioTagMetadata:
    """Read the basic metadata fields used by library import and indexing.

    Edge cases handled:
    - unsupported files return a deterministic AudioMetadataError for caller fallback
    - blank or malformed text tags are normalized to None instead of empty strings
    - missing or invalid durations are clamped to 0.0 to avoid negative UI state
    """
    tag = _read_tinytag(file_path, image=False)
    return AudioTagMetadata(
        title=_normalize_text(getattr(tag, "title", None)),
        artist=_normalize_text(getattr(tag, "artist", None)),
        album=_normalize_text(getattr(tag, "album", None)),
        duration=_normalize_duration(getattr(tag, "duration", None)),
    )


def read_audio_rich_metadata(file_path: str) -> AudioTagMetadata:
    """Read duration plus optional artwork and lyrics for richer audio views.

    Edge cases handled:
    - files without embedded artwork still return usable duration/basic metadata
    - TinyTag additional fields may expose lyrics as multi-value lists or be absent
    - binary/image payloads are validated before returning to UI consumers
    """
    tag = _read_tinytag(file_path, image=True)
    other_fields = getattr(tag, "other", {})
    if not isinstance(other_fields, Mapping):
        other_fields = {}
    cover_image, cover_mime_type = _extract_cover_data(tag)

    return AudioTagMetadata(
        title=_normalize_text(getattr(tag, "title", None)),
        artist=_normalize_text(getattr(tag, "artist", None)),
        album=_normalize_text(getattr(tag, "album", None)),
        duration=_normalize_duration(getattr(tag, "duration", None)),
        cover_image=cover_image,
        cover_mime_type=cover_mime_type,
        lyrics=_first_other_value(other_fields, "lyrics"),
    )


__all__ = [
    "AudioMetadataDependencyUnavailableError",
    "AudioMetadataError",
    "AudioTagMetadata",
    "read_audio_basic_metadata",
    "read_audio_rich_metadata",
]
