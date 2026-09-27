from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from collections.abc import Mapping
from typing import List, Optional

from src.model.component_database.db_core import DbCore
from src.utils.exceptions import DatabaseError, IntegrityError, NotFoundError

logger = logging.getLogger(__name__)

FAVORITES_PATH_EXCEPTIONS = (AttributeError, OSError, TypeError, ValueError)
_PATH_MATCH_TERM = (
    "LOWER(REPLACE(path, '\\', '/')) = "
    "LOWER(REPLACE(?, '\\', '/'))"
)


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

        where_clause = " OR ".join([_PATH_MATCH_TERM] * len(candidates))
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
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        """Insert one favorite and verify the exact write result.

        Edge cases:
            1. A path-equivalent favorite already exists before the write.
            2. SQLite rejects the insert because of a concurrent unique conflict.
            3. A write reports an unexpected zero or multi-row result.
        """
        path = _normalize_storage_path(path)
        candidates = _build_path_candidates(path)
        timestamp = datetime.now().isoformat()
        values = (
            path,
            title,
            media_type,
            duration,
            json.dumps(metadata) if metadata else None,
            timestamp,
        )
        if candidates:
            where_clause = " OR ".join([_PATH_MATCH_TERM] * len(candidates))
            query = (
                "INSERT INTO favorites "
                "(path, title, media_type, duration, metadata, added_at) "
                "SELECT ?, ?, ?, ?, ?, ? WHERE NOT EXISTS "
                f"(SELECT 1 FROM favorites WHERE {where_clause})"
            )
            params = values + tuple(candidates)
        else:
            query = (
                "INSERT INTO favorites "
                "(path, title, media_type, duration, metadata, added_at) "
                "VALUES (?, ?, ?, ?, ?, ?)"
            )
            params = values
        try:
            result = self.db_core._execute_write(query, params)
        except IntegrityError as error:
            raise IntegrityError(
                "Favorite insert violates a database integrity constraint.",
                details=str(error),
            ) from error
        except DatabaseError as error:
            raise DatabaseError(
                f"Error adding favorite: {title}", details=str(error)
            ) from error
        if result.rowcount == 0:
            raise IntegrityError(f"Favorite already exists: {path}")
        if result.rowcount != 1:
            raise DatabaseError(
                "Favorite insert affected an unexpected number of rows.",
                details=f"expected=1; actual={result.rowcount}; path={path}",
            )
        logger.info("Favorite added: %s", title)

    def remove_favorite(self, path: str) -> None:
        """Delete all known path variants using the direct cursor rowcount.

        Edge cases:
            1. No equivalent stored path exists.
            2. Another writer removes every candidate before this delete commits.
            3. Legacy separator or case variants require deleting multiple rows.
        """
        candidates = _build_path_candidates(path)
        if not candidates:
            raise NotFoundError(f"Favorite not found: {path}")
        where_clause = " OR ".join([_PATH_MATCH_TERM] * len(candidates))
        query = f"DELETE FROM favorites WHERE {where_clause}"
        try:
            result = self.db_core._execute_write(query, tuple(candidates))
        except IntegrityError as error:
            raise IntegrityError(
                "Favorite delete violates a database integrity constraint.",
                details=str(error),
            ) from error
        except DatabaseError as error:
            raise DatabaseError(
                f"Error removing favorite: {path}", details=str(error)
            ) from error
        if result.rowcount == 0:
            raise NotFoundError(f"Favorite not found: {path}")
        logger.info("Favorite removed: %s (%s row(s))", path, result.rowcount)

    def get_favorites(self) -> List[dict[str, object]]:
        query = (
            "SELECT id, path, title, media_type, duration, metadata, added_at "
            "FROM favorites ORDER BY added_at DESC"
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
                    logger.error("Invalid JSON in favorite metadata")
                    item["metadata"] = {}
                items.append(item)
        return items

    def get_all_favorite_items(self) -> List[dict[str, object]]:
        return self.get_favorites()

    def is_favorite(self, path: str) -> bool:
        return bool(self._find_matching_paths(path))
