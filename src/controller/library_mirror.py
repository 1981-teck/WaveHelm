"""Derived SQLite mirror synchronization for the canonical media library.

The mirror never controls canonical state. Failures are contained at the database
boundary and returned as typed degraded evidence, so callers do not roll back an
already committed ``library.json`` file.

Edge cases handled:
- failed inventory queries still allow safe idempotent upserts;
- malformed rows never authorize deletion of unknown records;
- missing rows during deletion are already consistent;
- unresolved canonical paths remain protected from stale-row removal;
- database methods returning explicit ``False`` are treated as refusal.

Complexity: delta synchronization is O(A + R); full reconciliation is O(C + D),
where C is the resolved/canonical library size and D is the mirror inventory size.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
import os
from typing import Protocol

from src.controller.library_catalog_store import CatalogCommitStatus
from src.model.media_file import MediaFile
from src.utils.exceptions import DatabaseError, NotFoundError

_MAX_ERROR_TEXT = 320
_MAX_FAILURE_RECORDS = 128


class LibraryDatabaseGateway(Protocol):
    """Minimal database facade used by the derived library mirror."""

    def add_library_item(
        self,
        *,
        path: str,
        title: str,
        media_type: str,
        duration: float,
        metadata: dict[str, object],
    ) -> object: ...

    def remove_library_item(self, path: str) -> object: ...

    def get_all_library_items(self) -> list[dict[str, object]]: ...


class LibraryMirrorStatus(StrEnum):
    """Describe whether SQLite matches the authoritative library state."""

    SYNCHRONIZED = "synchronized"
    DEGRADED = "degraded"


@dataclass(frozen=True, slots=True)
class LibraryMirrorFailure:
    """One bounded failure observed at the database mirror boundary."""

    operation: str
    path: str | None
    error_type: str
    message: str


@dataclass(frozen=True, slots=True)
class LibraryMirrorResult:
    """Outcome of a delta or full mirror reconciliation attempt."""

    status: LibraryMirrorStatus
    upserted_count: int
    removed_count: int
    failures: tuple[LibraryMirrorFailure, ...]

    @property
    def is_synchronized(self) -> bool:
        """Return whether every requested mirror operation succeeded."""
        return self.status is LibraryMirrorStatus.SYNCHRONIZED


@dataclass(frozen=True, slots=True)
class LibraryMutationResult:
    """Observable result of one canonical library mutation or save."""

    catalog_status: CatalogCommitStatus
    mirror_result: LibraryMirrorResult
    added_count: int
    removed_count: int
    total_catalog_paths: int
    resolved_media_count: int
    revision: int

    @property
    def is_fully_synchronized(self) -> bool:
        """Return whether durability and the derived mirror are both healthy."""
        return self.catalog_status.is_fully_durable and self.mirror_result.is_synchronized


def synchronize_library_mirror(
    database: LibraryDatabaseGateway,
    *,
    catalog_paths: Sequence[str],
    resolved_media: Sequence[MediaFile],
    upsert_media: Sequence[MediaFile],
    remove_paths: Sequence[str],
    canonicalize: Callable[[str], str],
    full_reconcile: bool,
    allow_stale_removal: bool,
) -> LibraryMirrorResult:
    """Synchronize SQLite without mutating or invalidating canonical state.

    Edge cases:
        1. Inventory failure must not suppress safe idempotent upserts.
        2. An untrusted catalog must never authorize stale-row deletion.
        3. A missing row during deletion is already consistent.
        4. Malformed rows remain explicit degraded evidence.
    """
    failures: list[LibraryMirrorFailure] = []
    existing_paths: tuple[str, ...] | None = None
    if full_reconcile:
        existing_paths = _read_existing_paths(database, failures)
        if not allow_stale_removal:
            error = RuntimeError(
                "stale-row removal disabled because the canonical catalog is untrusted"
            )
            _record_failure(
                failures, build_library_mirror_failure("stale_removal", None, error)
            )

    selected_media = resolved_media if full_reconcile else upsert_media
    upserted = _upsert_media(database, selected_media, failures)
    removal_candidates = list(remove_paths)
    removal_seen = set(removal_candidates)
    if full_reconcile and allow_stale_removal and existing_paths is not None:
        allowed = tuple(catalog_paths) + tuple(media.path for media in resolved_media)
        for stale_path in _find_stale_paths(existing_paths, allowed, canonicalize):
            if stale_path not in removal_seen:
                removal_seen.add(stale_path)
                removal_candidates.append(stale_path)
    removed = _remove_paths(database, removal_candidates, failures)

    status = LibraryMirrorStatus.SYNCHRONIZED if not failures else LibraryMirrorStatus.DEGRADED
    return LibraryMirrorResult(status, upserted, removed, tuple(failures))


def _read_existing_paths(
    database: LibraryDatabaseGateway,
    failures: list[LibraryMirrorFailure],
) -> tuple[str, ...] | None:
    try:
        rows = database.get_all_library_items()
    except Exception as error:  # explicit external database inventory boundary
        _record_failure(failures, build_library_mirror_failure("inventory", None, error))
        return None
    if not isinstance(rows, list):
        _record_failure(
            failures,
            build_library_mirror_failure("inventory", None, TypeError("inventory must be a list")),
        )
        return None

    paths: list[str] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            error = TypeError(f"database library row {index} must be a mapping")
            _record_failure(
                failures, build_library_mirror_failure("inventory_row", None, error)
            )
            continue
        path = row.get("path")
        if not isinstance(path, str) or not path:
            error = ValueError(f"database library row {index} has no valid path")
            _record_failure(
                failures, build_library_mirror_failure("inventory_row", None, error)
            )
            continue
        paths.append(path)
    return tuple(paths)


def _upsert_media(
    database: LibraryDatabaseGateway,
    media_records: Sequence[MediaFile],
    failures: list[LibraryMirrorFailure],
) -> int:
    count = 0
    for media in media_records:
        try:
            result = database.add_library_item(
                path=media.path,
                title=media.title or os.path.basename(media.path),
                media_type=media.media_type.name,
                duration=float(media.duration),
                metadata=media.metadata,
            )
            if result is False:
                raise DatabaseError("database rejected library upsert")
        except Exception as error:  # explicit external database upsert boundary
            _record_failure(
                failures, build_library_mirror_failure("upsert", media.path, error)
            )
            continue
        count += 1
    return count


def _remove_paths(
    database: LibraryDatabaseGateway,
    paths: Sequence[str],
    failures: list[LibraryMirrorFailure],
) -> int:
    count = 0
    for path in paths:
        try:
            result = database.remove_library_item(path)
            if result is False:
                raise DatabaseError("database rejected library removal")
        except NotFoundError:
            count += 1
            continue
        except Exception as error:  # explicit external database removal boundary
            _record_failure(
                failures, build_library_mirror_failure("remove", path, error)
            )
            continue
        count += 1
    return count


def _find_stale_paths(
    existing_paths: Sequence[str],
    allowed_paths: Sequence[str],
    canonicalize: Callable[[str], str],
) -> tuple[str, ...]:
    allowed_keys = {canonicalize(path) for path in allowed_paths}
    stale: list[str] = []
    seen: set[str] = set()
    for path in existing_paths:
        key = canonicalize(path)
        if key not in allowed_keys and path not in seen:
            seen.add(path)
            stale.append(path)
    return tuple(stale)


def _record_failure(
    failures: list[LibraryMirrorFailure], failure: LibraryMirrorFailure
) -> None:
    if len(failures) < _MAX_FAILURE_RECORDS:
        failures.append(failure)
    elif len(failures) == _MAX_FAILURE_RECORDS:
        failures.append(
            LibraryMirrorFailure(
                "failure_limit",
                None,
                "OverflowError",
                "Additional database mirror failures were omitted.",
            )
        )


def build_library_mirror_failure(
    operation: str, path: str | None, error: BaseException
) -> LibraryMirrorFailure:
    """Render external failures without allowing hostile ``__str__`` methods to escape."""
    error_type = type(error).__name__
    try:
        detail = str(error)
    except Exception as rendering_error:  # explicit external error-rendering boundary
        detail = f"<unprintable; rendering raised {type(rendering_error).__name__}>"
    text = f"{error_type}: {detail}"
    if len(text) > _MAX_ERROR_TEXT:
        text = text[: _MAX_ERROR_TEXT - 3] + "..."
    return LibraryMirrorFailure(operation, path, error_type, text)


__all__ = [
    "LibraryDatabaseGateway",
    "build_library_mirror_failure",
    "LibraryMirrorFailure",
    "LibraryMirrorResult",
    "LibraryMirrorStatus",
    "LibraryMutationResult",
    "synchronize_library_mirror",
]
