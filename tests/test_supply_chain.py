from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools.supply_chain import generate_direct_sbom, generate_license_inventory
from tools.supply_chain_contract import load_policy, write_json_atomic
from tools.supply_chain_validation import create_manifest, validate_evidence, verify_manifest

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "tools" / "supply_chain_policy.json"
SHA256 = "a" * 64


def _policy(tmp_path: Path) -> Path:
    raw = json.loads(POLICY.read_text(encoding="utf-8"))
    raw["project"] = {"name": "wavehelm", "version": "1.0.2.dev23"}
    raw["required_source_license_files"] = ["LICENSE"]
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def _write_project_identity(root: Path) -> None:
    (root / "src" / "config").mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "wavehelm"\nversion = "1.0.2.dev23"\n',
        encoding="utf-8",
    )
    (root / "src" / "config" / "app_info.json").write_text(
        '{"general":{"version":"1.0.2.dev23"}}\n',
        encoding="utf-8",
    )


def _component(name: str, version: str) -> dict[str, object]:
    reference = f"pkg:pypi/{name.lower()}@{version}"
    return {
        "type": "library",
        "bom-ref": reference,
        "name": name,
        "version": version,
        "purl": reference,
    }


def _write_valid_evidence(root: Path, evidence: Path, policy_path: Path) -> None:
    evidence.mkdir()
    _write_project_identity(root)
    (root / "requirements.txt").write_text(
        'Alpha==1.0\nBeta_Pkg==2.0; platform_system == "Windows"\n', encoding="utf-8"
    )
    (root / "LICENSE").write_text("fixture license\n", encoding="utf-8")
    freeze = {"alpha": "1.0", "beta-pkg": "2.0", "transitive": "3.0"}
    (evidence / "runtime-freeze.txt").write_text(
        "Alpha==1.0\nBeta-Pkg==2.0\nTransitive==3.0\n", encoding="utf-8"
    )
    (evidence / "pip-bootstrap-exit-code.txt").write_text("0\n", encoding="utf-8")
    (evidence / "audit-tool-install-exit-code.txt").write_text("0\n", encoding="utf-8")
    (evidence / "tool-versions-exit-code.txt").write_text("0\n", encoding="utf-8")
    (evidence / "runtime-install-exit-code.txt").write_text("0\n", encoding="utf-8")
    (evidence / "runtime-freeze-exit-code.txt").write_text("0\n", encoding="utf-8")
    installs = []
    for name, version in freeze.items():
        installs.append(
            {
                "download_info": {
                    "url": f"https://files.pythonhosted.org/{name}-{version}.whl",
                    "archive_info": {"hashes": {"sha256": SHA256}},
                },
                "metadata": {"name": name, "version": version},
                "requested": name in {"alpha", "beta-pkg"},
            }
        )
    write_json_atomic(evidence / "runtime-install-report.json", {"version": "1", "install": installs})
    tool_installs = []
    for name, version in (("pip-audit", "2.10.1"), ("cyclonedx-bom", "7.3.1")):
        tool_installs.append(
            {
                "download_info": {
                    "url": f"https://files.pythonhosted.org/{name}-{version}.whl",
                    "archive_info": {"hashes": {"sha256": SHA256}},
                },
                "metadata": {"name": name, "version": version},
                "requested": True,
            }
        )
    write_json_atomic(
        evidence / "audit-tool-install-report.json",
        {"version": "1", "install": tool_installs},
    )
    (evidence / "runtime-pip-check.txt").write_text(
        "No broken requirements found.\n", encoding="utf-8"
    )
    (evidence / "runtime-pip-check-exit-code.txt").write_text("0\n", encoding="utf-8")
    write_json_atomic(
        evidence / "pip-audit.json",
        {
            "dependencies": [
                {"name": name, "version": version, "vulns": []}
                for name, version in freeze.items()
            ]
        },
    )
    (evidence / "pip-audit-exit-code.txt").write_text("0\n", encoding="utf-8")
    project_ref = "pkg:pypi/wavehelm@1.0.2.dev23"
    components = [_component(name, version) for name, version in freeze.items()]
    write_json_atomic(
        evidence / "sbom.cdx.json",
        {
            "bomFormat": "CycloneDX",
            "specVersion": "1.6",
            "version": 1,
            "metadata": {
                "component": {
                    "type": "application",
                    "bom-ref": project_ref,
                    "name": "wavehelm",
                    "version": "1.0.2.dev23",
                }
            },
            "components": components,
            "dependencies": [
                {"ref": project_ref, "dependsOn": [item["bom-ref"] for item in components]},
                *({"ref": item["bom-ref"], "dependsOn": []} for item in components),
            ],
        },
    )
    (evidence / "sbom-exit-code.txt").write_text("0\n", encoding="utf-8")
    license_bytes = (root / "LICENSE").read_bytes()
    license_digest = hashlib.sha256(license_bytes).hexdigest()
    write_json_atomic(
        evidence / "runtime-license-inventory.json",
        {
            "schema": "wavehelm-runtime-license-inventory-v1",
            "project": {"name": "wavehelm", "version": "1.0.2.dev23"},
            "site_packages": "C:/fixture/site-packages",
            "packages": [
                {
                    "name": name,
                    "canonical_name": name,
                    "version": version,
                    "license_expression": "MIT",
                    "license_metadata": "",
                    "license_classifiers": [],
                    "license_files": [],
                }
                for name, version in freeze.items()
            ],
            "source_license_files": [
                {"path": "LICENSE", "sha256": license_digest, "size": len(license_bytes)}
            ],
        },
    )
    (evidence / "license-inventory-exit-code.txt").write_text("0\n", encoding="utf-8")
    policy = load_policy(policy_path)
    write_json_atomic(
        evidence / "supply-chain-tool-versions.json",
        {
            "schema": "wavehelm-supply-chain-tool-versions-v1",
            "python": "3.12.0",
            "platform": "Windows",
            "tools": {
                "pip": policy.pip_version,
                "pip-audit": policy.pip_audit_version,
                "cyclonedx-bom": policy.cyclonedx_bom_version,
            },
        },
    )


def test_direct_declared_sbom_is_deterministic_and_covers_markers(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write_project_identity(root)
    (root / "requirements.txt").write_text(
        'Alpha==1.0\nBeta_Pkg==2.0; platform_system == "Windows"\n', encoding="utf-8"
    )
    policy = load_policy(_policy(tmp_path))
    first = generate_direct_sbom(root, policy)
    second = generate_direct_sbom(root, policy)
    assert first == second
    assert first["bomFormat"] == "CycloneDX"
    assert first["specVersion"] == "1.6"
    components = {item["name"]: item for item in first["components"]}
    assert components["Beta_Pkg"]["properties"][0]["value"] == 'platform_system == "Windows"'


def test_valid_installed_graph_evidence_passes_and_manifest_detects_tampering(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    evidence = tmp_path / "evidence"
    root.mkdir()
    policy_path = _policy(tmp_path)
    _write_valid_evidence(root, evidence, policy_path)
    policy = load_policy(policy_path)
    status = validate_evidence(root, evidence, policy)
    assert status["verdict"] == "PASS", status["findings"]
    write_json_atomic(evidence / "supply-chain-status.json", status)
    write_json_atomic(evidence / "supply-chain-evidence-manifest.json", create_manifest(evidence, policy))
    assert verify_manifest(evidence, policy)["verdict"] == "PASS"
    (evidence / "runtime-freeze.txt").write_text("Alpha==9\n", encoding="utf-8")
    result = verify_manifest(evidence, policy)
    assert result["verdict"] == "FAIL"
    assert "manifest:mismatch:runtime-freeze.txt" in result["findings"]


def test_hash_locked_install_report_all_packages_requested_passes(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    evidence = tmp_path / "evidence"
    root.mkdir()
    policy_path = _policy(tmp_path)
    _write_valid_evidence(root, evidence, policy_path)
    report = json.loads((evidence / "runtime-install-report.json").read_text(encoding="utf-8"))
    for item in report["install"]:
        item["requested"] = True
    write_json_atomic(evidence / "runtime-install-report.json", report)
    status = validate_evidence(root, evidence, load_policy(policy_path))
    assert status["verdict"] == "PASS", status["findings"]


def test_partial_requested_set_remains_blocking(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    evidence = tmp_path / "evidence"
    root.mkdir()
    policy_path = _policy(tmp_path)
    _write_valid_evidence(root, evidence, policy_path)
    report = json.loads((evidence / "runtime-install-report.json").read_text(encoding="utf-8"))
    for item in report["install"]:
        name = item["metadata"]["name"]
        item["requested"] = name in {"alpha", "transitive"}
    write_json_atomic(evidence / "runtime-install-report.json", report)
    status = validate_evidence(root, evidence, load_policy(policy_path))
    assert status["verdict"] == "FAIL"
    assert "install_report:missing_requested:beta-pkg" in status["findings"]
    assert "install_report:unexpected_requested:transitive" in status["findings"]


def test_vulnerability_non_https_and_missing_hash_are_blocking(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    evidence = tmp_path / "evidence"
    root.mkdir()
    policy_path = _policy(tmp_path)
    _write_valid_evidence(root, evidence, policy_path)
    report = json.loads((evidence / "runtime-install-report.json").read_text(encoding="utf-8"))
    report["install"][0]["download_info"] = {
        "url": "http://example.invalid/alpha.whl",
        "archive_info": {"hashes": {}},
    }
    write_json_atomic(evidence / "runtime-install-report.json", report)
    audit = json.loads((evidence / "pip-audit.json").read_text(encoding="utf-8"))
    audit["dependencies"][0]["vulns"] = [{"id": "PYSEC-TEST-1", "fix_versions": []}]
    write_json_atomic(evidence / "pip-audit.json", audit)
    (evidence / "pip-audit-exit-code.txt").write_text("1\n", encoding="utf-8")
    status = validate_evidence(root, evidence, load_policy(policy_path))
    assert status["verdict"] == "FAIL"
    assert any(item.startswith("install_report:non_https:alpha") for item in status["findings"])
    assert any(item.startswith("install_report:missing_sha256:alpha") for item in status["findings"])
    assert "pip_audit:vulnerability:alpha:PYSEC-TEST-1" in status["findings"]
    assert "pip_audit:blocking_vulnerabilities:1" in status["findings"]


def test_cli_validate_writes_failure_status_and_manifest_before_failing(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    evidence = tmp_path / "evidence"
    root.mkdir()
    policy_path = _policy(tmp_path)
    _write_valid_evidence(root, evidence, policy_path)
    (evidence / "runtime-pip-check-exit-code.txt").write_text("1\n", encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "supply_chain.py"),
            "validate",
            "--root",
            str(root),
            "--policy",
            str(policy_path),
            "--evidence-dir",
            str(evidence),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1, completed.stderr
    status = json.loads((evidence / "supply-chain-status.json").read_text(encoding="utf-8"))
    assert status["verdict"] == "FAIL"
    assert (evidence / "supply-chain-evidence-manifest.json").is_file()


def test_license_inventory_hashes_metadata_and_license_payloads(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    site = tmp_path / "site-packages"
    dist_info = site / "Demo_Pkg-1.2.dist-info"
    root.mkdir()
    _write_project_identity(root)
    dist_info.mkdir(parents=True)
    (root / "LICENSE").write_text("project license\n", encoding="utf-8")
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.4\nName: Demo-Pkg\nVersion: 1.2\nLicense-Expression: MIT\n",
        encoding="utf-8",
    )
    (dist_info / "LICENSE.txt").write_text("MIT fixture\n", encoding="utf-8")
    (dist_info / "RECORD").write_text(
        "Demo_Pkg-1.2.dist-info/METADATA,,\n"
        "Demo_Pkg-1.2.dist-info/LICENSE.txt,,\n"
        "Demo_Pkg-1.2.dist-info/RECORD,,\n",
        encoding="utf-8",
    )
    policy = load_policy(_policy(tmp_path))
    inventory = generate_license_inventory(root, site, policy)
    assert inventory["schema"] == "wavehelm-runtime-license-inventory-v1"
    package = inventory["packages"][0]
    assert package["canonical_name"] == "demo-pkg"
    assert package["license_expression"] == "MIT"
    assert package["license_files"][0]["path"].endswith("LICENSE.txt")


def test_failed_install_or_freeze_exit_is_blocking(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    evidence = tmp_path / "evidence"
    root.mkdir()
    policy_path = _policy(tmp_path)
    _write_valid_evidence(root, evidence, policy_path)
    (evidence / "runtime-install-exit-code.txt").write_text("1\n", encoding="utf-8")
    (evidence / "runtime-freeze-exit-code.txt").write_text("2\n", encoding="utf-8")
    status = validate_evidence(root, evidence, load_policy(policy_path))
    assert "runtime_install:exit:1" in status["findings"]
    assert "runtime_freeze:exit:2" in status["findings"]


def test_manifest_rejects_evidence_symlink(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    link = evidence / "linked.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        return
    policy = load_policy(POLICY)
    with pytest.raises(Exception, match="symlink"):
        create_manifest(evidence, policy)


def test_cli_validate_terminalizes_missing_evidence(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    evidence = tmp_path / "evidence"
    root.mkdir()
    evidence.mkdir()
    _write_project_identity(root)
    policy_path = _policy(tmp_path)
    (root / "requirements.txt").write_text("Alpha==1.0\n", encoding="utf-8")
    (root / "LICENSE").write_text("fixture license\n", encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "supply_chain.py"),
            "validate",
            "--root",
            str(root),
            "--policy",
            str(policy_path),
            "--evidence-dir",
            str(evidence),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1, completed.stderr
    status = json.loads((evidence / "supply-chain-status.json").read_text(encoding="utf-8"))
    assert status["verdict"] == "FAIL"
    assert any(item.startswith("evidence:missing:") for item in status["findings"])
    assert (evidence / "supply-chain-evidence-manifest.json").is_file()


def test_cli_does_not_create_bytecode_in_source_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cleanup_tool = ROOT / "tools" / "source_hygiene.py"
    output = tmp_path / "direct-sbom.json"
    monkeypatch.delenv("PYTHONPYCACHEPREFIX", raising=False)
    monkeypatch.setattr(sys, "pycache_prefix", None, raising=False)
    environment = dict(os.environ)
    environment.pop("PYTHONDONTWRITEBYTECODE", None)
    subprocess.run(
        [
            sys.executable,
            str(cleanup_tool),
            "clean",
            "--root",
            str(ROOT),
        ],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    completed = subprocess.run(
        [
            sys.executable,
            "tools/supply_chain.py",
            "direct-sbom",
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
    assert output.is_file()
    assert not (ROOT / "tools" / "__pycache__").exists()
