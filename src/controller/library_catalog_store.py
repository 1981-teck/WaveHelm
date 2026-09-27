"""Fail-closed persistence for the canonical ``library.json`` path catalog.

The store prevents destructive replacement of malformed or externally changed
catalogs. Directory-sync degradation remains distinct from pre-replace failure.

Edge cases handled:
- missing catalogs are valid and writable;
- linked, non-regular, oversized, malformed, and partially invalid catalogs block writes;
- valid entries in a partially invalid catalog remain available for read-only recovery;
- external edits detected before commit fail without replacement;
- invalid UTF-8, control characters, and unbounded path lists are rejected.

Complexity: load, validation, serialization, and conflict checks are O(P), where
P is the catalog payload size.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import os
from pathlib import Path
import stat
from typing import Final

from src.utils.bounded_json import (
    BoundedJsonError,
    JsonLimits,
    parse_json_bytes,
    serialize_json_bytes,
)
from src.utils.durable_io import (
    DurabilityStatus,
    sync_parent_directory,
    write_bytes_atomic_durable,
)

_MAX_CATALOG_BYTES: Final = 16 * 1024 * 1024
_MAX_CATALOG_ENTRIES: Final = 100_000
_MAX_PATH_CHARS: Final = 32_767
_MAX_PATH_BYTES: Final = 131_072
_READ_CHUNK_BYTES: Final = 64 * 1024
_MAX_ERROR_TEXT: Final = 320
_MAX_CATALOG_ISSUES: Final = 64
_LIBRARY_JSON_LIMITS = JsonLimits(
    max_bytes=_MAX_CATALOG_BYTES,
    max_depth=8,
    max_nodes=200_000,
    max_container_items=_MAX_CATALOG_ENTRIES,
    max_key_chars=256,
    max_key_bytes=1_024,
    max_string_chars=_MAX_PATH_CHARS,
    max_string_bytes=_MAX_PATH_BYTES,
    max_total_text_chars=_MAX_CATALOG_BYTES,
    max_total_text_bytes=_MAX_CATALOG_BYTES,
)


class LibraryCatalogError(RuntimeError):
    """Base error for canonical library catalog failures."""


class LibraryCatalogReadError(LibraryCatalogError):
    """Raised when the existing canonical catalog cannot be read safely."""


class LibraryCatalogWriteError(LibraryCatalogError):
    """Raised when a candidate catalog cannot be serialized or persisted."""


class LibraryCatalogWriteBlockedError(LibraryCatalogWriteError):
    """Raised when a malformed existing catalog forbids destructive replacement."""


class LibraryCatalogConflictError(LibraryCatalogWriteError):
    """Raised when another writer changed the catalog after it was loaded."""


class CatalogCommitStatus(StrEnum):
    """Describe the canonical file outcome for one controller mutation."""

    UNCHANGED = "unchanged"
    DURABLE = "durable"
    COMMITTED_WITHOUT_DIRECTORY_SYNC = "committed_without_directory_sync"

    @classmethod
    def from_durability(cls, status: DurabilityStatus) -> CatalogCommitStatus:
        """Convert the shared atomic-writer result without losing semantics."""
        if status is DurabilityStatus.DURABLE:
            return cls.DURABLE
        return cls.COMMITTED_WITHOUT_DIRECTORY_SYNC

    @property
    def is_fully_durable(self) -> bool:
        """Return whether the catalog was unchanged or durably committed."""
        return self in {self.UNCHANGED, self.DURABLE}


@dataclass(frozen=True, slots=True)
class LibraryCatalogLoadResult:
    """Validated catalog paths plus any condition that blocks future writes."""

    paths: tuple[str, ...]
    write_blocked: bool
    issues: tuple[str, ...]


class LibraryCatalogStore:
    """Read and atomically replace the canonical path catalog."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._expected_exists = False
        self._expected_digest: str | None = None
        self._write_block_reason: str | None = None
        self._directory_sync_pending = False
        self._parent_identity: tuple[int, int] | None = None

    @property
    def write_block_reason(self) -> str | None:
        """Return the reason destructive writes are currently forbidden."""
        return self._write_block_reason

    def load(self) -> LibraryCatalogLoadResult:
        """Load valid paths and block writes when the source is not fully trusted.

        Edge cases:
            1. Missing files remain writable empty catalogs.
            2. Linked or replaced files are never followed silently.
            3. Invalid entries produce a read-only recovery subset.
            4. Oversized or malformed documents are never auto-replaced.
        """
        pending_digest = self._expected_digest if self._directory_sync_pending else None
        try:
            parent_identity = _read_parent_identity(self.path.parent)
            payload = _read_existing_catalog(self.path)
        except (LibraryCatalogReadError, OSError) as error:
            return self._block_load((), error)
        self._parent_identity = parent_identity
        if payload is None:
            self._remember_source(None)
            self._directory_sync_pending = False
            self._write_block_reason = None
            return LibraryCatalogLoadResult((), False, ())

        self._remember_source(payload)
        self._directory_sync_pending = pending_digest == self._expected_digest
        try:
            paths, issues = _parse_catalog_payload(payload)
        except (
            BoundedJsonError,
            LibraryCatalogReadError,
            RecursionError,
            UnicodeError,
        ) as error:
            return self._block_load((), error)
        if issues:
            self._write_block_reason = "; ".join(issues)
            return LibraryCatalogLoadResult(paths, True, issues)
        self._write_block_reason = None
        return LibraryCatalogLoadResult(paths, False, ())

    def commit(self, paths: Sequence[str]) -> CatalogCommitStatus:
        """Persist one candidate without overwriting a changed or corrupt source.

        Edge cases:
            1. Blocked sources remain byte-for-byte unchanged.
            2. External edits fail before replacement.
            3. Serialization failures leave source and expected digest unchanged.
            4. Directory-sync failure remains observable after commit.
        """
        if self._write_block_reason is not None:
            raise LibraryCatalogWriteBlockedError(
                "Library catalog writes are blocked: " + self._write_block_reason
            )
        normalized = _normalize_catalog_paths(paths)
        payload = _serialize_catalog(normalized)
        current = self._verify_expected_source()
        if current is not None and _digest(current) == _digest(payload):
            if self._directory_sync_pending:
                return self._retry_directory_sync()
            return CatalogCommitStatus.UNCHANGED
        try:
            synced = write_bytes_atomic_durable(self.path, payload)
            durability = DurabilityStatus.from_directory_sync(synced)
        except (OSError, TypeError, ValueError) as error:
            raise LibraryCatalogWriteError(
                f"Unable to persist library catalog ({type(error).__name__}: {error})."
            ) from error
        self._remember_source(payload)
        self._directory_sync_pending = not durability.is_fully_durable
        return CatalogCommitStatus.from_durability(durability)

    def _retry_directory_sync(self) -> CatalogCommitStatus:
        self._verify_parent_identity()
        try:
            sync_parent_directory(self.path.parent)
        except OSError:
            return CatalogCommitStatus.COMMITTED_WITHOUT_DIRECTORY_SYNC
        self._directory_sync_pending = False
        return CatalogCommitStatus.DURABLE

    def _verify_expected_source(self) -> bytes | None:
        self._verify_parent_identity()
        try:
            current = _read_existing_catalog(self.path)
        except (LibraryCatalogReadError, OSError) as error:
            raise LibraryCatalogConflictError(
                f"Unable to verify current library catalog ({type(error).__name__}: {error})."
            ) from error
        exists = current is not None
        digest = _digest(current) if current is not None else None
        if exists != self._expected_exists or digest != self._expected_digest:
            raise LibraryCatalogConflictError(
                "Library catalog changed after it was loaded; refusing to overwrite it."
            )
        return current

    def _verify_parent_identity(self) -> None:
        if self._parent_identity is None:
            raise LibraryCatalogConflictError("library catalog parent was not validated")
        try:
            current = _read_parent_identity(self.path.parent)
        except (LibraryCatalogReadError, OSError) as error:
            raise LibraryCatalogConflictError(
                f"Unable to verify library catalog parent ({type(error).__name__}: {error})."
            ) from error
        if current != self._parent_identity:
            raise LibraryCatalogConflictError(
                "Library catalog parent changed after load; refusing to write."
            )

    def _remember_source(self, payload: bytes | None) -> None:
        self._expected_exists = payload is not None
        self._expected_digest = _digest(payload) if payload is not None else None

    def _block_load(
        self,
        paths: tuple[str, ...],
        error: BaseException,
    ) -> LibraryCatalogLoadResult:
        reason = _bounded_error_text(error)
        self._write_block_reason = reason
        return LibraryCatalogLoadResult(paths, True, (reason,))


