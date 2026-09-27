"""Portable contracts for the native supply-chain matrix collector."""
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from tools.windows_supply_chain_matrix import AuditResult, _archive, _matrix_status

ROOT = Path(__file__).resolve().parents[1]


def test_status_requires_three_complete_targets_and_integrity() -> None:
    results = [AuditResult(version, status="AUDIT_PASS") for version in ("3.11", "3.12", "3.13")]
    assert _matrix_status(results, True) == "MATRIX_AUDIT_PASS"
    results[1].status = "AUDIT_BLOCKED"
    assert _matrix_status(results, True) == "MATRIX_AUDIT_BLOCKED"
    results[2].status = "FAILED"
    assert _matrix_status(results, True) == "INCOMPLETE"
    assert _matrix_status(results[:2], True) == "INCOMPLETE"
    assert _matrix_status([AuditResult("3.11", status="AUDIT_PASS") for _ in range(3)], False) == "INCOMPLETE"


def test_archive_contains_evidence_and_raw_commands_but_not_environments(tmp_path: Path) -> None:
    output = tmp_path / "result"
    (output / "evidence/target").mkdir(parents=True)
    (output / "raw-commands/target").mkdir(parents=True)
    (output / "environments/secret").mkdir(parents=True)
    (output / "evidence/target/result.json").write_text("{}", encoding="utf-8")
    (output / "raw-commands/target/stdout.txt").write_text("ok\n", encoding="utf-8")
    (output / "environments/secret/private.txt").write_text("excluded", encoding="utf-8")
    archive = _archive(output)
    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
    assert "evidence/target/result.json" in names
    assert "raw-commands/target/stdout.txt" in names
    assert "evidence-manifest.json" in names
    assert all(not name.startswith("environments/") for name in names)


def test_wrapper_and_documentation_preserve_fail_closed_boundaries() -> None:
    wrapper = (ROOT / "tools/WaveHelm-SupplyChainMatrix.ps1").read_text(encoding="utf-8")
    for required in ("windows_supply_chain_matrix.py", "PYTHON_MANAGER_AUTOMATIC_INSTALL",
                     "finally {", "exit $Code"):
        assert required in wrapper
    for forbidden in ("Invoke-Expression", "Set-ExecutionPolicy", "ExecutionPolicy Bypass", "git push"):
        assert forbidden not in wrapper
    docs = (ROOT / "docs/windows-supply-chain-matrix.md").read_text(encoding="utf-8")
    for required in ("MATRIX_AUDIT_PASS", "MATRIX_AUDIT_BLOCKED", "INCOMPLETE",
                     "human review", "NOT_VERIFIED", "results_archive"):
        assert required in docs


def test_non_windows_execution_refuses_before_output(tmp_path: Path) -> None:
    if sys.platform == "win32":
        pytest.skip("foreign-platform refusal test")
    output = tmp_path / "must-not-exist"
    command = [sys.executable, "-B", str(ROOT / "tools/windows_supply_chain_matrix.py"),
               "--output", str(output)]
    result = subprocess.run(command, text=True, capture_output=True, timeout=15, check=False)
    assert result.returncode == 1
    assert "native Windows" in result.stdout
    assert not output.exists()
