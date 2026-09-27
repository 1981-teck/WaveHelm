"""Contract/fault tests for LOCAL staging, not native release qualification.

Successful receipts below are deliberately synthetic test fixtures. They exercise
state transitions only and never constitute dependency, build or Windows proof.
Failure-path tests also execute real subprocesses without replacing dependencies.
"""
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from tools import local_preflight as gate
from tools import stage_candidate as stage
from tools import staging_integrity as integrity
from tools.staging_integrity import StagingError


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "input"
    root.mkdir()
    (root / "app.py").write_text('"""State-machine fixture only."""\nVALUE = 3\n', encoding="utf-8")
    return root


def synthetic_receipt(root, evidence, digest):
    """Unit fixture: seven successful records, not seven executed commands."""
    evidence.mkdir()
    checks = []
    commands = list(gate._commands(root, evidence))
    commands.append(("cleanup-after-build", commands[3][1]))
    for name, argv in commands:
        path = evidence / f"{name}.log"
        path.write_text("SYNTHETIC UNIT FIXTURE; NOT EXECUTION EVIDENCE\n", encoding="utf-8")
        checks.append({"name": name, "argv": list(argv), "exit_code": 0,
                       "elapsed_seconds": 0.0, "log": asdict(integrity.seal_file(path, path.name))})
    dist = evidence.parent / "dist"
    dist.mkdir()
    for name in ("fixture.whl", "fixture.tar.gz"):
        (dist / name).write_bytes(b"UNIT FIXTURE; NOT AN INSTALLABLE BUILD")
    result = {"schema": "wavehelm-local-preflight-v1", "source_digest": digest,
              "scope": "local-development-only", "verdict": "PASS",
              "source_unchanged": True, "syntax_errors": [], "checks": checks,
              "artifacts": [asdict(integrity.seal_file(p, p.name)) for p in sorted(dist.iterdir())],
              "native_windows_qualification": "NOT_VERIFIED", "release_readiness": "NOT_VERIFIED"}
    integrity.write_record(evidence / "preflight.json", result)
    return result


@pytest.fixture
def prepared(source, tmp_path, monkeypatch):
    monkeypatch.setattr(stage, "preflight", synthetic_receipt)
    session = tmp_path / "session"
    result = stage.prepare(source, session)
    assert result["state"] == "PREPARED_LOCAL"
    assert result["release_readiness"] == "NOT_VERIFIED"
    return session


def test_inventory_copy_is_byte_identical_and_excludes_only_root_git(source, tmp_path):
    (source / ".git").mkdir()
    (source / ".git/private-state").write_text("not source", encoding="utf-8")
    before = integrity.inventory(source)
    target = tmp_path / "copy"
    integrity.copy_sealed(source, target, before)
    assert integrity.inventory(target) == before
    assert not (target / ".git").exists()


@pytest.mark.parametrize("name", ["CON.txt", "AUX", "bad.", "bad "])
def test_nonportable_names_rejected(source, name):
    if os.name == "nt":
        with pytest.raises(StagingError):
            integrity._portable_name(name)
    else:
        (source / name).write_text("bad", encoding="utf-8")
        with pytest.raises(StagingError, match="nonportable"):
            integrity.inventory(source)


def test_case_aliases_rejected(source):
    if os.name == "nt":
        assert integrity._portable_name("app.py") == integrity._portable_name("APP.PY")
        return
    (source / "APP.PY").write_text("bad", encoding="utf-8")
    with pytest.raises(StagingError, match="collision"):
        integrity.inventory(source)


def test_hardlinks_rejected(source):
    os.link(source / "app.py", source / "alias.py")
    with pytest.raises(StagingError, match="hard link"):
        integrity.inventory(source)


