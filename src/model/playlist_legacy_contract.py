"""Strict schema contract for the public legacy :class:`Playlist` model.

The legacy model remains importable for compatibility, but its persistence
boundary now accepts only a bounded, typed JSON document. Validation is O(N)
in the number of songs, tags, and JSON nodes and is bounded by the constants
below.

Edge cases handled:
- cyclic, oversized, duplicate-key, or non-finite documents fail closed;
- song identifiers must be unique within one playlist;
- malformed timestamps and timelines are rejected before state is created;
- caller-owned songs and tag collections are copied before storage.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
import math
from typing import Final, cast
import unicodedata

from src.model.song import Song
from src.utils.bounded_json import (
    BoundedJsonError,
    JsonLimits,
    JsonValue,
    normalize_json_mapping,
    parse_json_bytes,
    serialize_json_bytes,
)

MAX_PLAYLIST_BYTES: Final = 8 * 1024 * 1024
MAX_PLAYLIST_SONGS: Final = 10_000
MAX_TAGS_PER_SONG: Final = 128
MAX_IDENTIFIER_CHARS: Final = 256
MAX_IDENTIFIER_BYTES: Final = 1_024
MAX_NAME_CHARS: Final = 512
MAX_NAME_BYTES: Final = 2_048
MAX_TEXT_CHARS: Final = 32_768
MAX_TEXT_BYTES: Final = 131_072
MAX_TAG_CHARS: Final = 256
MAX_TAG_BYTES: Final = 1_024
MAX_SONG_DURATION_SECONDS: Final = 315_576_000.0
MAX_TRACK_NUMBER: Final = 1_000_000
MAX_YEAR: Final = 9_999

PLAYLIST_JSON_LIMITS = JsonLimits(
    max_bytes=MAX_PLAYLIST_BYTES,
    max_depth=10,
    max_nodes=250_000,
    max_container_items=MAX_PLAYLIST_SONGS,
    max_key_chars=64,
    max_key_bytes=256,
    max_string_chars=MAX_TEXT_CHARS,
    max_string_bytes=MAX_TEXT_BYTES,
    max_total_text_chars=MAX_PLAYLIST_BYTES,
    max_total_text_bytes=MAX_PLAYLIST_BYTES,
)

_PLAYLIST_KEYS = frozenset(
    {
        "id",
        "name",
        "songs",
        "creation_date",
        "last_modified",
        "description",
        "cover_art",
    }
)
_SONG_KEYS = frozenset(
    {
        "id",
        "title",
        "artist",
        "album",
        "duration",
        "file_path",
        "genre",
        "year",
        "track_number",
        "artwork_url",
        "tags",
    }
)

class LegacyPlaylistContractError(ValueError):
    """Raised when legacy playlist state violates its bounded schema."""

@dataclass(frozen=True, slots=True)
class LegacyPlaylistSnapshot:
    """Detached, validated state used by runtime and persistence boundaries."""

    playlist_id: str
    name: str
    songs: tuple[Song, ...]
    creation_date: datetime
    last_modified: datetime
    description: str | None
    cover_art: str | None
def validate_playlist_identifier(value: object) -> str:
    """Validate an identifier without using it directly as a path component."""
    return _validate_text(
        value,
        label="playlist id",
        max_chars=MAX_IDENTIFIER_CHARS,
        max_bytes=MAX_IDENTIFIER_BYTES,
        allow_empty=False,
    )
def validate_playlist_state(
    *,
    playlist_id: object,
    name: object,
    songs: object,
    creation_date: object,
    last_modified: object,
    description: object,
    cover_art: object,
) -> LegacyPlaylistSnapshot:
    """Validate and detach one in-memory legacy playlist state."""
    validated_id = validate_playlist_identifier(playlist_id)
    validated_name = _validate_text(
        name,
        label="playlist name",
        max_chars=MAX_NAME_CHARS,
        max_bytes=MAX_NAME_BYTES,
        allow_empty=False,
    )
    validated_creation = _validate_datetime(creation_date, "creation_date")
    validated_modified = _validate_datetime(last_modified, "last_modified")
    _validate_timeline(validated_creation, validated_modified)
    return LegacyPlaylistSnapshot(
        playlist_id=validated_id,
        name=validated_name,
        songs=_copy_song_sequence(songs),
        creation_date=validated_creation,
        last_modified=validated_modified,
        description=_validate_optional_text(description, "description"),
        cover_art=_validate_optional_text(cover_art, "cover_art"),
    )
def playlist_snapshot_from_mapping(data: object) -> LegacyPlaylistSnapshot:
    """Parse a caller-provided mapping using the same strict document budget."""
    try:
        normalized = normalize_json_mapping(data, limits=PLAYLIST_JSON_LIMITS)
    except BoundedJsonError as error:
        raise LegacyPlaylistContractError(str(error)) from error
    return _snapshot_from_document(normalized)
def parse_playlist_payload(payload: bytes) -> LegacyPlaylistSnapshot:
    """Parse one bounded UTF-8 playlist payload into detached typed state."""
    if type(payload) is not bytes:
        raise LegacyPlaylistContractError("playlist payload must be bytes")
    try:
        document = parse_json_bytes(
            payload,
            limits=PLAYLIST_JSON_LIMITS,
            root="object",
        )
    except BoundedJsonError as error:
        raise LegacyPlaylistContractError(str(error)) from error
    return _snapshot_from_document(cast(dict[str, JsonValue], document))
def playlist_snapshot_to_document(
    snapshot: LegacyPlaylistSnapshot,
) -> dict[str, JsonValue]:
    """Return a detached JSON document for one validated snapshot."""
    songs = [_song_to_document(song) for song in snapshot.songs]
    return {
        "id": snapshot.playlist_id,
        "name": snapshot.name,
        "songs": songs,
        "creation_date": snapshot.creation_date.isoformat(),
        "last_modified": snapshot.last_modified.isoformat(),
        "description": snapshot.description,
        "cover_art": snapshot.cover_art,
    }
def serialize_playlist_snapshot(snapshot: LegacyPlaylistSnapshot) -> bytes:
    """Serialize a snapshot within the same hard budget used by the loader."""
    document = playlist_snapshot_to_document(snapshot)
    try:
        _normalized, payload = serialize_json_bytes(
            document,
            limits=PLAYLIST_JSON_LIMITS,
            root="object",
            indent=2,
            sort_keys=True,
            trailing_newline=True,
        )
    except BoundedJsonError as error:
        raise LegacyPlaylistContractError(str(error)) from error
    return payload
def copy_song(song: Song) -> Song:
    """Return a validated detached copy of one Song instance."""
    return _validate_song_instance(song)
def _snapshot_from_document(
    document: dict[str, JsonValue],
) -> LegacyPlaylistSnapshot:
    _require_exact_keys(document, _PLAYLIST_KEYS, "playlist")
    songs_value = document["songs"]
    if type(songs_value) is not list:
        raise LegacyPlaylistContractError("playlist songs must be a JSON array")
    songs = tuple(_song_from_document(item) for item in songs_value)
    return validate_playlist_state(
        playlist_id=document["id"],
        name=document["name"],
        songs=songs,
        creation_date=_parse_datetime_text(document["creation_date"], "creation_date"),
        last_modified=_parse_datetime_text(document["last_modified"], "last_modified"),
        description=document["description"],
        cover_art=document["cover_art"],
    )
def _copy_song_sequence(value: object) -> tuple[Song, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise LegacyPlaylistContractError("playlist songs must be a sequence")
    copied: list[Song] = []
    identifiers: set[str] = set()
    try:
        for index, song in enumerate(value):
            if index >= MAX_PLAYLIST_SONGS:
                raise LegacyPlaylistContractError("playlist song limit exceeded")
            copied_song = _validate_song_instance(song)
            if copied_song.id in identifiers:
                raise LegacyPlaylistContractError("playlist song ids must be unique")
            identifiers.add(copied_song.id)
            copied.append(copied_song)
    except LegacyPlaylistContractError:
        raise
    except Exception as error:  # explicit caller-provided Sequence boundary
        raise LegacyPlaylistContractError(
            f"unable to enumerate playlist songs: {type(error).__name__}"
        ) from error
    return tuple(copied)
def _validate_song_instance(value: object) -> Song:
    if type(value) is not Song:
        raise LegacyPlaylistContractError("playlist entries must be Song instances")
    duration = _validate_duration(value.duration)
    return Song(
        id=_validate_identifier_text(value.id, "song id"),
        title=_validate_name_text(value.title, "song title"),
        artist=_validate_name_text(value.artist, "song artist"),
        album=_validate_name_text(value.album, "song album"),
        duration=duration,
        file_path=_validate_path_text(value.file_path, "song file_path"),
        genre=_validate_optional_text(value.genre, "song genre"),
        year=_validate_optional_integer(value.year, "song year", 0, MAX_YEAR),
        track_number=_validate_optional_integer(
            value.track_number,
            "song track_number",
            1,
            MAX_TRACK_NUMBER,
        ),
        artwork_url=_validate_optional_text(value.artwork_url, "song artwork_url"),
        tags=_copy_tags(value.tags),
    )
def _song_from_document(value: JsonValue) -> Song:
    if type(value) is not dict:
        raise LegacyPlaylistContractError("playlist song entry must be an object")
    document = cast(dict[str, JsonValue], value)
    _require_exact_keys(document, _SONG_KEYS, "song")
    duration = _duration_from_number(document["duration"])
    return Song(
        id=_validate_identifier_text(document["id"], "song id"),
        title=_validate_name_text(document["title"], "song title"),
        artist=_validate_name_text(document["artist"], "song artist"),
        album=_validate_name_text(document["album"], "song album"),
        duration=duration,
        file_path=_validate_path_text(document["file_path"], "song file_path"),
        genre=_validate_optional_text(document["genre"], "song genre"),
        year=_validate_optional_integer(document["year"], "song year", 0, MAX_YEAR),
        track_number=_validate_optional_integer(
            document["track_number"],
            "song track_number",
            1,
            MAX_TRACK_NUMBER,
        ),
        artwork_url=_validate_optional_text(document["artwork_url"], "song artwork_url"),
        tags=_copy_tags(document["tags"]),
    )
def _song_to_document(song: Song) -> dict[str, JsonValue]:
    validated = _validate_song_instance(song)
    return {
        "id": validated.id,
        "title": validated.title,
        "artist": validated.artist,
        "album": validated.album,
        "duration": validated.duration.total_seconds(),
        "file_path": validated.file_path,
        "genre": validated.genre,
        "year": validated.year,
        "track_number": validated.track_number,
        "artwork_url": validated.artwork_url,
        "tags": list(validated.tags or ()),
    }
def _copy_tags(value: object) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise LegacyPlaylistContractError("song tags must be a sequence")
    tags: list[str] = []
    try:
        for index, tag in enumerate(value):
            if index >= MAX_TAGS_PER_SONG:
                raise LegacyPlaylistContractError("song tag limit exceeded")
            tags.append(
                _validate_text(
                    tag,
                    label="song tag",
                    max_chars=MAX_TAG_CHARS,
                    max_bytes=MAX_TAG_BYTES,
                    allow_empty=False,
                )
            )
    except LegacyPlaylistContractError:
        raise
    except Exception as error:  # explicit caller-provided Sequence boundary
        raise LegacyPlaylistContractError(
            f"unable to enumerate song tags: {type(error).__name__}"
        ) from error
    return tags
def _validate_duration(value: object) -> timedelta:
    if type(value) is not timedelta:
        raise LegacyPlaylistContractError("song duration must be a timedelta")
    seconds = value.total_seconds()
    return _duration_from_number(seconds)
def _duration_from_number(value: object) -> timedelta:
    if type(value) not in (int, float):
        raise LegacyPlaylistContractError("song duration must be numeric")
    seconds = float(cast(int | float, value))
    if not math.isfinite(seconds) or not 0 <= seconds <= MAX_SONG_DURATION_SECONDS:
        raise LegacyPlaylistContractError("song duration is outside its valid range")
    return timedelta(seconds=seconds)
def _validate_datetime(value: object, label: str) -> datetime:
    if type(value) is not datetime:
        raise LegacyPlaylistContractError(f"{label} must be a datetime")
    try:
        return datetime.fromisoformat(value.isoformat())
    except Exception as error:  # explicit caller-provided tzinfo boundary
        raise LegacyPlaylistContractError(f"{label} is invalid") from error
def _parse_datetime_text(value: object, label: str) -> datetime:
    if type(value) is not str or len(value) > 64:
        raise LegacyPlaylistContractError(f"{label} must be bounded ISO text")
    try:
        return datetime.fromisoformat(value)
    except ValueError as error:
        raise LegacyPlaylistContractError(f"{label} is not valid ISO datetime text") from error
def _validate_timeline(created: datetime, modified: datetime) -> None:
    created_aware = created.utcoffset() is not None
    modified_aware = modified.utcoffset() is not None
    if created_aware != modified_aware:
        raise LegacyPlaylistContractError("playlist timestamps must use the same timezone mode")
    if modified < created:
        raise LegacyPlaylistContractError("last_modified cannot precede creation_date")
def _validate_identifier_text(value: object, label: str) -> str:
    return _validate_text(
        value,
        label=label,
        max_chars=MAX_IDENTIFIER_CHARS,
        max_bytes=MAX_IDENTIFIER_BYTES,
        allow_empty=False,
    )
def _validate_name_text(value: object, label: str) -> str:
    return _validate_text(
        value,
        label=label,
        max_chars=MAX_NAME_CHARS,
        max_bytes=MAX_NAME_BYTES,
        allow_empty=False,
    )
def _validate_path_text(value: object, label: str) -> str:
    return _validate_text(
        value,
        label=label,
        max_chars=MAX_TEXT_CHARS,
        max_bytes=MAX_TEXT_BYTES,
        allow_empty=False,
    )
def _validate_optional_text(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _validate_text(
        value,
        label=label,
        max_chars=MAX_TEXT_CHARS,
        max_bytes=MAX_TEXT_BYTES,
        allow_empty=True,
    )
def _validate_optional_integer(
    value: object,
    label: str,
    minimum: int,
    maximum: int,
) -> int | None:
    if value is None:
        return None
    if type(value) is not int:
        raise LegacyPlaylistContractError(f"{label} must be an integer")
    number = cast(int, value)
    if not minimum <= number <= maximum:
        raise LegacyPlaylistContractError(f"{label} is outside its valid range")
    return number
def _validate_text(
    value: object,
    *,
    label: str,
    max_chars: int,
    max_bytes: int,
    allow_empty: bool,
) -> str:
    if type(value) is not str:
        raise LegacyPlaylistContractError(f"{label} must be text")
    text = cast(str, value)
    if len(text) > max_chars:
        raise LegacyPlaylistContractError(f"{label} exceeds its character limit")
    if not allow_empty and not text.strip():
        raise LegacyPlaylistContractError(f"{label} must not be empty")
    if any(unicodedata.category(character).startswith("C") for character in text):
        raise LegacyPlaylistContractError(f"{label} contains a control character")
    try:
        encoded = text.encode("utf-8")
    except UnicodeEncodeError as error:
        raise LegacyPlaylistContractError(f"{label} is not valid UTF-8 text") from error
    if len(encoded) > max_bytes:
        raise LegacyPlaylistContractError(f"{label} exceeds its UTF-8 byte limit")
    return text
def _require_exact_keys(
    document: Mapping[str, JsonValue],
    expected: frozenset[str],
    label: str,
) -> None:
    actual = frozenset(document)
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if missing or unexpected:
        raise LegacyPlaylistContractError(
            f"{label} keys are incompatible; missing={missing}, unexpected={unexpected}"
        )
__all__ = [
    "LegacyPlaylistContractError",
    "LegacyPlaylistSnapshot",
    "MAX_PLAYLIST_BYTES",
    "MAX_PLAYLIST_SONGS",
    "PLAYLIST_JSON_LIMITS",
    "copy_song",
    "parse_playlist_payload",
    "playlist_snapshot_from_mapping",
    "playlist_snapshot_to_document",
    "serialize_playlist_snapshot",
    "validate_playlist_identifier",
    "validate_playlist_state",
]
