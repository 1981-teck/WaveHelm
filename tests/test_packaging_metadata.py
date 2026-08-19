from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
README_PATH = ROOT / "README.md"
PYPROJECT_PATH = ROOT / "pyproject.toml"
MANIFEST_PATH = ROOT / "MANIFEST.in"
PACKAGING_DOC_PATH = ROOT / "docs" / "windows-packaging.md"
APP_INFO_PATH = ROOT / "src" / "config" / "app_info.json"
SECTION7_TERMS_PATH = ROOT / "GPL_SECTION7_ADDITIONAL_TERMS.md"
DEV_REQUIREMENTS_PATH = ROOT / "requirements-dev.txt"
CHANGELOG_PATH = ROOT / "CHANGELOG.md"
ROADMAP_PATH = ROOT / "ROADMAP.md"
CI_WORKFLOW_PATH = ROOT / ".github" / "workflows" / "ci.yml"
DEPENDABOT_PATH = ROOT / ".github" / "dependabot.yml"
GITIGNORE_PATH = ROOT / ".gitignore"
SECURITY_PATH = ROOT / "SECURITY.md"


def _load_pyproject() -> dict:
    with PYPROJECT_PATH.open("rb") as handle:
        return tomllib.load(handle)


def _load_app_info() -> dict:
    with APP_INFO_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def test_packaging_baseline_files_exist():
    assert README_PATH.exists()
    assert PYPROJECT_PATH.exists()
    assert MANIFEST_PATH.exists()
    assert PACKAGING_DOC_PATH.exists()
    assert SECTION7_TERMS_PATH.exists()
    assert DEV_REQUIREMENTS_PATH.exists()
    assert CHANGELOG_PATH.exists()
    assert ROADMAP_PATH.exists()
    assert CI_WORKFLOW_PATH.exists()
    assert DEPENDABOT_PATH.exists()
    assert SECURITY_PATH.exists()


def test_pyproject_matches_product_identity():
    pyproject = _load_pyproject()
    project = pyproject["project"]
    app_info = _load_app_info()
    general = app_info["general"]

    assert project["name"] == "wavehelm"
    assert project["version"] == general["version"]
    assert project["readme"] == "README.md"
    assert project["version"] == "1.0.1"
    assert project["requires-python"] == ">=3.11"
    assert project["license"] == "GPL-3.0-or-later"
    assert project["license-files"] == ["LICENSE", "GPL_SECTION7_ADDITIONAL_TERMS.md"]
    assert project["scripts"]["wavehelm"] == "main:main"
    assert project["urls"]["Homepage"] == general["website_url"]
    assert not any(classifier.startswith("License ::") for classifier in project["classifiers"])
    assert "Programming Language :: Python :: 3.10" not in project["classifiers"]
    assert "Programming Language :: Python :: 3.13" in project["classifiers"]


def test_pyproject_includes_runtime_package_data_and_dynamic_requirements():
    pyproject = _load_pyproject()

    package_data = pyproject["tool"]["setuptools"]["package-data"]
    assert package_data["src"] == [
        "config/*.json",
        "locales/*.json",
        "resources/manual/*.html",
        "resources/manual/*.json",
        "resources/legal/*.md",
        "resources/legal/*.json",
        "resources/legal/licenses/*.txt",
    ]

    dynamic_dependencies = pyproject["tool"]["setuptools"]["dynamic"]["dependencies"]
    assert dynamic_dependencies["file"] == ["requirements.txt"]


def test_readme_and_packaging_doc_cover_compatibility_and_native_dependencies():
    readme = README_PATH.read_text(encoding="utf-8")
    packaging_doc = PACKAGING_DOC_PATH.read_text(encoding="utf-8")

    assert "Compatibility Matrix" in readme
    assert "Windows" in readme
    assert "Media Foundation" in readme
    assert "ffprobe" in readme

    assert "Native Dependencies" in packaging_doc
    assert "comtypes" in packaging_doc
    assert "pywin32" in packaging_doc
    assert "ambient_sounds" in packaging_doc
    assert "app_info.json" in packaging_doc


def test_section7_terms_file_mentions_attribution_marking_and_project_name():
    text = SECTION7_TERMS_PATH.read_text(encoding="utf-8")

    assert "Pastoris Marco Vincenzo" in text
    assert "modified" in text
    assert "WaveHelm" in text


