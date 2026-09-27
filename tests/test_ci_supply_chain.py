from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
POLICY = ROOT / "tools" / "supply_chain_policy.json"
ACTION_PATTERN = re.compile(r"^\s*uses:\s*([^\s#]+)", re.MULTILINE)


def test_workflow_uses_shared_source_hygiene_gate_before_and_after_tests() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "tools/source_hygiene.py check" in text
    assert "tools/source_hygiene.py clean" in text
    assert "pre-test-source-hygiene.json" in text
    assert "post-test-source-hygiene.json" in text
    assert "if-no-files-found: error" in text


def test_supply_chain_job_collects_and_validates_complete_windows_evidence() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    required = (
        "audit-tool-install-report.json",
        "pip-bootstrap-exit-code.txt",
        "runtime-install-exit-code.txt",
        "runtime-freeze-exit-code.txt",
        "--report \"$evidence/runtime-install-report.json\"",
        "runtime-freeze.txt",
        "runtime-pip-check-exit-code.txt",
        "python -m pip_audit --path $sitePackages --strict",
        "pip-audit-exit-code.txt",
        "python -m cyclonedx_py environment .wavehelm-runtime-audit",
        "--output-reproducible",
        "--sv 1.6",
        "runtime-license-inventory.json",
        "supply_chain.py validate",
        "supply_chain.py verify",
        "supply-chain-evidence/*",
        "supply-chain-manifest-verify.json",
    )
    for value in required:
        assert value in text
    assert "if: always()" in text
    assert "if-no-files-found: warn" not in text
    assert "continue-on-error: true" not in text


def test_all_external_actions_are_pinned_to_full_commit_sha() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    actions = ACTION_PATTERN.findall(text)
    assert actions
    for action in actions:
        assert "@" in action
        revision = action.rsplit("@", 1)[1]
        assert re.fullmatch(r"[0-9a-f]{40}", revision)


def test_policy_is_fail_closed_and_has_no_vulnerability_exceptions() -> None:
    raw = json.loads(POLICY.read_text(encoding="utf-8"))
    assert raw["project"] == {"name": "wavehelm", "version": "1.0.2"}
    assert raw["tools"] == {
        "cyclonedx-bom": "7.3.1",
        "pip": "26.2.1",
        "pip-audit": "2.10.1",
    }
    assert raw["sbom_spec_version"] == "1.6"
    assert raw["fail_on_vulnerability"] is True
    assert raw["require_https_downloads"] is True
    assert raw["vulnerability_exceptions"] == []


def test_ci_pins_pip_and_avoids_unbounded_upgrade() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "pip==26.2.1" in text
    assert "pip install --upgrade pip" not in text
