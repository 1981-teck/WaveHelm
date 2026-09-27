"""Descriptor-bound storage for the public legacy Playlist model.

Playlist identifiers are hashed before filename construction, so untrusted IDs
never become path components. POSIX writes are performed relative to an opened
and identity-checked directory descriptor; the portable fallback repeats root
and target checks around the shared atomic writer.

Edge cases handled:
- linked, replaced, non-directory, or overlong storage roots fail closed;
- linked, non-regular, hard-linked, oversized, or changing inputs are rejected;
- temporary files are removed after every pre-replace failure;
- directory-sync degradation remains distinct from pre-commit failure.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import secrets
import stat
from typing import Final

from src.model.playlist_legacy_contract import (
    MAX_PLAYLIST_BYTES,
    validate_playlist_identifier,
)
from src.utils.durable_io import (
    DurabilityStatus,
    write_bytes_atomic_durable,
)

_MAX_PATH_CHARS: Final = 32_767
_MAX_PATH_BYTES: Final = 131_072
_MAX_TEMP_ATTEMPTS: Final = 32
_READ_CHUNK_BYTES: Final = 64 * 1024


class LegacyPlaylistStorageError(RuntimeError):
    """Base error for the legacy playlist filesystem boundary."""


class LegacyPlaylistPathError(LegacyPlaylistStorageError):
    """Raised when a path violates containment or file-type rules."""


class LegacyPlaylistReadError(LegacyPlaylistStorageError):
    """Raised when a playlist payload cannot be read safely."""


class LegacyPlaylistWriteError(LegacyPlaylistStorageError):
    """Raised when a playlist payload cannot be committed atomically."""


@dataclass(frozen=True, slots=True)
class LegacyPlaylistSaveResult:
    """Path and durability reached by one successful atomic replacement."""

    path: Path
    durability: DurabilityStatus


def playlist_storage_filename(playlist_id: object) -> str:
    """Return a deterministic path-safe filename without embedding the raw ID."""
    validated = validate_playlist_identifier(playlist_id)
    digest = hashlib.sha256(validated.encode("utf-8")).hexdigest()
    return f"playlist-{digest}.json"


def save_playlist_payload(
    directory: object,
    playlist_id: object,
    payload: bytes,
) -> LegacyPlaylistSaveResult:
    """Atomically persist one bounded payload inside a validated directory."""
    if type(payload) is not bytes:
        raise LegacyPlaylistWriteError("playlist payload must be bytes")
    if len(payload) > MAX_PLAYLIST_BYTES:
        raise LegacyPlaylistWriteError("playlist payload exceeds its byte limit")
    filename = playlist_storage_filename(playlist_id)
    root, identity = _prepare_directory(directory, create=True)
    try:
        if _supports_descriptor_boundary():
            durability = _write_posix_atomic(root, identity, filename, payload)
        else:
            durability = _write_portable_atomic(root, identity, filename, payload)
    except LegacyPlaylistStorageError:
        raise
    except (OSError, TypeError, ValueError) as error:
        raise LegacyPlaylistWriteError(
            f"unable to persist legacy playlist ({type(error).__name__})"
        ) from error
    return LegacyPlaylistSaveResult(path=root / filename, durability=durability)
def load_playlist_payload(file_path: object) -> bytes:
    """Read one stable, regular playlist file within the hard byte limit."""
    path = _coerce_path(file_path, "playlist file")
    parent, identity = _prepare_directory(path.parent, create=False)
    filename = path.name
    if not filename or filename in {".", ".."}:
        raise LegacyPlaylistPathError("playlist filename is invalid")
    try:
        if _supports_descriptor_boundary():
            return _read_posix_file(parent, identity, filename)
        return _read_portable_file(parent, identity, filename)
    except LegacyPlaylistStorageError:
        raise
    except (OSError, TypeError, ValueError) as error:
        raise LegacyPlaylistReadError(
            f"unable to read legacy playlist ({type(error).__name__})"
        ) from error
def _prepare_directory(directory: object, *, create: bool) -> tuple[Path, tuple[int, int]]:
    path = _coerce_path(directory, "playlist directory")
    if create and _supports_descriptor_boundary():
        return _ensure_posix_directory(path)
    if create:
        _validate_existing_path_chain(path)
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise LegacyPlaylistPathError("unable to create playlist directory") from error
    if _is_link_or_junction(path):
        raise LegacyPlaylistPathError("playlist directory cannot be a link or junction")
    try:
        absolute = Path(os.path.abspath(path))
        resolved = path.resolve(strict=True)
        metadata = resolved.stat()
    except (OSError, RuntimeError) as error:
        raise LegacyPlaylistPathError("playlist directory is unavailable") from error
    if not stat.S_ISDIR(metadata.st_mode):
        raise LegacyPlaylistPathError("playlist storage root is not a directory")
    if os.path.normcase(str(absolute)) != os.path.normcase(str(resolved)):
        raise LegacyPlaylistPathError("playlist directory contains a linked path component")
    return resolved, (metadata.st_dev, metadata.st_ino)

def _ensure_posix_directory(path: Path) -> tuple[Path, tuple[int, int]]:
    absolute = Path(os.path.abspath(path))
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(absolute.anchor, flags)
    try:
        for component in absolute.parts[1:]:
            metadata = _ensure_posix_component(descriptor, component)
            if stat.S_ISLNK(metadata.st_mode):
                raise LegacyPlaylistPathError("playlist directory contains a linked path component")
            if not stat.S_ISDIR(metadata.st_mode):
                raise LegacyPlaylistPathError("playlist directory path contains a non-directory")
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            _close_quietly(descriptor)
            descriptor = next_descriptor
        metadata = os.fstat(descriptor)
        identity = (metadata.st_dev, metadata.st_ino)
    except OSError as error:
        raise LegacyPlaylistPathError("unable to create playlist directory safely") from error
    finally:
        _close_quietly(descriptor)
    try:
        resolved = absolute.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise LegacyPlaylistPathError("playlist directory became unavailable") from error
    if os.path.normcase(str(absolute)) != os.path.normcase(str(resolved)):
        raise LegacyPlaylistPathError("playlist directory contains a linked path component")
    return resolved, identity

def _ensure_posix_component(descriptor: int, component: str) -> os.stat_result:
    try:
        return os.stat(component, dir_fd=descriptor, follow_symlinks=False)
    except FileNotFoundError:
        try:
            os.mkdir(component, 0o777, dir_fd=descriptor)
        except FileExistsError:
            pass
        return os.stat(component, dir_fd=descriptor, follow_symlinks=False)
def _validate_existing_path_chain(path: Path) -> None:
    absolute = Path(os.path.abspath(path))
    chain = list(reversed(absolute.parents)) + [absolute]
    for candidate in chain:
        if not candidate.exists() and not candidate.is_symlink():
            continue
        if _is_link_or_junction(candidate):
            raise LegacyPlaylistPathError("playlist directory contains a linked path component")
        try:
            metadata = candidate.stat()
        except OSError as error:
            raise LegacyPlaylistPathError("unable to inspect playlist directory path") from error
        if candidate != absolute and not stat.S_ISDIR(metadata.st_mode):
            raise LegacyPlaylistPathError("playlist directory ancestor is not a directory")
def _coerce_path(value: object, label: str) -> Path:
    try:
        raw = os.fspath(value)
    except Exception as error:  # explicit caller-provided PathLike boundary
        raise LegacyPlaylistPathError(f"{label} must be path-like") from error
    if type(raw) is not str:
        raise LegacyPlaylistPathError(f"{label} must be a text path")
    if not raw or "\x00" in raw:
        raise LegacyPlaylistPathError(f"{label} is empty or contains NUL")
    if len(raw) > _MAX_PATH_CHARS:
        raise LegacyPlaylistPathError(f"{label} exceeds its character limit")
    try:
        encoded = raw.encode("utf-8")
    except UnicodeEncodeError as error:
        raise LegacyPlaylistPathError(f"{label} is not valid UTF-8 text") from error
    if len(encoded) > _MAX_PATH_BYTES:
        raise LegacyPlaylistPathError(f"{label} exceeds its UTF-8 byte limit")
    return Path(raw)
def _is_link_or_junction(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        junction_check = getattr(path, "is_junction", None)
        return bool(callable(junction_check) and junction_check())
    except OSError as error:
        raise LegacyPlaylistPathError("unable to inspect playlist path") from error
def _supports_descriptor_boundary() -> bool:
    return os.name != "nt" and os.open in os.supports_dir_fd and hasattr(os, "O_DIRECTORY")
def _open_directory(root: Path, identity: tuple[int, int]) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(root, flags)
    valid = False
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise LegacyPlaylistPathError("opened playlist root is not a directory")
        if (metadata.st_dev, metadata.st_ino) != identity:
            raise LegacyPlaylistPathError("playlist directory identity changed")
        _verify_directory_path_identity(root, identity)
        valid = True
        return descriptor
    finally:
        if not valid:
            _close_quietly(descriptor)
def _verify_directory_path_identity(root: Path, identity: tuple[int, int]) -> None:
    if _is_link_or_junction(root):
        raise LegacyPlaylistPathError("playlist directory became a link or junction")
    try:
        metadata = root.stat()
    except OSError as error:
        raise LegacyPlaylistPathError("playlist directory became unavailable") from error
    if (metadata.st_dev, metadata.st_ino) != identity:
        raise LegacyPlaylistPathError("playlist directory identity changed")
def _write_posix_atomic(
    root: Path,
    identity: tuple[int, int],
    filename: str,
    payload: bytes,
) -> DurabilityStatus:
    root_descriptor = _open_directory(root, identity)
    temporary_name: str | None = None
    file_descriptor: int | None = None
    try:
        _validate_directory_entry(root_descriptor, filename, allow_missing=True)
        temporary_name, file_descriptor = _create_temporary_file(root_descriptor, filename)
        _write_all(file_descriptor, payload)
        os.fsync(file_descriptor)
        os.close(file_descriptor)
        file_descriptor = None
        _verify_directory_path_identity(root, identity)
        os.replace(
            temporary_name,
            filename,
            src_dir_fd=root_descriptor,
            dst_dir_fd=root_descriptor,
        )
        temporary_name = None
        try:
            _sync_directory_descriptor(root_descriptor)
        except OSError:
            return DurabilityStatus.COMMITTED_WITHOUT_DIRECTORY_SYNC
        return DurabilityStatus.DURABLE
    finally:
        if file_descriptor is not None:
            _close_quietly(file_descriptor)
        if temporary_name is not None:
            _unlink_quietly(root_descriptor, temporary_name)
        _close_quietly(root_descriptor)
def _write_portable_atomic(
    root: Path,
    identity: tuple[int, int],
    filename: str,
    payload: bytes,
) -> DurabilityStatus:
    target = root / filename
    _verify_directory_path_identity(root, identity)
    _validate_portable_target(target, allow_missing=True)
    synced = write_bytes_atomic_durable(target, payload)
    _verify_directory_path_identity(root, identity)
    _validate_portable_target(target, allow_missing=False)
    return DurabilityStatus.from_directory_sync(synced)
def _read_posix_file(root: Path, identity: tuple[int, int], filename: str) -> bytes:
    root_descriptor = _open_directory(root, identity)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(filename, flags, dir_fd=root_descriptor)
        try:
            before = os.fstat(descriptor)
            _validate_open_file(before)
            payload = _read_bounded(descriptor)
            after = os.fstat(descriptor)
            _verify_stable_file(before, after)
            return payload
        finally:
            _close_quietly(descriptor)
    except FileNotFoundError as error:
        raise LegacyPlaylistReadError("playlist file does not exist") from error
    except OSError as error:
        raise LegacyPlaylistReadError("unable to open playlist file safely") from error
    finally:
        _close_quietly(root_descriptor)
def _read_portable_file(root: Path, identity: tuple[int, int], filename: str) -> bytes:
    target = root / filename
    _verify_directory_path_identity(root, identity)
    try:
        _validate_portable_target(target, allow_missing=False)
        with target.open("rb") as handle:
            before = os.fstat(handle.fileno())
            _validate_open_file(before)
            payload = handle.read(MAX_PLAYLIST_BYTES + 1)
            after = os.fstat(handle.fileno())
    except FileNotFoundError as error:
        raise LegacyPlaylistReadError("playlist file does not exist") from error
    except OSError as error:
        raise LegacyPlaylistReadError("unable to read playlist file") from error
    _verify_stable_file(before, after)
    if len(payload) > MAX_PLAYLIST_BYTES:
        raise LegacyPlaylistReadError("playlist file exceeds its byte limit")
    return payload
def _validate_directory_entry(descriptor: int, filename: str, *, allow_missing: bool) -> None:
    try:
        metadata = os.stat(filename, dir_fd=descriptor, follow_symlinks=False)
    except FileNotFoundError:
        if allow_missing:
            return
        raise
    _validate_open_file(metadata)
def _validate_portable_target(target: Path, *, allow_missing: bool) -> None:
    # Preserve missing/denied/special-file distinctions for the operation boundary.
    try:
        metadata = target.lstat()
    except FileNotFoundError:
        if allow_missing:
            return
        raise
    except OSError as error:
        raise LegacyPlaylistPathError("unable to inspect playlist file") from error
    if stat.S_ISLNK(metadata.st_mode) or _is_link_or_junction(target):
        raise LegacyPlaylistPathError("playlist file cannot be a link or junction")
    _validate_open_file(metadata)
def _validate_open_file(metadata: os.stat_result) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise LegacyPlaylistPathError("playlist input is not a regular file")
    if metadata.st_nlink > 1:
        raise LegacyPlaylistPathError("hard-linked playlist files are not allowed")
    if metadata.st_size > MAX_PLAYLIST_BYTES:
        raise LegacyPlaylistReadError("playlist file exceeds its byte limit")
def _verify_stable_file(before: os.stat_result, after: os.stat_result) -> None:
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if identity_before != identity_after:
        raise LegacyPlaylistReadError("playlist file changed while it was being read")
def _create_temporary_file(descriptor: int, filename: str) -> tuple[str, int]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    for _attempt in range(_MAX_TEMP_ATTEMPTS):
        candidate = f".{filename}.{secrets.token_hex(12)}.tmp"
        try:
            return candidate, os.open(candidate, flags, 0o600, dir_fd=descriptor)
        except FileExistsError:
            continue
    raise LegacyPlaylistWriteError("unable to allocate a bounded temporary filename")
def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    offset = 0
    while offset < len(view):
        written = os.write(descriptor, view[offset:])
        if written <= 0:
            raise LegacyPlaylistWriteError("playlist write made no progress")
        offset += written
def _read_bounded(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    remaining = MAX_PLAYLIST_BYTES + 1
    while remaining > 0:
        chunk = os.read(descriptor, min(_READ_CHUNK_BYTES, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    payload = b"".join(chunks)
    if len(payload) > MAX_PLAYLIST_BYTES:
        raise LegacyPlaylistReadError("playlist file exceeds its byte limit")
    return payload
def _sync_directory_descriptor(descriptor: int) -> None:
    os.fsync(descriptor)
def _close_quietly(descriptor: int) -> None:
    try:
        os.close(descriptor)
    except OSError:
        pass
def _unlink_quietly(descriptor: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=descriptor)
    except OSError:
        pass
__all__ = [
    "LegacyPlaylistPathError",
    "LegacyPlaylistReadError",
    "LegacyPlaylistSaveResult",
    "LegacyPlaylistStorageError",
    "LegacyPlaylistWriteError",
    "load_playlist_payload",
    "playlist_storage_filename",
    "save_playlist_payload",
]
