"""Collect native Windows runtime vulnerability, SBOM and license evidence.

The source is copied to a sealed snapshot and never modified. Each supported
CPython minor gets a private audit-tool environment and a private runtime target
installed from the checked-in runtime hash lock. Known vulnerabilities remain a
blocking result rather than an execution error.

Edge cases: a missing interpreter fails that target; a vulnerability exit still
continues through SBOM/license collection; source or snapshot drift prevents a
complete matrix; existing/aliased output paths are rejected before execution.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import uuid
import zipfile

sys.dont_write_bytecode = True
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.staging_integrity import (
    FileSeal,
    StagingError,
    copy_sealed,
    inventory,
    inventory_digest,
    regular_path,
    seal_file,
    trusted_directory,
    write_record,
)
from tools.windows_lock_matrix import NETWORK, PROBE, VERSIONS, require_native_host, validate_probe
from tools.windows_lock_support import MatrixError, run_command

AUDIT_TOOLS = ("pip-audit==2.10.1", "cyclonedx-bom==7.3.1")
PIP_VERSION = "26.2.1"


@dataclass
class AuditResult:
    target: str
    status: str = "NOT_RUN"
    error: str | None = None
    audit_exit: int | None = None
    validate_exit: int | None = None
    manifest_exit: int | None = None
    findings: int | None = None

    @property
    def key(self) -> str:
        return f"windows-py{self.target.replace('.', '')}-runtime"


def _write_exit(path: Path, code: int | None) -> None:
    value = "NOT_RUN" if code is None else str(code)
    path.write_text(value + "\n", encoding="utf-8", newline="\n")


def _copy_output(source: str, destination: Path) -> None:
    data = Path(source).read_bytes()
    destination.write_bytes(data)


def _execute(argv: tuple[str, ...], folder: Path, source: Path, timeout: int = 900):
    print(f"Running {folder.parent.name}/{folder.name}", flush=True)
    return run_command(argv, folder, source, timeout)


def _require_ok(result, label: str) -> None:
    if result.exit_code != 0 or result.error is not None:
        raise MatrixError(f"{label}: exit={result.exit_code}; error={result.error}")


def _python_in(environment: Path) -> Path:
    python = environment / "Scripts" / "python.exe"
    regular_path(python)
    return python


def _probe_base(version: str, launcher: str, commands: Path, source: Path) -> Path:
    result = _execute((launcher, f"-{version}", "-I", "-B", "-c", PROBE),
                      commands / "probe", source, 60)
    _require_ok(result, "interpreter probe")
    try:
        payload = json.loads(Path(result.stdout).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MatrixError(f"cannot parse interpreter probe: {exc}") from exc
    if not isinstance(payload, dict):
        raise MatrixError("interpreter probe root is not an object")
    return validate_probe(payload, version)


def _install_tools(base: Path, environment: Path, evidence: Path,
                   commands: Path, source: Path, version: str) -> Path:
    create = _execute((str(base), "-I", "-B", "-m", "venv", "--copies", str(environment)),
                      commands / "create-tools", source, 300)
    _require_ok(create, "audit-tool environment creation")
    python = _python_in(environment)
    bootstrap = _execute((str(python), "-I", "-B", "-m", "pip", "--isolated", "install",
        *NETWORK, f"pip=={PIP_VERSION}"), commands / "bootstrap-pip", source, 900)
    _write_exit(evidence / "pip-bootstrap-exit-code.txt", bootstrap.exit_code)
    _require_ok(bootstrap, "pip bootstrap")
    report = evidence / "audit-tool-install-report.json"
    install = _execute((str(python), "-I", "-B", "-m", "pip", "--isolated", "install",
        *NETWORK, "--report", str(report), *AUDIT_TOOLS), commands / "install-tools", source, 1200)
    _write_exit(evidence / "audit-tool-install-exit-code.txt", install.exit_code)
    _require_ok(install, "audit-tool installation")
    versions = _execute((str(python), "-I", "-B", str(source / "tools/supply_chain.py"),
        "tool-versions", "--root", str(source), "--output",
        str(evidence / "supply-chain-tool-versions.json")), commands / "tool-versions", source, 60)
    _write_exit(evidence / "tool-versions-exit-code.txt", versions.exit_code)
    _require_ok(versions, "audit-tool version check")
    probe = _execute((str(python), "-I", "-B", "-c", PROBE),
                     commands / "tools-probe", source, 60)
    _require_ok(probe, "audit-tool environment probe")
    payload = json.loads(Path(probe.stdout).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("system_site_packages") is not False:
        raise MatrixError("audit-tool environment is not private")
    validate_probe(payload, version)
    return python


def _install_runtime(base: Path, tool_python: Path, environment: Path, lock: Path,
                     evidence: Path, commands: Path, source: Path) -> Path:
    create = _execute((str(base), "-I", "-B", "-m", "venv", "--without-pip", "--copies",
        str(environment)), commands / "create-runtime", source, 300)
    _require_ok(create, "runtime environment creation")
    runtime_python = _python_in(environment)
    report = evidence / "runtime-install-report.json"
    install = _execute((str(tool_python), "-I", "-B", "-m", "pip", "--isolated",
        "--python", str(runtime_python), "install", *NETWORK, "--ignore-installed",
        "--require-hashes", "--report", str(report), "-r", str(lock)),
        commands / "install-runtime", source, 1800)
    _write_exit(evidence / "runtime-install-exit-code.txt", install.exit_code)
    _require_ok(install, "runtime hash-locked installation")
    check = _execute((str(tool_python), "-I", "-B", "-m", "pip", "--isolated", "--python",
        str(runtime_python), "check"), commands / "pip-check", source, 60)
    _copy_output(check.stdout, evidence / "runtime-pip-check.txt")
    _write_exit(evidence / "runtime-pip-check-exit-code.txt", check.exit_code)
    _require_ok(check, "runtime pip check")
    freeze = _execute((str(tool_python), "-I", "-B", "-m", "pip", "--isolated", "--python",
        str(runtime_python), "freeze", "--all"), commands / "freeze", source, 60)
    _copy_output(freeze.stdout, evidence / "runtime-freeze.txt")
    _write_exit(evidence / "runtime-freeze-exit-code.txt", freeze.exit_code)
    _require_ok(freeze, "runtime freeze")
    return runtime_python


def _collect_analysis(tool_python: Path, runtime_python: Path, environment: Path,
                      evidence: Path, commands: Path, source: Path) -> int:
    site = environment / "Lib" / "site-packages"
    trusted_directory(site)
    audit = _execute((str(tool_python), "-I", "-B", "-m", "pip_audit", "--path", str(site),
        "--strict", "--progress-spinner", "off", "--timeout", "30", "--format", "json",
        "--output", str(evidence / "pip-audit.json")), commands / "pip-audit", source, 900)
    _write_exit(evidence / "pip-audit-exit-code.txt", audit.exit_code)
    sbom = _execute((str(tool_python), "-I", "-B", "-m", "cyclonedx_py", "environment",
        str(runtime_python), "--output-reproducible", "--pyproject", str(source / "pyproject.toml"),
        "--mc-type", "application", "--sv", "1.6", "--of", "JSON", "-o",
        str(evidence / "sbom.cdx.json")), commands / "sbom", source, 900)
    _write_exit(evidence / "sbom-exit-code.txt", sbom.exit_code)
    licenses = _execute((str(tool_python), "-I", "-B", str(source / "tools/supply_chain.py"),
        "licenses", "--root", str(source), "--site-packages", str(site), "--output",
        str(evidence / "runtime-license-inventory.json")), commands / "licenses", source, 300)
    _write_exit(evidence / "license-inventory-exit-code.txt", licenses.exit_code)
    if sbom.exit_code != 0 or licenses.exit_code != 0:
        raise MatrixError("SBOM or license collection failed")
    return audit.exit_code if audit.exit_code is not None else 2


def _validate(tool_python: Path, evidence: Path, target_root: Path,
              commands: Path, source: Path) -> tuple[int, int, int | None]:
    validate = _execute((str(tool_python), "-I", "-B", str(source / "tools/supply_chain.py"),
        "validate", "--root", str(source), "--evidence-dir", str(evidence)),
        commands / "validate", source, 300)
    verify_output = target_root / "supply-chain-manifest-verify.json"
    verify = _execute((str(tool_python), "-I", "-B", str(source / "tools/supply_chain.py"),
        "verify", "--root", str(source), "--evidence-dir", str(evidence), "--output",
        str(verify_output)), commands / "verify-manifest", source, 300)
    if validate.exit_code is None or validate.error is not None:
        raise MatrixError(f"validator execution failed: {validate.error}")
    if verify.exit_code is None or verify.error is not None:
        raise MatrixError(f"manifest verification execution failed: {verify.error}")
    findings: int | None = None
    status = evidence / "supply-chain-status.json"
    if status.is_file():
        try:
            payload = json.loads(status.read_text(encoding="utf-8"))
            raw = payload.get("findings") if isinstance(payload, dict) else None
            findings = len(raw) if isinstance(raw, list) else None
        except (OSError, UnicodeError, json.JSONDecodeError):
            findings = None
    return validate.exit_code, verify.exit_code, findings


def _run_version(result: AuditResult, launcher: str, output: Path, source: Path) -> None:
    key = result.key
    target_root = output / "evidence" / key
    commands = output / "raw-commands" / key
    target_root.mkdir()
    commands.mkdir()
    evidence = target_root / "supply-chain-evidence"
    evidence.mkdir()
    try:
        base = _probe_base(result.target, launcher, commands, source)
        tool_env = output / "environments" / f"audit-tools-py{result.target.replace('.', '')}"
        tool_python = _install_tools(base, tool_env, evidence, commands, source, result.target)
        lock = source / "locks" / key / "requirements.lock"
        regular_path(lock)
        runtime_env = output / "environments" / key
        runtime_python = _install_runtime(base, tool_python, runtime_env, lock,
                                          evidence, commands, source)
        result.audit_exit = _collect_analysis(tool_python, runtime_python, runtime_env,
                                              evidence, commands, source)
        result.validate_exit, result.manifest_exit, result.findings = _validate(
            tool_python, evidence, target_root, commands, source)
        if result.validate_exit == 0 and result.manifest_exit == 0:
            result.status = "AUDIT_PASS"
        elif result.manifest_exit == 0:
            result.status = "AUDIT_BLOCKED"
        else:
            result.status = "FAILED"
    except (MatrixError, StagingError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        result.status = "FAILED"
        result.error = f"{type(exc).__name__}: {exc}"
    write_record(target_root / "target-result.json", asdict(result))


def _prepare_output(source: Path, output: Path) -> Path:
    source = trusted_directory(source)
    trusted_directory(output.parent)
    destination = output.absolute()
    if destination.is_relative_to(source) or source.is_relative_to(destination):
        raise MatrixError("output must be separate from source")
    if destination.exists() or destination.is_symlink():
        raise MatrixError("output must be a new directory")
    destination.mkdir()
    for name in ("evidence", "raw-commands", "environments"):
        (destination / name).mkdir()
    return destination


def _archive(output: Path) -> Path:
    archive = output / "WaveHelm-supply-chain-matrix-results.zip"
    if archive.exists() or archive.is_symlink():
        raise MatrixError("result archive already exists")
    members: list[tuple[Path, FileSeal]] = []
    for name in ("evidence", "raw-commands"):
        root = trusted_directory(output / name)
        for seal in inventory(root):
            members.append((root / seal.path, FileSeal(f"{name}/{seal.path}", seal.size, seal.sha256)))
    manifest = output / "evidence-manifest.json"
    write_record(manifest, {"schema": "wavehelm-supply-chain-matrix-manifest-v1",
                            "files": [asdict(seal) for _, seal in members],
                            "excluded": ["environments/", "source-snapshot/", "archive itself"]})
    members.append((manifest, seal_file(manifest, manifest.name)))
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED) as target:
        for path, seal in members:
            if seal_file(path, seal.path) != seal:
                raise MatrixError(f"evidence changed before archive: {seal.path}")
            target.write(path, seal.path)
    with zipfile.ZipFile(archive) as check:
        if check.testzip() is not None:
            raise MatrixError("result archive CRC verification failed")
        for _, seal in members:
            data = check.read(seal.path)
            if len(data) != seal.size or hashlib.sha256(data).hexdigest() != seal.sha256:
                raise MatrixError(f"result archive bytes differ: {seal.path}")
    return archive


def _matrix_status(results: list[AuditResult], integrity: bool) -> str:
    if integrity and len(results) == 3 and all(r.status == "AUDIT_PASS" for r in results):
        return "MATRIX_AUDIT_PASS"
    if integrity and len(results) == 3 and all(r.status in {"AUDIT_PASS", "AUDIT_BLOCKED"} for r in results):
        return "MATRIX_AUDIT_BLOCKED"
    return "INCOMPLETE"


def run_matrix(source: Path, output: Path, launcher: str) -> tuple[dict[str, object], Path]:
    require_native_host()
    source = trusted_directory(source)
    seals = inventory(source)
    original = inventory_digest(seals)
    output = _prepare_output(source, output)
    snapshot = output / "source-snapshot"
    copy_sealed(source, snapshot, seals)
    results = [AuditResult(version) for version in VERSIONS]
    for result in results:
        _run_version(result, launcher, output, snapshot)
    source_ok = inventory_digest(inventory(source)) == original
    snapshot_ok = inventory_digest(inventory(snapshot)) == original
    status = _matrix_status(results, source_ok and snapshot_ok)
    summary = {"schema": "wavehelm-supply-chain-matrix-v1", "status": status,
        "source_digest": original, "source_unchanged": source_ok, "snapshot_unchanged": snapshot_ok,
        "targets": [asdict(result) for result in results], "human_review": "REQUIRED",
        "abi": "NOT_RUN", "application_tests": "NOT_RUN", "release_readiness": "NOT_VERIFIED",
        "finished_utc": datetime.now(timezone.utc).isoformat()}
    write_record(output / "evidence" / "SUMMARY.json", summary)
    return summary, _archive(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--launcher", default="py")
    args = parser.parse_args()
    try:
        require_native_host()
        launcher = shutil.which(args.launcher)
        if launcher is None:
            raise MatrixError("Python launcher missing")
        if args.output is None:
            local = os.environ.get("LOCALAPPDATA")
            if not local:
                raise MatrixError("LOCALAPPDATA is missing; supply --output")
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            args.output = Path(local) / f"WaveHelm-supply-chain-{stamp}-{uuid.uuid4().hex[:8]}"
        summary, archive = run_matrix(Path(__file__).resolve().parents[1], args.output, launcher)
    except (MatrixError, StagingError, OSError, UnicodeError) as exc:
        print(json.dumps({"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}))
        return 1
    print(json.dumps({"status": summary["status"], "results_archive": str(archive)}, indent=2))
    return 0 if summary["status"] == "MATRIX_AUDIT_PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
