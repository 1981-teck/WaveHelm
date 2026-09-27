"""Resolve or verify native Windows x64 hash-lock bundles; never installs packages."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packaging.markers import default_environment
from tools.dependency_lock import (
    LockError, MAX_BYTES, PIP_VERSION, read_report, render_lock, root_requirements, verify_lock,
)
from tools.staging_integrity import StagingError, regular_path, trusted_directory
from tools.windows_lock_support import MatrixError, require_command, run_command


def require_windows() -> str:
    """Reject foreign OS, pointer width, implementation and unsupported Python."""
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    if (sys.platform != "win32" or platform.python_implementation() != "CPython"
            or platform.machine().lower() not in {"amd64", "x86_64"}
            or sys.maxsize <= 2**32 or version not in {"3.11", "3.12", "3.13"}):
        raise LockError("native Windows x64 CPython 3.11-3.13 is required")
    return version


def _capture_report(report: Path, diagnostics: Path) -> None:
    """Keep even malformed reports for diagnosis, without accepting oversized input."""
    regular_path(report)
    with report.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise LockError("resolver report exceeds diagnostic budget")
    with (diagnostics / "pip-report.json").open("xb") as stream:
        stream.write(data)


def _resolve_into(requirements: Path, staging: Path, diagnostics: Path,
                  version: str) -> str:
    """Run the resolver and validate its output; retain nonzero/timeout diagnostics."""
    report = staging / "pip-report.json"
    command = (sys.executable, "-I", "-B", "-m", "pip", "--isolated", "install",
               "--dry-run", "--ignore-installed", "--disable-pip-version-check",
               "--no-input", "--no-cache-dir", "--only-binary=:all:", "--retries", "0",
               "--timeout", "30", "--index-url", "https://pypi.org/simple", "--report",
               str(report), "-r", str(requirements.absolute()))
    result = run_command(command, diagnostics, requirements.absolute().parent, timeout=900)
    if report.exists():
        _capture_report(report, diagnostics)
    require_command(result)
    data = read_report(report)
    environment = data.get("environment")
    if not isinstance(environment, dict) or environment.get("python_version") != version:
        raise LockError("resolver target differs from current interpreter")
    text = render_lock(data, requirements)
    (staging / "requirements.lock").write_text(text, encoding="utf-8", newline="\n")
    return verify_lock(staging / "requirements.lock", report, requirements)


def resolve(requirements: Path, output: Path,
            diagnostics: Path | None = None) -> dict[str, object]:
    """Publish two validated files together; never discard failed-command logs.

    Invalid roots are rejected before pip runs. Existing destinations, linked
    ancestors, failed commands or malformed reports cannot create an accepted lock.
    """
    version = require_windows()
    if importlib.metadata.version("pip") != PIP_VERSION:
        raise LockError(f"install pip=={PIP_VERSION} before resolution")
    root_requirements(requirements, default_environment())
    trusted_directory(output.parent)
    if output.exists() or output.is_symlink():
        raise LockError("output must be new and its parent must already exist")
    diagnostics = diagnostics or output.with_name(output.name + "-diagnostics")
    trusted_directory(diagnostics.parent)
    if diagnostics.exists() or diagnostics.is_symlink():
        raise LockError("diagnostics must be a new directory")
    if diagnostics.absolute() == output.absolute():
        raise LockError("diagnostics and accepted bundle must be separate")
    with tempfile.TemporaryDirectory(prefix=".wavehelm-lock-", dir=output.parent) as temporary:
        staging = Path(temporary) / "bundle"
        staging.mkdir()
        digest = _resolve_into(requirements, staging, diagnostics, version)
        if output.exists():
            raise LockError("output appeared concurrently")
        staging.rename(output)
    return {"schema": "wavehelm-lock-result-v1", "status": "RESOLVED_NOT_AUDITED",
            "target": version, "lock_sha256": digest, "output": str(output),
            "diagnostics": str(diagnostics)}


def verify(requirements: Path, bundle: Path) -> dict[str, object]:
    """Reject missing, extra, altered or wrong-interpreter lock bundle content."""
    version = require_windows()
    trusted_directory(bundle)
    if {p.name for p in bundle.iterdir()} != {"requirements.lock", "pip-report.json"}:
        raise LockError("lock bundle inventory differs")
    report = read_report(bundle / "pip-report.json")
    env = report.get("environment")
    if not isinstance(env, dict) or env.get("python_version") != version:
        raise LockError("lock interpreter differs from execution interpreter")
    digest = verify_lock(bundle / "requirements.lock", bundle / "pip-report.json", requirements)
    return {"schema": "wavehelm-lock-result-v1", "status": "LOCK_VERIFIED_NOT_AUDITED",
            "target": version, "lock_sha256": digest,
            "report_sha256": hashlib.sha256((bundle / "pip-report.json").read_bytes()).hexdigest()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("resolve", "verify"))
    parser.add_argument("--requirements", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--diagnostics", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "resolve":
            result = resolve(args.requirements, args.bundle, args.diagnostics)
        else:
            if args.diagnostics is not None:
                raise LockError("--diagnostics is only valid with resolve")
            result = verify(args.requirements, args.bundle)
    except (LockError, MatrixError, StagingError, OSError, UnicodeError, subprocess.SubprocessError,
            importlib.metadata.PackageNotFoundError) as exc:
        print(json.dumps({"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
