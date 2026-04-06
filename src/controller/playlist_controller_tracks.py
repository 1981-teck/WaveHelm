from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.audio.audio_events import AudioEventType
from src.model.media_file import MediaFile, MediaType

logger = logging.getLogger(__name__)

PLAYLIST_TRACKS_OPERATION_EXCEPTIONS = (AttributeError, OSError, RuntimeError, TypeError, ValueError)
PLAYLIST_TRACKS_DB_EXCEPTIONS = (AttributeError, OSError, RuntimeError, TypeError, ValueError)
PLAYLIST_TRACKS_REMOVE_EXCEPTIONS = (AttributeError, OSError, RuntimeError, TypeError, ValueError)


def add_files_to_current_playlist(self, file_paths: List[str]):
    if not self._current_playlist_id:
        self._notify_feedback("no_playlist_selected_for_track_add", color="orange")
        return

    if not file_paths:
        self._notify_feedback("no_files_selected", color="orange")
        return

    logger.info(
        "[PlaylistController] Adding %s file(s) to playlist %s",
        len(file_paths),
        self._current_playlist_id,
    )

    added_count = 0
    failed_count = 0
    for file_path in file_paths:
        try:
            media_file = self._ensure_media_in_library(file_path)
            if media_file:
                success = self._add_media_to_playlist(self._current_playlist_id, media_file)
                if success:
                    added_count += 1
                    logger.info(
                        "[PlaylistController] Added to playlist %s: %s",
                        self._current_playlist_id,
                        file_path,
                    )
                else:
                    failed_count += 1
                    logger.warning(
                        "[PlaylistController] File not added to playlist %s (duplicate or DB refusal): %s",
                        self._current_playlist_id,
                        file_path,
                    )
            else:
                failed_count += 1
                logger.warning(
                    "[PlaylistController] Could not resolve media file for playlist add: %s",
                    file_path,
                )
        except PLAYLIST_TRACKS_OPERATION_EXCEPTIONS as error:
            failed_count += 1
            logger.error(
                "[PlaylistController] Error adding file %s: %s",
                file_path,
                error,
                exc_info=True,
            )

    if added_count > 0:
        self._sync_playlist_file(self._current_playlist_id)
        self._notify_playlist_updated(self._current_playlist_id)
        self._notify_feedback("tracks_added_to_playlist", count=added_count)

    if failed_count > 0:
        self._notify_feedback("some_tracks_failed_to_add", color="orange", count=failed_count)

    logger.info("Added %s tracks to playlist, %s failed", added_count, failed_count)


def _ensure_media_in_library(self, file_path: str) -> Optional[MediaFile]:
    media_file = self.library_controller.get_media_by_path(file_path)
    if not media_file:
        self.library_controller.add_media_files([file_path])
        media_file = self.library_controller.get_media_by_path(file_path)
    return media_file


def _add_media_to_playlist(self, playlist_id: int, media_file: MediaFile) -> bool:
    if not media_file or not media_file.path:
        return False

    self._ensure_cache_valid()
    if playlist_id not in self._playlists_cache:
        return False

    current_paths = [path for _, path in self._playlist_items_cache.get(playlist_id, [])]
    if media_file.path in current_paths:
        logger.debug("File %s already in playlist %s", media_file.path, playlist_id)
        return False

    try:
        current_items = self._playlist_items_cache.get(playlist_id, [])
        position = len(current_items) + 1
        self.database_manager.add_playlist_item(playlist_id, media_file.path, position)

        if playlist_id not in self._playlist_items_cache:
            self._playlist_items_cache[playlist_id] = []
        self._playlist_items_cache[playlist_id].append((position, media_file.path))

        logger.debug(
            "Added %s to playlist %s at position %s",
            media_file.path,
            playlist_id,
            position,
        )
        return True
    except PLAYLIST_TRACKS_DB_EXCEPTIONS as error:
        logger.error("Error adding media to playlist: %s", error)
        return False


def remove_from_playlist(self, playlist_id: int, media_path: str, *, notify: bool = True) -> bool:
    self._ensure_cache_valid()

    if playlist_id not in self._playlists_cache:
        if notify:
            self._notify_feedback("playlist_not_found", color="red")
        return False

    current_items = self._playlist_items_cache.get(playlist_id, [])
    if not any(path == media_path for _, path in current_items):
        if notify:
            self._notify_feedback("track_not_in_playlist", color="orange")
        return False

    try:
        self.database_manager.remove_playlist_item(playlist_id, media_path)
        updated_items = []
        position = 1
        for _, path in current_items:
            if path != media_path:
                updated_items.append((position, path))
                position += 1
        self._playlist_items_cache[playlist_id] = updated_items

        if notify:
            self._sync_playlist_file(playlist_id)
            self._notify_playlist_updated(playlist_id)
            self._notify_feedback("track_removed_from_playlist")

        logger.info("Removed %s from playlist %s", media_path, playlist_id)
        return True
    except PLAYLIST_TRACKS_REMOVE_EXCEPTIONS as error:
        self._handle_error(error, "error_removing_from_playlist")
        return False


