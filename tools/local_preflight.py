"""Execute local development gates; explicitly not native or remote qualification.

No filtered tests, fake dependency modules, network install or fallback build.
Missing tools, timeout, test failure or source drift cause a failed preflight.
Only a disposable staged copy is tested; the caller source remains untouched.
"""
from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
import os
from pathlib import Path
import subprocess
import sys
import time

from tools.staging_integrity import FileSeal, StagingError, inventory, inventory_digest, seal_file, write_record


@dataclass(frozen=True)
class CheckResult:
    name: str
    argv: tuple[str, ...]
    exit_code: int
    elapsed_seconds: float
    log: FileSeal


REQUIRED_LOCAL = ("pip-check", "hygiene-pre", "full-tests", "cleanup", "hygiene-post", "build")


def run_check(name: str, argv: tuple[str, ...], root: Path, evidence: Path) -> CheckResult:
    """Preserve exit and raw output, including timeout and executable failures."""
    log = evidence / f"{name}.log"
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONHASHSEED="0",
               PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    for key in ("PYTHONPATH", "PYTHONHOME", "PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
        env.pop(key, None)
    started = time.monotonic()
    with log.open("xb") as stream:
        try:
            result = subprocess.run(argv, cwd=root, env=env, stdout=stream,
                                    stderr=subprocess.STDOUT, timeout=1200, check=False)
            code = result.returncode
        except subprocess.TimeoutExpired as exc:
            stream.write(f"\nTIMEOUT: {exc}\n".encode("utf-8"))
            code = 124
        except OSError as exc:
            stream.write(f"\nEXECUTION ERROR: {type(exc).__name__}: {exc}\n".encode("utf-8"))
            code = 127
    seal = seal_file(log, log.name)
    return CheckResult(name, argv, code, round(time.monotonic() - started, 6), seal)


def _commands(root: Path, evidence: Path) -> tuple[tuple[str, tuple[str, ...]], ...]:
    python = (sys.executable, "-B")
    hygiene = (*python, "tools/source_hygiene.py")
    return (
        ("pip-check", (*python, "-m", "pip", "check")),
        ("hygiene-pre", (*hygiene, "check", "--root", str(root))),
        ("full-tests", (*python, "-m", "pytest", "-p", "no:cacheprovider", "-q", "tests",
                        "--basetemp", str(evidence.parent / "test-temp"),
                        "--junitxml", str(evidence / "full-tests.junit.xml"))),
        ("cleanup", (*hygiene, "clean", "--root", str(root))),
        ("hygiene-post", (*hygiene, "check", "--root", str(root))),
        ("build", (*python, "-m", "build", "--no-isolation", "--outdir", str(evidence.parent / "dist"))),
    )


def preflight(root: Path, evidence: Path, expected_digest: str) -> dict[str, object]:
    """Execute all gates and retain both failures and successes without promotion."""
    evidence.mkdir(exist_ok=False)
    before = inventory(root)
    if inventory_digest(before) != expected_digest:
        raise StagingError("staged source differs before preflight")
    syntax_errors: list[str] = []
    for seal in before:
        if seal.path.endswith(".py"):
            try:
                ast.parse((root / seal.path).read_bytes(), filename=seal.path)
            except (SyntaxError, UnicodeError) as exc:
                syntax_errors.append(f"{seal.path}: {exc}")
    results = [run_check(name, argv, root, evidence) for name, argv in _commands(root, evidence)]
    # Build may generate egg-info in its copy; the established cleanup policy owns it.
    results.append(run_check("cleanup-after-build", (sys.executable, "-B", "tools/source_hygiene.py",
                                                    "clean", "--root", str(root)), root, evidence))
    unchanged = inventory(root) == before
    success = unchanged and not syntax_errors and all(r.exit_code == 0 for r in results)
    artifacts = tuple(seal_file(p, p.name) for p in sorted((evidence.parent / "dist").glob("*")))
    has_wheel = any(s.path.endswith(".whl") for s in artifacts)
    has_sdist = any(s.path.endswith(".tar.gz") for s in artifacts)
    success = success and len(artifacts) == 2 and has_wheel and has_sdist
    report = {"schema": "wavehelm-local-preflight-v1", "source_digest": expected_digest,
              "scope": "local-development-only", "verdict": "PASS" if success else "FAIL",
              "syntax_errors": syntax_errors, "source_unchanged": unchanged,
              "checks": [asdict(r) for r in results], "artifacts": [asdict(a) for a in artifacts],
              "native_windows_qualification": "NOT_VERIFIED", "release_readiness": "NOT_VERIFIED"}
    write_record(evidence / "preflight.json", report)
    return report


def validate_local_receipt(session: Path, digest: str) -> None:
    """Recheck receipt scope, gate outcomes and sealed logs/artifacts before commit."""
    from tools.staging_integrity import read_record
    evidence = session / "evidence"
    raw = read_record(evidence / "preflight.json")
    if (raw.get("schema") != "wavehelm-local-preflight-v1" or raw.get("source_digest") != digest
            or raw.get("scope") != "local-development-only" or raw.get("verdict") != "PASS"
            or raw.get("source_unchanged") is not True or raw.get("syntax_errors") != []):
        raise StagingError("preflight receipt does not authorize a local snapshot commit")
    checks = raw.get("checks")
    if not isinstance(checks, list) or len(checks) != len(REQUIRED_LOCAL) + 1:
        raise StagingError("preflight check inventory differs")
    commands = dict(_commands(session / "source", evidence))
    commands["cleanup-after-build"] = commands["cleanup"]
    expected = [*REQUIRED_LOCAL, "cleanup-after-build"]
    for name, record in zip(expected, checks):
        if not isinstance(record, dict) or record.get("name") != name or type(record.get("exit_code")) is not int or record["exit_code"] != 0:
            raise StagingError(f"missing or failed check: {name}")
        if record.get("argv") != list(commands[name]):
            raise StagingError(f"preflight command differs: {name}")
        actual = asdict(seal_file(evidence / f"{name}.log", f"{name}.log"))
        if actual != record.get("log"):
            raise StagingError(f"preflight log changed: {name}")
    artifacts = raw.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != 2:
        raise StagingError("expected one wheel and one sdist")
    actual = [asdict(seal_file(p, p.name)) for p in sorted((session / "dist").glob("*"))]
    names = [item["path"] for item in actual]
    if (artifacts != actual or sum(n.endswith(".whl") for n in names) != 1
            or sum(n.endswith(".tar.gz") for n in names) != 1):
        raise StagingError("built artifacts changed or have incorrect kinds")
