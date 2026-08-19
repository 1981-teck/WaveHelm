from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime

from src.model.component_database.db_core import DbCore
from src.model.component_database.playlist_mirror_journal import (
    PlaylistMirrorJournal,
    PlaylistMirrorJournalError,
    enqueue_playlist_mirror_delete,
    enqueue_playlist_mirror_sync,
)
from src.model.component_database.playlist_mirror_maintenance import (
    PlaylistMirrorMaintenance,
)
from src.utils.exceptions import DatabaseError, IntegrityError, NotFoundError

logger = logging.getLogger(__name__)

PLAYLIST_MANAGER_DB_EXCEPTIONS = (
    PlaylistMirrorJournalError,
    sqlite3.Error,
    TypeError,
    ValueError,
)


def _require_connection(db_core: DbCore) -> sqlite3.Connection:
    connection = db_core.conn
    if connection is None:
        db_core.connect()
        connection = db_core.conn
    if connection is None:
        raise DatabaseError("Database not connected.")
    return connection


def _rollback_if_active(connection: sqlite3.Connection) -> None:
    if connection.in_transaction:
        connection.rollback()


def _playlist_identity(
    connection: sqlite3.Connection, playlist_id: int
) -> tuple[int, str] | None:
    row = connection.execute(
        "SELECT id, name FROM playlists WHERE id = ?", (playlist_id,)
    ).fetchone()
    if row is None:
        return None
    return int(row[0]), str(row[1])


def _raise_not_found(playlist_id: int) -> None:
    raise NotFoundError(f"Playlist not found: {playlist_id}")


