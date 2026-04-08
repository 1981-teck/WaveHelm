from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = ROOT / "docs"


def test_github_pages_site_files_exist():
    expected = [
        DOCS_DIR / "index.html",
        DOCS_DIR / "privacy-policy.html",
        DOCS_DIR / "license-terms.html",
        DOCS_DIR / "source-code-and-licenses.html",
        DOCS_DIR / "support.html",
        DOCS_DIR / "404.html",
        DOCS_DIR / "GITHUB_PAGES_SETUP.md",
        DOCS_DIR / ".nojekyll",
        DOCS_DIR / "assets" / "site.css",
        DOCS_DIR / "assets" / "wavehelm-icon.svg",
        DOCS_DIR / "assets" / "wavehelm-wordmark.svg",
    ]

    for path in expected:
        assert path.exists(), f"missing site file: {path.relative_to(ROOT)}"


def test_index_page_references_selected_screenshots():
    text = (DOCS_DIR / "index.html").read_text(encoding="utf-8")

    assert "screenshots/library.png" in text
    assert "screenshots/playlist.png" in text
    assert "screenshots/equalizer.png" in text
    assert "screenshots/visualizer-external.png" in text
    assert "screenshots/video-window.png" in text


def test_pages_setup_doc_matches_docs_publish_model():
    text = (DOCS_DIR / "GITHUB_PAGES_SETUP.md").read_text(encoding="utf-8")

    assert "Deploy from a branch" in text
    assert "main" in text
    assert "/docs" in text
    assert "https://1981-teck.github.io/WaveHelm/" in text
