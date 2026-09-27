"""No-follow cleanup primitives for the WaveHelm source hygiene gate."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Protocol


class HygieneError(RuntimeError):
    """Raised when the hygiene gate cannot inspect or clean source safely."""


class CleanupPolicy(Protocol):
    max_files: int
    max_total_bytes: int
    forbidden_parts: frozenset[str]
    forbidden_suffixes: frozenset[str]


def _git_tracked(root: Path) -> frozenset[str]:
    if not (root / ".git").exists():
        return frozenset()
    try:
        output = subprocess.check_output(
            ["git", "-C", str(root), "ls-files", "-z"], stderr=subprocess.STDOUT
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise HygieneError(f"cannot enumerate tracked files: {exc}") from exc
    return frozenset(item.decode("utf-8") for item in output.split(b"\0") if item)


def _is_generated(relative: str, policy: CleanupPolicy) -> bool:
    path = Path(relative)
    if any(part in policy.forbidden_parts for part in path.parts):
        return True
    if path.suffix.lower() in policy.forbidden_suffixes:
        return True
    if path.name.endswith(".egg-info"):
        return True
    return len(path.parts) >= 2 and path.parts[0] == "tests" and path.parts[1].startswith("_")


def _scan_candidates(root: Path, policy: CleanupPolicy) -> list[Path]:
    candidates: set[Path] = set()
    stack = [root]
    file_count = 0
    total_bytes = 0
    while stack:
        directory = stack.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            raise HygieneError(f"cannot scan cleanup path {directory}: {exc}") from exc
        for entry in entries:
            if entry.name == ".git" and directory == root:
                continue
            path = Path(entry.path)
            relative = path.relative_to(root).as_posix()
            try:
                is_link = entry.is_symlink()
                is_dir = entry.is_dir(follow_symlinks=False)
                is_file = entry.is_file(follow_symlinks=False)
                size = entry.stat(follow_symlinks=False).st_size
            except OSError as exc:
                raise HygieneError(f"cannot inspect cleanup path {path}: {exc}") from exc
            generated = _is_generated(relative, policy)
            if is_link:
                if not generated:
                    raise HygieneError(f"cleanup refuses non-generated symlink: {relative}")
                candidates.add(path)
                continue
            if is_dir:
                if generated:
                    candidates.add(path)
                else:
                    stack.append(path)
                continue
            if not is_file:
                raise HygieneError(f"cleanup refuses unsupported object: {relative}")
            file_count += 1
            total_bytes += size
            if file_count > policy.max_files or total_bytes > policy.max_total_bytes:
                raise HygieneError("cleanup scan exceeds source budgets")
            if generated:
                candidates.add(path)
    return sorted(candidates, key=lambda path: (len(path.parts), path.as_posix()))


def _tracked_under(relative: str, tracked: frozenset[str]) -> list[str]:
    prefix = relative + "/"
    return [item for item in tracked if item == relative or item.startswith(prefix)]


def cleanup(root: Path, policy: CleanupPolicy) -> dict[str, object]:
    tracked = _git_tracked(root)
    removed: list[str] = []
    for path in _scan_candidates(root, policy):
        relative = path.relative_to(root).as_posix()
        tracked_items = _tracked_under(relative, tracked)
        if tracked_items:
            raise HygieneError(f"cleanup refuses tracked content: {relative}")
        if path.is_symlink():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
        removed.append(relative)
    return {
        "schema": "wavehelm-source-cleanup-report-v1",
        "verdict": "PASS",
        "removed": removed,
    }
