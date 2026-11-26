from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NOTICE_PATH = ROOT / "THIRD_PARTY_NOTICES.md"
INDEX_PATH = ROOT / "src" / "resources" / "legal" / "NOTICE_INDEX.json"
PROVENANCE_PATH = ROOT / "src" / "resources" / "legal" / "WHEEL_LICENSE_PROVENANCE.json"
LICENSES_ROOT = ROOT / "src" / "resources" / "legal" / "licenses"
WINDOWS_312_LOCK = ROOT / "locks" / "windows-py312-runtime" / "requirements.lock"
REQUIREMENTS_PATH = ROOT / "requirements.txt"

EXPECTED_PACKAGES = {
    "wxPython",
    "pygame",
    "numpy",
    "scipy",
    "soundfile",
    "tinytag",
    "Pillow",
    "python-dateutil",
    "tzdata",
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
    assert "tinytag==2.3.2" in text
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
    assert "tinytag==2.3.2" in requirements
    assert "wxPython" in requirements


def test_selected_wheel_licenses_are_bundled_with_exact_contents():
    provenance = json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))
    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    assert provenance["schema"] == "wavehelm-wheel-license-provenance-v1"
    assert len(provenance["records"]) == 6
    assert {record["package"] for record in provenance["records"]} == {
        "Pillow", "tinytag", "python-dateutil", "tzdata"
    }
    lock = WINDOWS_312_LOCK.read_text(encoding="utf-8")
    indexed: dict[str, set[str]] = {}

    for record in provenance["records"]:
        relative = Path(record["bundled_license_path"])
        assert relative.parts[0] == "licenses"
        assert len(relative.parts) == 2
        payload = ROOT / "src" / "resources" / "legal" / relative
        assert payload.is_file()
        assert hashlib.sha256(payload.read_bytes()).hexdigest() == record["bundled_license_sha256"]
        indexed.setdefault(record["package"], set()).add(relative.name)
        pinned = f'{record["package"].lower()}=={record["version"]} --hash=sha256:{record["wheel_sha256"]}'
        assert pinned in lock
    for package, names in indexed.items():
        assert set(index["packages"][package]["wheel_license_texts"]) == names
