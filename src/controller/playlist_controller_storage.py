from __future__ import annotations

import errno
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Callable, Optional

from src.model.component_database.playlist_mirror_journal import MAX_RECOVERY_BATCH, PlaylistMirrorJob, PlaylistMirrorJournal, PlaylistMirrorJournalError
from src.utils.durable_io import durable_replace, sync_parent_directory
from src.utils.exceptions import WaveHelmError
from src.utils.helpers import safe_filename

logger = logging.getLogger(__name__)

PLAYLIST_FILE_MAX_BYTES = 32 * 1024 * 1024
PLAYLIST_JSON_CHUNK_CHARS = 64 * 1024

class PlaylistStorageError(RuntimeError):
    """Base error for deterministic playlist mirror persistence failures."""

    def __init__(
        self,
        message: str,
        *,
        playlist_id: int,
        target: Path | None = None,
        committed: bool = False,
    ) -> None:
        super().__init__(message)
        self.playlist_id = playlist_id
        self.target = target
        self.committed = committed

class PlaylistSnapshotError(PlaylistStorageError):
    """Raised when the canonical playlist snapshot cannot be constructed."""

class PlaylistSerializationError(PlaylistStorageError):
    """Raised when a playlist snapshot cannot be encoded as bounded JSON."""

class PlaylistWriteError(PlaylistStorageError):
    """Raised when an atomic playlist mirror write cannot be completed."""

class PlaylistCleanupError(PlaylistStorageError):
    """Raised after commit when a superseded playlist file cannot be removed."""

class PlaylistDeleteError(PlaylistStorageError):
    """Raised when a playlist mirror file cannot be deleted."""

class PlaylistReconciliationError(RuntimeError):
    """Raised when startup cannot reconcile the durable mirror outbox."""

def _get_mirror_journal(self) -> PlaylistMirrorJournal | None:
    database_manager = getattr(self, "database_manager", None)
    playlist_manager = getattr(database_manager, "playlists", None)
    journal = getattr(playlist_manager, "mirror_journal", None)
    if database_manager is None:
        return None
    if playlist_manager is None or journal is None:
        raise PlaylistReconciliationError("playlist mirror journal binding is missing")
    if not isinstance(journal, PlaylistMirrorJournal):
        raise PlaylistReconciliationError("playlist mirror journal binding is invalid")
    return journal

def _apply_reconciliation_operation(self, operation: PlaylistMirrorJob) -> None:
    """Apply one journal job idempotently against the canonical cache.

    Edge cases:
        1. A delete intent can meet a recreated canonical playlist ID.
        2. Multiple failed renames can leave several stale mirror names.
        3. Missing mirror files remain successful idempotent deletions.
    """
    names = (operation.playlist_name, *operation.stale_names)
    playlist = self._playlists_cache.get(operation.playlist_id)
    if playlist is None:
        for name in names:
            self._delete_playlist_storage(operation.playlist_id, name)
        return
    current_name = str(playlist.get("name", ""))
    stale_names = tuple(name for name in names if name != current_name)
    previous_name = stale_names[0] if stale_names else None
    self._sync_playlist_file(operation.playlist_id, previous_name=previous_name)
    for stale_name in stale_names[1:]:
        self._delete_playlist_storage(operation.playlist_id, stale_name)

def _reconcile_playlist_journal(self) -> set[int]:
    """Recover a bounded batch and persist deterministic retry state."""
    journal = _get_mirror_journal(self)
    if journal is None:
        return set()
    if journal.exhausted_count() > 0:
        raise PlaylistReconciliationError(
            "playlist mirror journal contains operations at the retry ceiling"
        )
    recovered: set[int] = set()
    failures: list[str] = []
    for operation in journal.fetch_due(limit=MAX_RECOVERY_BATCH):
        try:
            _apply_reconciliation_operation(self, operation)
            journal.acknowledge(operation.playlist_id)
            recovered.add(operation.playlist_id)
        except (
            OSError, PlaylistMirrorJournalError, PlaylistStorageError, TypeError, ValueError
        ) as error:
            try:
                journal.record_failure(operation.playlist_id, error)
            except PlaylistMirrorJournalError as journal_error:
                raise PlaylistReconciliationError(
                    f"playlist mirror recovery and retry recording failed: {journal_error}"
                ) from journal_error
            failure = f"{operation.playlist_id}: {type(error).__name__}: {error}"
            failures.append(failure[:512])
    if failures:
        raise PlaylistReconciliationError(
            "playlist mirror reconciliation failed: " + "; ".join(failures)
        )
    return recovered

