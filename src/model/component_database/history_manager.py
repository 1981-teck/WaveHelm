from __future__ import annotations

from collections.abc import Mapping

from src.model.component_database.db_core import DbCore
from src.model.component_database.db_primitives import SqlExecutor
from src.model.component_database.history_contract import (
    DEFAULT_HISTORY_QUERY_LIMIT,
    DEFAULT_HISTORY_RETENTION,
    HistoryCapabilityError,
    HistoryRecord,
    decode_history_row,
    prepare_history_entry,
    validate_history_limit,
    validate_history_retention,
)
from src.utils.exceptions import DatabaseError, IntegrityError


class HistoryManager:
    """Persist and query bounded playback history through typed DB primitives."""

    def __init__(
        self,
        db_core: DbCore,
        *,
        max_entries: int = DEFAULT_HISTORY_RETENTION,
    ) -> None:
        self.db_core = db_core
        self._max_entries = validate_history_retention(max_entries)

    def add_history_entry(
        self,
        path: str,
        title: str,
        media_type: str,
        duration: float,
        metadata: Mapping[str, object] | None = None,
        additional_data: Mapping[str, object] | None = None,
    ) -> int:
        """Insert one playback entry and enforce retention in the same transaction.

        Edge cases:
            1. Invalid metadata fails before the database transaction starts.
            2. Insert or retention failures roll back the complete mutation.
            3. Concurrent writers retain at most the configured newest entries.
        """
        prepared = prepare_history_entry(
            path=path,
            title=title,
            media_type=media_type,
            duration=duration,
            metadata=metadata,
            additional_data=additional_data,
        )
        try:
            with self.db_core.shared_transaction(mode="IMMEDIATE") as transaction:
                cursor = transaction.execute(
                    "INSERT INTO history "
                    "(title, path, media_type, duration, metadata, timestamp) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        prepared.title,
                        prepared.path,
                        prepared.media_type,
                        prepared.duration,
                        prepared.metadata_json,
                        prepared.timestamp,
                    ),
                )
                if cursor.rowcount != 1 or cursor.lastrowid is None:
                    raise DatabaseError(
                        "History insert returned invalid write metadata.",
                        details=(
                            f"rowcount={cursor.rowcount}; "
                            f"lastrowid={cursor.lastrowid!r}"
                        ),
                    )
                record_id = cursor.lastrowid
                self._enforce_retention(transaction)
        except IntegrityError as error:
            raise IntegrityError(
                "History entry violates a database integrity constraint.",
                details=str(error),
            ) from error
        except DatabaseError as error:
            raise DatabaseError(
                "Error adding history entry.", details=str(error)
            ) from error
        return record_id

    def add_history_item(
        self,
        title: str,
        path: str,
        media_type: str,
        duration: float,
        metadata: Mapping[str, object] | None = None,
    ) -> int:
        """Preserve the legacy title/path order while delegating by keyword."""
        return self.add_history_entry(
            path=path,
            title=title,
            media_type=media_type,
            duration=duration,
            metadata=metadata,
        )

    def get_history(
        self,
        limit: int | None = DEFAULT_HISTORY_QUERY_LIMIT,
    ) -> list[HistoryRecord]:
        """Return newest entries first with a stable, bounded query contract.

        Edge cases:
            1. Zero, negative, boolean, or oversized limits are rejected.
            2. ``None`` resolves to the documented bounded default.
            3. Corrupt persisted metadata raises instead of becoming an empty object.
        """
        normalized_limit = validate_history_limit(limit)
        rows = self.db_core._execute_query(
            "SELECT id, title, path, media_type, duration, metadata, timestamp "
            "FROM history ORDER BY id DESC LIMIT ?",
            (normalized_limit,),
            fetch_all=True,
        )
        if rows is None:
            return []
        return [decode_history_row(dict(row)) for row in rows]

    def get_completed_downloads(self) -> list[HistoryRecord]:
        """Reject an unsupported capability instead of returning playback history."""
        raise HistoryCapabilityError(
            "Completed-download history is unavailable because the core history "
            "schema stores playback entries without download status."
        )

    def clear_history(self) -> int:
        """Delete all history rows and return the exact affected-row count."""
        try:
            result = self.db_core._execute_write("DELETE FROM history")
        except IntegrityError as error:
            raise IntegrityError(
                "History clear violates a database integrity constraint.",
                details=str(error),
            ) from error
        except DatabaseError as error:
            raise DatabaseError("Error clearing history.", details=str(error)) from error
        if result.rowcount < 0:
            raise DatabaseError(
                "History clear returned invalid write metadata.",
                details=f"rowcount={result.rowcount}",
            )
        return result.rowcount

    def _enforce_retention(self, transaction: SqlExecutor) -> None:
        """Keep only the newest configured IDs inside the owning transaction."""
        cursor = transaction.execute(
            "DELETE FROM history WHERE id < COALESCE("
            "(SELECT id FROM history ORDER BY id DESC LIMIT 1 OFFSET ?), 0)",
            (self._max_entries - 1,),
        )
        if cursor.rowcount < 0:
            raise DatabaseError(
                "History retention returned invalid write metadata.",
                details=f"rowcount={cursor.rowcount}",
            )
        count_row = transaction.execute("SELECT COUNT(*) FROM history").fetchone()
        if count_row is None or len(count_row) != 1:
            raise DatabaseError("History retention count could not be verified.")
        count = count_row[0]
        if isinstance(count, bool) or not isinstance(count, int):
            raise DatabaseError("History retention count has an invalid type.")
        if count < 0 or count > self._max_entries:
            raise DatabaseError(
                "History retention invariant was not satisfied.",
                details=f"count={count}; max={self._max_entries}",
            )
