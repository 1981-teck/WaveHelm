from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NOTICE_PATH = ROOT / "THIRD_PARTY_NOTICES.md"
INDEX_PATH = ROOT / "src" / "resources" / "legal" / "NOTICE_INDEX.json"
LICENSES_ROOT = ROOT / "src" / "resources" / "legal" / "licenses"
REQUIREMENTS_PATH = ROOT / "requirements.txt"

EXPECTED_PACKAGES = {
    "wxPython",
    "pygame",
    "numpy",
    "scipy",
    "soundfile",
    "tinytag",
    "Pillow",
    "opencv-python",
    "matplotlib",
    "pywin32",
    "comtypes",
}


def test_third_party_notices_bundle_exists_and_covers_runtime_stack():
    assert NOTICE_PATH.exists()
    assert INDEX_PATH.exists()

    text = NOTICE_PATH.read_text(encoding="utf-8")
    assert "baseline third-party notices bundle" in text
    assert "tinytag==2.2.1" in text
    assert "Microsoft Store" in text
    assert "wxPython" in text

    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    assert set(index["packages"]) == EXPECTED_PACKAGES
    assert index["packages"]["tinytag"]["baseline_license_texts"] == ["MIT.txt"]
    assert "MIT-licensed release" in index["packages"]["tinytag"]["notes"][0]
    assert index["packages"]["wxPython"]["baseline_license_texts"] == ["wxWindows-Library-Licence-3.1.txt"]


def test_baseline_license_text_files_exist():
    expected_files = {
        "Apache-2.0.txt",
        "BSD-3-Clause.txt",
        "LGPL-2.1.txt",
        "MIT.txt",
        "MIT-CMU.txt",
        "PSF-2.0.txt",
        "wxWindows-Library-Licence-3.1.txt",
    }
    assert {path.name for path in LICENSES_ROOT.iterdir()} >= expected_files


def test_requirements_pin_tinytag_to_current_permissive_line():
    requirements = REQUIREMENTS_PATH.read_text(encoding="utf-8")
    assert "tinytag==2.2.1" in requirements
    assert "wxPython" in requirements
