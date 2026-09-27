"""Bounded file seals for local candidate staging, not a hostile-user sandbox.

Reject links/reparse points, path aliases, changing files and excess resources.
Source/output ancestors and session storage must be controlled by the operator;
a process with write access to both tools and evidence is outside this boundary.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import unicodedata
import uuid

MAX_FILES = 10000
MAX_BYTES = 256 * 1024 * 1024
MAX_FILE_BYTES = 32 * 1024 * 1024


class StagingError(RuntimeError):
    """Candidate bytes or transaction state cannot be trusted."""


@dataclass(frozen=True)
class FileSeal:
    path: str
    size: int
    sha256: str


def regular_path(path: Path, directory: bool = False) -> os.stat_result:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise StagingError(f"link or reparse point: {path}")
    allowed = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not allowed or (not directory and info.st_nlink != 1):
        raise StagingError(f"unexpected filesystem object or hard link: {path}")
    return info


def trusted_directory(path: Path) -> Path:
    if ".." in path.parts:
        raise StagingError("parent traversal is not an accepted directory spelling")
    absolute = path.absolute()
    for parent in (*reversed(absolute.parents), absolute):
        regular_path(parent, directory=True)
    return absolute


def _portable_name(relative: str) -> str:
    parts = relative.split("/")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10))}
    if any(not p or p in {".", ".."} or p.endswith((" ", ".")) or
           re.search(r'[<>:"\\|?*\x00-\x1f]', p) or p.split(".")[0].upper() in reserved
           for p in parts):
        raise StagingError(f"nonportable source path: {relative}")
    return unicodedata.normalize("NFC", relative).casefold()


def seal_file(path: Path, relative: str) -> FileSeal:
    before = regular_path(path)
    if before.st_size > MAX_FILE_BYTES:
        raise StagingError(f"file exceeds budget: {relative}")
    digest = hashlib.sha256()
    count = 0
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise StagingError(f"file replaced while opening: {relative}")
        while chunk := stream.read(1024 * 1024):
            count += len(chunk)
            if count > MAX_FILE_BYTES:
                raise StagingError(f"file grew beyond budget: {relative}")
            digest.update(chunk)
    after = regular_path(path)
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if count != before.st_size or any(getattr(before, f) != getattr(after, f) for f in fields):
        raise StagingError(f"file changed while reading: {relative}")
    return FileSeal(relative, count, digest.hexdigest())


def inventory(root: Path) -> tuple[FileSeal, ...]:
    """Hash regular source files; the root Git administration is excluded only."""
    root = trusted_directory(root)
    stack = [root]
    aliases: set[str] = set()
    seals: list[FileSeal] = []
    count = total = 0
    while stack:
        directory = stack.pop()
        regular_path(directory, directory=True)
        with os.scandir(directory) as entries:
            for entry in entries:
                count += 1
                if count > MAX_FILES:
                    raise StagingError("source entry budget exceeded")
                path = Path(entry.path)
                if directory == root and entry.name == ".git":
                    continue
                relative = path.relative_to(root).as_posix()
                alias = _portable_name(relative)
                if alias in aliases:
                    raise StagingError(f"case/Unicode path collision: {relative}")
                aliases.add(alias)
                if entry.is_dir(follow_symlinks=False):
                    regular_path(path, directory=True)
                    stack.append(path)
                    continue
                seal = seal_file(path, relative)
                seals.append(seal)
                total += seal.size
                if total > MAX_BYTES:
                    raise StagingError("source byte budget exceeded")
    if not seals:
        raise StagingError("empty source")
    return tuple(sorted(seals, key=lambda item: item.path))


def inventory_digest(seals: tuple[FileSeal, ...]) -> str:
    data = json.dumps([asdict(s) for s in seals], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def copy_sealed(source: Path, target: Path, seals: tuple[FileSeal, ...]) -> None:
    """Copy into a new directory; any drift leaves an unqualified partial copy."""
    target.mkdir(exist_ok=False)
    for seal in seals:
        origin, destination = source / seal.path, target / seal.path
        regular_path(origin)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with origin.open("rb") as src, destination.open("xb") as dst:
            count = 0
            while chunk := src.read(1024 * 1024):
                count += len(chunk)
                if count > seal.size:
                    raise StagingError(f"source grew during copy: {seal.path}")
                dst.write(chunk)
            dst.flush()
            os.fsync(dst.fileno())
        if seal_file(destination, seal.path) != seal:
            raise StagingError(f"copy mismatch: {seal.path}")
    if inventory(source) != seals or inventory(target) != seals:
        raise StagingError("inventory changed while copying")


def write_record(path: Path, payload: dict[str, object]) -> None:
    """Atomic same-directory record replacement, with explicit disk errors."""
    trusted_directory(path.parent)
    if path.exists() or path.is_symlink():
        regular_path(path)
    data = (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    if len(data) > MAX_FILE_BYTES:
        raise StagingError("record exceeds byte budget")
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise StagingError(f"duplicate record key: {key}")
        result[key] = value
    return result


def _nonfinite(value: str) -> object:
    raise StagingError(f"nonfinite record number: {value}")


def read_record(path: Path) -> dict[str, object]:
    regular_path(path)
    with path.open("rb") as stream:
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise StagingError("record exceeds byte budget")
    try:
        raw = json.loads(data, object_pairs_hook=_unique, parse_constant=_nonfinite)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise StagingError(f"invalid record: {exc}") from exc
    if not isinstance(raw, dict):
        raise StagingError("record must be an object")
    return raw
