from __future__ import annotations
from collections.abc import Sequence
import logging
import os
import sqlite3
from typing import cast

from src.audio.audio_events import AudioEventBus, AudioEventType
from src.controller.library_catalog_store import (CatalogCommitStatus, LibraryCatalogLoadResult, LibraryCatalogStore)
from src.controller.library_media_loader import load_library_media_files
from src.controller.library_scan import LibraryScanCancellation, LibraryScanLimits
from src.controller.library_mirror import (
    LibraryDatabaseGateway,
    LibraryMirrorResult,
    LibraryMirrorStatus,
    LibraryMutationResult,
    build_library_mirror_failure,
    synchronize_library_mirror,
)
from src.model.database_manager import DatabaseManager
from src.model.media_file import MediaFile
from src.utils.durable_io import SerializedCommitGate
from src.utils.exceptions import DatabaseError, NotFoundError
from src.utils.helpers import get_app_data_path
logger = logging.getLogger(__name__)
PATH_EXCEPTIONS = (AttributeError, OSError, TypeError, ValueError)
FAVORITE_DATABASE_EXCEPTIONS = (
    AttributeError,
    DatabaseError,
    NotFoundError,
    OSError,
    RuntimeError,
    sqlite3.Error,
    TypeError,
    ValueError,
)

def _canon_path_win(path_value: str) -> str:
    try:
        return os.path.normcase(os.path.abspath(os.path.normpath(path_value)))
    except PATH_EXCEPTIONS:
        return path_value

