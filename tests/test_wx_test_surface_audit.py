from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS_ROOT = ROOT / "tests"
WX_ROOT = ROOT / "src" / "ui_wx"
LEGACY_UI_ROOT = TESTS_ROOT / "legacy_ui_retired"
LEGACY_DOCS_ROOT = ROOT / "docs" / "migration"

EXPECTED_WX_TESTS = {
    "test_wx_callback_registration.py",
    "test_wx_effects_view.py",
    "test_wx_equalizer_view.py",
    "test_wx_favorites_view.py",
    "test_wx_library_view.py",
    "test_wx_main_view.py",
    "test_wx_mini_player.py",
    "test_wx_playlist_view.py",
    "test_wx_settings_view.py",
    "test_wx_simple_views.py",
    "test_wx_test_surface_audit.py",
    "test_wx_video_view.py",
    "test_wx_visualizer_view.py",
}

EXPECTED_WX_MODULES = {
    "about_view.py",
    "ambient_view.py",
    "effects_view.py",
    "equalizer_view.py",
    "favorites_view.py",
    "library_view.py",
    "main_view.py",
    "mini_player.py",
    "playlist_view.py",
    "readmi_view.py",
    "settings_view.py",
    "video_view.py",
    "visualizer_view.py",
}


def _obsolete_ui_test_files() -> set[str]:
    matches: set[str] = set()
    for path in TESTS_ROOT.glob("test_*.py"):
        if path.name == "test_wx_test_surface_audit.py":
            continue
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
        has_obsolete_import = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                has_obsolete_import = has_obsolete_import or any(alias.name.startswith("PySide6") for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                has_obsolete_import = has_obsolete_import or bool(node.module and node.module.startswith("PySide6"))
        if has_obsolete_import or "qtbot" in text:
            matches.add(path.name)
    return matches


def test_active_test_surface_contains_no_obsolete_ui_tests_after_cutover():
    assert _obsolete_ui_test_files() == set()


def test_no_retired_ui_archive_remains_after_cutover_cleanup():
    assert not LEGACY_UI_ROOT.exists()


def test_migrated_wx_surface_has_expected_modules_and_tests():
    excluded = {"__init__.py", "common.py", "main_view_shell.py", "signal.py", "view_factory.py", "mini_player_shared.py"}
    actual_modules = {path.name for path in WX_ROOT.glob("*.py") if path.name not in excluded}
    actual_tests = {path.name for path in TESTS_ROOT.glob("test_wx*.py")}

    assert EXPECTED_WX_MODULES <= actual_modules
    assert EXPECTED_WX_TESTS <= actual_tests


def test_no_migration_plan_document_remains_after_cutover_cleanup():
    assert not LEGACY_DOCS_ROOT.exists()
