"""Filesystem traversal implementation for bounded library scans.

Edge cases handled:
- scalar values preserve the legacy empty-discovery result;
- linked roots are rejected and linked descendants are skipped;
- directory identity prevents cycles within one root traversal;
- traversal errors terminate the scan instead of returning partial success.

Complexity: O(I + D + E), bounded by ``LibraryScanLimits``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
import os
import stat

from src.controller.library_scan import (
    LibraryPathScanResult,
    LibraryScanLimitKind,
    LibraryScanSession,
    MediaFileIdentity,
    validate_scan_path_text,
)

DirectoryIdentity = tuple[str, int, int] | tuple[str, str]
DirectorySnapshot = tuple[DirectoryIdentity, int, int, int]


def scan_supported_paths(
    path_values: Sequence[object],
    *,
    accept_path: Callable[[str], bool],
    coerce_path: Callable[[object], str | None],
    session: LibraryScanSession,
) -> LibraryPathScanResult:
    """Return a complete path result or raise without exposing partial paths."""
    paths = discover_supported_paths(
        path_values,
        accept_path=accept_path,
        coerce_path=coerce_path,
        session=session,
    )
    return LibraryPathScanResult(paths, session.complete_report())


def discover_supported_paths(
    path_values: Sequence[object],
    *,
    accept_path: Callable[[str], bool],
    coerce_path: Callable[[object], str | None],
    session: LibraryScanSession,
) -> tuple[str, ...]:
    """Discover supported paths while preserving one shared scan session."""
    session.checkpoint()
    if isinstance(path_values, (str, bytes, os.PathLike)):
        session.record_invalid_input()
        return ()
    if not isinstance(path_values, Sequence):
        raise TypeError("path_values must be a sequence")
    try:
        input_count = len(path_values)
    except Exception as error:  # explicit external sequence boundary
        session.fail_input(error)
    if input_count > session.limits.max_input_paths:
        session.fail_limit(
            LibraryScanLimitKind.INPUT_PATHS,
            "library scan input sequence exceeds the input_paths limit",
        )

    discovered: list[str] = []
    try:
        iterator = iter(path_values)
    except Exception as error:  # explicit external sequence boundary
        session.fail_input(error)
    while True:
        session.checkpoint()
        try:
            value = next(iterator)
        except StopIteration:
            break
        except Exception as error:  # explicit external sequence boundary
            session.fail_input(error)
        session.claim_input_path()
        path = coerce_path(value)
        if path is None:
            session.record_invalid_input()
            continue
        _scan_root(path, discovered, accept_path=accept_path, session=session)
    return tuple(discovered)


def _scan_root(
    path: str,
    discovered: list[str],
    *,
    accept_path: Callable[[str], bool],
    session: LibraryScanSession,
) -> None:
    session.checkpoint()
    if _is_link_or_junction(path, session):
        session.fail_traversal(path, "linked scan roots are not allowed")
    try:
        metadata = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        session.record_missing_path()
        return
    except OSError as error:
        session.fail_traversal(path, error)
    session.checkpoint()
    if stat.S_ISDIR(metadata.st_mode):
        _scan_directory(
            path,
            discovered,
            accept_path=accept_path,
            session=session,
            root_snapshot=_directory_snapshot_from_metadata(path, metadata, session),
        )
        return
    if stat.S_ISREG(metadata.st_mode):
        _consider_media_path(
            path,
            discovered,
            accept_path=accept_path,
            session=session,
            metadata=metadata,
        )
        return
    session.record_unsupported_entry()


def _scan_directory(
    root: str,
    discovered: list[str],
    *,
    accept_path: Callable[[str], bool],
    session: LibraryScanSession,
    root_snapshot: DirectorySnapshot,
) -> None:
    pending = [(root, root_snapshot)]
    visited: set[DirectoryIdentity] = set()
    while pending:
        current, expected_snapshot = pending.pop()
        session.checkpoint()
        current_snapshot = _directory_snapshot(current, session)
        if current_snapshot != expected_snapshot:
            session.fail_traversal(current, "directory changed before traversal")
        identity = current_snapshot[0]
        if identity in visited:
            session.record_duplicate_directory()
            continue
        visited.add(identity)
        session.claim_directory()
        child_directories = _scan_directory_entries(
            current,
            current_snapshot,
            discovered,
            pending=pending,
            accept_path=accept_path,
            session=session,
        )
        pending.extend(reversed(child_directories))


def _scan_directory_entries(
    current: str,
    snapshot: DirectorySnapshot,
    discovered: list[str],
    *,
    pending: list[tuple[str, DirectorySnapshot]],
    accept_path: Callable[[str], bool],
    session: LibraryScanSession,
) -> list[tuple[str, DirectorySnapshot]]:
    """Scan one stable directory and return its bounded child worklist."""
    children: list[tuple[str, DirectorySnapshot]] = []
    try:
        with os.scandir(current) as entries:
            if _directory_snapshot(current, session) != snapshot:
                session.fail_traversal(current, "directory changed during open")
            for entry in entries:
                session.claim_entry()
                entry_path = entry.path
                if _entry_is_link_or_junction(entry, entry_path, session):
                    session.record_link_skipped()
                    continue
                try:
                    # DirEntry.stat caches incomplete identity on Windows. All
                    # snapshots must use fresh no-follow stat, also before loading.
                    metadata = os.stat(entry_path, follow_symlinks=False)
                    if stat.S_ISDIR(metadata.st_mode):
                        _guard_directory_queue(pending, children, session)
                        children.append(
                            (
                                entry_path,
                                _directory_snapshot_from_metadata(
                                    entry_path, metadata, session
                                ),
                            )
                        )
                    elif stat.S_ISREG(metadata.st_mode):
                        _consider_media_path(
                            entry_path,
                            discovered,
                            accept_path=accept_path,
                            session=session,
                            metadata=metadata,
                        )
                    else:
                        session.record_unsupported_entry()
                except OSError as error:
                    session.fail_traversal(entry_path, error)
                session.checkpoint()
            if _directory_snapshot(current, session) != snapshot:
                session.fail_traversal(current, "directory changed during traversal")
    except OSError as error:
        session.fail_traversal(current, error)
    return children


def _directory_snapshot(path: str, session: LibraryScanSession) -> DirectorySnapshot:
    if _is_link_or_junction(path, session):
        session.fail_traversal(path, "linked directories are not allowed")
    try:
        metadata = os.stat(path, follow_symlinks=False)
    except OSError as error:
        session.fail_traversal(path, error)
    if not stat.S_ISDIR(metadata.st_mode):
        session.fail_traversal(path, "directory changed type during scan")
    return _directory_snapshot_from_metadata(path, metadata, session)


def _directory_snapshot_from_metadata(
    path: str,
    metadata: os.stat_result,
    session: LibraryScanSession,
) -> DirectorySnapshot:
    device, inode = int(metadata.st_dev), int(metadata.st_ino)
    identity: DirectoryIdentity
    if inode != 0:
        identity = "inode", device, inode
    else:
        try:
            identity = "path", os.path.normcase(os.path.abspath(path))
        except (OSError, ValueError) as error:
            session.fail_traversal(path, error)
    return (
        identity,
        int(metadata.st_mode),
        _stat_time_ns(metadata, "st_mtime_ns", "st_mtime"),
        _stat_time_ns(metadata, "st_ctime_ns", "st_ctime"),
    )


def _guard_directory_queue(
    pending: list[tuple[str, DirectorySnapshot]],
    children: list[tuple[str, DirectorySnapshot]],
    session: LibraryScanSession,
) -> None:
    session.ensure_directory_queue_capacity(len(pending) + len(children) + 1)


def _consider_media_path(
    path: str,
    discovered: list[str],
    *,
    accept_path: Callable[[str], bool],
    session: LibraryScanSession,
    metadata: os.stat_result,
) -> None:
    session.checkpoint()
    if not validate_scan_path_text(path):
        session.fail_traversal(path, "discovered media path exceeds text limits")
    if accept_path(path):
        identity = _media_identity_from_metadata(metadata)
        session.claim_media_path()
        session.remember_media_identity(path, identity)
        discovered.append(path)
    else:
        session.record_unsupported_entry()


def verify_media_file_identity(
    path: str,
    *,
    session: LibraryScanSession,
    expected: MediaFileIdentity | None = None,
) -> MediaFileIdentity:
    """Verify that a scanned path remains one stable regular non-linked file."""
    session.checkpoint()
    if _is_link_or_junction(path, session):
        session.fail_traversal(path, "linked media files are not allowed")
    try:
        metadata = os.stat(path, follow_symlinks=False)
    except OSError as error:
        session.fail_traversal(path, error)
    if not stat.S_ISREG(metadata.st_mode):
        session.fail_traversal(path, "media path changed type during scan")
    identity = _media_identity_from_metadata(metadata)
    if expected is not None and identity != expected:
        session.fail_traversal(path, "media file changed during metadata loading")
    session.checkpoint()
    return identity


def _media_identity_from_metadata(metadata: os.stat_result) -> MediaFileIdentity:
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(metadata.st_mode),
        int(metadata.st_size),
        _stat_time_ns(metadata, "st_mtime_ns", "st_mtime"),
        _stat_time_ns(metadata, "st_ctime_ns", "st_ctime"),
    )


def _stat_time_ns(metadata: os.stat_result, nanoseconds: str, seconds: str) -> int:
    direct = getattr(metadata, nanoseconds, None)
    if direct is not None:
        return int(direct)
    return int(float(getattr(metadata, seconds, 0.0)) * 1_000_000_000)


def _entry_is_link_or_junction(
    entry: os.DirEntry[str],
    path: str,
    session: LibraryScanSession,
) -> bool:
    try:
        return entry.is_symlink() or _is_junction(path)
    except OSError as error:
        session.fail_traversal(path, error)


def _is_link_or_junction(path: str, session: LibraryScanSession) -> bool:
    try:
        return os.path.islink(path) or _is_junction(path)
    except OSError as error:
        session.fail_traversal(path, error)


def _is_junction(path: str) -> bool:
    checker = getattr(os.path, "isjunction", None)
    return bool(checker(path)) if callable(checker) else False


__all__ = [
    "discover_supported_paths",
    "scan_supported_paths",
    "verify_media_file_identity",
]
