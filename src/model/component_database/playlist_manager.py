from __future__ import annotations
import sqlite3
import json
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from src.model.component_database.db_core import DbCore
from src.utils.exceptions import IntegrityError, NotFoundError, DatabaseError

logger = logging.getLogger(__name__)

PLAYLIST_MANAGER_DB_EXCEPTIONS = (sqlite3.Error, TypeError, ValueError)


class PlaylistManager:
    def __init__(self, db_core: DbCore):
        self.db_core = db_core

    def rename_playlist(self, playlist_id: int, new_name: str):
        new_name = (new_name or "").strip()
        if not new_name:
            raise ValueError("Playlist name required")

        with self.db_core._db_lock:
            if not self.db_core.conn:
                raise DatabaseError("Database not connected.")

            try:
                self.db_core.conn.execute("BEGIN TRANSACTION")
                existing = self.db_core._execute_query(
                    "SELECT id, name FROM playlists WHERE id = ?",
                    (playlist_id,),
                    fetch_one=True,
                )
                if not existing:
                    self.db_core.conn.rollback()
                    raise NotFoundError(f"Playlist not found: {playlist_id}")

                name_check = self.db_core._execute_query(
                    "SELECT id FROM playlists WHERE LOWER(name) = LOWER(?) AND id != ?",
                    (new_name, playlist_id),
                    fetch_one=True,
                )
                if name_check:
                    self.db_core.conn.rollback()
                    raise IntegrityError(f"Playlist already exists: {new_name}")

                self.db_core._execute_query(
                    "UPDATE playlists SET name = ?, last_modified = ? WHERE id = ?",
                    (new_name, datetime.now().isoformat(), playlist_id),
                )
                self.db_core.conn.commit()
                logger.info(f"Playlist renamed: {playlist_id} to {new_name}")
            except (IntegrityError, NotFoundError):
                self.db_core.conn.rollback()
                raise
            except PLAYLIST_MANAGER_DB_EXCEPTIONS as e:
                self.db_core.conn.rollback()
                raise DatabaseError(f"Error renaming playlist: {e}")

    def delete_playlist(self, playlist_id: int):
        with self.db_core._db_lock:
            if not self.db_core.conn:
                raise DatabaseError("Database not connected.")

            try:
                self.db_core.conn.execute("BEGIN TRANSACTION")
                existing = self.db_core._execute_query(
                    "SELECT id, name FROM playlists WHERE id = ?",
                    (playlist_id,),
                    fetch_one=True,
                )
                if not existing:
                    self.db_core.conn.rollback()
                    raise NotFoundError(f"Playlist not found: {playlist_id}")

                self.db_core._execute_query(
                    "DELETE FROM playlist_items WHERE playlist_id = ?", (playlist_id,)
                )
                self.db_core._execute_query(
                    "DELETE FROM playlists WHERE id = ?", (playlist_id,)
                )

                if self.db_core._last_changes() == 0:
                    self.db_core.conn.rollback()
                    raise NotFoundError(f"Playlist not found: {playlist_id}")

                self.db_core.conn.commit()
                logger.info(f"Playlist deleted: {playlist_id}")
            except (NotFoundError, DatabaseError):
                self.db_core.conn.rollback()
                raise
            except PLAYLIST_MANAGER_DB_EXCEPTIONS as e:
                self.db_core.conn.rollback()
                raise DatabaseError(f"Error deleting playlist: {e}")

    def create_playlist(
        self,
        name: str,
        description: Optional[str] = None,
        cover_art: Optional[str] = None,
    ) -> int:
        query = """
        INSERT INTO playlists (name, description, cover_art, creation_date, last_modified)
        VALUES (?, ?, ?, ?, ?)
        """
        timestamp = datetime.now().isoformat()
        try:
            self.db_core._execute_query(
                query, (name, description, cover_art, timestamp, timestamp)
            )
            playlist_id = self.db_core.conn.execute(
                "SELECT last_insert_rowid()"
            ).fetchone()[0]
            logger.info(f"Playlist created: {name} with id {playlist_id}")
            return playlist_id
        except (IntegrityError, sqlite3.IntegrityError):
            raise IntegrityError(f"Playlist already exists: {name}")
        except DatabaseError as e:
            raise DatabaseError(f"Error creating playlist: {name}, error: {e}")

    def get_all_playlists(self) -> List[Dict[str, Any]]:
        query = "SELECT id, name, description, cover_art, creation_date, last_modified FROM playlists ORDER BY name"
        rows = self.db_core._execute_query(query, fetch_all=True)
        return [dict(row) for row in rows] if rows else []

    def get_playlist_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        query = "SELECT id, name, description, cover_art, creation_date, last_modified FROM playlists WHERE name = ?"
        row = self.db_core._execute_query(query, (name,), fetch_one=True)
        return dict(row) if row else None

    def get_playlist_by_id(self, playlist_id: int) -> Optional[Dict[str, Any]]:
        query = "SELECT id, name, description, cover_art, creation_date, last_modified FROM playlists WHERE id = ?"
        row = self.db_core._execute_query(query, (playlist_id,), fetch_one=True)
        return dict(row) if row else None

    def add_playlist_item(
        self, playlist_id: int, media_path: str, position: Optional[int] = None
    ):
        if not media_path or not media_path.strip():
            raise ValueError("Media path required")

        media_path = media_path.strip()

        with self.db_core._db_lock:
            if not self.db_core.conn:
                raise DatabaseError("Database not connected.")

            try:
                self.db_core.conn.execute("BEGIN TRANSACTION")

                playlist_check = self.db_core._execute_query(
                    "SELECT id, name FROM playlists WHERE id = ?",
                    (playlist_id,),
                    fetch_one=True,
                )
                if not playlist_check:
                    self.db_core.conn.rollback()
                    raise NotFoundError(f"Playlist not found: {playlist_id}")

                existing_item = self.db_core._execute_query(
                    "SELECT playlist_id FROM playlist_items WHERE playlist_id = ? AND media_path = ?",
                    (playlist_id, media_path),
                    fetch_one=True,
                )
                if existing_item:
                    self.db_core.conn.rollback()
                    raise IntegrityError(
                        f"Playlist item already exists: {media_path} in playlist {playlist_id}"
                    )

                if position is None:
                    max_pos_row = self.db_core._execute_query(
                        "SELECT COALESCE(MAX(position), 0) as max_pos FROM playlist_items WHERE playlist_id = ?",
                        (playlist_id,),
                        fetch_one=True,
                    )
                    position = (max_pos_row["max_pos"] if max_pos_row else 0) + 1
                else:
                    if position < 1:
                        position = 1
                    self.db_core._execute_query(
                        "UPDATE playlist_items SET position = position + 1 WHERE playlist_id = ? AND position >= ?",
                        (playlist_id, position),
                    )

                timestamp = datetime.now().isoformat()
                self.db_core._execute_query(
                    "INSERT INTO playlist_items (playlist_id, media_path, position, added_at) VALUES (?, ?, ?, ?)",
                    (playlist_id, media_path, position, timestamp),
                )
                self.db_core._execute_query(
                    "UPDATE playlists SET last_modified = ? WHERE id = ?",
                    (timestamp, playlist_id),
                )

                self.db_core.conn.commit()
                logger.info(
                    f"Playlist item added: {media_path} to playlist {playlist_id} at position {position}"
                )

            except (NotFoundError, IntegrityError, ValueError):
                self.db_core.conn.rollback()
                raise
            except PLAYLIST_MANAGER_DB_EXCEPTIONS as e:
                self.db_core.conn.rollback()
                raise DatabaseError(f"Error adding playlist item: {e}")

    def remove_playlist_item(self, playlist_id: int, media_path: str):
        if not media_path or not media_path.strip():
            raise ValueError("Media path required")

        media_path = media_path.strip()

        with self.db_core._db_lock:
            if not self.db_core.conn:
                raise DatabaseError("Database not connected.")

            try:
                self.db_core.conn.execute("BEGIN TRANSACTION")

                playlist_check = self.db_core._execute_query(
                    "SELECT id, name FROM playlists WHERE id = ?",
                    (playlist_id,),
                    fetch_one=True,
                )
                if not playlist_check:
                    self.db_core.conn.rollback()
                    raise NotFoundError(f"Playlist not found: {playlist_id}")

                existing_item = self.db_core._execute_query(
                    "SELECT position FROM playlist_items WHERE playlist_id = ? AND media_path = ?",
                    (playlist_id, media_path),
                    fetch_one=True,
                )
                if not existing_item:
                    self.db_core.conn.rollback()
                    raise NotFoundError(
                        f"Playlist item not found: {media_path} in playlist {playlist_id}"
                    )

                position_to_remove = existing_item["position"]

                self.db_core._execute_query(
                    "DELETE FROM playlist_items WHERE playlist_id = ? AND media_path = ?",
                    (playlist_id, media_path),
                )
                self.db_core._execute_query(
                    "UPDATE playlist_items SET position = position - 1 WHERE playlist_id = ? AND position > ?",
                    (playlist_id, position_to_remove),
                )
                timestamp = datetime.now().isoformat()
                self.db_core._execute_query(
                    "UPDATE playlists SET last_modified = ? WHERE id = ?",
                    (timestamp, playlist_id),
                )

                self.db_core.conn.commit()
                logger.info(
                    f"Playlist item removed: {media_path} from playlist {playlist_id}"
                )

            except (NotFoundError, ValueError):
                self.db_core.conn.rollback()
                raise
            except PLAYLIST_MANAGER_DB_EXCEPTIONS as e:
                self.db_core.conn.rollback()
                raise DatabaseError(f"Error removing playlist item: {e}")

    def get_playlist_items(self, playlist_id: int) -> List[Dict[str, Any]]:
        query = """
        SELECT pi.playlist_id, pi.media_path, pi.position, pi.added_at,
               li.title, li.media_type, li.duration, li.metadata
        FROM playlist_items pi
        LEFT JOIN library_items li ON pi.media_path = li.path
        WHERE pi.playlist_id = ? ORDER BY pi.position
        """
        rows = self.db_core._execute_query(query, (playlist_id,), fetch_all=True)
        items = []
        if rows:
            for row in rows:
                item = dict(row)
                try:
                    item["metadata"] = (
                        json.loads(item["metadata"]) if item["metadata"] else {}
                    )
                except json.JSONDecodeError:
                    logger.error("Invalid JSON in playlist item metadata")
                    item["metadata"] = {}
                items.append(item)
        return items

    def reorder_playlist_item(
        self, playlist_id: int, media_path: str, new_position: int
    ):
        with self.db_core._db_lock:
            if not self.db_core.conn:
                raise DatabaseError("Database not connected.")
            try:
                item_info = self.db_core._execute_query(
                    """SELECT position FROM playlist_items WHERE playlist_id = ? AND media_path = ?""",
                    (playlist_id, media_path),
                    fetch_one=True,
                )
                if not item_info:
                    raise NotFoundError(
                        f"Playlist item not found: {media_path} in playlist {playlist_id}"
                    )

                old_position = item_info["position"]
                if old_position == new_position:
                    return

                max_row = self.db_core._execute_query(
                    """SELECT MAX(position) FROM playlist_items WHERE playlist_id = ?""",
                    (playlist_id,),
                    fetch_one=True,
                )
                max_position = max_row[0] if max_row and max_row[0] is not None else 0

                if new_position < 0:
                    new_position = 0
                elif new_position > max_position:
                    new_position = max_position

                self.db_core.conn.execute("BEGIN TRANSACTION")

                if new_position < old_position:
                    self.db_core._execute_query(
                        """UPDATE playlist_items SET position = position + 1 WHERE playlist_id = ? AND position >= ? AND position < ?""",
                        (playlist_id, new_position, old_position),
                    )
                else:
                    self.db_core._execute_query(
                        """UPDATE playlist_items SET position = position - 1 WHERE playlist_id = ? AND position > ? AND position <= ?""",
                        (playlist_id, old_position, new_position),
                    )

                self.db_core._execute_query(
                    """UPDATE playlist_items SET position = ? WHERE playlist_id = ? AND media_path = ?""",
                    (new_position, playlist_id, media_path),
                )
                self.db_core.conn.commit()
                logger.info(
                    f"Playlist item reordered: {media_path} from {old_position} to {new_position}"
                )
            except PLAYLIST_MANAGER_DB_EXCEPTIONS as e:
                self.db_core.conn.rollback()
                raise DatabaseError(f"Error reordering playlist item: {e}")