def _unique_paths(paths: Sequence[str]) -> tuple[str, ...]:
    """Deduplicate persisted paths by the runtime's canonical path identity."""
    unique: list[str] = []
    seen: set[str] = set()
    for path in paths:
        key = _canon_path_win(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return tuple(unique)

def _dedupe_media(media_files: Sequence[MediaFile]) -> list[MediaFile]:
    """Keep the first resolved record for each canonical media path."""
    unique: list[MediaFile] = []
    seen: set[str] = set()
    for media in media_files:
        key = _canon_path_win(media.path)
        if key not in seen:
            seen.add(key)
            unique.append(media)
    return unique

def _copy_media(media: MediaFile) -> MediaFile:
    """Detach caller-owned mutable media fields before committing controller state."""
    return MediaFile.from_mapping(media.to_dict())

class LibraryController:
    """Coordinate canonical paths, resolved media, a derived DB mirror, and events.

    Edge cases:
        1. Catalog failure leaves memory, SQLite, and success events unchanged.
        2. Post-commit mirror failure remains explicit degraded evidence.
        3. Concurrent mutations serialize without holding a mutex during I/O.
        4. Temporarily unavailable paths survive later catalog rewrites.
    """

    def __init__(self, event_bus: AudioEventBus, database_manager: DatabaseManager) -> None:
        self.event_bus = event_bus
        self.database_manager = database_manager
        self._media: list[MediaFile] = []
        self._catalog_paths: tuple[str, ...] = ()
        self._paths_index: set[str] = set()
        self._commit_gate = SerializedCommitGate()
        self._revision = 0
        self._mirror_needs_full_reconcile = True
        self._last_mirror_result = LibraryMirrorResult(
            LibraryMirrorStatus.SYNCHRONIZED, 0, 0, ()
        )
        self._last_persistence_result: LibraryMutationResult | None = None
        self._catalog_load_result = LibraryCatalogLoadResult((), False, ())
        self._app_dir = get_app_data_path()
        self._library_path = self._app_dir / "library.json"
        self._catalog_store = LibraryCatalogStore(self._library_path)
        self.load_persistent_library()
    @property
    def catalog_paths(self) -> tuple[str, ...]:
        """Return the detached canonical path snapshot, including unresolved paths."""
        return self._catalog_paths

    @property
    def unresolved_catalog_paths(self) -> tuple[str, ...]:
        """Return persisted paths without a currently resolved media record."""
        resolved = {_canon_path_win(media.path) for media in self._media}
        return tuple(path for path in self._catalog_paths if _canon_path_win(path) not in resolved)
    @property
    def catalog_write_blocked(self) -> bool:
        """Return whether a corrupt source currently forbids catalog replacement."""
        return self._catalog_load_result.write_blocked
    @property
    def last_persistence_result(self) -> LibraryMutationResult | None:
        """Return the immutable result of the most recent mutation or explicit save."""
        return self._last_persistence_result
    def _safe_publish(self, event_type: AudioEventType, payload: dict[str, object]) -> bool:
        try:
            result = self.event_bus.publish(event_type, payload)
            if result is False:
                logger.warning("[Library] Event bus rejected %s.", event_type)
                return False
            return True
        except Exception as error:  # explicit external event-publisher boundary
            logger.error("[Library] Failed to publish %s: %s", event_type, error, exc_info=True)
            return False
    def _set_live_state(self, media: Sequence[MediaFile], paths: Sequence[str]) -> None:
        self._media = list(media)
        self._catalog_paths = _unique_paths(paths)
        self._paths_index = {_canon_path_win(path) for path in self._catalog_paths}
    def _sync_mirror(
        self,
        *,
        upsert_media: Sequence[MediaFile],
        remove_paths: Sequence[str],
        force_full: bool,
        allow_stale_removal: bool = True,
    ) -> LibraryMirrorResult:
        full_reconcile = force_full or self._mirror_needs_full_reconcile
        try:
            result = synchronize_library_mirror(
                cast(LibraryDatabaseGateway, self.database_manager),
                catalog_paths=self._catalog_paths,
                resolved_media=self._media,
                upsert_media=upsert_media,
                remove_paths=remove_paths,
                canonicalize=_canon_path_win,
                full_reconcile=full_reconcile,
                allow_stale_removal=allow_stale_removal,
            )
        except Exception as error:  # explicit post-commit database-mirror boundary
            failure = build_library_mirror_failure("synchronize", None, error)
            result = LibraryMirrorResult(LibraryMirrorStatus.DEGRADED, 0, 0, (failure,))
        self._last_mirror_result = result
        self._mirror_needs_full_reconcile = not result.is_synchronized
        for failure in result.failures:
            logger.error(
                "[Library] Mirror %s failed for %s (%s): %s",
                failure.operation,
                failure.path or "<catalog>",
                failure.error_type,
                failure.message,
            )
        return result
    def load_persistent_library(self) -> None:
        """Load the canonical catalog, preserve unresolved paths, and rebuild the mirror."""
        with self._commit_gate.transaction():
            self._load_persistent_library()
    def _load_persistent_library(self) -> None:
        load_result = self._catalog_store.load()
        self._catalog_load_result = load_result
        paths = _unique_paths(load_result.paths)
        media = _dedupe_media(load_library_media_files(paths))
        self._set_live_state(media, paths)
        mirror = self._sync_mirror(
            upsert_media=media,
            remove_paths=(),
            force_full=True,
            allow_stale_removal=not load_result.write_blocked,
        )
        if load_result.write_blocked:
            logger.error(
                "[Library] Catalog loaded read-only; writes blocked: %s",
                "; ".join(load_result.issues),
            )
        logger.info(
            "[Library] Restored %s resolved items from %s canonical paths; mirror=%s.",
            len(media),
            len(paths),
            mirror.status,
        )
    def _build_add_candidate(
        self, media_files: Sequence[MediaFile]
    ) -> tuple[list[MediaFile], tuple[str, ...], list[MediaFile]]:
        candidate_media = list(self._media)
        candidate_paths = list(self._catalog_paths)
        catalog_keys = set(self._paths_index)
        resolved_keys = {_canon_path_win(media.path) for media in self._media}
        added: list[MediaFile] = []
        for source_media in media_files:
            if not isinstance(source_media, MediaFile):
                raise TypeError("library additions must contain MediaFile values")
            media = _copy_media(source_media)
            key = _canon_path_win(media.path)
            if key not in catalog_keys:
                candidate_paths.append(media.path)
                catalog_keys.add(key)
            if key not in resolved_keys:
                candidate_media.append(media)
                resolved_keys.add(key)
                added.append(media)
        return candidate_media, tuple(candidate_paths), added

    def _commit_candidate(
        self,
        candidate_media: Sequence[MediaFile],
        candidate_paths: Sequence[str],
        *,
        added_media: Sequence[MediaFile],
        removed_paths: Sequence[str],
        force_catalog_write: bool = False,
        force_full_mirror: bool = False,
    ) -> LibraryMutationResult:
        prepared_media = list(candidate_media)
        prepared_paths = _unique_paths(candidate_paths)
        prepared_index = {_canon_path_win(path) for path in prepared_paths}
        catalog_changed = prepared_paths != self._catalog_paths
        state_changed = prepared_media != self._media or catalog_changed
        if force_catalog_write or catalog_changed:
            catalog_status = self._catalog_store.commit(prepared_paths)
        else:
            catalog_status = CatalogCommitStatus.UNCHANGED
        if state_changed:
            self._media = prepared_media
            self._catalog_paths = prepared_paths
            self._paths_index = prepared_index
            self._revision += 1
        mirror = self._sync_mirror(
            upsert_media=added_media,
            remove_paths=removed_paths,
            force_full=force_full_mirror,
        )
        result = LibraryMutationResult(
            catalog_status,
            mirror,
            len(added_media),
            len(removed_paths),
            len(self._catalog_paths),
            len(self._media),
            self._revision,
        )
        self._last_persistence_result = result
        if catalog_status is CatalogCommitStatus.COMMITTED_WITHOUT_DIRECTORY_SYNC:
            logger.warning("[Library] Catalog committed without confirmed directory sync.")
        return result
    def _announce_change(
        self,
        result: LibraryMutationResult,
        *,
        emit_event: bool,
        emit_feedback: bool,
        action_message: str,
    ) -> None:
        if emit_event:
            self._safe_publish(
                AudioEventType.LIBRARY_UPDATED,
                {
                    "count": result.resolved_media_count,
                    "added": result.added_count,
                    "removed": result.removed_count,
                    "catalog_status": result.catalog_status.value,
                    "mirror_status": result.mirror_result.status.value,
                    "revision": result.revision,
                },
            )
        if not emit_feedback:
            return
        if result.is_fully_synchronized:
            message, color = action_message, "green"
        elif not result.mirror_result.is_synchronized:
            message, color = action_message + " Indice database da riconciliare.", "orange"
        else:
            message, color = action_message + " Persistenza con durability ridotta.", "orange"
        self._safe_publish(AudioEventType.FEEDBACK_MESSAGE, {"message": message, "color": color})
    def add_media_files_from_objects(
        self,
        media_files: Sequence[MediaFile],
        emit_event: bool = True,
        emit_feedback: bool = True,
    ) -> LibraryMutationResult:
        with self._commit_gate.transaction():
            candidate_media, candidate_paths, added = self._build_add_candidate(media_files)
            result = self._commit_candidate(
                candidate_media,
                candidate_paths,
                added_media=added,
                removed_paths=(),
            )
        if added:
            self._announce_change(
                result,
                emit_event=emit_event,
                emit_feedback=emit_feedback,
                action_message=f"{len(added)} file aggiunti alla libreria.",
            )
        elif emit_feedback:
            self._safe_publish(
                AudioEventType.FEEDBACK_MESSAGE,
                {"message": "Nessun file riproducibile trovato.", "color": "orange"},
            )
        return result
    def add_media(
        self, path: str, emit_event: bool = True, emit_feedback: bool = True,
        *, scan_limits: LibraryScanLimits | None = None,
        cancellation: LibraryScanCancellation | None = None,
    ) -> LibraryMutationResult:
        values = [path] if path else []
        media_files = (
            load_library_media_files(values)
            if scan_limits is None and cancellation is None
            else load_library_media_files(
                values, limits=scan_limits, cancellation=cancellation
            )
        )
        return self.add_media_files_from_objects(media_files, emit_event, emit_feedback)
    def add_media_files(
        self, paths: Sequence[str], emit_event: bool = True, emit_feedback: bool = True,
        *, scan_limits: LibraryScanLimits | None = None,
        cancellation: LibraryScanCancellation | None = None,
    ) -> LibraryMutationResult:
        media_files = (
            load_library_media_files(paths)
            if scan_limits is None and cancellation is None
            else load_library_media_files(
                paths, limits=scan_limits, cancellation=cancellation
            )
        )
        return self.add_media_files_from_objects(media_files, emit_event, emit_feedback)
    def remove_media(
        self, path: str, emit_event: bool = True, emit_feedback: bool = True
    ) -> bool:
        if not path:
            return False
        key = _canon_path_win(path)
        with self._commit_gate.transaction():
            removed_paths = tuple(
                saved for saved in self._catalog_paths if _canon_path_win(saved) == key
            )
            candidate_media = [
                media for media in self._media if _canon_path_win(media.path) != key
            ]
            candidate_paths = tuple(
                saved for saved in self._catalog_paths if _canon_path_win(saved) != key
            )
            if not removed_paths and len(candidate_media) == len(self._media):
                return False
            result = self._commit_candidate(
                candidate_media,
                candidate_paths,
                added_media=(),
                removed_paths=removed_paths or (path,),
            )
        self._announce_change(
            result,
            emit_event=emit_event,
            emit_feedback=emit_feedback,
            action_message=f"Rimosso dalla libreria: {os.path.basename(path)}",
        )
        return True
    def remove_media_by_path(
        self, path: str, emit_event: bool = True, emit_feedback: bool = True
    ) -> bool:
        return self.remove_media(path, emit_event, emit_feedback)
    def save_library(self) -> LibraryMutationResult:
        """Verify, persist, and reconcile the current canonical snapshot."""
        with self._commit_gate.transaction():
            return self._commit_candidate(
                self._media,
                self._catalog_paths,
                added_media=(),
                removed_paths=(),
                force_catalog_write=True,
                force_full_mirror=True,
            )
    def add_to_favorites(
        self,
        media_file: MediaFile,
        *,
        emit_feedback: bool = True,
        emit_event: bool = True,
    ) -> bool:
        if not media_file or not media_file.path:
            return False
        try:
            if self.database_manager.is_favorite(media_file.path):
                if emit_feedback:
                    self._safe_publish(
                        AudioEventType.FEEDBACK_MESSAGE,
                        {"message": f"Gia' nei preferiti: {media_file.title}", "color": "orange"},
                    )
                return False
            self.database_manager.add_favorite(
                path=media_file.path,
                title=media_file.title,
                media_type=media_file.media_type.name,
                duration=media_file.duration,
                metadata=media_file.metadata,
            )
        except FAVORITE_DATABASE_EXCEPTIONS as error:
            logger.error("Error adding favorite: %s", error)
            if emit_feedback:
                self._safe_publish(
                    AudioEventType.FEEDBACK_MESSAGE,
                    {"message": f"Errore nell'aggiungere il preferito: {media_file.title}", "color": "red"},
                )
            return False
        if emit_event:
            self._safe_publish(
                AudioEventType.FAVORITE_CHANGED,
                {"path": media_file.path, "is_favorite": True},
            )
        if emit_feedback:
            self._safe_publish(
                AudioEventType.FEEDBACK_MESSAGE,
                {"message": f"Aggiunto ai preferiti: {media_file.title}", "color": "green"},
            )
        return True
    def get_all_media(self) -> list[MediaFile]:
        return [_copy_media(media) for media in self._media]
    def get_media_by_path(self, path: str) -> MediaFile | None:
        wanted = _canon_path_win(path)
        for media in self._media:
            if _canon_path_win(media.path) == wanted:
                return _copy_media(media)
        return None
    def search_media(self, query: str) -> list[MediaFile]:
        normalized_query = (query or "").strip().lower()
        if not normalized_query:
            return self.get_all_media()
        result: list[MediaFile] = []
        for media in self._media:
            metadata = media.metadata
            values = (
                media.title,
                str(metadata.get("artist", "") or ""),
                str(metadata.get("album", "") or ""),
                media.path,
            )
            if any(normalized_query in value.lower() for value in values):
                result.append(_copy_media(media))
        return result
