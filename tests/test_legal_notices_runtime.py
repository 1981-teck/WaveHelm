from __future__ import annotations

from pathlib import Path

import pytest

from src.boot.runtime_bootstrap import _ensure_app_dirs
from src.utils import legal_notices
from src.utils.legal_notices import ensure_third_party_notices_dir, get_third_party_notices_dir


def test_ensure_third_party_notices_dir_materializes_runtime_bundle(tmp_path: Path):
    destination = ensure_third_party_notices_dir(tmp_path)

    expected = {
        "THIRD_PARTY_NOTICES.md",
        "NOTICE_INDEX.json",
        "WHEEL_LICENSE_PROVENANCE.json",
        "README.md",
        "GPL_SECTION7_ADDITIONAL_TERMS.md",
        "Apache-2.0.txt",
        "BSD-3-Clause.txt",
        "LGPL-2.1.txt",
        "LGPL-3.0.txt",
        "MIT.txt",
        "MIT-CMU.txt",
        "PSF-2.0.txt",
        "wxWindows-Library-Licence-3.1.txt",
        "Pillow-13.0.0-WHEEL-LICENSE.txt",
        "TinyTag-2.3.2-LICENSE.txt",
        "python-dateutil-4.0.0-AUTHORS.md",
        "python-dateutil-4.0.0-LICENSE.txt",
        "tzdata-2026.6-LICENSE.txt",
        "tzdata-2026.6-LICENSE_APACHE.txt",
        "LGPL_SOURCE_CODE_OFFER_TEMPLATE.txt",
    }

    assert destination == get_third_party_notices_dir(tmp_path)
    assert expected <= {path.name for path in destination.iterdir()}


def test_runtime_notices_copy_nested_wheel_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    wheel_resource = tmp_path / "resource"
    nested = wheel_resource / "wheels" / "demo-1.0.dist-info" / "licenses"
    nested.mkdir(parents=True)
    (nested / "LICENSE").write_bytes(b"Exact wheel notice\r\n")
    real_files = legal_notices.resources.files

    def fake_files(package: str):
        if package == "src.resources.legal.licenses":
            return wheel_resource
        return real_files(package)

    monkeypatch.setattr(legal_notices.resources, "files", fake_files)
    destination = ensure_third_party_notices_dir(tmp_path / "profile")
    assert (destination / "wheels" / "demo-1.0.dist-info" / "licenses" / "LICENSE").read_bytes() == b"Exact wheel notice\r\n"


def test_runtime_bootstrap_creates_logs_and_notice_bundle(tmp_path: Path):
    _ensure_app_dirs(tmp_path)

    assert (tmp_path / "logs").is_dir()
    assert (tmp_path / "licenses").is_dir()
    assert (tmp_path / "licenses" / "THIRD_PARTY_NOTICES.md").exists()


def test_section7_additional_terms_are_runtime_accessible(tmp_path: Path):
    destination = ensure_third_party_notices_dir(tmp_path)
    payload = (destination / "GPL_SECTION7_ADDITIONAL_TERMS.md").read_text(encoding="utf-8")

    assert "Pastoris Marco Vincenzo" in payload
    assert "modified" in payload and "unofficial" in payload
    assert "WaveHelm" in payload
