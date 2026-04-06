from __future__ import annotations
import json
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from src.model.component_database.db_core import DbCore
from src.utils.exceptions import DatabaseError

logger = logging.getLogger(__name__)


class HistoryManager:
    def __init__(self, db_core: DbCore):
        self.db_core = db_core

    def add_history_item(
        self,
        title: str,
        path: str,
        media_type: str,
        duration: float,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        query = """
        INSERT INTO history (title, path, media_type, duration, metadata, timestamp)
        VALUES (?, ?, ?, ?, ?, ?)
        """
        timestamp = datetime.now().isoformat()
        try:
            self.db_core._execute_query(
                query,
                (
                    title,
                    path,
                    media_type,
                    duration,
                    json.dumps(metadata) if metadata else None,
                    timestamp,
                ),
            )
            logger.info(f"History item added: {title} ({path})")
        except DatabaseError as e:
            raise DatabaseError(f"Error adding history item: {title}, error: {e}")

    def get_history(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        query = """SELECT id, title, path, media_type, duration, metadata, timestamp FROM history ORDER BY timestamp DESC"""
        params = None
        if limit:
            query += " LIMIT ?"
            params = (limit,)

        rows = self.db_core._execute_query(query, params, fetch_all=True)
        items = []
        if rows:
            for row in rows:
                item = dict(row)
                try:
                    item["metadata"] = (
                        json.loads(item["metadata"]) if item["metadata"] else {}
                    )
                except json.JSONDecodeError:
                    logger.error("Invalid JSON in history metadata")
                    item["metadata"] = {}
                items.append(item)
        return items

    def get_completed_downloads(self) -> List[Dict[str, Any]]:
        """
        This is a placeholder. Currently returns all history items.
        """
        return self.get_history()

    def clear_history(self):
        query = """DELETE FROM history"""
        try:
            self.db_core._execute_query(query)
            logger.info("History cleared")
        except DatabaseError as e:
            raise DatabaseError(f"Error clearing history: {e}")
