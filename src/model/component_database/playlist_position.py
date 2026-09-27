from __future__ import annotations

from dataclasses import dataclass
import sqlite3

from src.model.component_database.db_primitives import SqlExecutor
from src.utils.exceptions import DatabaseError


MAX_PLAYLIST_ITEMS_PER_PLAYLIST = 100_000
MAX_PLAYLIST_ITEMS_PER_DATABASE = 1_000_000
MAX_PLAYLIST_MEDIA_PATH_CHARS = 32_768
MAX_PLAYLIST_ADDED_AT_CHARS = 128
PLAYLIST_POSITION_INDEX = "ux_playlist_items_position"


class PlaylistPositionInvariantError(DatabaseError):
    """Raised when a runtime playlist sequence is not exactly contiguous 1..N."""


@dataclass(frozen=True, slots=True)
class PlaylistPositionState:
    """Validated count and bounds for one persisted playlist sequence."""

    item_count: int
    minimum: int | None
    maximum: int | None


def _row_values(row: object, label: str) -> tuple[object, ...]:
    if isinstance(row, sqlite3.Row):
        return tuple(row)
    if isinstance(row, tuple):
        return row
    raise PlaylistPositionInvariantError(f"{label} returned a malformed row.")


def _read_non_negative_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PlaylistPositionInvariantError(f"{label} is not a non-negative integer.")
    return value


def validate_playlist_id(playlist_id: int) -> int:
    if isinstance(playlist_id, bool) or not isinstance(playlist_id, int):
        raise TypeError("Playlist ID must be an integer")
    if playlist_id < 1:
        raise ValueError("Playlist ID must be positive")
    return playlist_id


def validate_insert_position(position: int | None, item_count: int) -> int:
    """Return an exact insertion point in 1..N+1 without coercion or clamping.

    Edge cases:
        1. ``bool`` and numeric strings are rejected instead of coerced to positions.
        2. Zero, negative, and sparse positions fail before any shift statement.
        3. Appending beyond the configured per-playlist capacity fails closed.
    """
    if item_count >= MAX_PLAYLIST_ITEMS_PER_PLAYLIST:
        raise PlaylistPositionInvariantError("Playlist item capacity has been reached.")
    if position is None:
        return item_count + 1
    if isinstance(position, bool) or not isinstance(position, int):
        raise TypeError("Playlist item position must be an integer")
    if not 1 <= position <= item_count + 1:
        raise ValueError("Playlist item position must be within 1..N+1")
    return position


def validate_reorder_position(position: int, item_count: int) -> int:
    """Return an exact reorder target in 1..N without coercion or clamping."""
    if isinstance(position, bool) or not isinstance(position, int):
        raise TypeError("Playlist item position must be an integer")
    if not 1 <= position <= item_count:
        raise ValueError("Playlist item position must be within 1..N")
    return position


def validate_playlist_database_capacity(connection: SqlExecutor) -> int:
    """Return the bounded global item count or reject a saturated database."""
    row = connection.execute("SELECT COUNT(*) FROM playlist_items").fetchone()
    values = _row_values(row, "playlist item database count") if row is not None else ()
    if len(values) != 1:
        raise PlaylistPositionInvariantError("Playlist item database count is malformed.")
    count = _read_non_negative_integer(values[0], "playlist item database count")
    if count >= MAX_PLAYLIST_ITEMS_PER_DATABASE:
        raise PlaylistPositionInvariantError("Playlist item database capacity has been reached.")
    return count


def validate_playlist_position_sequence(
    connection: SqlExecutor, playlist_id: int
) -> PlaylistPositionState:
    """Validate one sequence using bounded aggregate invariants.

    Edge cases:
        1. Empty playlists are valid and report no minimum or maximum.
        2. Sparse, duplicate, zero, negative, or non-integer positions fail closed.
        3. Oversized playlists fail before any O(N) position-shift operation.
    """
    playlist_id = validate_playlist_id(playlist_id)
    row = connection.execute(
        "SELECT COUNT(*), MIN(position), MAX(position), "
        "COALESCE(SUM(CASE WHEN typeof(position) = 'integer' THEN 1 ELSE 0 END), 0), "
        "COUNT(DISTINCT position) FROM playlist_items WHERE playlist_id = ?",
        (playlist_id,),
    ).fetchone()
    values = _row_values(row, "playlist position summary") if row is not None else ()
    if len(values) != 5:
        raise PlaylistPositionInvariantError("Playlist position summary is malformed.")
    count = _read_non_negative_integer(values[0], "playlist item count")
    typed_count = _read_non_negative_integer(values[3], "typed playlist item count")
    distinct_count = _read_non_negative_integer(values[4], "distinct position count")
    if count > MAX_PLAYLIST_ITEMS_PER_PLAYLIST:
        raise PlaylistPositionInvariantError("Playlist item count exceeds its hard cap.")
    if count == 0:
        if (
            values[1] is not None
            or values[2] is not None
            or typed_count != 0
            or distinct_count != 0
        ):
            raise PlaylistPositionInvariantError("Empty playlist position bounds are invalid.")
        return PlaylistPositionState(0, None, None)
    minimum, maximum = values[1], values[2]
    valid_bounds = (
        not isinstance(minimum, bool)
        and isinstance(minimum, int)
        and not isinstance(maximum, bool)
        and isinstance(maximum, int)
    )
    if (
        not valid_bounds
        or typed_count != count
        or distinct_count != count
        or minimum != 1
        or maximum != count
    ):
        raise PlaylistPositionInvariantError("Playlist positions are not contiguous 1..N.")
    return PlaylistPositionState(count, minimum, maximum)


