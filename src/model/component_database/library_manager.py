from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import List, Optional

from src.model.component_database.db_core import DbCore
from src.utils.exceptions import DatabaseError, IntegrityError, NotFoundError

logger = logging.getLogger(__name__)


class LibraryManager:
    def __init__(self, db_core: DbCore):
        self.db_core = db_core

    def add_library_item(
        self,
        path_or_item,
        title: Optional[str] = None,
        media_type: Optional[str] = None,
        duration: Optional[float] = None,
        metadata: Optional[Mapping[str, object]] = None,
        additional_data: Optional[Mapping[str, object]] = None,
    ) -> None:
        """Insert or update one mirrored library row with checked metadata.

        Edge cases:
            1. Required fields violate a database integrity constraint.
            2. The path already exists and the statement follows its update arm.
            3. The write reports an unexpected zero or multi-row result.
        """
        if isinstance(path_or_item, dict):
            item = dict(path_or_item)
        else:
            item = {
                "path": path_or_item,
                "title": title or "",
                "media_type": media_type or "",
                "duration": float(duration or 0.0),
                "metadata": metadata or {},
            }
            if additional_data:
                item.update(additional_data)

        query = """
        INSERT INTO library_items (path, title, media_type, duration, metadata)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            title = excluded.title,
            media_type = excluded.media_type,
            duration = excluded.duration,
            metadata = excluded.metadata
        """
        try:
            result = self.db_core._execute_write(
                query,
                (
                    item["path"],
                    item["title"],
                    item["media_type"],
                    item["duration"],
                    json.dumps(item["metadata"]) if "metadata" in item else None,
                ),
            )
        except IntegrityError as error:
            raise IntegrityError(
                "Library item violates a database integrity constraint.",
                details=str(error),
            ) from error
        except DatabaseError as error:
            raise DatabaseError(
                "Error adding library item.", details=str(error)
            ) from error
        if result.rowcount != 1:
            raise DatabaseError(
                "Library upsert affected an unexpected number of rows.",
                details=(
                    f"expected=1; actual={result.rowcount}; "
                    f"path={item.get('path')!r}"
                ),
            )
        logger.info("Library item added: %s", item["title"])

    def get_library_item(self, file_path: str) -> Optional[dict[str, object]]:
        query = """
        SELECT id, path, title, media_type, duration, metadata, timestamp
        FROM library_items
        WHERE path = ?
        """
        row = self.db_core._execute_query(query, (file_path,), fetch_one=True)
        if not row:
            return None

        item = dict(row)
        try:
            item["metadata"] = json.loads(item["metadata"]) if item["metadata"] else {}
        except json.JSONDecodeError:
            logger.error("Invalid JSON in library metadata")
            item["metadata"] = {}
        return item

    def get_all_library_items(self) -> List[dict[str, object]]:
        query = (
            "SELECT id, path, title, media_type, duration, metadata, timestamp "
            "FROM library_items"
        )
        rows = self.db_core._execute_query(query, fetch_all=True)
        items = []
        if rows:
            for row in rows:
                item = dict(row)
                try:
                    item["metadata"] = (
                        json.loads(item["metadata"]) if item["metadata"] else {}
                    )
                except json.JSONDecodeError:
                    logger.error("Invalid JSON in library metadata")
                    item["metadata"] = {}
                items.append(item)
        return items

    def remove_library_item(self, file_path: str) -> None:
        """Delete exactly one mirrored library row.

        Edge cases:
            1. The requested path does not exist.
            2. A concurrent delete removes the row before this statement runs.
            3. Schema drift permits an impossible multi-row delete.
        """
        query = "DELETE FROM library_items WHERE path = ?"
        try:
            result = self.db_core._execute_write(query, (file_path,))
        except IntegrityError as error:
            raise IntegrityError(
                "Library delete violates a database integrity constraint.",
                details=str(error),
            ) from error
        except DatabaseError as error:
            raise DatabaseError(
                "Error removing library item.", details=str(error)
            ) from error
        if result.rowcount == 0:
            raise NotFoundError(f"Library item not found: {file_path}")
        if result.rowcount != 1:
            raise DatabaseError(
                "Library delete affected an unexpected number of rows.",
                details=f"expected=1; actual={result.rowcount}; path={file_path}",
            )
        logger.info("Library item removed: %s", file_path)

    def update_library_item(
        self, file_path: str, updates: Mapping[str, object]
    ) -> None:
        """Update one existing row without using an upsert resurrection path.

        Edge cases:
            1. The row disappears after the initial read and before the update.
            2. A path rename collides with another unique path.
            3. Schema drift permits an impossible multi-row update.
        """
        existing = self.get_library_item(file_path)
        if not existing:
            raise NotFoundError(f"Library item not found: {file_path}")

        merged = dict(existing)
        merged.update(updates or {})
        normalized_title = merged["title"] or ""
        normalized_media_type = merged["media_type"] or ""
        normalized_duration = float(merged["duration"] or 0.0)
        normalized_metadata = merged.get("metadata") or {}
        query = """
        UPDATE library_items
        SET path = ?, title = ?, media_type = ?, duration = ?, metadata = ?
        WHERE path = ?
        """
        try:
            result = self.db_core._execute_write(
                query,
                (
                    merged["path"],
                    normalized_title,
                    normalized_media_type,
                    normalized_duration,
                    json.dumps(normalized_metadata),
                    file_path,
                ),
            )
        except IntegrityError as error:
            raise IntegrityError(
                "Library update violates a database integrity constraint.",
                details=str(error),
            ) from error
        except DatabaseError as error:
            raise DatabaseError(
                "Error updating library item.", details=str(error)
            ) from error
        if result.rowcount == 0:
            raise NotFoundError(f"Library item not found: {file_path}")
        if result.rowcount != 1:
            raise DatabaseError(
                "Library update affected an unexpected number of rows.",
                details=f"expected=1; actual={result.rowcount}; path={file_path}",
            )
        logger.info("Library item updated: %s", file_path)
