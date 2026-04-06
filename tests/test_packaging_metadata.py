from __future__ import annotations

import json
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
README_PATH = ROOT / "README.md"
PYPROJECT_PATH = ROOT / "pyproject.toml"
MANIFEST_PATH = ROOT / "MANIFEST.in"
PACKAGING_DOC_PATH = ROOT / "docs" / "windows-packaging.md"
APP_INFO_PATH = ROOT / "src" / "config" / "app_info.json"
SECTION7_TERMS_PATH = ROOT / "GPL_SECTION7_ADDITIONAL_TERMS.md"


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


def test_pyproject_matches_product_identity():
    pyproject = _load_pyproject()
    project = pyproject["project"]
    app_info = _load_app_info()
    general = app_info["general"]

    assert project["name"] == "wavehelm"
    assert project["version"] == general["version"]
    assert project["readme"] == "README.md"
    assert project["requires-python"] == ">=3.10"
    assert project["scripts"]["wavehelm"] == "main:main"
    assert project["urls"]["Homepage"] == general["website_url"]
    assert "License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)" in project["classifiers"]


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