def _get_playlist_storage_file(
    self, playlist_id: int, name: str, *, create_parent: bool = True
) -> Path:
    """Build a bounded Windows-safe playlist storage path.

    Edge cases:
        1. Reserved device names must not become invalid storage components.
        2. Invalid-only names must use a deterministic playlist-specific fallback.
        3. Long Unicode names must leave room for the ID prefix and JSON suffix.
    """
    if create_parent:
        self._playlists_storage_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"{playlist_id}_"
    suffix = ".json"
    name_budget = 255 - len(prefix) - len(suffix)
    if name_budget < 1:
        raise ValueError("playlist ID leaves no room for a storage filename")
    safe_name = safe_filename(
        name,
        max_length=name_budget,
        fallback=f"playlist_{playlist_id}",
    )
    return self._playlists_storage_dir / f"{prefix}{safe_name}{suffix}"

def _build_playlist_snapshot(self, playlist_id: int) -> dict[str, object]:
    """Build the file-based snapshot for one playlist."""
    playlist = self._playlists_cache.get(playlist_id, {})
    tracks = self.get_tracks_in_playlist(playlist_id)
    return {
        "id": playlist_id,
        "name": playlist.get("name", ""),
        "description": playlist.get("description", "") or "",
        "creation_date": playlist.get("creation_date", "") or "",
        "last_modified": playlist.get("last_modified", "") or "",
        "tracks": [
            track.to_dict()
            if hasattr(track, "to_dict")
            else {"path": getattr(track, "path", "")}
            for track in tracks
        ],
    }

def _append_json_text(payload: bytearray, text: str, playlist_id: int, target: Path) -> None:
    """Append strict UTF-8 text while enforcing the complete payload budget."""
    for start in range(0, len(text), PLAYLIST_JSON_CHUNK_CHARS):
        encoded = text[start : start + PLAYLIST_JSON_CHUNK_CHARS].encode(
            "utf-8", errors="strict"
        )
        if len(payload) + len(encoded) > PLAYLIST_FILE_MAX_BYTES:
            raise PlaylistSerializationError(
                f"playlist {playlist_id} exceeds the "
                f"{PLAYLIST_FILE_MAX_BYTES}-byte storage limit",
                playlist_id=playlist_id,
                target=target,
            )
        payload.extend(encoded)

def _serialize_playlist_snapshot(
    snapshot: dict[str, object], playlist_id: int, target: Path
) -> bytes:
    """Encode one playlist snapshot as bounded, strict UTF-8 JSON.

    Edge cases:
        1. Track serializers can return unsupported Python objects.
        2. NaN, infinity, recursive containers, or invalid Unicode are not valid output.
        3. Excessive output is rejected while accumulation remains hard-capped.
    """
    payload = bytearray()
    encoder = json.JSONEncoder(ensure_ascii=False, indent=2, allow_nan=False)
    try:
        for text in encoder.iterencode(snapshot):
            _append_json_text(payload, text, playlist_id, target)
        _append_json_text(payload, "\n", playlist_id, target)
    except PlaylistSerializationError:
        raise
    except (OverflowError, RecursionError, TypeError, UnicodeEncodeError, ValueError) as error:
        raise PlaylistSerializationError(
            f"playlist {playlist_id} cannot be serialized: {error}",
            playlist_id=playlist_id,
            target=target,
        ) from error
    return bytes(payload)

