from __future__ import annotations

import os
import tempfile
from pathlib import Path

_MOVEFILE_REPLACE_EXISTING = 0x00000001
_MOVEFILE_WRITE_THROUGH = 0x00000008


def _replace_windows_write_through(source: Path, target: Path) -> None:
    """Replace *target* using the native Windows write-through move primitive."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    move_file_ex = kernel32.MoveFileExW
    move_file_ex.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    move_file_ex.restype = wintypes.BOOL
    flags = _MOVEFILE_REPLACE_EXISTING | _MOVEFILE_WRITE_THROUGH
    if move_file_ex(str(source), str(target), flags):
        return
    error_code = ctypes.get_last_error()
    raise OSError(error_code, ctypes.FormatError(error_code), str(target))


def durable_replace(source: Path, target: Path) -> None:
    """Atomically replace *target*, requesting write-through semantics on Windows."""
    if os.name == "nt":
        _replace_windows_write_through(source, target)
        return
    os.replace(source, target)


def sync_parent_directory(parent: Path) -> None:
    """Persist a replaced directory entry where the platform exposes directory fsync."""
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_fd = os.open(parent, flags)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _write_all(file_descriptor: int, payload: bytes) -> None:
    payload_view = memoryview(payload)
    offset = 0
    while offset < len(payload_view):
        written = os.write(file_descriptor, payload_view[offset:])
        if written <= 0:
            raise OSError("atomic write made no progress")
        offset += written


def write_bytes_atomic_durable(target: Path, payload: bytes) -> bool:
    """Write *payload* atomically and durably where platform primitives allow.

    Returns ``True`` when the parent directory durability step completed (or is
    represented by the Windows write-through replacement). A ``False`` return
    means the target was already replaced successfully but POSIX directory fsync
    failed, so callers should keep in-memory state aligned with the committed file
    while recording the weakened crash-durability guarantee.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor: int | None = None
    temporary_path: Path | None = None
    try:
        file_descriptor, raw_path = tempfile.mkstemp(
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(raw_path)
        _write_all(file_descriptor, payload)
        os.fsync(file_descriptor)
        os.close(file_descriptor)
        file_descriptor = None
        durable_replace(temporary_path, target)
        temporary_path = None
        try:
            sync_parent_directory(target.parent)
        except OSError:
            return False
        return True
    except OSError:
        if file_descriptor is not None:
            try:
                os.close(file_descriptor)
            except OSError:
                pass
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise
