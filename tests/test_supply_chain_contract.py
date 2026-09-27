from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tools.supply_chain_contract import (
    SupplyChainError,
    active_exception,
    canonicalize_name,
    load_policy,
    parse_freeze,
    parse_requirements,
    read_json,
    sha256_file,
    validate_project_identity,
    write_json_atomic,
)

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "tools" / "supply_chain_policy.json"


def test_repository_policy_and_runtime_requirements_are_strict() -> None:
    policy = load_policy(POLICY_PATH)
    requirements = parse_requirements(ROOT / "requirements.txt", policy.max_requirements)
    assert policy.project_name == "wavehelm"
    assert policy.project_version == "1.0.2"
    assert policy.pip_version == "26.2.1"
    assert policy.pip_audit_version == "2.10.1"
    assert policy.cyclonedx_bom_version == "7.3.1"
    assert policy.vulnerability_exceptions == ()
    assert {item.canonical_name for item in requirements} == {
        "comtypes",
        "matplotlib",
        "numpy",
        "opencv-python",
        "pillow",
        "pygame",
        "pywin32",
        "scipy",
        "soundfile",
        "tinytag",
        "wxpython",
    }


def test_requirement_parser_rejects_unpinned_duplicate_and_empty_files(tmp_path: Path) -> None:
    path = tmp_path / "requirements.txt"
    path.write_text("demo>=1\n", encoding="utf-8")
    with pytest.raises(SupplyChainError, match="exact pin"):
        parse_requirements(path, 10)
    path.write_text("Demo==1\ndemo_pkg==2\ndemo-pkg==3\n", encoding="utf-8")
    with pytest.raises(SupplyChainError, match="duplicate"):
        parse_requirements(path, 10)
    path.write_text("# comments only\n", encoding="utf-8")
    with pytest.raises(SupplyChainError, match="empty"):
        parse_requirements(path, 10)


def test_freeze_parser_rejects_markers_direct_urls_and_boolean_limits(tmp_path: Path) -> None:
    path = tmp_path / "freeze.txt"
    path.write_text('demo==1; platform_system == "Windows"\n', encoding="utf-8")
    with pytest.raises(SupplyChainError, match="installed pin"):
        parse_freeze(path, 10)
    path.write_text("demo @ https://example.invalid/demo.whl\n", encoding="utf-8")
    with pytest.raises(SupplyChainError, match="installed pin"):
        parse_freeze(path, 10)
    with pytest.raises(SupplyChainError, match="freeze maximum"):
        parse_freeze(path, False)


def test_json_reader_is_bounded_and_rejects_non_standard_constants(tmp_path: Path) -> None:
    path = tmp_path / "evidence.json"
    path.write_text('{"value": NaN}', encoding="utf-8")
    with pytest.raises((SupplyChainError, ValueError)):
        read_json(path, 1_024)
    path.write_bytes(b"{\"value\":\"" + b"x" * 100 + b"\"}")
    with pytest.raises(SupplyChainError, match="byte budget"):
        read_json(path, 32)


def test_atomic_writer_and_hasher_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    write_json_atomic(path, {"schema": "test", "value": 1})
    digest, size = sha256_file(path, 1_024)
    assert len(digest) == 64
    assert size == path.stat().st_size
    assert json.loads(path.read_text(encoding="utf-8"))["value"] == 1
    assert not list(tmp_path.glob("*.tmp-*"))


def test_canonical_name_and_vulnerability_exception_expiry(tmp_path: Path) -> None:
    assert canonicalize_name("Demo_Pkg.Name") == "demo-pkg-name"
    raw = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    raw["vulnerability_exceptions"] = [
        {
            "package": "Demo_Pkg",
            "version": "1.2.3",
            "vulnerability_id": "PYSEC-TEST-1",
            "expires_utc": (now + timedelta(days=1)).isoformat(),
            "reason": "Temporary documented containment for a test fixture.",
        }
    ]
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    policy = load_policy(path)
    assert active_exception(policy, "demo-pkg", "1.2.3", "PYSEC-TEST-1", now) is not None
    assert active_exception(policy, "demo-pkg", "1.2.3", "OTHER", now) is None
    assert active_exception(
        policy, "demo-pkg", "1.2.3", "PYSEC-TEST-1", now + timedelta(days=2)
    ) is None


def test_policy_rejects_unknown_exception_shape_and_duplicate_entries(tmp_path: Path) -> None:
    raw = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    exception = {
        "package": "demo",
        "version": "1.0",
        "vulnerability_id": "CVE-TEST",
        "expires_utc": "2099-01-01T00:00:00+00:00",
        "reason": "fixture",
    }
    raw["vulnerability_exceptions"] = [exception, exception]
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(SupplyChainError, match="duplicate"):
        load_policy(path)


def test_json_reader_rejects_duplicate_keys_and_non_finite_overflow(tmp_path: Path) -> None:
    path = tmp_path / "evidence.json"
    path.write_text('{"value": 1, "value": 2}', encoding="utf-8")
    with pytest.raises(SupplyChainError, match="duplicate JSON key"):
        read_json(path, 1_024)
    path.write_text('{"value": 1e400}', encoding="utf-8")
    with pytest.raises(SupplyChainError, match="non-finite"):
        read_json(path, 1_024)


def test_policy_rejects_unsafe_source_license_paths(tmp_path: Path) -> None:
    raw = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    raw["required_source_license_files"] = ["../outside"]
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(SupplyChainError, match="unsafe path"):
        load_policy(path)


def test_project_identity_must_match_policy_and_runtime_metadata(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "src" / "config").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "wavehelm"\nversion = "1.0.2"\n',
        encoding="utf-8",
    )
    app_info = root / "src" / "config" / "app_info.json"
    app_info.write_text('{"general":{"version":"1.0.2"}}', encoding="utf-8")
    policy = load_policy(POLICY_PATH)
    validate_project_identity(root, policy)
    app_info.write_text('{"general":{"version":"1.0.1"}}', encoding="utf-8")
    with pytest.raises(SupplyChainError, match="runtime version"):
        validate_project_identity(root, policy)