def _build_playlist_payload(self, playlist_id: int, target: Path) -> bytes:
    """Construct and serialize a playlist snapshot through one typed boundary.

    Edge cases:
        1. Database reads can fail before a snapshot is available.
        2. Track conversion can reject malformed persisted media records.
        3. Serialization can fail or exceed the bounded mirror budget.
    """
    try:
        snapshot = self._build_playlist_snapshot(playlist_id)
    except (
        AttributeError,
        LookupError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
        WaveHelmError,
    ) as error:
        raise PlaylistSnapshotError(
            f"playlist {playlist_id} snapshot construction failed: {error}",
            playlist_id=playlist_id,
            target=target,
        ) from error
    return _serialize_playlist_snapshot(snapshot, playlist_id, target)

def _write_all(file_descriptor: int, payload: bytes) -> None:
    """Write the complete bounded payload or raise on a short/failed write."""
    payload_view = memoryview(payload)
    offset = 0
    while offset < len(payload_view):
        written = os.write(file_descriptor, payload_view[offset:])
        if written <= 0:
            raise OSError(errno.EIO, "playlist temporary file write made no progress")
        offset += written

def _sync_parent_directory(parent: Path) -> None:
    """Persist a replaced directory entry using the shared durability primitive."""
    sync_parent_directory(parent)


def _replace_file_durable(source: Path, target: Path) -> None:
    """Replace one mirror file with platform-appropriate durability semantics."""
    durable_replace(source, target)

def _cleanup_temporary_file(file_descriptor: int | None, temporary_path: Path | None) -> None:
    """Best-effort cleanup that never masks the original persistence error."""
    if file_descriptor is not None:
        try:
            os.close(file_descriptor)
        except OSError as error:
            logger.warning("Failed closing playlist temporary file: %s", error)
    if temporary_path is not None:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError as error:
            logger.warning("Failed removing playlist temporary file %s: %s", temporary_path, error)

