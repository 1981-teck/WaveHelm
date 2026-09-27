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
from src.model.component_database.playlist_identity import normalize_playlist_name
from src.model.component_database.playlist_position import (
    MAX_PLAYLIST_MEDIA_PATH_CHARS,
    PlaylistPositionInvariantError,
    compact_playlist_after_delete,
    prepare_playlist_item_insert,
    reorder_playlist_positions,
    validate_playlist_id,
    validate_playlist_position_sequence,
)
from src.utils.exceptions import DatabaseError, IntegrityError, NotFoundError

logger = logging.getLogger(__name__)

PLAYLIST_MANAGER_DB_EXCEPTIONS = (
    PlaylistMirrorJournalError,
    sqlite3.Error,
    TypeError,
    ValueError,
)


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


def _normalize_media_path(media_path: str) -> str:
    if not isinstance(media_path, str):
        raise TypeError("Media path must be text")
    normalized = media_path.strip()
    if not normalized or "\x00" in normalized:
        raise ValueError("Media path required")
    if len(normalized) > MAX_PLAYLIST_MEDIA_PATH_CHARS:
        raise ValueError("Media path exceeds its hard cap")
    return normalized


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
            2. Canonically equivalent duplicates roll back name and journal changes.
            3. A concurrent equivalent rename is contained by the unique index.
        """
        identity = normalize_playlist_name(new_name)

        with self.db_core.durable_write_connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing = _playlist_identity(connection, playlist_id)
                if existing is None:
                    _raise_not_found(playlist_id)
                old_name = existing[1]
                duplicate = connection.execute(
                    "SELECT id FROM playlists "
                    "WHERE canonical_name = ? AND id != ?",
                    (identity.canonical, playlist_id),
                ).fetchone()
                if duplicate is not None:
                    raise IntegrityError(f"Playlist already exists: {identity.display}")
                connection.execute(
                    "UPDATE playlists SET name = ?, canonical_name = ?, "
                    "last_modified = ? WHERE id = ?",
                    (
                        identity.display,
                        identity.canonical,
                        datetime.now().isoformat(),
                        playlist_id,
                    ),
                )
                enqueue_playlist_mirror_sync(
                    connection,
                    playlist_id,
                    identity.display,
                    previous_name=old_name,
                )
                connection.commit()
                logger.info("Playlist renamed: %s to %s", playlist_id, identity.display)
            except sqlite3.IntegrityError as error:
                _rollback_if_active(connection)
                raise IntegrityError(
                    f"Playlist already exists: {identity.display}"
                ) from error
            except (IntegrityError, NotFoundError):
                _rollback_if_active(connection)
                raise
            except PLAYLIST_MANAGER_DB_EXCEPTIONS as error:
                _rollback_if_active(connection)
                raise DatabaseError(f"Error renaming playlist: {error}") from error

    def delete_playlist(self, playlist_id: int) -> None:
        """Delete canonical playlist data and enqueue mirror deletion atomically."""
        with self.db_core.durable_write_connection() as connection:
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
        identity = normalize_playlist_name(name)
        timestamp = datetime.now().isoformat()

        with self.db_core.durable_write_connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    """
                    INSERT INTO playlists (
                        name, canonical_name, description, cover_art,
                        creation_date, last_modified
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        identity.display,
                        identity.canonical,
                        description,
                        cover_art,
                        timestamp,
                        timestamp,
                    ),
                )
                playlist_id = int(cursor.lastrowid)
                enqueue_playlist_mirror_sync(connection, playlist_id, identity.display)
                connection.commit()
                logger.info(
                    "Playlist created: %s with id %s", identity.display, playlist_id
                )
                return playlist_id
            except sqlite3.IntegrityError as error:
                _rollback_if_active(connection)
                raise IntegrityError(f"Playlist already exists: {identity.display}") from error
            except PLAYLIST_MANAGER_DB_EXCEPTIONS as error:
                _rollback_if_active(connection)
                raise DatabaseError(
                    f"Error creating playlist: {identity.display}, error: {error}"
                ) from error

    def get_all_playlists(self) -> list[dict[str, object]]:
        query = (
            "SELECT id, name, description, cover_art, creation_date, last_modified "
            "FROM playlists ORDER BY name"
        )
        rows = self.db_core._execute_query(query, fetch_all=True)
        return [dict(row) for row in rows] if rows else []

    def get_playlist_by_name(self, name: str) -> dict[str, object] | None:
        identity = normalize_playlist_name(name)
        query = (
            "SELECT id, name, description, cover_art, creation_date, last_modified "
            "FROM playlists WHERE canonical_name = ?"
        )
        row = self.db_core._execute_query(
            query, (identity.canonical,), fetch_one=True
        )
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
        """Add one item at an exact 1..N+1 position and enqueue mirror sync."""
        playlist_id = validate_playlist_id(playlist_id)
        normalized_path = _normalize_media_path(media_path)

        with self.db_core.durable_write_connection() as connection:
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
                final_position = prepare_playlist_item_insert(
                    connection, playlist_id, position
                )
                timestamp = datetime.now().isoformat()
                cursor = connection.execute(
                    "INSERT INTO playlist_items "
                    "(playlist_id, media_path, position, added_at) VALUES (?, ?, ?, ?)",
                    (playlist_id, normalized_path, final_position, timestamp),
                )
                if cursor.rowcount != 1:
                    raise PlaylistPositionInvariantError(
                        "Playlist item insert did not affect exactly one row."
                    )
                validate_playlist_position_sequence(connection, playlist_id)
                self._touch_playlist(connection, playlist_id, timestamp)
                enqueue_playlist_mirror_sync(connection, playlist_id, playlist[1])
                connection.commit()
                logger.info(
                    "Playlist item added: %s to playlist %s at position %s",
                    normalized_path, playlist_id, final_position,
                )
            except (
                IntegrityError, NotFoundError, PlaylistPositionInvariantError,
                TypeError, ValueError,
            ):
                _rollback_if_active(connection)
                raise
            except sqlite3.IntegrityError as error:
                _rollback_if_active(connection)
                raise IntegrityError(
                    f"Playlist item violates an integrity constraint: {normalized_path}"
                ) from error
            except PLAYLIST_MANAGER_DB_EXCEPTIONS as error:
                _rollback_if_active(connection)
                raise DatabaseError(f"Error adding playlist item: {error}") from error

    @staticmethod
    def _touch_playlist(
        connection: sqlite3.Connection, playlist_id: int, timestamp: str
    ) -> None:
        connection.execute(
            "UPDATE playlists SET last_modified = ? WHERE id = ?",
            (timestamp, playlist_id),
        )

    def remove_playlist_item(self, playlist_id: int, media_path: str) -> None:
        """Remove one item and compact the committed sequence to 1..N."""
        playlist_id = validate_playlist_id(playlist_id)
        normalized_path = _normalize_media_path(media_path)

        with self.db_core.durable_write_connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                playlist = _playlist_identity(connection, playlist_id)
                if playlist is None:
                    _raise_not_found(playlist_id)
                state = validate_playlist_position_sequence(connection, playlist_id)
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
                removed_position = row[0]
                if isinstance(removed_position, bool) or not isinstance(removed_position, int):
                    raise PlaylistPositionInvariantError(
                        "Stored playlist item position is not an integer."
                    )
                cursor = connection.execute(
                    "DELETE FROM playlist_items "
                    "WHERE playlist_id = ? AND media_path = ?",
                    (playlist_id, normalized_path),
                )
                if cursor.rowcount != 1:
                    raise PlaylistPositionInvariantError(
                        "Playlist item delete did not affect exactly one row."
                    )
                compact_playlist_after_delete(
                    connection, playlist_id, removed_position, state.item_count
                )
                timestamp = datetime.now().isoformat()
                self._touch_playlist(connection, playlist_id, timestamp)
                enqueue_playlist_mirror_sync(connection, playlist_id, playlist[1])
                connection.commit()
                logger.info(
                    "Playlist item removed: %s from playlist %s",
                    normalized_path, playlist_id,
                )
            except (NotFoundError, PlaylistPositionInvariantError):
                _rollback_if_active(connection)
                raise
            except PLAYLIST_MANAGER_DB_EXCEPTIONS as error:
                _rollback_if_active(connection)
                raise DatabaseError(f"Error removing playlist item: {error}") from error

    def get_playlist_items(self, playlist_id: int) -> list[dict[str, object]]:
        playlist_id = validate_playlist_id(playlist_id)
        query = """
        SELECT pi.playlist_id, pi.media_path, pi.position, pi.added_at,
               li.title, li.media_type, li.duration, li.metadata
        FROM playlist_items pi
        LEFT JOIN library_items li ON pi.media_path = li.path
        WHERE pi.playlist_id = ? ORDER BY pi.position
        """
        with self.db_core.shared_transaction(mode="DEFERRED") as transaction:
            validate_playlist_position_sequence(transaction, playlist_id)
            rows = transaction.execute(query, (playlist_id,)).fetchall()
        items: list[dict[str, object]] = []
        for row in rows:
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
        """Move one item to an exact 1..N position and enqueue mirror sync."""
        playlist_id = validate_playlist_id(playlist_id)
        normalized_path = _normalize_media_path(media_path)
        if isinstance(new_position, bool) or not isinstance(new_position, int):
            raise TypeError("Playlist item position must be an integer")

        with self.db_core.durable_write_connection() as connection:
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
                old_position = row[0]
                if isinstance(old_position, bool) or not isinstance(old_position, int):
                    raise PlaylistPositionInvariantError(
                        "Stored playlist item position is not an integer."
                    )
                target_position = reorder_playlist_positions(
                    connection, playlist_id, normalized_path, old_position, new_position
                )
                if old_position == target_position:
                    connection.rollback()
                    return
                self._touch_playlist(connection, playlist_id, datetime.now().isoformat())
                enqueue_playlist_mirror_sync(connection, playlist_id, playlist[1])
                connection.commit()
                logger.info(
                    "Playlist item reordered: %s from %s to %s",
                    normalized_path, old_position, target_position,
                )
            except (NotFoundError, PlaylistPositionInvariantError, ValueError):
                _rollback_if_active(connection)
                raise
            except sqlite3.IntegrityError as error:
                _rollback_if_active(connection)
                raise PlaylistPositionInvariantError(
                    "Playlist reorder violated the position constraints."
                ) from error
            except PLAYLIST_MANAGER_DB_EXCEPTIONS as error:
                _rollback_if_active(connection)
                raise DatabaseError(f"Error reordering playlist item: {error}") from error
