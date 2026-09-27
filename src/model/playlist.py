"""Compatibility model for legacy file-backed playlists.

The database-backed playlist subsystem is the canonical WaveHelm runtime. This
legacy model remains importable for external compatibility, but all file I/O is
strictly bounded, descriptor-contained, and atomic.
"""

from __future__ import annotations

from threading import RLock
from dataclasses import dataclass, field
from datetime import datetime
import logging
import math
from pathlib import Path
from typing import Mapping

from src.model.playlist_legacy_contract import (
    LegacyPlaylistContractError,
    LegacyPlaylistSnapshot,
    MAX_PLAYLIST_SONGS,
    copy_song,
    parse_playlist_payload,
    playlist_snapshot_from_mapping,
    playlist_snapshot_to_document,
    serialize_playlist_snapshot,
    validate_playlist_state,
)
from src.model.playlist_legacy_store import (
    LegacyPlaylistPathError,
    LegacyPlaylistReadError,
    LegacyPlaylistSaveResult,
    LegacyPlaylistStorageError,
    LegacyPlaylistWriteError,
    load_playlist_payload,
    playlist_storage_filename,
    save_playlist_payload,
)
from src.model.song import Song
from src.utils.bounded_json import JsonValue
from src.utils.durable_io import SerializedCommitGate

logger = logging.getLogger(__name__)