def _read_parent_identity(parent: Path) -> tuple[int, int]:
    try:
        metadata = os.lstat(parent)
    except FileNotFoundError as error:
        raise LibraryCatalogReadError("library catalog parent does not exist") from error
    if stat.S_ISLNK(metadata.st_mode):
        raise LibraryCatalogReadError("library catalog parent must not be a symbolic link")
    try:
        junction_check = getattr(parent, "is_junction", None)
        if callable(junction_check) and junction_check():
            raise LibraryCatalogReadError("library catalog parent must not be a junction")
    except OSError as error:
        raise LibraryCatalogReadError("unable to inspect library catalog parent") from error
    if not stat.S_ISDIR(metadata.st_mode):
        raise LibraryCatalogReadError("library catalog parent must be a directory")
    return metadata.st_dev, metadata.st_ino


def _read_existing_catalog(path: Path) -> bytes | None:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(metadata.st_mode):
        raise LibraryCatalogReadError("library catalog must not be a symbolic link")
    if not stat.S_ISREG(metadata.st_mode):
        raise LibraryCatalogReadError("library catalog must be a regular file")
    if metadata.st_nlink != 1:
        raise LibraryCatalogReadError("library catalog must not have multiple hard links")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise LibraryCatalogReadError("library catalog changed during open")
        payload = _read_bounded(descriptor)
        if _stat_changed(opened, os.fstat(descriptor)):
            raise LibraryCatalogReadError("library catalog changed during read")
        return payload
    finally:
        os.close(descriptor)


