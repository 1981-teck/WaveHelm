from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from src.utils.helpers import safe_filename

logger = logging.getLogger(__name__)

PLAYLIST_STORAGE_SYNC_EXCEPTIONS = (OSError, RuntimeError, TypeError, ValueError)
PLAYLIST_STORAGE_DELETE_EXCEPTIONS = (OSError, RuntimeError, TypeError, ValueError)


def _get_playlist_storage_file(
    self, playlist_id: int, name: str, *, create_parent: bool = True
) -> Path:
    """Restituisce il file JSON della playlist nello storage utente."""
    if create_parent:
        self._playlists_storage_dir.mkdir(parents=True, exist_ok=True)
    safe_name = safe_filename(name) or f"playlist_{playlist_id}"
    return self._playlists_storage_dir / f"{playlist_id}_{safe_name}.json"


def _build_playlist_snapshot(self, playlist_id: int) -> Dict[str, Any]:
    """Costruisce il payload file-based della playlist."""
    playlist = self._playlists_cache.get(playlist_id, {})
    tracks = self.get_tracks_in_playlist(playlist_id)
    return {
        "id": playlist_id,
        "name": playlist.get("name", ""),
        "description": playlist.get("description", "") or "",
        "creation_date": playlist.get("creation_date", "") or "",
        "last_modified": playlist.get("last_modified", "") or "",
        "tracks": [
            track.to_dict() if hasattr(track, "to_dict") else {"path": getattr(track, "path", "")}
            for track in tracks
        ],
    }


def _sync_playlist_file(self, playlist_id: Optional[int], previous_name: Optional[str] = None) -> None:
    """Sincronizza la playlist con un file JSON nella cartella utente dell'app."""
    if playlist_id is None:
        return

    playlist = self._playlists_cache.get(playlist_id)
    if not playlist:
        return

    try:
        target = self._get_playlist_storage_file(
            playlist_id, str(playlist.get("name", "")), create_parent=True
        )
        if previous_name:
            old_path = self._get_playlist_storage_file(
                playlist_id, previous_name, create_parent=False
            )
            if old_path != target and old_path.exists():
                old_path.unlink()

        snapshot = self._build_playlist_snapshot(playlist_id)
        with target.open("w", encoding="utf-8") as handle:
            json.dump(snapshot, handle, ensure_ascii=False, indent=2)
    except PLAYLIST_STORAGE_SYNC_EXCEPTIONS as exc:
        logger.warning(
            "[PlaylistController] Sync playlist file failed for %s: %s",
            playlist_id,
            exc,
            exc_info=True,
        )


def _delete_playlist_storage(self, playlist_id: int, playlist_name: str) -> None:
    """Rimuove il file JSON della playlist dallo storage utente."""
    try:
        candidate = self._get_playlist_storage_file(
            playlist_id, playlist_name, create_parent=False
        )
        if candidate.exists():
            candidate.unlink()
    except PLAYLIST_STORAGE_DELETE_EXCEPTIONS:
        logger.debug(
            "[PlaylistController] Failed deleting playlist storage for %s",
            playlist_id,
            exc_info=True,
        )


def _sync_all_playlist_files(self) -> None:
    """Allinea tutti i file playlist nello storage utente."""
    try:
        self._playlists_storage_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.debug("Playlist storage dir creation failed.", exc_info=True)
        return

    for playlist_id in list(self._playlists_cache.keys()):
        self._sync_playlist_file(playlist_id)


_PLAYLIST_CONTROLLER_STORAGE_METHODS: tuple[tuple[str, Callable[..., Any]], ...] = (
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
        raise TypeError('Playlist controller storage behavior can only be attached to classes')
    if getattr(cls, '_playlist_controller_storage_behavior_attached', False):
        return

    seen_names: set[str] = set()
    for attribute_name, method in _PLAYLIST_CONTROLLER_STORAGE_METHODS:
        if not attribute_name:
            raise TypeError('Playlist controller storage binding name cannot be empty')
        if attribute_name in seen_names:
            raise TypeError(f'Duplicate playlist controller storage binding: {attribute_name}')
        if not callable(method):
            raise TypeError(f'Invalid playlist controller storage binding: {attribute_name}')
        setattr(cls, attribute_name, method)
        seen_names.add(attribute_name)

    setattr(cls, '_playlist_controller_storage_behavior_attached', True)


def attach_playlist_controller_storage_behavior(cls) -> None:
    """Compatibility shim for legacy storage attach imports."""
    install_playlist_controller_storage_behavior(cls)