def test_links_rejected_without_following_target(source, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("preserve", encoding="utf-8")
    try:
        (source / "link.txt").symlink_to(outside)
    except OSError:
        pytest.skip("native symlink creation not permitted")
    with pytest.raises(StagingError, match="link"):
        integrity.inventory(source)
    assert outside.read_text(encoding="utf-8") == "preserve"


def test_empty_and_bounded_inventory(tmp_path, source, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(StagingError, match="empty"):
        integrity.inventory(empty)
    monkeypatch.setattr(integrity, "MAX_FILE_BYTES", 1)
    with pytest.raises(StagingError, match="budget"):
        integrity.inventory(source)


def test_copy_refuses_drift_and_existing_target(source, tmp_path):
    seals = integrity.inventory(source)
    (source / "app.py").write_text("changed\n", encoding="utf-8")
    target = tmp_path / "copy"
    with pytest.raises(StagingError, match="mismatch"):
        integrity.copy_sealed(source, target, seals)
    with pytest.raises(FileExistsError):
        integrity.copy_sealed(source, target, seals)


@pytest.mark.parametrize("data", ['{"a":1,"a":2}', '{"a":NaN}', '[]', '{bad'])
def test_record_rejects_duplicates_nonfinite_nonobject_and_invalid(tmp_path, data):
    path = tmp_path / "state.json"
    path.write_text(data, encoding="utf-8")
    with pytest.raises(StagingError):
        integrity.read_record(path)


def test_parent_traversal_and_source_session_overlap_rejected(source, tmp_path):
    with pytest.raises(StagingError, match="parent traversal"):
        integrity.trusted_directory(source / "..")
    with pytest.raises(StagingError, match="disjoint"):
        stage.prepare(source, source / "session")
    with pytest.raises(StagingError, match="parent traversal"):
        stage.prepare(source, tmp_path / ".." / "session")


def test_real_failing_preflight_has_no_commit_and_does_not_modify_input(source, tmp_path):
    before = integrity.inventory(source)
    session = tmp_path / "real-failure"
    result = stage.prepare(source, session)
    assert result["state"] == "FAILED"
    receipt = integrity.read_record(session / "evidence/preflight.json")
    assert receipt["verdict"] == "FAIL"
    assert any(item["exit_code"] != 0 for item in receipt["checks"])
    assert stage.git(session / "source", "rev-parse", "--verify", "--quiet", "HEAD", allowed=(0, 1)) == ""
    assert integrity.inventory(source) == before
    assert not (source / ".git").exists()
    with pytest.raises(StagingError):
        stage.commit(session)


def test_prepare_existing_session_does_not_overwrite(prepared, source):
    before = (prepared / "state.json").read_bytes()
    with pytest.raises(FileExistsError):
        stage.prepare(source, prepared)
    assert (prepared / "state.json").read_bytes() == before


def test_local_commit_idempotent_and_caller_untouched(prepared, source):
    before = integrity.inventory(source)
    first = stage.commit(prepared)
    second = stage.commit(prepared)
    assert first["local_commit"] == second["local_commit"]
    assert second["state"] == "COMMITTED_LOCAL"
    assert second["release_readiness"] == "NOT_VERIFIED"
    assert stage.git(prepared / "source", "rev-list", "--count", "HEAD") == "1"
    assert integrity.inventory(source) == before
    assert not (source / ".git").exists()


def test_resume_after_commit_before_state_save(prepared):
    first = stage.commit(prepared)
    record = integrity.read_record(prepared / "state.json")
    stage._save(prepared, record, "COMMITTING_LOCAL")
    stage.git(prepared / "source", "pack-refs", "--all")
    assert stage.commit(prepared)["local_commit"] == first["local_commit"]


@pytest.mark.parametrize("relative", ["source/app.py", "evidence/preflight.json",
                                      "evidence/full-tests.log", "dist/fixture.whl"])
def test_commit_rejects_changed_source_receipt_log_or_artifact(prepared, relative):
    path = prepared / relative
    path.write_bytes(path.read_bytes() + b"\nTAMPER\n")
    with pytest.raises(StagingError):
        stage.commit(prepared)


@pytest.mark.parametrize("offset", [-stage.MAX_AGE_SECONDS - 1, 60])
def test_commit_rejects_expired_or_future_receipt(prepared, offset):
    record = integrity.read_record(prepared / "state.json")
    record["prepared_unix"] = time.time() + offset
    integrity.write_record(prepared / "state.json", record)
    with pytest.raises(StagingError, match="expired"):
        stage.commit(prepared)


def test_commit_rejects_remote_without_network_call(prepared):
    stage.git(prepared / "source", "remote", "add", "forbidden", "https://invalid.invalid/repo")
    with pytest.raises(StagingError, match="remote"):
        stage.commit(prepared)


def test_cancel_preserves_diagnostics_and_refuses_commit(prepared):
    original = (prepared / "evidence/preflight.json").read_bytes()
    assert stage.cancel(prepared)["state"] == "CANCELLED"
    assert (prepared / "evidence/preflight.json").read_bytes() == original
    with pytest.raises(StagingError):
        stage.commit(prepared)


def test_concurrent_operation_lease_rejected(prepared):
    with stage.operation_lease(prepared):
        with pytest.raises(StagingError, match="another process"):
            with stage.operation_lease(prepared):
                pytest.fail("must not acquire twice")


def test_inherited_git_redirection_is_not_used(prepared, tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "outside.git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "outside.index"))
    assert stage.commit(prepared)["state"] == "COMMITTED_LOCAL"
    assert not (tmp_path / "outside.git").exists()
    assert not (tmp_path / "outside.index").exists()


def test_receipt_rejects_wrong_command_even_when_resealed(prepared):
    path = prepared / "evidence/preflight.json"
    receipt = integrity.read_record(path)
    receipt["checks"][0]["argv"] = ["echo", "fake"]
    integrity.write_record(path, receipt)
    state = integrity.read_record(prepared / "state.json")
    state["receipt_sha256"] = integrity.seal_file(path, path.name).sha256
    integrity.write_record(prepared / "state.json", state)
    with pytest.raises(StagingError, match="command differs"):
        stage.commit(prepared)


def test_real_subprocess_nonzero_and_missing_executable_are_recorded(tmp_path):
    result = gate.run_check("nonzero", (sys.executable, "-c", "raise SystemExit(7)"), tmp_path, tmp_path)
    assert result.exit_code == 7
    missing = gate.run_check("missing", (str(tmp_path / "absent-executable"),), tmp_path, tmp_path)
    assert missing.exit_code == 127
    assert "EXECUTION ERROR" in (tmp_path / "missing.log").read_text(encoding="utf-8")


def test_timeout_is_failure_with_diagnostic(tmp_path, monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("fixture", 1)
    monkeypatch.setattr(gate.subprocess, "run", timeout)
    result = gate.run_check("timeout", (sys.executable,), tmp_path, tmp_path)
    assert result.exit_code == 124
    assert "TIMEOUT" in (tmp_path / "timeout.log").read_text(encoding="utf-8")


def test_preflight_blocks_inherited_plugin_and_test_selection_injection(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTEST_PLUGINS", "not_a_project_plugin")
    monkeypatch.setenv("PYTEST_ADDOPTS", "--ignore=tests")
    monkeypatch.setenv("PYTHONPATH", "not-a-project-path")
    code = "import os,json;print(json.dumps({k:os.getenv(k) for k in ['PYTEST_DISABLE_PLUGIN_AUTOLOAD','PYTEST_PLUGINS','PYTEST_ADDOPTS','PYTHONPATH']}))"
    result = gate.run_check("environment", (sys.executable, "-c", code), tmp_path, tmp_path)
    assert result.exit_code == 0
    values = json.loads((tmp_path / "environment.log").read_text(encoding="utf-8"))
    assert values == {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTEST_PLUGINS": None,
                      "PYTEST_ADDOPTS": None, "PYTHONPATH": None}


def test_ci_has_same_plugin_autoload_policy_as_preflight():
    root = Path(__file__).resolve().parents[1]
    text = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert 'PYTEST_DISABLE_PLUGIN_AUTOLOAD: "1"' in text
