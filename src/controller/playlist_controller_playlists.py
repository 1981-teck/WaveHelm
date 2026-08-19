from __future__ import annotations

import logging
from typing import Callable, Optional

from src.audio.audio_events import AudioEventType
from src.controller.playlist_controller_storage import PlaylistStorageError
from src.utils.exceptions import DatabaseError, NotFoundError

logger = logging.getLogger(__name__)

PLAYLIST_PLAYLISTS_CREATE_EXCEPTIONS = (
    AttributeError, DatabaseError, OSError, RuntimeError, TypeError, ValueError
)
PLAYLIST_PLAYLISTS_RENAME_EXCEPTIONS = (
    AttributeError, DatabaseError, NotFoundError, OSError, RuntimeError, TypeError, ValueError
)
PLAYLIST_PLAYLISTS_DELETE_EXCEPTIONS = (
    AttributeError, DatabaseError, NotFoundError, OSError, RuntimeError, TypeError, ValueError
)


class PlaylistPersistenceRollbackError(RuntimeError):
    """Raised when a database compensation cannot restore playlist consistency."""


def _rollback_created_playlist(self, playlist_id: int) -> None:
    """Compensate a failed pre-commit mirror write for a new playlist."""
    try:
        self.database_manager.delete_playlist(playlist_id)
    except PLAYLIST_PLAYLISTS_DELETE_EXCEPTIONS as rollback_error:
        self._cache_valid = False
        raise PlaylistPersistenceRollbackError(
            f"playlist {playlist_id} mirror write and database rollback both failed: {rollback_error}"
        ) from rollback_error
    self._playlists_cache.pop(playlist_id, None)
    self._playlist_items_cache.pop(playlist_id, None)
    if self._current_playlist_id == playlist_id:
        self._current_playlist_id = None


def _rollback_playlist_rename(self, playlist_id: int, old_name: str) -> None:
    """Restore the previous database/cache name after a pre-commit mirror failure."""
    try:
        self.database_manager.update_playlist_name(playlist_id, old_name)
    except PLAYLIST_PLAYLISTS_RENAME_EXCEPTIONS as rollback_error:
        self._cache_valid = False
        raise PlaylistPersistenceRollbackError(
            f"playlist {playlist_id} mirror write and rename rollback both failed: {rollback_error}"
        ) from rollback_error
    playlist = self._playlists_cache.get(playlist_id)
    if playlist is not None:
        playlist["name"] = old_name


def create_playlist(self, name: str, description: Optional[str] = None) -> Optional[int]:
    """Create a playlist and require its persistent mirror to commit."""
    name = (name or "").strip()
    if not self._validate_playlist_name(name):
        self._notify_feedback("invalid_playlist_name", color="orange")
        return None
    if self._playlist_name_exists(name):
        self._notify_feedback("playlist_already_exists", color="orange", name=name)
        return None

    playlist_id: int | None = None
    try:
        playlist_id = self.database_manager.create_playlist(
            name=name, description=description or ""
        )
        self._playlists_cache[playlist_id] = {
            "id": playlist_id,
            "name": name,
            "description": description or "",
            "creation_date": "",
            "last_modified": "",
        }
        self._playlist_items_cache[playlist_id] = []
        self._current_playlist_id = playlist_id
        self._sync_playlist_file(playlist_id)
        self._notify_playlist_updated(playlist_id)
        self._notify_feedback("playlist_created", name=name)
        logger.info("Created playlist: %s (ID: %s)", name, playlist_id)
        return playlist_id
    except PlaylistStorageError as error:
        if playlist_id is not None and not error.committed:
            try:
                _rollback_created_playlist(self, playlist_id)
            except PlaylistPersistenceRollbackError as rollback_error:
                self._handle_error(rollback_error, "error_creating_playlist", name=name)
                raise
        self._handle_error(error, "error_creating_playlist", name=name)
        raise
    except PLAYLIST_PLAYLISTS_CREATE_EXCEPTIONS as error:
        self._handle_error(error, "error_creating_playlist", name=name)
        return None


def rename_playlist(self, playlist_id: int, new_name: str) -> None:
    """Rename a playlist without deleting its old mirror before commit."""
    new_name = (new_name or "").strip()
    if not self._validate_playlist_name(new_name):
        self._notify_feedback("invalid_playlist_name", color="orange")
        return

    self._ensure_cache_valid()
    if playlist_id not in self._playlists_cache:
        self._notify_feedback("playlist_not_found", color="red")
        return
    if self._playlist_name_exists(new_name, exclude_id=playlist_id):
        self._notify_feedback("playlist_already_exists", color="orange", name=new_name)
        return

    old_name = str(self._playlists_cache[playlist_id].get("name", ""))
    try:
        self.database_manager.update_playlist_name(playlist_id, new_name)
        self._playlists_cache[playlist_id]["name"] = new_name
        self._sync_playlist_file(playlist_id, previous_name=old_name)
        self._notify_playlist_updated(playlist_id)
        self._notify_feedback("playlist_renamed", old_name=old_name, new_name=new_name)
        logger.info("Renamed playlist ID %s to: %s", playlist_id, new_name)
    except PlaylistStorageError as error:
        if not error.committed:
            try:
                _rollback_playlist_rename(self, playlist_id, old_name)
            except PlaylistPersistenceRollbackError as rollback_error:
                self._handle_error(rollback_error, "error_renaming_playlist", name=new_name)
                raise
        self._handle_error(error, "error_renaming_playlist", name=new_name)
        raise
    except PLAYLIST_PLAYLISTS_RENAME_EXCEPTIONS as error:
        self._handle_error(error, "error_renaming_playlist", name=new_name)