def test_development_tooling_and_ci_are_pinned_and_release_aware():
    requirements = DEV_REQUIREMENTS_PATH.read_text(encoding="utf-8")
    workflow = CI_WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "pytest==9.0.2" in requirements
    assert "build==1.3.0" in requirements
    assert "pip-audit==2.10.1" in requirements
    assert "cyclonedx-bom==7.3.1" in requirements
    assert "python -m pytest -q tests" in workflow
    assert "python -m build" in workflow
    assert "python -m pip_audit" in workflow
    assert "--path $sitePackages" in workflow
    assert "python -m venv --without-pip .wavehelm-runtime-audit" in workflow
    assert "cyclonedx-py environment .wavehelm-runtime-audit" in workflow
    assert "runtime-freeze.txt" in workflow
    assert "cyclonedx-py requirements" not in workflow



def test_ci_actions_are_immutable_current_generation():
    workflow = CI_WORKFLOW_PATH.read_text(encoding="utf-8")
    expected = {
        "actions/checkout": ("3d3c42e5aac5ba805825da76410c181273ba90b1", "v7.0.1"),
        "actions/setup-python": ("5fda3b95a4ea91299a34e894583c3862153e4b97", "v7.0.0"),
        "actions/upload-artifact": ("043fb46d1a93c77aae656e7c1c64a875d1fc6a0a", "v7.0.1"),
    }
    refs = re.findall(r"uses:\s+(actions/[a-z0-9-]+)@([0-9a-f]{40})\s+#\s+(v[0-9.]+)", workflow)
    assert refs
    for action, sha, version in refs:
        assert action in expected
        assert (sha, version) == expected[action]

    assert workflow.count("persist-credentials: false") == workflow.count("actions/checkout@")
    assert "actions/checkout@v" not in workflow
    assert "actions/setup-python@v" not in workflow
    assert "actions/upload-artifact@v" not in workflow


def test_ci_limits_permissions_concurrency_time_and_artifact_retention():
    workflow = CI_WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "permissions:\n  contents: read" in workflow
    assert "group: ci-${{ github.workflow }}-${{ github.head_ref || github.ref_name }}" in workflow
    assert "cancel-in-progress: true" in workflow
    assert workflow.count("timeout-minutes:") == 3
    assert "retention-days: 14" in workflow
    assert "retention-days: 30" in workflow


def test_dependabot_monitors_github_actions_weekly():
    config = DEPENDABOT_PATH.read_text(encoding="utf-8")

    assert "version: 2" in config
    assert 'package-ecosystem: "github-actions"' in config
    assert 'directory: "/"' in config
    assert 'interval: "weekly"' in config

def test_release_ignore_policy_preserves_legal_sources_and_excludes_test_outputs():
    ignore_lines = {
        line.strip()
        for line in GITIGNORE_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert "licenses/" not in ignore_lines
    assert "/licenses/" in ignore_lines
    assert "/tests/_*_runtime*/" in ignore_lines
    assert "/tests/_tmp_locales/" in ignore_lines


def test_readme_screenshot_references_resolve_to_repository_files():
    readme = README_PATH.read_text(encoding="utf-8")
    expected_paths = {
        "docs/screenshots/library.png",
        "docs/screenshots/playlist.png",
        "docs/screenshots/favorites.png",
        "docs/screenshots/equalizer.png",
        "docs/screenshots/effects.png",
        "docs/screenshots/visualizer.png",
        "docs/screenshots/ambient.png",
        "docs/screenshots/visualizer-external.png",
        "docs/screenshots/setting.png",
        "docs/screenshots/video-window.png",
    }

    for relative_path in expected_paths:
        assert relative_path in readme
        assert (ROOT / relative_path).is_file()

    assert "docs/images/" not in readme


def test_manifest_retains_development_sources_and_prunes_generated_outputs():
    manifest = MANIFEST_PATH.read_text(encoding="utf-8")

    assert "recursive-include tests *.py" in manifest
    assert "recursive-include .github *.yml" in manifest
    assert "recursive-include docs *.md *.html *.css *.svg *.png" in manifest
    assert "recursive-include src/resources/legal *" in manifest
    assert "include requirements-dev.txt" in manifest
    assert "include CHANGELOG.md" in manifest
    assert "include ROADMAP.md" in manifest
    assert "include SECURITY.md" in manifest
    assert "prune tests/_*_runtime*" in manifest
    assert "prune tests/_tmp_locales" in manifest
