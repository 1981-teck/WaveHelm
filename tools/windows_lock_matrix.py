"""Collect six native Windows hash locks and clean installed-graph comparisons.

Creates a NEW operator-owned directory, never changes source or global Python.
Missing interpreters, network failures and incomplete targets remain failures.
No remote publication, vulnerability audit, ABI test or application test occurs.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import uuid

sys.dont_write_bytecode = True
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.staging_integrity import (
    StagingError, copy_sealed, inventory, inventory_digest, read_record,
    regular_path, trusted_directory, write_record,
)
from tools.windows_lock_support import MatrixError, evidence_archive, require_command, run_command

VERSIONS = ("3.11", "3.12", "3.13")
KINDS = ("runtime", "dev")
PIP_VERSION = "26.2.1"
PACKAGING_VERSION = "25.0"
# SHA-256 of the published wheels, independently read from the official PyPI records.
BOOTSTRAP = (
    "pip==26.2.1 --hash=sha256:71138adf1f4ca900cdb7d289c21b7494329f2332b6d85f0e1c42108c0384ed3e\n"
    "packaging==25.0 --hash=sha256:29572ef2b1f17581046b3a2227d5c611fb25ec70ca1ba8554b24b0e69331a484\n"
)
PROBE = '''import importlib.metadata as m, json, platform, struct, sys
from pathlib import Path
config = Path(sys.prefix) / "pyvenv.cfg"
settings = {}
if config.is_file():
    for line in config.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator:
            settings[key.strip().lower()] = value.strip().lower()
rows = [{"name": d.metadata["Name"], "version": d.version} for d in m.distributions()]
print(json.dumps({"schema": "wavehelm-live-inventory-v1", "platform": sys.platform,
    "implementation": platform.python_implementation(), "bits": struct.calcsize("P") * 8,
    "machine": platform.machine(), "version": f"{sys.version_info.major}.{sys.version_info.minor}",
    "full_version": platform.python_version(), "executable": sys.executable,
    "isolated": bool(sys.flags.isolated), "prefix": sys.prefix, "base_prefix": sys.base_prefix,
    "system_site_packages": settings.get("include-system-site-packages") != "false",
    "packages": rows}))'''
NETWORK = ("--disable-pip-version-check", "--no-input", "--no-cache-dir", "--retries", "0",
           "--timeout", "30", "--index-url", "https://pypi.org/simple", "--only-binary=:all:")


@dataclass
class TargetResult:
    target: str
    kind: str
    status: str = "NOT_RUN"
    error: str | None = None
    lock_digest: str | None = None

    @property
    def key(self) -> str:
        return f"windows-py{self.target.replace('.', '')}-{self.kind}"


def require_native_host() -> None:
    """Do not reinterpret Linux, ARM64 or PyPy as native Windows x64."""
    if (sys.platform != "win32" or platform.python_implementation() != "CPython"
            or platform.machine().lower() not in {"amd64", "x86_64"}
            or sys.maxsize <= 2**32):
        raise MatrixError("native Windows x64 CPython is required; no targets were run")


def validate_probe(data: dict[str, object], version: str) -> Path:
    """Reject the wrong interpreter, architecture, implementation or executable."""
    if (data.get("platform") != "win32" or data.get("implementation") != "CPython"
            or data.get("bits") != 64 or type(data.get("bits")) is not int
            or data.get("version") != version or data.get("isolated") is not True
            or data.get("machine") not in {"AMD64", "amd64", "x86_64"}):
        raise MatrixError(f"interpreter probe is not isolated Windows x64 CPython {version}")
    executable = data.get("executable")
    if not isinstance(executable, str) or not executable or len(executable) > 32768:
        raise MatrixError("missing interpreter executable")
    path = Path(executable)
    if not path.is_absolute():
        raise MatrixError("interpreter path is not absolute")
    regular_path(path)
    return path


def execute(argv: tuple[str, ...], folder: Path, snapshot: Path, timeout: int = 900) -> Path:
    """Return a captured output only after the actual command has exited zero."""
    print(f"Running {folder.parent.name}/{folder.name}", flush=True)
    result = run_command(argv, folder, snapshot, timeout)
    require_command(result)
    return Path(result.stdout)


def pip_command(python: Path, target: Path | None = None) -> tuple[str, ...]:
    result = (str(python), "-I", "-B", "-m", "pip", "--isolated")
    return result if target is None else (*result, "--python", str(target))


def bootstrap(version: str, launcher: str, output: Path, snapshot: Path) -> tuple[Path, Path]:
    """Create only a private resolver environment and hash-pin its two tools."""
    folder = output / "evidence" / f"bootstrap-py{version.replace('.', '')}"
    folder.mkdir()
    probe = execute((launcher, f"-{version}", "-I", "-B", "-c", PROBE),
                    folder / "probe", snapshot, 60)
    base = validate_probe(read_record(probe), version)
    environment = output / "environments" / f"resolver-py{version.replace('.', '')}"
    execute((str(base), "-I", "-B", "-m", "venv", "--copies", str(environment)),
            folder / "create-environment", snapshot, 300)
    python = environment / "Scripts" / "python.exe"
    regular_path(python)
    requirements = output / "evidence" / "bootstrap-requirements.lock"
    execute((*pip_command(python), "install", *NETWORK, "--require-hashes", "--no-deps",
             "--report", str(folder / "bootstrap-installation.json"), "-r", str(requirements)),
            folder / "install-bootstrap", snapshot)
    current = execute((str(python), "-I", "-B", "-c", PROBE),
                      folder / "bootstrap-inventory", snapshot, 60)
    data = read_record(current)
    validate_probe(data, version)
    if data.get("system_site_packages") is not False or data.get("prefix") == data.get("base_prefix"):
        raise MatrixError("resolver environment is not private")
    packages = data.get("packages")
    if not isinstance(packages, list):
        raise MatrixError("resolver package inventory is missing")
    versions = {r.get("name"): r.get("version") for r in packages if isinstance(r, dict)}
    if versions.get("pip") != PIP_VERSION or versions.get("packaging") != PACKAGING_VERSION:
        raise MatrixError("bootstrap tool versions differ")
    return base, python


def run_target(result: TargetResult, base: Path, resolver: Path, output: Path, snapshot: Path) -> None:
    """Resolve, install to an empty venv, check and compare one exact graph."""
    folder = output / "evidence" / result.key
    folder.mkdir()
    bundle = output / "locks" / result.key
    requirements = snapshot / ("requirements.txt" if result.kind == "runtime" else "requirements-dev.txt")
    tool = snapshot / "tools" / "resolve_windows_lock.py"
    common = (str(resolver), "-I", "-B", str(tool))
    options = ("--requirements", str(requirements), "--bundle", str(bundle))
    execute((*common, "resolve", *options, "--diagnostics", str(folder / "resolver")),
            folder / "resolve", snapshot, 1000)
    execute((*common, "verify", *options), folder / "verify-lock", snapshot, 60)
    environment = output / "environments" / result.key
    execute((str(base), "-I", "-B", "-m", "venv", "--without-pip", "--copies", str(environment)),
            folder / "create-target", snapshot, 300)
    python = environment / "Scripts" / "python.exe"
    regular_path(python)
    installation = folder / "installation.json"
    execute((*pip_command(resolver, python), "install", *NETWORK, "--ignore-installed",
             "--require-hashes", "--report", str(installation), "-r", str(bundle / "requirements.lock")),
            folder / "install-locked", snapshot, 1800)
    execute((*pip_command(resolver, python), "check"), folder / "pip-check", snapshot, 60)
    live = execute((str(python), "-I", "-B", "-c", PROBE), folder / "live-inventory", snapshot, 60)
    validate_probe(read_record(live), result.target)
    comparison = execute((str(resolver), "-I", "-B", str(snapshot / "tools/verify_windows_install.py"),
        *options, "--installation", str(installation), "--live", str(live), "--target", result.target),
        folder / "compare-graph", snapshot, 60)
    if read_record(comparison).get("status") != "INSTALLED_GRAPH_MATCH_NOT_AUDITED":
        raise MatrixError("graph comparison did not return the required result")
    result.lock_digest = inventory_digest(inventory(bundle))
    result.status = "INSTALLED_GRAPH_MATCH_NOT_AUDITED"


def run_version(version: str, launcher: str, output: Path, snapshot: Path,
                results: list[TargetResult]) -> None:
    """Record failed setup for both legs; never silently drop a target."""
    targets = [result for result in results if result.target == version]
    try:
        base, resolver = bootstrap(version, launcher, output, snapshot)
    except (MatrixError, StagingError, OSError, UnicodeError) as exc:
        for result in targets:
            result.status, result.error = "BOOTSTRAP_FAILED", f"{type(exc).__name__}: {exc}"
        return
    for result in targets:
        result.status = "RUNNING"
        try:
            run_target(result, base, resolver, output, snapshot)
        except (MatrixError, StagingError, OSError, UnicodeError) as exc:
            result.status, result.error = "FAILED", f"{type(exc).__name__}: {exc}"
        write_record(output / "evidence" / result.key / "target-result.json", asdict(result))


def prepare_output(source: Path, output: Path) -> Path:
    """Forbid existing destinations, nested source/output paths and linked parents."""
    source = trusted_directory(source)
    trusted_directory(output.parent)
    destination = output.absolute()
    if destination.is_relative_to(source) or source.is_relative_to(destination):
        raise MatrixError("output must be separate from source")
    if destination.exists() or destination.is_symlink():
        raise MatrixError("output must be a new directory; nothing will be overwritten")
    destination.mkdir()
    for name in ("evidence", "locks", "environments"):
        (destination / name).mkdir()
    return destination


def locks_unchanged(output: Path, results: list[TargetResult]) -> bool:
    """Require six byte-bound bundles; status strings alone never close a matrix."""
    expected = {f"windows-py{v.replace('.', '')}-{k}" for v in VERSIONS for k in KINDS}
    if len(results) != 6 or {r.key for r in results} != expected:
        return False
    root = trusted_directory(output / "locks")
    if {p.name for p in root.iterdir()} != expected:
        return False
    for result in results:
        bundle = trusted_directory(root / result.key)
        if {p.name for p in bundle.iterdir()} != {"pip-report.json", "requirements.lock"}:
            return False
        if inventory_digest(inventory(bundle)) != result.lock_digest:
            return False
    return True


def _finalize(source: Path, snapshot: Path, output: Path, original_digest: str,
              results: list[TargetResult], error: str | None, finished: bool) -> dict[str, object]:
    """Do not promote incomplete, interrupted or changed-source campaigns."""
    source_ok = snapshot_ok = lock_ok = False
    try:
        source_ok = inventory_digest(inventory(source)) == original_digest
        snapshot_ok = inventory_digest(inventory(snapshot)) == original_digest
        lock_ok = locks_unchanged(output, results)
    except (StagingError, OSError) as exc:
        error = f"{error or ''} integrity: {type(exc).__name__}: {exc}".strip()
    complete = (finished and error is None and source_ok and snapshot_ok and lock_ok and len(results) == 6
                and all(r.status == "INSTALLED_GRAPH_MATCH_NOT_AUDITED" for r in results))
    summary = {"schema": "wavehelm-lock-matrix-v1", "source_digest": original_digest,
        "status": "MATRIX_INSTALLED_NOT_AUDITED" if complete else "INCOMPLETE",
        "source_unchanged": source_ok, "snapshot_unchanged": snapshot_ok,
        "lock_bundles_unchanged": lock_ok,
        "error": error, "targets": [asdict(r) for r in results],
        "vulnerabilities": "NOT_RUN", "abi": "NOT_RUN", "application_tests": "NOT_RUN",
        "human_review": "REQUIRED", "release_readiness": "NOT_VERIFIED",
        "source_inventory_exclusions": ["root .git administration only"],
        "bundle_exclusions": ["environments/", "source-snapshot/", "pip cache (disabled)"],
        "finished_utc": datetime.now(timezone.utc).isoformat()}
    write_record(output / "evidence" / "SUMMARY.json", summary)
    return summary


def run_matrix(source: Path, output: Path, launcher: str) -> tuple[dict[str, object], Path]:
    """Preserve a return bundle for handled failures; never reuse prior results."""
    require_native_host()
    source = trusted_directory(source)
    seals = inventory(source)
    for name in ("requirements.txt", "requirements-dev.txt", "tools/resolve_windows_lock.py",
                 "tools/verify_windows_install.py"):
        regular_path(source / name)
    output = prepare_output(source, output)
    snapshot = output / "source-snapshot"
    results = [TargetResult(v, k) for v in VERSIONS for k in KINDS]
    error: str | None = None
    finished = False
    try:
        write_record(output / "evidence" / "source-inventory.json", {
            "schema": "wavehelm-lock-input-v1", "files": [asdict(s) for s in seals]})
        (output / "evidence" / "bootstrap-requirements.lock").write_text(BOOTSTRAP, encoding="utf-8")
        copy_sealed(source, snapshot, seals)
        for version in VERSIONS:
            run_version(version, launcher, output, snapshot, results)
            write_record(output / "evidence" / "progress.json", {
                "targets": [asdict(r) for r in results], "release_readiness": "NOT_VERIFIED"})
        finished = True
    except (MatrixError, StagingError, OSError, UnicodeError) as exc:
        error = f"{type(exc).__name__}: {exc}"
    except KeyboardInterrupt:
        # Explicit CLI-controlled shutdown: preserve incomplete evidence, never resume blindly.
        error = "KeyboardInterrupt: collection interrupted; no complete matrix"
    finally:
        summary = _finalize(source, snapshot, output, inventory_digest(seals), results, error, finished)
    return summary, evidence_archive(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--launcher", default="py")
    args = parser.parse_args()
    try:
        require_native_host()
        launcher = shutil.which(args.launcher)
        if launcher is None:
            raise MatrixError("Python launcher missing; install CPython 3.11, 3.12 and 3.13 x64 first")
        if args.output is None:
            local = os.environ.get("LOCALAPPDATA")
            if not local:
                raise MatrixError("LOCALAPPDATA is missing; supply --output with a new external directory")
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            args.output = Path(local) / f"WaveHelm-locks-{stamp}-{uuid.uuid4().hex[:8]}"
        summary, archive = run_matrix(Path(__file__).resolve().parents[1], args.output, launcher)
    except (MatrixError, StagingError, OSError, UnicodeError) as exc:
        print(json.dumps({"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}))
        return 1
    print(json.dumps({"status": summary["status"], "results_archive": str(archive)}, indent=2))
    return 0 if summary["status"] == "MATRIX_INSTALLED_NOT_AUDITED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