class PlaylistManager:
    """SQLite-backed playlist manager with transactional mirror outbox intents."""

    def __init__(self, db_core: DbCore):
        self.db_core = db_core
        self._mirror_journal = PlaylistMirrorJournal(db_core)
        self._mirror_maintenance = PlaylistMirrorMaintenance(db_core)

    @property
    def mirror_journal(self) -> PlaylistMirrorJournal:
        return self._mirror_journal

    @property
    def mirror_maintenance(self) -> PlaylistMirrorMaintenance:
        return self._mirror_maintenance

    def rename_playlist(self, playlist_id: int, new_name: str) -> None:
        """Rename a playlist and enqueue mirror reconciliation atomically.

        Edge cases:
            1. Missing playlists abort without producing an outbox intent.
            2. Case-insensitive duplicates roll back both name and journal changes.
            3. Journal capacity or schema failures roll back the canonical rename.
        """
        normalized_name = (new_name or "").strip()
        if not normalized_name:
            raise ValueError("Playlist name required")

        with self.db_core._db_lock:
            connection = _require_connection(self.db_core)
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing = _playlist_identity(connection, playlist_id)
                if existing is None:
                    _raise_not_found(playlist_id)
                old_name = existing[1]
                duplicate = connection.execute(
                    "SELECT id FROM playlists "
                    "WHERE LOWER(name) = LOWER(?) AND id != ?",
                    (normalized_name, playlist_id),
                ).fetchone()
                if duplicate is not None:
                    raise IntegrityError(f"Playlist already exists: {normalized_name}")
                connection.execute(
                    "UPDATE playlists SET name = ?, last_modified = ? WHERE id = ?",
                    (normalized_name, datetime.now().isoformat(), playlist_id),
                )
                enqueue_playlist_mirror_sync(
                    connection,
                    playlist_id,
                    normalized_name,
                    previous_name=old_name,
                )
                connection.commit()
                logger.info("Playlist renamed: %s to %s", playlist_id, normalized_name)
            except (IntegrityError, NotFoundError):
                _rollback_if_active(connection)
                raise
            except PLAYLIST_MANAGER_DB_EXCEPTIONS as error:
                _rollback_if_active(connection)
                raise DatabaseError(f"Error renaming playlist: {error}") from error

    def delete_playlist(self, playlist_id: int) -> None:
        """Delete canonical playlist data and enqueue mirror deletion atomically."""
        with self.db_core._db_lock:
            connection = _require_connection(self.db_core)
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing = _playlist_identity(connection, playlist_id)
                if existing is None:
                    _raise_not_found(playlist_id)
                playlist_name = existing[1]
                connection.execute(
                    "DELETE FROM playlist_items WHERE playlist_id = ?", (playlist_id,)
                )
                cursor = connection.execute(
                    "DELETE FROM playlists WHERE id = ?", (playlist_id,)
                )
                if cursor.rowcount != 1:
                    _raise_not_found(playlist_id)
                enqueue_playlist_mirror_delete(connection, playlist_id, playlist_name)
                connection.commit()
                logger.info("Playlist deleted: %s", playlist_id)
            except NotFoundError:
                _rollback_if_active(connection)
                raise
            except PLAYLIST_MANAGER_DB_EXCEPTIONS as error:
                _rollback_if_active(connection)
                raise DatabaseError(f"Error deleting playlist: {error}") from error

    def create_playlist(
        self,
        name: str,
        description: str | None = None,
        cover_art: str | None = None,
    ) -> int:
        """Create a playlist and its durable mirror intent in one transaction."""
        normalized_name = (name or "").strip()
        if not normalized_name:
            raise ValueError("Playlist name required")
        timestamp = datetime.now().isoformat()

        with self.db_core._db_lock:
            connection = _require_connection(self.db_core)
            try:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    """
                    INSERT INTO playlists (
                        name, description, cover_art, creation_date, last_modified
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (normalized_name, description, cover_art, timestamp, timestamp),
                )
                playlist_id = int(cursor.lastrowid)
                enqueue_playlist_mirror_sync(connection, playlist_id, normalized_name)
                connection.commit()
                logger.info(
                    "Playlist created: %s with id %s", normalized_name, playlist_id
                )
                return playlist_id
            except sqlite3.IntegrityError as error:
                _rollback_if_active(connection)
                raise IntegrityError(f"Playlist already exists: {normalized_name}") from error
            except PLAYLIST_MANAGER_DB_EXCEPTIONS as error:
                _rollback_if_active(connection)
                raise DatabaseError(
                    f"Error creating playlist: {normalized_name}, error: {error}"
                ) from error

    def get_all_playlists(self) -> list[dict[str, object]]:
        query = (
            "SELECT id, name, description, cover_art, creation_date, last_modified "
            "FROM playlists ORDER BY name"
        )
        rows = self.db_core._execute_query(query, fetch_all=True)
        return [dict(row) for row in rows] if rows else []

    def get_playlist_by_name(self, name: str) -> dict[str, object] | None:
        query = (
            "SELECT id, name, description, cover_art, creation_date, last_modified "
            "FROM playlists WHERE name = ?"
        )
        row = self.db_core._execute_query(query, (name,), fetch_one=True)
        return dict(row) if row else None

    def get_playlist_by_id(self, playlist_id: int) -> dict[str, object] | None:
        query = (
            "SELECT id, name, description, cover_art, creation_date, last_modified "
            "FROM playlists WHERE id = ?"
        )
        row = self.db_core._execute_query(query, (playlist_id,), fetch_one=True)
        return dict(row) if row else None

    def add_playlist_item(
        self, playlist_id: int, media_path: str, position: int | None = None
    ) -> None:
        """Add an item and coalesce a mirror sync inside the same transaction."""
        normalized_path = (media_path or "").strip()
        if not normalized_path:
            raise ValueError("Media path required")

        with self.db_core._db_lock:
            connection = _require_connection(self.db_core)
            try:
                connection.execute("BEGIN IMMEDIATE")
                playlist = _playlist_identity(connection, playlist_id)
                if playlist is None:
                    _raise_not_found(playlist_id)
                duplicate = connection.execute(
                    "SELECT 1 FROM playlist_items "
                    "WHERE playlist_id = ? AND media_path = ?",
                    (playlist_id, normalized_path),
                ).fetchone()
                if duplicate is not None:
                    raise IntegrityError(
                        f"Playlist item already exists: {normalized_path} "
                        f"in playlist {playlist_id}"
                    )
                final_position = self._prepare_insert_position(
                    connection, playlist_id, position
                )
                timestamp = datetime.now().isoformat()
                connection.execute(
                    "INSERT INTO playlist_items "
                    "(playlist_id, media_path, position, added_at) VALUES (?, ?, ?, ?)",
                    (playlist_id, normalized_path, final_position, timestamp),
                )
                self._touch_playlist(connection, playlist_id, timestamp)
                enqueue_playlist_mirror_sync(connection, playlist_id, playlist[1])
                connection.commit()
                logger.info(
                    "Playlist item added: %s to playlist %s at position %s",
                    normalized_path,
                    playlist_id,
                    final_position,
                )
            except (IntegrityError, NotFoundError):
                _rollback_if_active(connection)
                raise
            except PLAYLIST_MANAGER_DB_EXCEPTIONS as error:
                _rollback_if_active(connection)
                raise DatabaseError(f"Error adding playlist item: {error}") from error

    @staticmethod
    def _prepare_insert_position(
        connection: sqlite3.Connection, playlist_id: int, position: int | None
    ) -> int:
        if position is None:
            row = connection.execute(
                "SELECT COALESCE(MAX(position), 0) FROM playlist_items "
                "WHERE playlist_id = ?",
                (playlist_id,),
            ).fetchone()
            return int(row[0]) + 1
        final_position = max(int(position), 1)
        connection.execute(
            "UPDATE playlist_items SET position = position + 1 "
            "WHERE playlist_id = ? AND position >= ?",
            (playlist_id, final_position),
        )
        return final_position

    @staticmethod
    def _touch_playlist(
        connection: sqlite3.Connection, playlist_id: int, timestamp: str
    ) -> None:
        connection.execute(
            "UPDATE playlists SET last_modified = ? WHERE id = ?",
            (timestamp, playlist_id),
        )

    def remove_playlist_item(self, playlist_id: int, media_path: str) -> None:
        """Remove an item and coalesce a mirror sync inside the same transaction."""
        normalized_path = (media_path or "").strip()
        if not normalized_path:
            raise ValueError("Media path required")

        with self.db_core._db_lock:
            connection = _require_connection(self.db_core)
            try:
                connection.execute("BEGIN IMMEDIATE")
                playlist = _playlist_identity(connection, playlist_id)
                if playlist is None:
                    _raise_not_found(playlist_id)
                row = connection.execute(
                    "SELECT position FROM playlist_items "
                    "WHERE playlist_id = ? AND media_path = ?",
                    (playlist_id, normalized_path),
                ).fetchone()
                if row is None:
                    raise NotFoundError(
                        f"Playlist item not found: {normalized_path} "
                        f"in playlist {playlist_id}"
                    )
                removed_position = int(row[0])
                connection.execute(
                    "DELETE FROM playlist_items "
                    "WHERE playlist_id = ? AND media_path = ?",
                    (playlist_id, normalized_path),
                )
                connection.execute(
                    "UPDATE playlist_items SET position = position - 1 "
                    "WHERE playlist_id = ? AND position > ?",
                    (playlist_id, removed_position),
                )
                timestamp = datetime.now().isoformat()
                self._touch_playlist(connection, playlist_id, timestamp)
                enqueue_playlist_mirror_sync(connection, playlist_id, playlist[1])
                connection.commit()
                logger.info(
                    "Playlist item removed: %s from playlist %s",
                    normalized_path,
                    playlist_id,
                )
            except NotFoundError:
                _rollback_if_active(connection)
                raise
            except PLAYLIST_MANAGER_DB_EXCEPTIONS as error:
                _rollback_if_active(connection)
                raise DatabaseError(f"Error removing playlist item: {error}") from error

    def get_playlist_items(self, playlist_id: int) -> list[dict[str, object]]:
        query = """
        SELECT pi.playlist_id, pi.media_path, pi.position, pi.added_at,
               li.title, li.media_type, li.duration, li.metadata
        FROM playlist_items pi
        LEFT JOIN library_items li ON pi.media_path = li.path
        WHERE pi.playlist_id = ? ORDER BY pi.position
        """
        rows = self.db_core._execute_query(query, (playlist_id,), fetch_all=True)
        items: list[dict[str, object]] = []
        for row in rows or []:
            item = dict(row)
            metadata = item.get("metadata")
            try:
                item["metadata"] = json.loads(metadata) if metadata else {}
            except (json.JSONDecodeError, TypeError):
                logger.error("Invalid JSON in playlist item metadata")
                item["metadata"] = {}
            items.append(item)
        return items

    def reorder_playlist_item(
        self, playlist_id: int, media_path: str, new_position: int
    ) -> None:
        """Reorder an item and enqueue the resulting mirror snapshot atomically."""
        with self.db_core._db_lock:
            connection = _require_connection(self.db_core)
            try:
                connection.execute("BEGIN IMMEDIATE")
                playlist = _playlist_identity(connection, playlist_id)
                if playlist is None:
                    _raise_not_found(playlist_id)
                row = connection.execute(
                    "SELECT position FROM playlist_items "
                    "WHERE playlist_id = ? AND media_path = ?",
                    (playlist_id, media_path),
                ).fetchone()
                if row is None:
                    raise NotFoundError(
                        f"Playlist item not found: {media_path} in playlist {playlist_id}"
                    )
                old_position = int(row[0])
                target_position = self._bounded_reorder_position(
                    connection, playlist_id, int(new_position)
                )
                if old_position == target_position:
                    connection.rollback()
                    return
                self._shift_playlist_positions(
                    connection, playlist_id, old_position, target_position
                )
                connection.execute(
                    "UPDATE playlist_items SET position = ? "
                    "WHERE playlist_id = ? AND media_path = ?",
                    (target_position, playlist_id, media_path),
                )
                self._touch_playlist(connection, playlist_id, datetime.now().isoformat())
                enqueue_playlist_mirror_sync(connection, playlist_id, playlist[1])
                connection.commit()
                logger.info(
                    "Playlist item reordered: %s from %s to %s",
                    media_path,
                    old_position,
                    target_position,
                )
            except NotFoundError:
                _rollback_if_active(connection)
                raise
            except PLAYLIST_MANAGER_DB_EXCEPTIONS as error:
                _rollback_if_active(connection)
                raise DatabaseError(f"Error reordering playlist item: {error}") from error

    @staticmethod
    def _bounded_reorder_position(
        connection: sqlite3.Connection, playlist_id: int, requested_position: int
    ) -> int:
        row = connection.execute(
            "SELECT MAX(position) FROM playlist_items WHERE playlist_id = ?",
            (playlist_id,),
        ).fetchone()
        maximum = int(row[0]) if row and row[0] is not None else 0
        return min(max(requested_position, 0), maximum)

    @staticmethod
    def _shift_playlist_positions(
        connection: sqlite3.Connection,
        playlist_id: int,
        old_position: int,
        new_position: int,
    ) -> None:
        if new_position < old_position:
            connection.execute(
                "UPDATE playlist_items SET position = position + 1 "
                "WHERE playlist_id = ? AND position >= ? AND position < ?",
                (playlist_id, new_position, old_position),
            )
            return
        connection.execute(
            "UPDATE playlist_items SET position = position - 1 "
            "WHERE playlist_id = ? AND position > ? AND position <= ?",
            (playlist_id, old_position, new_position),
        )
