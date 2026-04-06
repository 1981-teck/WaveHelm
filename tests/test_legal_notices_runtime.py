from __future__ import annotations

from pathlib import Path

from src.boot.runtime_bootstrap import _ensure_app_dirs
from src.utils.legal_notices import ensure_third_party_notices_dir, get_third_party_notices_dir


def test_ensure_third_party_notices_dir_materializes_runtime_bundle(tmp_path: Path):
    destination = ensure_third_party_notices_dir(tmp_path)

    expected = {
        "THIRD_PARTY_NOTICES.md",
        "NOTICE_INDEX.json",
        "README.md",
        "GPL_SECTION7_ADDITIONAL_TERMS.md",
        "Apache-2.0.txt",
        "BSD-3-Clause.txt",
        "LGPL-2.1.txt",
        "MIT.txt",
        "MIT-CMU.txt",
        "PSF-2.0.txt",
        "wxWindows-Library-Licence-3.1.txt",
        "LGPL_SOURCE_CODE_OFFER_TEMPLATE.txt",
    }

    assert destination == get_third_party_notices_dir(tmp_path)
    assert expected <= {path.name for path in destination.iterdir()}


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