def remove_many_from_playlist(self, playlist_id: int, media_paths: List[str]) -> int:
    self._ensure_cache_valid()
    if playlist_id not in self._playlists_cache:
        self._notify_feedback("playlist_not_found", color="red")
        return 0

    unique_paths: List[str] = []
    seen: set[str] = set()
    for media_path in media_paths:
        normalized = str(media_path or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique_paths.append(normalized)

    if not unique_paths:
        self._notify_feedback("track_not_in_playlist", color="orange")
        return 0

    logger.info(
        "[PlaylistController] Removing %s track(s) from playlist %s",
        len(unique_paths),
        playlist_id,
    )

    removed_count = 0
    failed_count = 0
    for media_path in unique_paths:
        if self.remove_from_playlist(playlist_id, media_path, notify=False):
            removed_count += 1
        else:
            failed_count += 1

    if removed_count > 0:
        self._sync_playlist_file(playlist_id)
        self._notify_playlist_updated(playlist_id)
        message = (
            "Brano rimosso dalla playlist."
            if removed_count == 1
            else f"{removed_count} brani rimossi dalla playlist."
        )
        self.event_bus.publish(
            AudioEventType.FEEDBACK_MESSAGE,
            {"message": message, "color": "green"},
        )

    if failed_count > 0:
        self.event_bus.publish(
            AudioEventType.FEEDBACK_MESSAGE,
            {
                "message": f"{failed_count} brani non sono stati rimossi dalla playlist.",
                "color": "orange",
            },
        )

    return removed_count


def get_tracks_in_playlist(self, playlist_id: int) -> List[MediaFile]:
    self._ensure_cache_valid()
    if playlist_id not in self._playlist_items_cache:
        return []

    media_files: List[MediaFile] = []
    track_paths = [path for _, path in self._playlist_items_cache[playlist_id]]
    playlist_rows_by_path = {
        item.get("media_path"): item
        for item in self.database_manager.get_playlist_items(playlist_id)
        if item.get("media_path")
    }

    for path in track_paths:
        media_file = self.library_controller.get_media_by_path(path)
        if media_file:
            media_files.append(media_file)
        else:
            playlist_row = playlist_rows_by_path.get(path)
            reconstructed = self._build_media_from_playlist_row(path, playlist_row)
            if reconstructed is not None:
                media_files.append(reconstructed)
            else:
                logger.warning("Media file not found in library: %s", path)

    return media_files


def get_playlist_track_count(self, playlist_id: int) -> int:
    self._ensure_cache_valid()
    return len(self._playlist_items_cache.get(playlist_id, []))


def _build_media_from_playlist_row(
    self, path: str, playlist_row: Optional[Dict[str, Any]]
) -> Optional[MediaFile]:
    if not path:
        return None

    metadata = {}
    if isinstance(playlist_row, dict):
        raw_metadata = playlist_row.get("metadata")
        if isinstance(raw_metadata, dict):
            metadata = dict(raw_metadata)

    media_type_value = ""
    if isinstance(playlist_row, dict):
        media_type_value = str(playlist_row.get("media_type") or "").lower()

    try:
        media_type = MediaType(media_type_value) if media_type_value else MediaType.UNKNOWN
    except ValueError:
        media_type = MediaType.UNKNOWN

    return MediaFile(
        path=path,
        title=(
            playlist_row.get("title")
            if isinstance(playlist_row, dict) and playlist_row.get("title")
            else Path(path).stem
        ),
        media_type=media_type,
        duration=(
            float(playlist_row.get("duration") or 0.0)
            if isinstance(playlist_row, dict)
            else 0.0
        ),
        metadata=metadata,
    )


_PLAYLIST_CONTROLLER_TRACKS_METHODS: tuple[tuple[str, Any], ...] = (
    ("add_files_to_current_playlist", add_files_to_current_playlist),
    ("_ensure_media_in_library", _ensure_media_in_library),
    ("_add_media_to_playlist", _add_media_to_playlist),
    ("remove_from_playlist", remove_from_playlist),
    ("remove_many_from_playlist", remove_many_from_playlist),
    ("get_tracks_in_playlist", get_tracks_in_playlist),
    ("get_playlist_track_count", get_playlist_track_count),
    ("_build_media_from_playlist_row", _build_media_from_playlist_row),
)


def install_playlist_controller_tracks_behavior(cls) -> None:
    """Install tracks bindings while keeping the central controller as the public attach point.

    Edge cases:
        1. A non-class target would receive controller methods unexpectedly.
        2. Empty or duplicate binding names would silently shadow playlist behavior.
        3. Re-import or reload could repeat method attachment without an idempotent guard.
    """
    if not isinstance(cls, type):
        raise TypeError('Playlist controller tracks behavior can only be installed on classes')
    if getattr(cls, '_playlist_controller_tracks_behavior_attached', False):
        return

    seen_names: set[str] = set()
    for attribute_name, method in _PLAYLIST_CONTROLLER_TRACKS_METHODS:
        if not attribute_name:
            raise TypeError('Playlist controller tracks binding name cannot be empty')
        if attribute_name in seen_names:
            raise TypeError(f'Duplicate playlist controller tracks binding: {attribute_name}')
        if not callable(method):
            raise TypeError(f'Invalid playlist controller tracks binding: {attribute_name}')
        setattr(cls, attribute_name, method)
        seen_names.add(attribute_name)

    setattr(cls, '_playlist_controller_tracks_behavior_attached', True)


def _bind_playlist_controller_tracks_methods(cls) -> None:
    """Backward-compatible internal helper for historical imports."""
    install_playlist_controller_tracks_behavior(cls)


def attach_playlist_controller_tracks_behavior(cls) -> None:
    """Backward-compatible shim that delegates to the neutral installer."""
    install_playlist_controller_tracks_behavior(cls)
