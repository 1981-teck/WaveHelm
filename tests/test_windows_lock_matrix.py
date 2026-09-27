"""Matrix contracts use explicit fixtures; only refusal/process tests run for real."""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from tools import windows_lock_matrix as matrix
from tools.dependency_lock import PIP_VERSION
from tools.staging_integrity import inventory, inventory_digest, write_record
from tools.windows_lock_support import MatrixError


def minimal_source(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    (root / "tools").mkdir(parents=True)
    for name in ("requirements.txt", "requirements-dev.txt", "tools/resolve_windows_lock.py",
                 "tools/verify_windows_install.py"):
        (root / name).write_text("synthetic fixture, not runnable source\n", encoding="utf-8")
    return root


def fake_success(result, base, resolver, output, snapshot):
    folder = output / "evidence" / result.key
    folder.mkdir()
    bundle = output / "locks" / result.key
    bundle.mkdir()
    for name in ("pip-report.json", "requirements.lock"):
        (bundle / name).write_text("synthetic fixture, not resolved graph", encoding="utf-8")
    result.lock_digest = inventory_digest(inventory(bundle))
    result.status = "INSTALLED_GRAPH_MATCH_NOT_AUDITED"


def fake_bootstrap(version, launcher, output, snapshot):
    return Path(sys.executable), Path(sys.executable)


def test_bootstrap_pins_match_existing_policy_and_hash_lengths() -> None:
    assert matrix.PIP_VERSION == PIP_VERSION
    assert "packaging==25.0" in matrix.BOOTSTRAP
    for line in matrix.BOOTSTRAP.splitlines():
        assert len(line.split("--hash=sha256:")[1]) == 64
    compile(matrix.PROBE, "<isolated-probe>", "exec")


def test_real_probe_is_parseable_and_process_is_isolated(tmp_path: Path) -> None:
    run = subprocess.run((sys.executable, "-I", "-B", "-c", matrix.PROBE),
                         capture_output=True, text=True, timeout=15, check=False, encoding="utf-8", errors="strict")
    assert run.returncode == 0
    data = json.loads(run.stdout)
    assert data["isolated"] is True
    assert data["platform"] == sys.platform
    assert data["schema"] == "wavehelm-live-inventory-v1"


def test_real_cli_refuses_non_windows_without_creating_output(tmp_path: Path) -> None:
    if sys.platform == "win32":
        pytest.skip("foreign platform refusal")
    script = Path(matrix.__file__)
    output = tmp_path / "run"
    run = subprocess.run((sys.executable, "-I", "-B", str(script), "--output", str(output)),
                         capture_output=True, text=True, timeout=15, check=False, encoding="utf-8", errors="strict")
    assert run.returncode == 1 and "native Windows" in run.stdout
    assert not output.exists()


@pytest.mark.parametrize("field,value", [("platform", "linux"), ("bits", 32), ("bits", "64"),
    ("version", "3.11"), ("isolated", False), ("implementation", "PyPy"), ("machine", "ARM64")])
def test_wrong_interpreter_probe_fails(tmp_path: Path, field: str, value: object) -> None:
    executable = tmp_path / "python.exe"
    executable.write_text("fixture", encoding="utf-8")
    data = {"platform": "win32", "bits": 64, "version": "3.12", "isolated": True,
            "implementation": "CPython", "machine": "AMD64", "executable": str(executable)}
    data[field] = value
    with pytest.raises(MatrixError):
        matrix.validate_probe(data, "3.12")


def test_cannot_reuse_or_nest_output(tmp_path: Path) -> None:
    source = minimal_source(tmp_path)
    with pytest.raises(MatrixError, match="separate"):
        matrix.prepare_output(source, source / "run")
    with pytest.raises(MatrixError, match="separate"):
        matrix.prepare_output(source, source.parent)
    output = tmp_path / "existing"
    output.mkdir()
    with pytest.raises(MatrixError, match="new directory"):
        matrix.prepare_output(source, output)


def test_six_fixture_targets_are_archived_without_source_or_env(tmp_path: Path, monkeypatch) -> None:
    source = minimal_source(tmp_path)
    before = inventory(source)
    monkeypatch.setattr(matrix, "require_native_host", lambda: None)
    monkeypatch.setattr(matrix, "bootstrap", fake_bootstrap)
    monkeypatch.setattr(matrix, "run_target", fake_success)
    summary, archive = matrix.run_matrix(source, tmp_path / "output", "fixture-launcher")
    assert summary["status"] == "MATRIX_INSTALLED_NOT_AUDITED"
    assert summary["release_readiness"] == "NOT_VERIFIED"
    assert summary["human_review"] == "REQUIRED"
    assert len(summary["targets"]) == 6
    assert inventory(source) == before
    with zipfile.ZipFile(archive) as data:
        assert "evidence/SUMMARY.json" in data.namelist()
        assert not any(n.startswith(("environments/", "source-snapshot/")) for n in data.namelist())


def test_missing_interpreter_marks_two_legs_and_continues(tmp_path: Path, monkeypatch) -> None:
    source = minimal_source(tmp_path)
    def bootstrap(version, launcher, output, snapshot):
        if version == "3.12":
            raise MatrixError("interpreter missing")
        return fake_bootstrap(version, launcher, output, snapshot)
    monkeypatch.setattr(matrix, "require_native_host", lambda: None)
    monkeypatch.setattr(matrix, "bootstrap", bootstrap)
    monkeypatch.setattr(matrix, "run_target", fake_success)
    summary, archive = matrix.run_matrix(source, tmp_path / "output", "fixture-launcher")
    assert summary["status"] == "INCOMPLETE"
    states = [r["status"] for r in summary["targets"]]
    assert states.count("BOOTSTRAP_FAILED") == 2
    assert states.count("INSTALLED_GRAPH_MATCH_NOT_AUDITED") == 4
    assert archive.is_file()


def test_single_target_failure_does_not_discard_other_legs(tmp_path: Path, monkeypatch) -> None:
    source = minimal_source(tmp_path)
    def target(result, base, resolver, output, snapshot):
        if result.key == "windows-py311-runtime":
            (output / "evidence" / result.key).mkdir()
            raise MatrixError("hash mismatch")
        fake_success(result, base, resolver, output, snapshot)
    monkeypatch.setattr(matrix, "require_native_host", lambda: None)
    monkeypatch.setattr(matrix, "bootstrap", fake_bootstrap)
    monkeypatch.setattr(matrix, "run_target", target)
    summary, archive = matrix.run_matrix(source, tmp_path / "output", "fixture-launcher")
    assert summary["status"] == "INCOMPLETE"
    assert sum(r["status"] == "FAILED" for r in summary["targets"]) == 1
    assert sum(r["status"] == "INSTALLED_GRAPH_MATCH_NOT_AUDITED" for r in summary["targets"]) == 5
    assert archive.is_file()


def test_status_strings_without_lock_bytes_are_not_sufficient(tmp_path: Path) -> None:
    (tmp_path / "locks").mkdir()
    results = [matrix.TargetResult(v, k, "INSTALLED_GRAPH_MATCH_NOT_AUDITED")
               for v in matrix.VERSIONS for k in matrix.KINDS]
    assert matrix.locks_unchanged(tmp_path, results) is False


def test_post_target_lock_tamper_forces_incomplete(tmp_path: Path, monkeypatch) -> None:
    source = minimal_source(tmp_path)
    def target(result, base, resolver, output, snapshot):
        fake_success(result, base, resolver, output, snapshot)
        if result.key == "windows-py313-dev":
            (output / "locks/windows-py311-runtime/requirements.lock").write_text("tampered", encoding="utf-8")
    monkeypatch.setattr(matrix, "require_native_host", lambda: None)
    monkeypatch.setattr(matrix, "bootstrap", fake_bootstrap)
    monkeypatch.setattr(matrix, "run_target", target)
    summary, archive = matrix.run_matrix(source, tmp_path / "output", "fixture-launcher")
    assert summary["status"] == "INCOMPLETE"
    assert summary["lock_bundles_unchanged"] is False


def test_interruption_preserves_incomplete_evidence(tmp_path: Path, monkeypatch) -> None:
    source = minimal_source(tmp_path)
    def interrupted(*args):
        raise KeyboardInterrupt()
    monkeypatch.setattr(matrix, "require_native_host", lambda: None)
    monkeypatch.setattr(matrix, "run_version", interrupted)
    summary, archive = matrix.run_matrix(source, tmp_path / "output", "fixture-launcher")
    assert summary["status"] == "INCOMPLETE" and "KeyboardInterrupt" in summary["error"]
    assert all(r["status"] == "NOT_RUN" for r in summary["targets"])
    assert archive.is_file()


def test_command_plan_installs_without_pip_seeds_and_enforces_hashes(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "output"
    for name in ("evidence", "locks", "environments"):
        (output / name).mkdir(parents=True)
    snapshot = minimal_source(tmp_path)
    calls = []
    def capture(argv, folder, root, timeout=900):
        calls.append(argv)
        folder.mkdir()
        path = folder / "stdout.txt"
        path.write_text('{"status":"INSTALLED_GRAPH_MATCH_NOT_AUDITED"}', encoding="utf-8")
        if folder.name == "resolve":
            bundle = Path(argv[argv.index("--bundle") + 1])
            bundle.mkdir()
            for name in ("requirements.lock", "pip-report.json"):
                (bundle / name).write_text("fixture", encoding="utf-8")
        return path
    monkeypatch.setattr(matrix, "execute", capture)
    monkeypatch.setattr(matrix, "validate_probe", lambda *args: Path(sys.executable))
    monkeypatch.setattr(matrix, "regular_path", lambda *args: None)
    result = matrix.TargetResult("3.12", "runtime")
    matrix.run_target(result, Path(sys.executable), Path(sys.executable), output, snapshot)
    install = next(c for c in calls if "--require-hashes" in c)
    assert "--python" in install and "--only-binary=:all:" in install
    assert "--ignore-installed" in install and "--report" in install
    create = next(c for c in calls if "venv" in c)
    assert "--without-pip" in create and "--system-site-packages" not in create
    assert not any(any(a in {"push", "Publish", "tag"} for a in c) for c in calls)
