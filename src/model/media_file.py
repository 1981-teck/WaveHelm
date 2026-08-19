"""Validated media-domain record with fail-closed construction.

Boundary edge cases and mitigations:
- empty, non-text, or NUL-containing paths are rejected before an instance is returned;
- negative, non-finite, boolean, or non-numeric durations raise a typed validation error;
- malformed, recursive, or oversized metadata is rejected within fixed depth, item, and byte budgets;
- local paths are normalized while supported stream and CDDA protocol paths remain unchanged.
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from numbers import Real
from pathlib import Path
from typing import Final, TypedDict

logger = logging.getLogger(__name__)

_STREAM_PREFIXES: Final = ("http://", "https://", "rtsp://", "rtmp://")
_NON_LOCAL_PREFIXES: Final = _STREAM_PREFIXES + ("cdda://",)
_AUDIO_EXTENSIONS: Final = frozenset({".mp3", ".wav", ".flac", ".ogg", ".aac", ".m4a", ".wma", ".opus"})
_VIDEO_EXTENSIONS: Final = frozenset({".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv", ".mpeg"})
_KNOWN_TEXT_METADATA_FIELDS: Final = ("title", "artist", "album", "genre")
_MAX_METADATA_DEPTH: Final = 12
_MAX_METADATA_ITEMS: Final = 8_192
_MAX_METADATA_SCALAR_BYTES: Final = 32 * 1024 * 1024
_MAX_METADATA_TEXT_CHARS: Final = 1_048_576
_MAX_METADATA_BINARY_BYTES: Final = 16 * 1024 * 1024
_MAX_METADATA_KEY_CHARS: Final = 256
_MAX_METADATA_INTEGER_BITS: Final = 128

class MediaFileValidationError(ValueError):
    """Raised when a media record violates a construction or assignment invariant."""

class MediaFileRecord(TypedDict):
    """Stable serialized representation emitted by :meth:`MediaFile.to_dict`."""

    path: str
    title: str
    media_type: str
    duration: float
    metadata: dict[str, object]

class MediaType(Enum):
    AUDIO = "audio"
    VIDEO = "video"
    CD_TRACK = "cd_track"
    PODCAST = "podcast"
    STREAM = "stream"
    UNKNOWN = "unknown"
    ALL = "all"

@dataclass
class _MetadataBudget:
    remaining_items: int = _MAX_METADATA_ITEMS
    remaining_scalar_bytes: int = _MAX_METADATA_SCALAR_BYTES

    def claim_item(self) -> None:
        self.remaining_items -= 1
        if self.remaining_items < 0:
            raise MediaFileValidationError("Media metadata exceeds the item limit.")

    def claim_scalar_bytes(self, byte_count: int) -> None:
        self.remaining_scalar_bytes -= byte_count
        if self.remaining_scalar_bytes < 0:
            raise MediaFileValidationError("Media metadata exceeds the scalar byte budget.")

def _is_non_local_path(path: str) -> bool:
    return path.lower().startswith(_NON_LOCAL_PREFIXES)

def _normalize_path(value: object) -> str:
    """Reject invalid PathLike, empty, and NUL-containing media identifiers."""
    if isinstance(value, os.PathLike):
        try:
            value = os.fspath(value)
        except (OSError, TypeError, ValueError) as error:
            raise MediaFileValidationError("Media path could not be resolved.") from error
    if not isinstance(value, str):
        raise MediaFileValidationError("Media path must be text.")
    if not value.strip():
        raise MediaFileValidationError("Media path must not be empty.")
    if "\x00" in value:
        raise MediaFileValidationError("Media path must not contain NUL characters.")
    return value if _is_non_local_path(value) else os.path.normpath(value)

def _normalize_title(value: object, *, allow_empty: bool) -> str:
    if not isinstance(value, str):
        raise MediaFileValidationError("Media title must be text.")
    title = value.strip()
    if not title and not allow_empty:
        raise MediaFileValidationError("Media title must not be empty after construction.")
    return title

def _normalize_duration(value: object) -> float:
    """Reject booleans, negatives, non-finite values, and float overflow."""
    if isinstance(value, bool) or not isinstance(value, Real):
        raise MediaFileValidationError("Media duration must be a real number.")
    try:
        duration = float(value)
    except (OverflowError, TypeError, ValueError) as error:
        raise MediaFileValidationError("Media duration cannot be represented safely.") from error
    if not math.isfinite(duration):
        raise MediaFileValidationError("Media duration must be finite.")
    if duration < 0.0:
        raise MediaFileValidationError("Media duration must not be negative.")
    return duration

def _normalize_media_type(value: object) -> MediaType:
    if not isinstance(value, MediaType):
        raise MediaFileValidationError("Media type must be a MediaType value.")
    if value is MediaType.ALL:
        raise MediaFileValidationError("MediaType.ALL is a filter and cannot identify media.")
    return value

def _parse_serialized_media_type(value: object, *, field_name: str) -> MediaType:
    if value is None:
        return MediaType.UNKNOWN
    if isinstance(value, MediaType):
        return _normalize_media_type(value)
    if not isinstance(value, str):
        raise MediaFileValidationError(f"{field_name} must be text.")
    normalized = value.strip().lower()
    if not normalized:
        return MediaType.UNKNOWN
    try:
        return _normalize_media_type(MediaType(normalized))
    except ValueError as error:
        raise MediaFileValidationError(f"Unsupported {field_name}: {value!r}.") from error

def _validate_known_metadata(metadata: Mapping[str, object]) -> None:
    for field_name in _KNOWN_TEXT_METADATA_FIELDS:
        value = metadata.get(field_name)
        if value is not None and not isinstance(value, str):
            raise MediaFileValidationError(f"metadata.{field_name} must be text or null.")
    if "media_type" in metadata:
        _parse_serialized_media_type(
            metadata.get("media_type"),
            field_name="metadata.media_type",
        )

def _utf8_size(value: str) -> int:
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise MediaFileValidationError("Media metadata text must be valid UTF-8.") from error

def _clone_metadata_key(key: object, budget: _MetadataBudget) -> str:
    if not isinstance(key, str):
        raise MediaFileValidationError("Media metadata keys must be text.")
    if len(key) > _MAX_METADATA_KEY_CHARS:
        raise MediaFileValidationError("Media metadata key exceeds the length limit.")
    budget.claim_scalar_bytes(_utf8_size(key))
    return key

def _clone_metadata_scalar(value: object, budget: _MetadataBudget) -> object:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        if len(value) > _MAX_METADATA_TEXT_CHARS:
            raise MediaFileValidationError("Media metadata text exceeds the length limit.")
        budget.claim_scalar_bytes(_utf8_size(value))
        return value
    if isinstance(value, (bytes, bytearray)):
        if len(value) > _MAX_METADATA_BINARY_BYTES:
            raise MediaFileValidationError("Media metadata binary value exceeds the size limit.")
        budget.claim_scalar_bytes(len(value))
        return bytes(value)
    if isinstance(value, int):
        if value.bit_length() > _MAX_METADATA_INTEGER_BITS:
            raise MediaFileValidationError("Media metadata integer exceeds the bit limit.")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise MediaFileValidationError("Media metadata numbers must be finite.")
        return value
    raise MediaFileValidationError(
        f"Unsupported media metadata value type: {type(value).__name__}."
    )

def _clone_metadata_value(
    value: object,
    *,
    budget: _MetadataBudget,
    depth: int,
    active_container_ids: set[int],
) -> object:
    if depth > _MAX_METADATA_DEPTH:
        raise MediaFileValidationError("Media metadata exceeds the depth limit.")
    budget.claim_item()
    if not isinstance(value, (Mapping, list, tuple)):
        return _clone_metadata_scalar(value, budget)

    container_id = id(value)
    if container_id in active_container_ids:
        raise MediaFileValidationError("Media metadata must not contain recursive containers.")
    active_container_ids.add(container_id)
    try:
        if isinstance(value, Mapping):
            return {
                _clone_metadata_key(key, budget): _clone_metadata_value(
                    item,
                    budget=budget,
                    depth=depth + 1,
                    active_container_ids=active_container_ids,
                )
                for key, item in value.items()
            }
        cloned_items = [
            _clone_metadata_value(
                item,
                budget=budget,
                depth=depth + 1,
                active_container_ids=active_container_ids,
            )
            for item in value
        ]
        return tuple(cloned_items) if isinstance(value, tuple) else cloned_items
    finally:
        active_container_ids.remove(container_id)

def _normalize_metadata(value: object) -> dict[str, object]:
    """Clone metadata while enforcing type, recursion, depth, item, and byte limits."""
    if not isinstance(value, Mapping):
        raise MediaFileValidationError("Media metadata must be a mapping.")
    cloned = _clone_metadata_value(
        value,
        budget=_MetadataBudget(
            remaining_items=_MAX_METADATA_ITEMS,
            remaining_scalar_bytes=_MAX_METADATA_SCALAR_BYTES,
        ),
        depth=0,
        active_container_ids=set(),
    )
    if not isinstance(cloned, dict):
        raise MediaFileValidationError("Media metadata must normalize to a mapping.")
    _validate_known_metadata(cloned)
    return cloned

def _derive_title(path: str, metadata: Mapping[str, object]) -> str:
    metadata_title = metadata.get("title")
    if isinstance(metadata_title, str) and metadata_title.strip():
        return metadata_title.strip()
    stem = Path(path).stem.strip()
    return stem or "Unknown"

def _detect_media_type(path: str, metadata: Mapping[str, object]) -> MediaType:
    lower_path = path.lower()
    if lower_path.startswith(_STREAM_PREFIXES):
        return MediaType.STREAM
    if lower_path.startswith("cdda://"):
        return MediaType.CD_TRACK

    extension = Path(path).suffix.lower()
    if extension in _AUDIO_EXTENSIONS:
        return MediaType.AUDIO
    if extension in _VIDEO_EXTENSIONS:
        return MediaType.VIDEO
    if extension == ".cda":
        return MediaType.CD_TRACK

    if "media_type" in metadata:
        return _parse_serialized_media_type(
            metadata.get("media_type"),
            field_name="metadata.media_type",
        )
    return MediaType.UNKNOWN

@dataclass(eq=True, init=False)
class MediaFile:
    """Mutable media record whose public assignments preserve core invariants."""

    _path: str = field(repr=False)
    _title: str = field(repr=False)
    _media_type: MediaType = field(repr=False)
    _duration: float = field(repr=False)
    _metadata: dict[str, object] = field(repr=False)

    def __init__(
        self,
        path: object,
        title: object = "",
        media_type: object = MediaType.UNKNOWN,
        duration: object = 0.0,
        metadata: object = None,
    ) -> None:
        """Validate all inputs atomically and detach caller-owned metadata."""
        normalized_path = _normalize_path(path)
        normalized_title = _normalize_title(title, allow_empty=True)
        normalized_media_type = _normalize_media_type(media_type)
        normalized_duration = _normalize_duration(duration)
        normalized_metadata = _normalize_metadata({} if metadata is None else metadata)
        if not normalized_title:
            normalized_title = _derive_title(normalized_path, normalized_metadata)
        if normalized_media_type is MediaType.UNKNOWN:
            normalized_media_type = _detect_media_type(normalized_path, normalized_metadata)

        object.__setattr__(self, "_path", normalized_path)
        object.__setattr__(self, "_title", normalized_title)
        object.__setattr__(self, "_media_type", normalized_media_type)
        object.__setattr__(self, "_duration", normalized_duration)
        object.__setattr__(self, "_metadata", normalized_metadata)

    def __repr__(self) -> str:
        return (
            "MediaFile("
            f"path={self.path!r}, title={self.title!r}, "
            f"media_type={self.media_type!r}, duration={self.duration!r}, "
            f"metadata={self._metadata!r})"
        )

    @property
    def path(self) -> str:
        return self._path

    @path.setter
    def path(self, value: object) -> None:
        normalized = _normalize_path(value)
        if normalized != self._path:
            raise MediaFileValidationError("Media path is immutable after construction.")

    @property
    def title(self) -> str:
        return self._title

    @title.setter
    def title(self, value: object) -> None:
        object.__setattr__(self, "_title", _normalize_title(value, allow_empty=False))

    @property
    def media_type(self) -> MediaType:
        return self._media_type

    @media_type.setter
    def media_type(self, value: object) -> None:
        normalized = _normalize_media_type(value)
        if normalized is not self._media_type:
            raise MediaFileValidationError("Media type is immutable after construction.")

    @property
    def duration(self) -> float:
        return self._duration

    @duration.setter
    def duration(self, value: object) -> None:
        object.__setattr__(self, "_duration", _normalize_duration(value))

    @property
    def metadata(self) -> dict[str, object]:
        return _normalize_metadata(self._metadata)

    @metadata.setter
    def metadata(self, value: object) -> None:
        object.__setattr__(self, "_metadata", _normalize_metadata(value))

    def get_display_title(self) -> str:
        artist = self._metadata.get("artist") or ""
        album = self._metadata.get("album") or ""
        if artist and album:
            return f"{self.title} - {artist} ({album})"
        if artist:
            return f"{artist} - {self.title}"
        if album:
            return f"{self.title} ({album})"
        return self.title

    def get_formatted_duration(self) -> str:
        total_seconds = int(self.duration)
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def is_local_file(self) -> bool:
        return not _is_non_local_path(self.path)

    def exists(self) -> bool:
        return self.is_local_file() and os.path.isfile(self.path)

    @property
    def file_extension(self) -> str:
        return Path(self.path).suffix.lower()

    @property
    def file_size(self) -> int:
        if not self.exists():
            return 0
        try:
            return os.path.getsize(self.path)
        except OSError as error:
            logger.debug("Unable to read media size for %s: %s", self.path, error)
            return 0

    def to_dict(self) -> MediaFileRecord:
        return {
            "path": self.path,
            "title": self.title,
            "media_type": self.media_type.value,
            "duration": self.duration,
            "metadata": _normalize_metadata(self._metadata),
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> MediaFile:
        """Reject missing/malformed fields while ignoring forward-compatible extras."""
        if not isinstance(data, Mapping):
            raise MediaFileValidationError("Media record must be a mapping.")
        if "path" not in data:
            raise MediaFileValidationError("Media record is missing path.")

        media_type = _parse_serialized_media_type(
            data.get("media_type"),
            field_name="media_type",
        )
        return cls(
            path=data.get("path"),
            title=data.get("title", ""),
            media_type=media_type,
            duration=data.get("duration", 0.0),
            metadata=data.get("metadata", {}),
        )

    @classmethod
    def from_dict(cls, data: object) -> MediaFile | None:
        """Compatibility boundary that drops invalid persisted records explicitly."""
        if not isinstance(data, Mapping):
            logger.warning("Invalid MediaFile record: expected a mapping.")
            return None
        try:
            return cls.from_mapping(data)
        except MediaFileValidationError as error:
            logger.warning("Invalid MediaFile record: %s", error)
            return None

Media = MediaFile

__all__ = [
    "Media",
    "MediaFile",
    "MediaFileRecord",
    "MediaFileValidationError",
    "MediaType",
]
