from __future__ import annotations
import json
import logging
import os
from typing import List, Dict, Any, Optional
from datetime import datetime

from src.model.component_database.db_core import DbCore
from src.utils.exceptions import IntegrityError, NotFoundError, DatabaseError

logger = logging.getLogger(__name__)

FAVORITES_PATH_EXCEPTIONS = (AttributeError, OSError, TypeError, ValueError)


def _normalize_storage_path(path: str) -> str:
    try:
        if path and not path.startswith(("http://", "https://", "rtsp://", "rtmp://")):
            return os.path.abspath(os.path.normpath(path.strip()))
    except FAVORITES_PATH_EXCEPTIONS as error:
        logger.debug(
            "Failed to normalize favorite storage path %r: %s",
            path,
            error,
            exc_info=True,
        )
    try:
        return str(path or "").strip()
    except FAVORITES_PATH_EXCEPTIONS:
        return ""


def _build_path_candidates(path: str) -> List[str]:
    raw = (path or "").strip()
    normalized = _normalize_storage_path(raw)
    candidates: List[str] = []
    seen: set[str] = set()

    def _add(candidate: str) -> None:
        if candidate and candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)

    _add(raw)
    _add(normalized)
    if normalized:
        _add(normalized.replace("/", "\\"))
        _add(normalized.replace("\\", "/"))
    if raw:
        _add(raw.replace("/", "\\"))
        _add(raw.replace("\\", "/"))
    return candidates


class FavoritesManager:
    def __init__(self, db_core: DbCore):
        self.db_core = db_core

    def _find_matching_paths(self, path: str) -> List[str]:
        candidates = _build_path_candidates(path)
        if not candidates:
            return []

        where_clause = " OR ".join(["LOWER(path) = LOWER(?)"] * len(candidates))
        rows = self.db_core._execute_query(
            f"SELECT path FROM favorites WHERE {where_clause}",
            tuple(candidates),
            fetch_all=True,
        )
        return [str(row["path"]) for row in rows] if rows else []

    def add_favorite(
        self,
        path: str,
        title: str,
        media_type: str,
        duration: float,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        path = _normalize_storage_path(path)
        if self._find_matching_paths(path):
            raise IntegrityError(f"Favorite already exists: {path}")
        query = """INSERT INTO favorites (path, title, media_type, duration, metadata, added_at) VALUES (?, ?, ?, ?, ?, ?)"""
        timestamp = datetime.now().isoformat()
        try:
            self.db_core._execute_query(
                query,
                (
                    path,
                    title,
                    media_type,
                    duration,
                    json.dumps(metadata) if metadata else None,
                    timestamp,
                ),
            )
            logger.info(f"Favorite added: {title}")
        except IntegrityError:
            raise IntegrityError(f"Favorite already exists: {path}")
        except DatabaseError as e:
            raise DatabaseError(f"Error adding favorite: {title}, error: {e}")

    def remove_favorite(self, path: str):
        matching_paths = self._find_matching_paths(path)
        if not matching_paths:
            raise NotFoundError(f"Favorite not found: {path}")

        placeholders = ", ".join(["?"] * len(matching_paths))
        query = f"DELETE FROM favorites WHERE path IN ({placeholders})"
        try:
            self.db_core._execute_query(query, tuple(matching_paths))
            if self.db_core._last_changes() > 0:
                logger.info(
                    "Favorite removed: %s (%s row(s))", path, len(matching_paths)
                )
            else:
                raise NotFoundError(f"Favorite not found: {path}")
        except DatabaseError as e:
            raise DatabaseError(f"Error removing favorite: {path}, error: {e}")

    def get_favorites(self) -> List[Dict[str, Any]]:
        query = """SELECT id, path, title, media_type, duration, metadata, added_at FROM favorites ORDER BY added_at DESC"""
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
                    logger.error("Invalid JSON in favorite metadata")
                    item["metadata"] = {}
                items.append(item)
        return items

    def get_all_favorite_items(self) -> List[Dict[str, Any]]:
        return self.get_favorites()

    def is_favorite(self, path: str) -> bool:
        return bool(self._find_matching_paths(path))
