from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from src.config import app_metadata


ROOT = Path(__file__).resolve().parents[1]
RELEASE_VERSION = "1.0.2"
PREVIOUS_PUBLIC_RELEASE_VERSION = "1.0.1"
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
    assert VERSION_PATTERN.fullmatch(RELEASE_VERSION)
    assert _project_version() == RELEASE_VERSION
    assert _runtime_version() == RELEASE_VERSION
    assert app_metadata.APP_VERSION_FALLBACK == RELEASE_VERSION
    assert app_metadata.get_default_app_metadata()["general"]["version"] == RELEASE_VERSION


def test_embedded_manuals_use_the_release_identity() -> None:
    manual_paths = sorted(MANUAL_ROOT.glob("wavehelm_user_manual.*.html"))
    assert len(manual_paths) == 4
    for manual_path in manual_paths:
        content = manual_path.read_text(encoding="utf-8")
        assert RELEASE_VERSION in content
        assert f"version {PREVIOUS_PUBLIC_RELEASE_VERSION}" not in content.lower()


def test_documents_identify_current_public_release() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    packaging = (ROOT / "docs" / "windows-packaging.md").read_text(encoding="utf-8")
    roadmap = (ROOT / "ROADMAP.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")

    assert f"Release identity:** {RELEASE_VERSION}" in readme
    assert f"Latest public source release:** {RELEASE_VERSION}" in readme
    assert f"WaveHelm `{RELEASE_VERSION}` is the **current public source release**" in packaging
    assert f"## Current source release: {RELEASE_VERSION}" in roadmap
    assert f"## [{RELEASE_VERSION}] - 2026-09-28" in changelog

    current_release_docs = "\n".join((readme, packaging, roadmap))
    assert "validation pending, not yet published" not in current_release_docs
    assert "not a publication approval" not in current_release_docs
    assert "publication pending" not in current_release_docs
    assert "public source release remains `1.0.1`" not in current_release_docs

    assert f"## [{PREVIOUS_PUBLIC_RELEASE_VERSION}]" in changelog
    assert f"beginning with WaveHelm {PREVIOUS_PUBLIC_RELEASE_VERSION}" in security


def test_active_metadata_surfaces_use_release_identity() -> None:
    active_paths = [
        ROOT / "pyproject.toml",
        ROOT / "src" / "config" / "app_info.json",
        ROOT / "src" / "config" / "app_metadata.py",
        *sorted(MANUAL_ROOT.glob("wavehelm_user_manual.*.html")),
    ]
    for path in active_paths:
        content = path.read_text(encoding="utf-8")
        assert RELEASE_VERSION in content
        assert "1.0.2.dev23" not in content