def _write_playlist_payload_atomic(
    target: Path, payload: bytes, playlist_id: int
) -> Path:
    """Commit a playlist payload without truncating the previous valid file.

    Edge cases:
        1. Permission denial or disk exhaustion must preserve the previous target.
        2. A failed fsync or replace must remove the incomplete temporary file.
        3. A post-replace directory fsync failure must report that commit occurred.
    """
    file_descriptor: int | None = None
    temporary_path: Path | None = None
    committed = False
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        file_descriptor, raw_path = tempfile.mkstemp(
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(raw_path)
        _write_all(file_descriptor, payload)
        os.fsync(file_descriptor)
        os.close(file_descriptor)
        file_descriptor = None
        _replace_file_durable(temporary_path, target)
        temporary_path = None
        committed = True
        _sync_parent_directory(target.parent)
        return target
    except OSError as error:
        _cleanup_temporary_file(file_descriptor, temporary_path)
        raise PlaylistWriteError(
            f"playlist {playlist_id} atomic write failed: {error}",
            playlist_id=playlist_id,
            target=target,
            committed=committed,
        ) from error

def _remove_superseded_playlist_file(
    previous_path: Path | None, target: Path, playlist_id: int
) -> None:
    """Remove a renamed playlist mirror only after the new file is committed."""
    if previous_path is None or previous_path == target:
        return
    try:
        previous_path.unlink(missing_ok=True)
        _sync_parent_directory(previous_path.parent)
    except OSError as error:
        raise PlaylistCleanupError(
            f"playlist {playlist_id} was committed but old mirror cleanup failed: {error}",
            playlist_id=playlist_id,
            target=target,
            committed=True,
        ) from error

def _sync_playlist_file(
    self, playlist_id: Optional[int], previous_name: Optional[str] = None
) -> Path:
    """Atomically synchronize one playlist mirror or raise a typed error."""
    if playlist_id is None:
        raise PlaylistWriteError("playlist ID is required", playlist_id=-1)
    playlist = self._playlists_cache.get(playlist_id)
    if not playlist:
        raise PlaylistWriteError(
            f"playlist {playlist_id} is absent from the controller cache",
            playlist_id=playlist_id,
        )
    try:
        target = self._get_playlist_storage_file(
            playlist_id,
            str(playlist.get("name", "")),
            create_parent=True,
        )
        previous_path = (
            self._get_playlist_storage_file(
                playlist_id,
                previous_name,
                create_parent=False,
            )
            if previous_name
            else None
        )
    except (OSError, TypeError, ValueError) as error:
        raise PlaylistWriteError(
            f"playlist {playlist_id} storage path cannot be prepared: {error}",
            playlist_id=playlist_id,
        ) from error

    payload = _build_playlist_payload(self, playlist_id, target)
    committed_path = _write_playlist_payload_atomic(target, payload, playlist_id)
    _remove_superseded_playlist_file(previous_path, committed_path, playlist_id)
    return committed_path

def _delete_playlist_storage(self, playlist_id: int, playlist_name: str) -> None:
    """Delete one playlist mirror or raise a typed error."""
    candidate: Path | None = None
    removed = False
    try:
        candidate = self._get_playlist_storage_file(
            playlist_id,
            playlist_name,
            create_parent=False,
        )
        if os.path.lexists(candidate):
            candidate.unlink()
            removed = True
            _sync_parent_directory(candidate.parent)
    except (OSError, TypeError, ValueError) as error:
        raise PlaylistDeleteError(
            f"playlist {playlist_id} mirror deletion failed: {error}",
            playlist_id=playlist_id,
            target=candidate,
            committed=removed,
        ) from error

def _sync_all_playlist_files(self) -> int:
    """Recover pending jobs, then refresh mirrors without bypassing backoff."""
    recovered_ids = _reconcile_playlist_journal(self)
    journal = _get_mirror_journal(self)
    pending_ids = journal.pending_playlist_ids() if journal is not None else frozenset()
    if pending_ids:
        preview = ", ".join(str(value) for value in sorted(pending_ids)[:8])
        raise PlaylistReconciliationError(
            f"playlist mirror reconciliation remains pending for IDs: {preview}")
    committed_count = len(recovered_ids)
    for playlist_id in list(self._playlists_cache.keys()):
        if playlist_id in recovered_ids:
            continue
        self._sync_playlist_file(playlist_id)
        committed_count += 1
    return committed_count

_PLAYLIST_CONTROLLER_STORAGE_METHODS: tuple[tuple[str, Callable[..., object]], ...] = (
    ("_get_playlist_storage_file", _get_playlist_storage_file),
    ("_build_playlist_snapshot", _build_playlist_snapshot),
    ("_sync_playlist_file", _sync_playlist_file),
    ("_delete_playlist_storage", _delete_playlist_storage),
    ("_sync_all_playlist_files", _sync_all_playlist_files),
)

def install_playlist_controller_storage_behavior(cls) -> None:
    """Install storage bindings from the split playlist controller storage module.

    Edge cases:
        1. The target is not a class and receives storage methods unexpectedly.
        2. A binding name is empty or duplicated and silently corrupts controller wiring.
        3. A split module exports a non-callable binding and breaks runtime attachment.
    """
    if not isinstance(cls, type):
        raise TypeError("Playlist controller storage behavior can only be attached to classes")
    if getattr(cls, "_playlist_controller_storage_behavior_attached", False):
        return

    seen_names: set[str] = set()
    for attribute_name, method in _PLAYLIST_CONTROLLER_STORAGE_METHODS:
        if not attribute_name:
            raise TypeError("Playlist controller storage binding name cannot be empty")
        if attribute_name in seen_names:
            raise TypeError(f"Duplicate playlist controller storage binding: {attribute_name}")
        if not callable(method):
            raise TypeError(f"Invalid playlist controller storage binding: {attribute_name}")
        setattr(cls, attribute_name, method)
        seen_names.add(attribute_name)

    setattr(cls, "_playlist_controller_storage_behavior_attached", True)

def attach_playlist_controller_storage_behavior(cls) -> None:
    """Compatibility shim for legacy storage attach imports."""
    install_playlist_controller_storage_behavior(cls)
