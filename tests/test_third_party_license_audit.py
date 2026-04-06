from pathlib import Path


def test_third_party_license_audit_exists_and_records_active_runtime_dependencies_only():
    audit = Path(__file__).resolve().parents[1] / "THIRD_PARTY_LICENSE_AUDIT.md"
    text = audit.read_text(encoding="utf-8")
    assert "tinytag==2.2.1" in text
    assert "MIT" in text
    assert "no current GPL blocker" in text
    assert "mutagen" not in text
    assert "historical audit note" not in text
    assert "GPL-3.0-or-later source distribution" in text
    assert "src/resources/legal/" in text