def _read_bounded(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    remaining = _MAX_CATALOG_BYTES + 1
    while remaining > 0:
        chunk = os.read(descriptor, min(_READ_CHUNK_BYTES, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    payload = b"".join(chunks)
    if len(payload) > _MAX_CATALOG_BYTES:
        raise LibraryCatalogReadError("library catalog exceeds the byte limit")
    return payload


def _stat_changed(before: os.stat_result, after: os.stat_result) -> bool:
    return (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or before.st_ino != after.st_ino
        or before.st_dev != after.st_dev
    )


def _parse_catalog_payload(payload: bytes) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if not payload:
        raise LibraryCatalogReadError("library catalog is empty")
    try:
        raw = parse_json_bytes(
            payload,
            limits=_LIBRARY_JSON_LIMITS,
            root="array",
        )
    except BoundedJsonError as error:
        if "parser nesting limit" in str(error):
            raise RecursionError(str(error)) from error
        raise
    if not isinstance(raw, list):
        raise LibraryCatalogReadError("library catalog root must be a JSON array")
    if len(raw) > _MAX_CATALOG_ENTRIES:
        raise LibraryCatalogReadError("library catalog exceeds the entry limit")

    paths: list[str] = []
    issues: list[str] = []
    invalid_count = 0
    seen: set[str] = set()
    for index, value in enumerate(raw):
        try:
            path = _validate_catalog_path(value)
        except LibraryCatalogReadError as error:
            invalid_count += 1
            if len(issues) < _MAX_CATALOG_ISSUES:
                issues.append(f"entry {index}: {error}")
            continue
        if path not in seen:
            seen.add(path)
            paths.append(path)
    omitted = invalid_count - len(issues)
    if omitted > 0:
        issues.append(f"{omitted} additional invalid entries omitted")
    return tuple(paths), tuple(issues)


def _normalize_catalog_paths(paths: Sequence[str]) -> tuple[str, ...]:
    if isinstance(paths, (str, bytes, os.PathLike)):
        raise LibraryCatalogWriteError("library catalog paths must be a sequence")
    normalized: list[str] = []
    seen: set[str] = set()
    try:
        for index, value in enumerate(paths):
            if index >= _MAX_CATALOG_ENTRIES:
                raise LibraryCatalogWriteError("library catalog exceeds the entry limit")
            try:
                path = _validate_catalog_path(value)
            except LibraryCatalogReadError as error:
                raise LibraryCatalogWriteError(str(error)) from error
            if path not in seen:
                seen.add(path)
                normalized.append(path)
    except LibraryCatalogWriteError:
        raise
    except Exception as error:  # explicit caller-provided Sequence boundary
        raise LibraryCatalogWriteError(
            f"unable to enumerate library catalog paths ({type(error).__name__}: {error})"
        ) from error
    return tuple(normalized)


def _validate_catalog_path(value: object) -> str:
    if type(value) is not str:
        raise LibraryCatalogReadError("path must be text")
    if not value.strip():
        raise LibraryCatalogReadError("path must not be empty or whitespace")
    if len(value) > _MAX_PATH_CHARS:
        raise LibraryCatalogReadError("path exceeds the character limit")
    if any(ord(character) < 32 for character in value):
        raise LibraryCatalogReadError("path contains a control character")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise LibraryCatalogReadError("path is not valid UTF-8") from error
    if len(encoded) > _MAX_PATH_BYTES:
        raise LibraryCatalogReadError("path exceeds the UTF-8 byte limit")
    return value


def _serialize_catalog(paths: Sequence[str]) -> bytes:
    try:
        _normalized, payload = serialize_json_bytes(
            list(paths),
            limits=_LIBRARY_JSON_LIMITS,
            root="array",
            indent=2,
            trailing_newline=True,
        )
    except BoundedJsonError as error:
        raise LibraryCatalogWriteError(
            f"Unable to serialize library catalog ({type(error).__name__}: {error})."
        ) from error
    return payload


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _bounded_error_text(error: BaseException) -> str:
    text = f"{type(error).__name__}: {error}"
    return text if len(text) <= _MAX_ERROR_TEXT else text[: _MAX_ERROR_TEXT - 3] + "..."


__all__ = [
    "CatalogCommitStatus",
    "LibraryCatalogConflictError",
    "LibraryCatalogError",
    "LibraryCatalogLoadResult",
    "LibraryCatalogReadError",
    "LibraryCatalogStore",
    "LibraryCatalogWriteBlockedError",
    "LibraryCatalogWriteError",
]