def delete_playlist(self, playlist_id: int) -> None:
    """Delete a playlist and propagate mirror cleanup failures to callers."""
    self._ensure_cache_valid()
    if playlist_id not in self._playlists_cache:
        self._notify_feedback("playlist_not_found", color="red")
        return

    playlist_name = self._playlists_cache[playlist_id].get("name", "Unknown")
    try:
        self.database_manager.delete_playlist(playlist_id)
        del self._playlists_cache[playlist_id]
        self._playlist_items_cache.pop(playlist_id, None)
        self._delete_playlist_storage(playlist_id, playlist_name)
        if self._current_playlist_id == playlist_id:
            self._current_playlist_id = None
        if self._currently_playing == playlist_id:
            self._currently_playing = None
        self.event_bus.publish(
            AudioEventType.PLAYLIST_UPDATED, {"deleted_id": playlist_id}
        )
        self._notify_feedback("playlist_deleted", name=playlist_name)
        logger.info("Deleted playlist: %s (ID: %s)", playlist_name, playlist_id)
    except PlaylistStorageError as error:
        self._handle_error(error, "error_deleting_playlist", name=playlist_name)
        raise
    except PLAYLIST_PLAYLISTS_DELETE_EXCEPTIONS as error:
        self._handle_error(error, "error_deleting_playlist", name=playlist_name)


def get_all_playlists(self) -> list[dict[str, object]]:
    self._ensure_cache_valid()
    return list(self._playlists_cache.values())


def get_playlist_by_id(self, playlist_id: int) -> Optional[dict[str, object]]:
    self._ensure_cache_valid()
    return self._playlists_cache.get(playlist_id)


def search_playlists(self, query: str) -> list[dict[str, object]]:
    query = (query or "").strip().lower()
    if not query:
        return self.get_all_playlists()

    self._ensure_cache_valid()
    results = []
    for playlist in self._playlists_cache.values():
        name = playlist.get("name", "").lower()
        description = playlist.get("description", "").lower()
        if query in name or query in description:
            results.append(playlist)
    return results


def set_current_playlist(self, playlist_id: int):
    """Imposta la playlist corrente."""
    self._ensure_cache_valid()
    if playlist_id in self._playlists_cache:
        self._current_playlist_id = playlist_id
        playlist_name = self._playlists_cache[playlist_id]["name"]
        self.event_bus.publish(
            AudioEventType.CURRENT_PLAYLIST_SET,
            {"id": playlist_id, "name": playlist_name},
        )
        self._notify_feedback("current_playlist_set", name=playlist_name)
        logger.info("Set current playlist to: %s (ID: %s)", playlist_name, playlist_id)


@property
def current_playlist_id(self) -> Optional[int]:
    return self._current_playlist_id


def set_currently_playing(self, playlist_id: int):
    self._currently_playing = playlist_id


def get_currently_playing(self) -> Optional[int]:
    return self._currently_playing


_PLAYLIST_CONTROLLER_PLAYLISTS_METHODS: tuple[tuple[str, object], ...] = (
    ("create_playlist", create_playlist),
    ("rename_playlist", rename_playlist),
    ("delete_playlist", delete_playlist),
    ("get_all_playlists", get_all_playlists),
    ("get_playlist_by_id", get_playlist_by_id),
    ("search_playlists", search_playlists),
    ("set_current_playlist", set_current_playlist),
    ("current_playlist_id", current_playlist_id),
    ("set_currently_playing", set_currently_playing),
    ("get_currently_playing", get_currently_playing),
)


def install_playlist_controller_playlists_behavior(cls) -> None:
    """Install playlists bindings from the split playlist controller playlists module.

    Edge cases:
        1. The target is not a class and receives playlist methods unexpectedly.
        2. A binding name is empty or duplicated and silently corrupts controller wiring.
        3. A split module exports a non-callable binding and breaks runtime attachment.
    """
    if not isinstance(cls, type):
        raise TypeError('Playlist controller playlists behavior can only be attached to classes')
    if getattr(cls, '_playlist_controller_playlists_behavior_attached', False):
        return

    seen_names: set[str] = set()
    for attribute_name, method in _PLAYLIST_CONTROLLER_PLAYLISTS_METHODS:
        if not attribute_name:
            raise TypeError('Playlist controller playlists binding name cannot be empty')
        if attribute_name in seen_names:
            raise TypeError(f'Duplicate playlist controller playlists binding: {attribute_name}')
        if not callable(method) and not isinstance(method, property):
            raise TypeError(f'Invalid playlist controller playlists binding: {attribute_name}')
        setattr(cls, attribute_name, method)
        seen_names.add(attribute_name)

    setattr(cls, '_playlist_controller_playlists_behavior_attached', True)


def attach_playlist_controller_playlists_behavior(cls) -> None:
    """Compatibility shim delegating to the neutral playlist playlists installer."""
    install_playlist_controller_playlists_behavior(cls)