@dataclass
class Playlist:
    """Represent one compatibility playlist with bounded persistence.

    Edge cases:
        1. Caller-owned song objects and tag lists are detached on every mutation.
        2. Duplicate song IDs and invalid positions fail before state changes.
        3. Concurrent public mutations and saves are serialized without holding a
           state mutex during filesystem I/O.
        4. Direct external field corruption is detected before serialization.
    """

    id: str
    name: str
    songs: list[Song] = field(default_factory=list)
    creation_date: datetime = field(default_factory=datetime.now)
    last_modified: datetime = field(default_factory=datetime.now)
    description: str | None = None
    cover_art: str | None = None
    _state_lock: RLock = field(default_factory=RLock, init=False, repr=False, compare=False)
    _operation_gate: SerializedCommitGate = field(
        default_factory=SerializedCommitGate,
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        snapshot = validate_playlist_state(
            playlist_id=self.id,
            name=self.name,
            songs=self.songs,
            creation_date=self.creation_date,
            last_modified=self.last_modified,
            description=self.description,
            cover_art=self.cover_art,
        )
        self._apply_snapshot(snapshot)

    def get_song_by_id(self, song_id: str) -> Song | None:
        """Return a detached song snapshot for an exact identifier match."""
        _validate_lookup_id(song_id)
        with self._state_lock:
            snapshot = self._snapshot_unlocked()
        for song in snapshot.songs:
            if song.id == song_id:
                return copy_song(song)
        return None

    def get_song_index(self, song_id: str) -> int | None:
        """Return the zero-based index for an exact identifier match."""
        _validate_lookup_id(song_id)
        with self._state_lock:
            snapshot = self._snapshot_unlocked()
        for index, song in enumerate(snapshot.songs):
            if song.id == song_id:
                return index
        return None

    def contains_song(self, song_id: str) -> bool:
        """Return whether the playlist contains an exact song identifier."""
        return self.get_song_index(song_id) is not None

    def add_song(self, song: Song) -> bool:
        """Add one detached song and return whether state changed."""
        candidate_song = copy_song(song)
        changed = False
        with self._operation_gate.transaction():
            with self._state_lock:
                snapshot = self._snapshot_unlocked()
                if any(item.id == candidate_song.id for item in snapshot.songs):
                    return False
                if len(snapshot.songs) >= MAX_PLAYLIST_SONGS:
                    raise LegacyPlaylistContractError("playlist song limit exceeded")
                candidate = list(snapshot.songs)
                candidate.append(candidate_song)
                updated = self._with_songs(snapshot, candidate)
                self._apply_snapshot(updated)
                changed = True
        if changed:
            _safe_log(logging.DEBUG, "Added song %s to legacy playlist %s", song.title, self.name)
        return changed

    def remove_song(self, song_id: str) -> bool:
        """Remove one song and return whether state changed."""
        _validate_lookup_id(song_id)
        changed = False
        with self._operation_gate.transaction():
            with self._state_lock:
                snapshot = self._snapshot_unlocked()
                candidate = [song for song in snapshot.songs if song.id != song_id]
                if len(candidate) == len(snapshot.songs):
                    return False
                updated = self._with_songs(snapshot, candidate)
                self._apply_snapshot(updated)
                changed = True
        if changed:
            _safe_log(logging.DEBUG, "Removed song %s from legacy playlist %s", song_id, self.name)
        return changed

    def move_song(self, song_id: str, new_position: int) -> bool:
        """Move one song to a validated zero-based position."""
        _validate_lookup_id(song_id)
        if type(new_position) is not int:
            raise TypeError("new_position must be an integer")
        changed = False
        with self._operation_gate.transaction():
            with self._state_lock:
                snapshot = self._snapshot_unlocked()
                current = _song_index(snapshot.songs, song_id)
                if current is None:
                    return False
                if not 0 <= new_position < len(snapshot.songs):
                    raise ValueError("new_position must be within the playlist")
                if current == new_position:
                    return False
                candidate = list(snapshot.songs)
                candidate.insert(new_position, candidate.pop(current))
                updated = self._with_songs(snapshot, candidate)
                self._apply_snapshot(updated)
                changed = True
        if changed:
            _safe_log(logging.DEBUG, "Moved song %s to position %d", song_id, new_position)
        return changed

    def get_duration(self) -> float:
        """Return the finite total duration in seconds."""
        with self._state_lock:
            snapshot = self._snapshot_unlocked()
        total = math.fsum(song.duration.total_seconds() for song in snapshot.songs)
        if not math.isfinite(total):
            raise LegacyPlaylistContractError("playlist duration is not finite")
        return total

    def formatted_duration(self) -> str:
        """Return the total duration as HH:MM:SS."""
        total_seconds = int(self.get_duration())
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def to_dict(self) -> dict[str, JsonValue]:
        """Return a detached strict-JSON document."""
        with self._state_lock:
            snapshot = self._snapshot_unlocked()
        return playlist_snapshot_to_document(snapshot)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Playlist:
        """Create one playlist from a strict bounded mapping."""
        snapshot = playlist_snapshot_from_mapping(data)
        return cls._from_snapshot(snapshot)

    def save_to_file(self, directory: str | Path) -> LegacyPlaylistSaveResult:
        """Atomically save inside *directory* without embedding the raw ID in the path."""
        with self._operation_gate.transaction():
            with self._state_lock:
                snapshot = self._snapshot_unlocked()
            payload = serialize_playlist_snapshot(snapshot)
            result = save_playlist_payload(directory, snapshot.playlist_id, payload)
        _safe_log(logging.INFO, "Saved legacy playlist %s to %s", snapshot.name, result.path)
        return result

    @classmethod
    def load_from_file(cls, file_path: str | Path) -> Playlist:
        """Load one regular, stable, bounded JSON file."""
        payload = load_playlist_payload(file_path)
        snapshot = parse_playlist_payload(payload)
        playlist = cls._from_snapshot(snapshot)
        _safe_log(logging.INFO, "Loaded legacy playlist %s from %s", playlist.name, file_path)
        return playlist

    @staticmethod
    def storage_filename(playlist_id: str) -> str:
        """Return the deterministic safe filename used by ``save_to_file``."""
        return playlist_storage_filename(playlist_id)

    def __len__(self) -> int:
        with self._state_lock:
            return len(self._snapshot_unlocked().songs)

    def __str__(self) -> str:
        return f"{self.name} ({len(self)} songs, {self.formatted_duration()})"

    @classmethod
    def _from_snapshot(cls, snapshot: LegacyPlaylistSnapshot) -> Playlist:
        return cls(
            id=snapshot.playlist_id,
            name=snapshot.name,
            songs=list(snapshot.songs),
            creation_date=snapshot.creation_date,
            last_modified=snapshot.last_modified,
            description=snapshot.description,
            cover_art=snapshot.cover_art,
        )

    def _snapshot_unlocked(self) -> LegacyPlaylistSnapshot:
        return validate_playlist_state(
            playlist_id=self.id,
            name=self.name,
            songs=self.songs,
            creation_date=self.creation_date,
            last_modified=self.last_modified,
            description=self.description,
            cover_art=self.cover_art,
        )

    def _with_songs(
        self,
        snapshot: LegacyPlaylistSnapshot,
        songs: list[Song],
    ) -> LegacyPlaylistSnapshot:
        return validate_playlist_state(
            playlist_id=snapshot.playlist_id,
            name=snapshot.name,
            songs=songs,
            creation_date=snapshot.creation_date,
            last_modified=max(
                datetime.now(tz=snapshot.last_modified.tzinfo),
                snapshot.last_modified,
            ),
            description=snapshot.description,
            cover_art=snapshot.cover_art,
        )

    def _apply_snapshot(self, snapshot: LegacyPlaylistSnapshot) -> None:
        self.id = snapshot.playlist_id
        self.name = snapshot.name
        self.songs = [copy_song(song) for song in snapshot.songs]
        self.creation_date = snapshot.creation_date
        self.last_modified = snapshot.last_modified
        self.description = snapshot.description
        self.cover_art = snapshot.cover_art


def _song_index(songs: tuple[Song, ...], song_id: str) -> int | None:
    for index, song in enumerate(songs):
        if song.id == song_id:
            return index
    return None


def _validate_lookup_id(song_id: object) -> None:
    if type(song_id) is not str or not song_id.strip():
        raise TypeError("song_id must be non-empty text")


def _safe_log(level: int, message: str, *arguments: object) -> None:
    try:
        logger.log(level, message, *arguments)
    except Exception:  # explicit external logging-handler boundary
        return


__all__ = [
    "LegacyPlaylistContractError",
    "LegacyPlaylistPathError",
    "LegacyPlaylistReadError",
    "LegacyPlaylistSaveResult",
    "LegacyPlaylistStorageError",
    "LegacyPlaylistWriteError",
    "Playlist",
]
