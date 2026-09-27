"""Cold, operator-owned process/evidence support for the Windows lock matrix.

Missing commands, timeouts and oversized logs cannot produce successful receipts.
No shell is used. Paths must be private to the operator; this is not a sandbox
against hostile descendant processes or concurrent replacement by another account.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import os
from pathlib import Path
import re
import subprocess
import time
import zipfile

from tools.staging_integrity import (
    FileSeal, StagingError, inventory, regular_path, seal_file,
    trusted_directory, write_record,
)

LOG_LIMIT = 32 * 1024 * 1024


class MatrixError(RuntimeError):
    """The native collection cannot claim a complete installed matrix."""


@dataclass(frozen=True)
class CommandResult:
    label: str
    argv: tuple[str, ...]
    exit_code: int | None
    elapsed_seconds: float
    error: str | None
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and self.error is None


def clean_environment() -> dict[str, str]:
    """Remove inherited package, interpreter and test overrides, not system paths."""
    excluded = {"VIRTUAL_ENV", "CONDA_PREFIX", "PYLAUNCHER_ALLOW_INSTALL",
                "PYLAUNCHER_ALWAYS_INSTALL"}
    result = {k: v for k, v in os.environ.items()
              if not k.upper().startswith(("PYTHON", "PYTEST", "PIP_"))
              and k.upper() not in excluded}
    result.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1", PIP_CONFIG_FILE=os.devnull,
                  PYTHON_MANAGER_AUTOMATIC_INSTALL="false")
    return result


def run_command(argv: tuple[str, ...], folder: Path, cwd: Path,
                timeout: int = 900) -> CommandResult:
    """Preserve stdout/stderr and exit even on failure; reject stale destinations.

    The timeout bounds the direct child. Log sizes are checked after completion,
    not protected by an OS disk quota; do not run untrusted executables here.
    """
    if not argv or len(argv) > 128 or any(not isinstance(a, str) or "\0" in a for a in argv):
        raise MatrixError("invalid command arguments")
    if type(timeout) is not int or not 1 <= timeout <= 3600:
        raise MatrixError("invalid command timeout")
    if re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", folder.name) is None:
        raise MatrixError("invalid command label")
    trusted_directory(folder.parent)
    trusted_directory(cwd)
    folder.mkdir(exist_ok=False)
    out, err = folder / "stdout.txt", folder / "stderr.txt"
    code: int | None = None
    error: str | None = None
    started = time.monotonic()
    with out.open("xb") as stdout, err.open("xb") as stderr:
        try:
            process = subprocess.run(argv, cwd=cwd, env=clean_environment(),
                                     stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                     timeout=timeout, check=False, shell=False)
            code = process.returncode
        except (OSError, subprocess.SubprocessError) as exc:
            error = f"{type(exc).__name__}: {exc}"
    if any(p.stat().st_size > LOG_LIMIT for p in (out, err)):
        error = "command log exceeds evidence budget; not accepted"
    result = CommandResult(folder.name, argv, code, round(time.monotonic() - started, 6),
                           error, str(out), str(err))
    write_record(folder / "command.json", asdict(result))
    return result


def require_command(result: CommandResult) -> None:
    """A recorded failure stops only the current target, not evidence collection."""
    if not result.ok:
        raise MatrixError(f"{result.label}: exit={result.exit_code}; error={result.error}")


def evidence_archive(output: Path) -> Path:
    """Archive only evidence and candidate locks, never environments or source.

    Reject linked, oversized or changing files. The manifest records exact bytes;
    integrity is not a signature or protection against rewriting all evidence.
    """
    trusted_directory(output)
    archive = output / "WaveHelm-lock-matrix-results.zip"
    if archive.exists() or archive.is_symlink():
        raise FileExistsError("evidence archive already exists; nothing was overwritten")
    members: list[tuple[Path, FileSeal]] = []
    for name in ("evidence", "locks"):
        root = output / name
        trusted_directory(root)
        if not any(root.iterdir()):
            continue
        for seal in inventory(root):
            members.append((root / seal.path, FileSeal(f"{name}/{seal.path}", seal.size, seal.sha256)))
    write_record(output / "evidence-manifest.json", {
        "schema": "wavehelm-lock-matrix-manifest-v1", "files": [asdict(s) for _, s in members],
        "excluded": ["environments/", "source-snapshot/", "archive itself"],
    })
    manifest = output / "evidence-manifest.json"
    members.append((manifest, seal_file(manifest, manifest.name)))
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED) as target:
        for path, seal in members:
            if seal_file(path, seal.path) != seal:
                raise MatrixError(f"evidence changed before archive: {seal.path}")
            target.write(path, seal.path)
    with zipfile.ZipFile(archive) as check:
        if check.testzip() is not None:
            raise MatrixError("archive CRC verification failed")
        for _, seal in members:
            data = check.read(seal.path)
            if len(data) != seal.size or hashlib.sha256(data).hexdigest() != seal.sha256:
                raise MatrixError(f"archive bytes differ: {seal.path}")
    regular_path(archive)
    return archive
