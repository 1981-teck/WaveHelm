from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from enum import StrEnum
import os
import tempfile
import threading
from pathlib import Path

_MOVEFILE_REPLACE_EXISTING = 0x00000001
_MOVEFILE_WRITE_THROUGH = 0x00000008


class DurabilityStatus(StrEnum):
    """Describe the durability reached after an atomic file replacement."""

    DURABLE = "durable"
    COMMITTED_WITHOUT_DIRECTORY_SYNC = "committed_without_directory_sync"

    @classmethod
    def from_directory_sync(cls, directory_synced: bool) -> DurabilityStatus:
        """Map the atomic writer's directory-sync result to an explicit status."""
        if type(directory_synced) is not bool:
            raise TypeError("directory_synced must be a bool")
        if directory_synced:
            return cls.DURABLE
        return cls.COMMITTED_WITHOUT_DIRECTORY_SYNC

    @property
    def is_fully_durable(self) -> bool:
        """Return whether the parent-directory durability step completed."""
        return self is DurabilityStatus.DURABLE


class SerializedCommitGate:
    """Serialize state/file commits without holding a mutex during the commit body.

    Edge cases:
        1. Spurious condition wakeups must not admit two concurrent commit bodies.
        2. A commit-body exception must always release the next waiting mutation.
        3. Reentrant mutation on the owner thread must fail instead of deadlocking.
        4. Filesystem I/O in the body must run after the condition mutex is released.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._busy = False
        self._owner_thread_id: int | None = None

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Enter one serialized commit turn and release it on every normal exception."""
        self._enter()
        try:
            yield
        finally:
            self._leave()

    def _enter(self) -> None:
        thread_id = threading.get_ident()
        with self._condition:
            if self._owner_thread_id == thread_id:
                raise RuntimeError("reentrant serialized commit is forbidden")
            while self._busy:
                self._condition.wait()
            self._busy = True
            self._owner_thread_id = thread_id

    def _leave(self) -> None:
        thread_id = threading.get_ident()
        with self._condition:
            if not self._busy or self._owner_thread_id != thread_id:
                raise RuntimeError("serialized commit gate ownership was lost")
            self._owner_thread_id = None
            self._busy = False
            self._condition.notify_all()


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
