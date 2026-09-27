"""Contracts for release integration; no GUI/network behavior is simulated here."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMMUNITY = (
    "CONTRIBUTING.md", "CODE_OF_CONDUCT.md", "SECURITY.md",
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/feature_request.yml",
    ".github/ISSUE_TEMPLATE/config.yml", ".github/pull_request_template.md",
)
PRIVATE_REPORT = "https://github.com/1981-teck/WaveHelm/security/advisories/new"


@pytest.mark.parametrize("relative", COMMUNITY, ids=[Path(p).stem for p in COMMUNITY])
def test_community_payload_exists_and_is_not_empty(relative: str) -> None:
    text = (ROOT / relative).read_text(encoding="utf-8")
    assert len(text.strip()) > 120
    assert not (ROOT / ".github/.github").exists()


@pytest.mark.parametrize("relative", ["SECURITY.md", "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md", ".github/ISSUE_TEMPLATE/config.yml"],
    ids=["security", "contributing", "conduct", "chooser"])
def test_private_reporting_route_is_consistent(relative: str) -> None:
    assert PRIVATE_REPORT in (ROOT / relative).read_text(encoding="utf-8")


@pytest.mark.parametrize("heading", ["## Summary", "## Validation performed",
    "## Risk and regression considerations", "## Checklist"])
def test_pull_request_template_has_required_sections(heading: str) -> None:
    assert heading in (ROOT / ".github/pull_request_template.md").read_text(encoding="utf-8")


def test_readme_preserves_public_presentation_and_newer_runtime_notes() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert text.index("docs/screenshots/library.png") < text.index("## Why this project exists")
    assert text.count("docs/screenshots/library.png") == 1
    assert "5658dc35-a158-466e-846a-19f6a827077e" in text
    assert "[▶ Watch the 55-second WaveHelm demo]" in text
    assert "img.shields.io/github/v/release/1981-teck/WaveHelm" in text
    assert "docs/wic-application-baseline.md" in text
    assert "WIC" in text


def test_changelog_ax_and_aw_entries_are_not_misnested() -> None:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert text.startswith("# Changelog\n")
    ax = text.split("## R5I step08AX", 1)[1].split("## R5I step08AW", 1)[0]
    aw = text.split("## R5I step08AW", 1)[1].split("\n## ", 1)[0]
    assert "Retire drained seek observations" in ax
    assert "Retain a submitted progress-bar preview" not in ax
    assert "Retain a submitted progress-bar preview" in aw
    assert "Windows validation pending" in ax  # retained historical qualifier


def test_manifest_includes_new_public_community_payloads() -> None:
    text = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    assert "include CONTRIBUTING.md" in text
    assert "include CODE_OF_CONDUCT.md" in text
    assert "recursive-include .github *.yml *.md" in text
    assert "include docs/release-integration-state.json" in text


def test_candidate_state_does_not_authorize_publication() -> None:
    state = json.loads((ROOT / "docs/release-integration-state.json").read_text(encoding="utf-8"))
    assert state["checkpoint"] == "RI03"
    assert state["version"] == "1.0.2"
    assert state["baseline_version"] == "1.0.2.dev23"
    assert state["status"] == "CANDIDATE_NOT_PUBLISHED"
    assert state["release_authorized"] is False
    assert state["runtime_logic_change"] is False
    assert state["dependencies_changed"] is False
    assert state["windows_final_qualification"] == "RI02_AUTOMATED_RECONCILED_RERUN_REQUIRED"
    assert state["ri02_automated_result"]["full_suite"] == "4763 PASS / 2 FAIL / 16 SKIP"
    assert state["ri02_automated_result"]["expected_windows_skip_set_exact"] is True
    assert state["current_vulnerability_audit"] == "NOT_RUN"
    assert state["remote_commit_reconciliation"] == "PENDING_OWNER_FETCH"


def test_readme_local_targets_exist() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    targets = re.findall(r"\]\(([^)]+)\)", text)
    for target in targets:
        if target.startswith(("https://", "http://", "#", "mailto:")):
            continue
        relative = target.split("#", 1)[0]
        assert (ROOT / relative).exists(), relative
