from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools.source_hygiene import HygieneError, cleanup, evaluate, load_policy

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "tools" / "source_hygiene_policy.json"


def _write_policy(tmp_path: Path, **updates: object) -> Path:
    raw = json.loads(POLICY.read_text(encoding="utf-8"))
    raw["legacy_runtime_asserts"] = updates.pop("legacy_runtime_asserts", [])
    raw.update(updates)
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def test_repository_passes_source_hygiene_gate() -> None:
    policy = load_policy(POLICY)
    cleanup(ROOT, policy)
    report = evaluate(ROOT, policy)
    assert report["verdict"] == "PASS", report["findings"]


def test_cli_writes_machine_readable_report(tmp_path: Path) -> None:
    cleanup(ROOT, load_policy(POLICY))
    output = tmp_path / "report.json"
    completed = subprocess.run(
        [
            sys.executable,
            "tools/source_hygiene.py",
            "check",
            "--root",
            str(ROOT),
            "--output",
            str(output),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["verdict"] == "PASS"


def test_gate_rejects_unpinned_action(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    workflow = root / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("steps:\n  - uses: actions/checkout@v4\n", encoding="utf-8")
    (root / "sample.py").write_text("def ok():\n    return 1\n", encoding="utf-8")
    policy_path = _write_policy(
        tmp_path,
        required_paths=[],
        legacy_oversize_files={},
        legacy_oversize_functions={},
        legacy_mixed_line_endings=[],
    )
    report = evaluate(root, load_policy(policy_path))
    assert report["verdict"] == "FAIL"
    assert any(str(item).startswith("action_unpinned:") for item in report["findings"])


def test_gate_rejects_runtime_assert_and_generated_output(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "bad.py").write_text("def f(x):\n    assert x\n", encoding="utf-8")
    (root / ".pytest_cache").mkdir()
    (root / ".pytest_cache" / "state").write_text("x", encoding="utf-8")
    (root / ".github" / "workflows").mkdir(parents=True)
    policy_path = _write_policy(
        tmp_path,
        required_paths=[],
        legacy_oversize_files={},
        legacy_oversize_functions={},
        legacy_mixed_line_endings=[],
    )
    report = evaluate(root, load_policy(policy_path))
    assert "runtime_assert:new:src/bad.py:2:1" in report["findings"]
    assert "generated:.pytest_cache/state" in report["findings"]


def test_cleanup_removes_only_authorized_untracked_output(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / ".github" / "workflows").mkdir(parents=True)
    cache = root / "src" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "x.pyc").write_bytes(b"cache")
    source = root / "src" / "keep.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    policy_path = _write_policy(
        tmp_path,
        required_paths=[],
        legacy_oversize_files={},
        legacy_oversize_functions={},
        legacy_mixed_line_endings=[],
    )
    report = cleanup(root, load_policy(policy_path))
    assert report["verdict"] == "PASS"
    assert source.exists()
    assert not cache.exists()


def test_cleanup_unlinks_generated_symlink_without_touching_target(tmp_path: Path) -> None:
    if not hasattr(Path, "symlink_to"):
        pytest.skip("symlinks unsupported")
    root = tmp_path / "repo"
    target = tmp_path / "outside"
    target.mkdir()
    marker = target / "marker.txt"
    marker.write_text("preserve", encoding="utf-8")
    link = root / ".pytest_cache"
    root.mkdir()
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable")
    policy_path = _write_policy(
        tmp_path,
        required_paths=[],
        legacy_oversize_files={},
        legacy_oversize_functions={},
        legacy_mixed_line_endings=[],
    )
    report = cleanup(root, load_policy(policy_path))
    assert report["verdict"] == "PASS"
    assert not link.exists()
    assert marker.read_text(encoding="utf-8") == "preserve"


def test_gate_ignores_binary_assets_but_rejects_invalid_text(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / "asset.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe")
    (root / "broken.json").write_bytes(b"{\xff}")
    policy_path = _write_policy(
        tmp_path,
        required_paths=[],
        legacy_oversize_files={},
        legacy_oversize_functions={},
        legacy_mixed_line_endings=[],
    )
    report = evaluate(root, load_policy(policy_path))
    assert "utf8:asset.png" not in report["findings"]
    assert "utf8:broken.json" in report["findings"]


def test_gate_rejects_sensitive_key_material_by_filename(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / "release-signing.key").write_text("fixture", encoding="utf-8")
    policy_path = _write_policy(
        tmp_path,
        required_paths=[],
        legacy_oversize_files={},
        legacy_oversize_functions={},
        legacy_mixed_line_endings=[],
    )
    report = evaluate(root, load_policy(policy_path))
    assert "generated:release-signing.key" in report["findings"]


def test_gate_requires_runtime_assert_allowances_to_be_exact(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / "src" / "legacy.py").write_text("def f(x):\n    assert x\n", encoding="utf-8")
    policy_path = _write_policy(
        tmp_path,
        required_paths=[],
        legacy_oversize_files={},
        legacy_oversize_functions={},
        legacy_mixed_line_endings=[],
        legacy_runtime_asserts=["src/legacy.py:2"],
    )
    assert evaluate(root, load_policy(policy_path))["verdict"] == "PASS"
    (root / "src" / "legacy.py").write_text("def f(x):\n    return bool(x)\n", encoding="utf-8")
    report = evaluate(root, load_policy(policy_path))
    assert "runtime_assert:stale:src/legacy.py:2:1" in report["findings"]


def test_cli_does_not_create_bytecode_in_source_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = load_policy(POLICY)
    cleanup(ROOT, policy)
    output = tmp_path / "report.json"
    monkeypatch.delenv("PYTHONPYCACHEPREFIX", raising=False)
    monkeypatch.setattr(sys, "pycache_prefix", None, raising=False)
    environment = dict(os.environ)
    environment.pop("PYTHONDONTWRITEBYTECODE", None)
    completed = subprocess.run(
        [
            sys.executable,
            "tools/source_hygiene.py",
            "check",
            "--root",
            str(ROOT),
            "--output",
            str(output),
        ],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert not (ROOT / "tools" / "__pycache__").exists()


def test_gate_rejects_unknown_untracked_file(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".github" / "workflows").mkdir(parents=True)
    tracked = root / "tracked.py"
    tracked.write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "tracked.py"], cwd=root, check=True)
    (root / "unexpected.json").write_text("{}\n", encoding="utf-8")
    policy_path = _write_policy(
        tmp_path,
        required_paths=[],
        allowed_untracked_paths=[],
        legacy_oversize_files={},
        legacy_oversize_functions={},
        legacy_mixed_line_endings=[],
    )
    report = evaluate(root, load_policy(policy_path))
    assert "untracked:unexpected.json" in report["findings"]


def test_gate_allows_only_explicit_ci_evidence_path(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".github" / "workflows").mkdir(parents=True)
    tracked = root / "tracked.py"
    tracked.write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "tracked.py"], cwd=root, check=True)
    (root / "gate.json").write_text("{}\n", encoding="utf-8")
    policy_path = _write_policy(
        tmp_path,
        required_paths=[],
        allowed_untracked_paths=["gate.json"],
        legacy_oversize_files={},
        legacy_oversize_functions={},
        legacy_mixed_line_endings=[],
    )
    report = evaluate(root, load_policy(policy_path))
    assert report["verdict"] == "PASS", report["findings"]