def _expect_rowcount(cursor: sqlite3.Cursor, expected: int, label: str) -> None:
    if cursor.rowcount != expected:
        raise PlaylistPositionInvariantError(
            f"{label} affected {cursor.rowcount} rows; expected {expected}."
        )


def _temporary_offset(item_count: int) -> int:
    if not 0 <= item_count <= MAX_PLAYLIST_ITEMS_PER_PLAYLIST:
        raise PlaylistPositionInvariantError("Playlist item count is outside its hard cap.")
    return item_count + 1


def prepare_playlist_item_insert(
    connection: SqlExecutor, playlist_id: int, requested_position: int | None
) -> int:
    """Open one exact position while preserving the unique position index."""
    validate_playlist_database_capacity(connection)
    state = validate_playlist_position_sequence(connection, playlist_id)
    target = validate_insert_position(requested_position, state.item_count)
    shifted = state.item_count - target + 1
    if shifted <= 0:
        return target
    offset = _temporary_offset(state.item_count)
    first = connection.execute(
        "UPDATE playlist_items SET position = position + ? "
        "WHERE playlist_id = ? AND position >= ?",
        (offset, playlist_id, target),
    )
    _expect_rowcount(first, shifted, "temporary insertion shift")
    second = connection.execute(
        "UPDATE playlist_items SET position = position - ? + 1 "
        "WHERE playlist_id = ? AND position >= ?",
        (offset, playlist_id, target + offset),
    )
    _expect_rowcount(second, shifted, "final insertion shift")
    return target


def compact_playlist_after_delete(
    connection: SqlExecutor,
    playlist_id: int,
    removed_position: int,
    previous_count: int,
) -> None:
    """Close one deleted position without transient unique-index collisions."""
    if not 1 <= removed_position <= previous_count:
        raise PlaylistPositionInvariantError("Removed playlist position is invalid.")
    shifted = previous_count - removed_position
    if shifted <= 0:
        validate_playlist_position_sequence(connection, playlist_id)
        return
    offset = _temporary_offset(previous_count)
    first = connection.execute(
        "UPDATE playlist_items SET position = position + ? "
        "WHERE playlist_id = ? AND position > ?",
        (offset, playlist_id, removed_position),
    )
    _expect_rowcount(first, shifted, "temporary delete compaction")
    second = connection.execute(
        "UPDATE playlist_items SET position = position - ? - 1 "
        "WHERE playlist_id = ? AND position > ?",
        (offset, playlist_id, removed_position + offset),
    )
    _expect_rowcount(second, shifted, "final delete compaction")
    validate_playlist_position_sequence(connection, playlist_id)


def reorder_playlist_positions(
    connection: SqlExecutor,
    playlist_id: int,
    media_path: str,
    old_position: int,
    requested_position: int,
) -> int:
    """Move one item while every committed state remains exactly 1..N.

    Edge cases:
        1. Moving upward and downward uses temporary disjoint positions.
        2. Rowcount drift aborts before the surrounding transaction can commit.
        3. A no-op target performs no updates but still validates the sequence.
    """
    state = validate_playlist_position_sequence(connection, playlist_id)
    target = validate_reorder_position(requested_position, state.item_count)
    if not 1 <= old_position <= state.item_count:
        raise PlaylistPositionInvariantError("Stored playlist item position is invalid.")
    if old_position == target:
        return target
    lower, upper = sorted((old_position, target))
    offset = _temporary_offset(state.item_count)
    moved = upper - lower + 1
    first = connection.execute(
        "UPDATE playlist_items SET position = position + ? "
        "WHERE playlist_id = ? AND position BETWEEN ? AND ?",
        (offset, playlist_id, lower, upper),
    )
    _expect_rowcount(first, moved, "temporary reorder shift")
    selected = connection.execute(
        "UPDATE playlist_items SET position = ? "
        "WHERE playlist_id = ? AND media_path = ?",
        (target, playlist_id, media_path),
    )
    _expect_rowcount(selected, 1, "reordered playlist item update")
    if target < old_position:
        remaining = connection.execute(
            "UPDATE playlist_items SET position = position - ? + 1 "
            "WHERE playlist_id = ? AND position BETWEEN ? AND ?",
            (offset, playlist_id, target + offset, old_position - 1 + offset),
        )
    else:
        remaining = connection.execute(
            "UPDATE playlist_items SET position = position - ? - 1 "
            "WHERE playlist_id = ? AND position BETWEEN ? AND ?",
            (offset, playlist_id, old_position + 1 + offset, target + offset),
        )
    _expect_rowcount(remaining, moved - 1, "final reorder shift")
    validate_playlist_position_sequence(connection, playlist_id)
    return target
