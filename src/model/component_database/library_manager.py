from __future__ import annotations
import json
import logging
from typing import List, Dict, Any, Optional

from src.model.component_database.db_core import DbCore
from src.utils.exceptions import IntegrityError, NotFoundError, DatabaseError

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
        metadata: Optional[Dict[str, Any]] = None,
        additional_data: Optional[Dict[str, Any]] = None,
    ):
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
            self.db_core._execute_query(
                query,
                (
                    item["path"],
                    item["title"],
                    item["media_type"],
                    item["duration"],
                    json.dumps(item["metadata"]) if "metadata" in item else None,
                ),
            )
            logger.info(f"Library item added: {item['title']}")
        except DatabaseError as e:
            raise DatabaseError(f"Error adding library item: {str(e)}")

    def get_library_item(self, file_path: str) -> Optional[Dict[str, Any]]:
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

    def get_all_library_items(self) -> List[Dict[str, Any]]:
        query = "SELECT id, path, title, media_type, duration, metadata, timestamp FROM library_items"
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

    def remove_library_item(self, file_path: str):
        query = "DELETE FROM library_items WHERE path = ?"
        try:
            self.db_core._execute_query(query, (file_path,))
            if self.db_core._last_changes() > 0:
                logger.info(f"Library item removed: {file_path}")
            else:
                raise NotFoundError(f"Library item not found: {file_path}")
        except DatabaseError as e:
            raise DatabaseError(f"Error removing library item: {str(e)}")

    def update_library_item(self, file_path: str, updates: Dict[str, Any]):
        existing = self.get_library_item(file_path)
        if not existing:
            raise NotFoundError(f"Library item not found: {file_path}")

        merged = dict(existing)
        merged.update(updates or {})
        self.add_library_item(
            merged["path"],
            merged["title"],
            merged["media_type"],
            merged["duration"],
            merged.get("metadata"),
        )
