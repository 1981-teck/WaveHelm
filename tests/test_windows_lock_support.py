"""Real portable command/archiving tests; no Windows qualification is implied."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import zipfile

import pytest

from tools.staging_integrity import StagingError
from tools.windows_lock_support import (
    MatrixError, clean_environment, evidence_archive, require_command, run_command,
)


def test_command_records_separate_streams_and_nonzero_exit(tmp_path: Path) -> None:
    code = "import sys; print('stdout'); print('stderr',file=sys.stderr);sys.exit(7)"
    result = run_command((sys.executable, "-I", "-c", code), tmp_path / "failure", tmp_path)
    assert not result.ok and result.exit_code == 7
    assert Path(result.stdout).read_text(encoding="utf-8").strip() == "stdout"
    assert Path(result.stderr).read_text(encoding="utf-8").strip() == "stderr"
    assert json.loads((tmp_path / "failure/command.json").read_text(encoding="utf-8"))["exit_code"] == 7
    with pytest.raises(MatrixError):
        require_command(result)


def test_arguments_are_not_evaluated_by_shell(tmp_path: Path) -> None:
    argument = "name with spaces; & echo NOT_A_COMMAND"
    result = run_command((sys.executable, "-I", "-c", "import sys;print(sys.argv[1])", argument),
                         tmp_path / "literal-args", tmp_path)
    require_command(result)
    assert Path(result.stdout).read_text(encoding="utf-8").strip() == argument


def test_missing_command_and_timeout_are_recorded(tmp_path: Path) -> None:
    missing = run_command((str(tmp_path / "nonexistent"),), tmp_path / "missing", tmp_path)
    assert missing.exit_code is None and "FileNotFoundError" in missing.error
    timeout = run_command((sys.executable, "-I", "-c", "import time;time.sleep(20)"),
                          tmp_path / "timeout", tmp_path, timeout=1)
    assert not timeout.ok and "TimeoutExpired" in timeout.error
    assert (tmp_path / "timeout/command.json").is_file()


def test_existing_output_is_not_overwritten(tmp_path: Path) -> None:
    folder = tmp_path / "existing"
    folder.mkdir()
    marker = folder / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        run_command((sys.executable, "-V"), folder, tmp_path)
    assert marker.read_text(encoding="utf-8") == "keep"


def test_environment_does_not_inherit_pip_python_or_pytest_overrides(monkeypatch) -> None:
    for key in ("PIP_INDEX_URL", "PYTHONPATH", "PYTEST_ADDOPTS", "PYLAUNCHER_ALLOW_INSTALL",
                "VIRTUAL_ENV", "CONDA_PREFIX", "PYTHON_MANAGER_AUTOMATIC_INSTALL"):
        monkeypatch.setenv(key, "untrusted")
    env = clean_environment()
    assert not any(env.get(key) == "untrusted" for key in (
        "PIP_INDEX_URL", "PYTHONPATH", "PYTEST_ADDOPTS", "PYLAUNCHER_ALLOW_INSTALL",
        "VIRTUAL_ENV", "CONDA_PREFIX"))
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert env["PYTHON_MANAGER_AUTOMATIC_INSTALL"] == "false"
    assert os.environ["PYTHON_MANAGER_AUTOMATIC_INSTALL"] == "untrusted"


def test_archive_excludes_environments_and_snapshot_and_checks_manifest(tmp_path: Path) -> None:
    for name in ("evidence", "locks", "environments", "source-snapshot"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "file.txt").write_text(name, encoding="utf-8")
    archive = evidence_archive(tmp_path)
    with zipfile.ZipFile(archive) as data:
        assert set(data.namelist()) == {"evidence/file.txt", "locks/file.txt", "evidence-manifest.json"}
        manifest = json.loads(data.read("evidence-manifest.json"))
        assert len(manifest["files"]) == 2
    manifest_before = (tmp_path / "evidence-manifest.json").read_bytes()
    (tmp_path / "evidence/file.txt").write_text("changed after archiving", encoding="utf-8")
    with pytest.raises(FileExistsError):
        evidence_archive(tmp_path)
    assert (tmp_path / "evidence-manifest.json").read_bytes() == manifest_before


def test_archive_rejects_linked_evidence(tmp_path: Path) -> None:
    (tmp_path / "evidence").mkdir()
    (tmp_path / "locks").mkdir()
    target = tmp_path / "target.txt"
    target.write_text("external", encoding="utf-8")
    try:
        (tmp_path / "evidence/link.txt").symlink_to(target)
    except OSError:
        pytest.skip("symlink privilege is unavailable")
    with pytest.raises(StagingError):
        evidence_archive(tmp_path)


@pytest.mark.parametrize("timeout", [0, -1, 3601, True])
def test_invalid_timeout_never_launches(tmp_path: Path, timeout: int) -> None:
    with pytest.raises(MatrixError):
        run_command((sys.executable, "-V"), tmp_path / "invalid", tmp_path, timeout)
    assert not (tmp_path / "invalid").exists()
