"""Fault-injected resolver tests distinguish diagnostics from accepted locks."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import resolve_windows_lock as resolver
from tools.dependency_lock import LockError
from tools.windows_lock_support import CommandResult, MatrixError


@pytest.fixture
def requirements(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(resolver, "require_windows", lambda: "3.12")
    monkeypatch.setattr(resolver.importlib.metadata, "version", lambda name: "26.2.1")
    path = tmp_path / "requirements.txt"
    path.write_text("alpha==1.0\n", encoding="utf-8")
    return path


def failed_command(argv, folder, cwd, timeout):
    folder.mkdir()
    out, err = folder / "stdout.txt", folder / "stderr.txt"
    out.write_text("resolver context", encoding="utf-8")
    err.write_text("network unavailable", encoding="utf-8")
    (folder / "command.json").write_text('{"exit_code":1}', encoding="utf-8")
    return CommandResult(folder.name, argv, 1, 0.1, None, str(out), str(err))


def test_resolution_failure_keeps_diagnostics_and_rejects_bundle(requirements: Path, monkeypatch) -> None:
    monkeypatch.setattr(resolver, "run_command", failed_command)
    output = requirements.parent / "bundle"
    with pytest.raises(MatrixError):
        resolver.resolve(requirements, output)
    assert not output.exists()
    diagnostics = requirements.parent / "bundle-diagnostics"
    assert (diagnostics / "stderr.txt").read_text(encoding="utf-8") == "network unavailable"
    assert not list(requirements.parent.glob(".wavehelm-lock-*"))


def test_malformed_report_is_preserved_without_lock(requirements: Path, monkeypatch) -> None:
    def malformed(argv, folder, cwd, timeout):
        result = failed_command(argv, folder, cwd, timeout)
        report = Path(argv[argv.index("--report") + 1])
        report.write_text("{broken", encoding="utf-8")
        return CommandResult(result.label, result.argv, 0, 0.1, None, result.stdout, result.stderr)
    monkeypatch.setattr(resolver, "run_command", malformed)
    output = requirements.parent / "bundle"
    with pytest.raises(LockError):
        resolver.resolve(requirements, output)
    assert not output.exists()
    assert (requirements.parent / "bundle-diagnostics/pip-report.json").read_text(encoding="utf-8") == "{broken"


def test_invalid_roots_cannot_trigger_network(requirements: Path, monkeypatch) -> None:
    requirements.write_text("--extra-index-url https://untrusted.invalid\n", encoding="utf-8")
    def forbidden(*args, **kwargs):
        raise AssertionError("network command must not execute")
    monkeypatch.setattr(resolver, "run_command", forbidden)
    with pytest.raises(LockError):
        resolver.resolve(requirements, requirements.parent / "bundle")
    assert not (requirements.parent / "bundle-diagnostics").exists()


def test_existing_diagnostics_are_not_overwritten(requirements: Path) -> None:
    diagnostics = requirements.parent / "diagnostics"
    diagnostics.mkdir()
    marker = diagnostics / "retained.txt"
    marker.write_text("retained", encoding="utf-8")
    with pytest.raises(LockError, match="new directory"):
        resolver.resolve(requirements, requirements.parent / "bundle", diagnostics)
    assert marker.read_text(encoding="utf-8") == "retained"


def test_explicit_diagnostic_directory_is_used(requirements: Path, monkeypatch) -> None:
    monkeypatch.setattr(resolver, "run_command", failed_command)
    diagnostics = requirements.parent / "explicit-diagnostics"
    with pytest.raises(MatrixError):
        resolver.resolve(requirements, requirements.parent / "bundle", diagnostics)
    assert json.loads((diagnostics / "command.json").read_text(encoding="utf-8"))["exit_code"] == 1


def test_bundle_and_diagnostics_must_be_separate(requirements: Path) -> None:
    path = requirements.parent / "same"
    with pytest.raises(LockError, match="separate"):
        resolver.resolve(requirements, path, path)
