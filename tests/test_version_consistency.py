from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from src.config import app_metadata


ROOT = Path(__file__).resolve().parents[1]
CANDIDATE_VERSION = "1.0.2"
PUBLIC_RELEASE_VERSION = "1.0.1"
MANUAL_ROOT = ROOT / "src" / "resources" / "manual"
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def _project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]
    version = project.get("version")
    assert isinstance(version, str)
    return version


def _runtime_version() -> str:
    with (ROOT / "src" / "config" / "app_info.json").open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    version = data["general"]["version"]
    assert isinstance(version, str)
    return version


def test_active_version_sources_are_exactly_aligned() -> None:
    assert VERSION_PATTERN.fullmatch(CANDIDATE_VERSION)
    assert _project_version() == CANDIDATE_VERSION
    assert _runtime_version() == CANDIDATE_VERSION
    assert app_metadata.APP_VERSION_FALLBACK == CANDIDATE_VERSION
    assert app_metadata.get_default_app_metadata()["general"]["version"] == CANDIDATE_VERSION


def test_embedded_manuals_use_the_release_integration_identity() -> None:
    manual_paths = sorted(MANUAL_ROOT.glob("wavehelm_user_manual.*.html"))
    assert len(manual_paths) == 4
    for manual_path in manual_paths:
        content = manual_path.read_text(encoding="utf-8")
        assert CANDIDATE_VERSION in content
        assert f"version {PUBLIC_RELEASE_VERSION}" not in content.lower()


def test_documents_distinguish_candidate_and_public_release() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    packaging = (ROOT / "docs" / "windows-packaging.md").read_text(encoding="utf-8")
    roadmap = (ROOT / "ROADMAP.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")

    assert f"Release-integration identity:** {CANDIDATE_VERSION}" in readme
    assert f"Latest public source release:** {PUBLIC_RELEASE_VERSION}" in readme
    assert CANDIDATE_VERSION in packaging
    assert PUBLIC_RELEASE_VERSION in packaging
    assert f"Current release-integration candidate: {CANDIDATE_VERSION}" in roadmap
    assert f"## [{CANDIDATE_VERSION}] - Unreleased (release-integration RI03)" in changelog
    assert "not a publication approval" in readme
    assert "validation pending, not yet published" in readme
    assert f"## [{PUBLIC_RELEASE_VERSION}]" in changelog
    assert f"beginning with WaveHelm {PUBLIC_RELEASE_VERSION}" in security


def test_active_metadata_surfaces_do_not_claim_public_release_identity() -> None:
    active_paths = [
        ROOT / "pyproject.toml",
        ROOT / "src" / "config" / "app_info.json",
        ROOT / "src" / "config" / "app_metadata.py",
        *sorted(MANUAL_ROOT.glob("wavehelm_user_manual.*.html")),
    ]
    for path in active_paths:
        content = path.read_text(encoding="utf-8")
        assert CANDIDATE_VERSION in content
        assert "1.0.2.dev23" not in content
        assert PUBLIC_RELEASE_VERSION not in content
