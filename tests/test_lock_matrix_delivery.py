"""Static delivery contracts; PowerShell execution is a separate native gate."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_wrapper_delegates_without_evaluation_or_policy_changes() -> None:
    text = (ROOT / "tools/WaveHelm-LockMatrix.ps1").read_text(encoding="utf-8")
    assert "windows_lock_matrix.py" in text
    assert "& $Executable.Source @Arguments" in text
    assert "'-I', '-B'" in text and "exit $Code" in text
    assert "'PYTHON_MANAGER_AUTOMATIC_INSTALL', 'false', 'Process'" in text
    assert "finally {" in text and "$SavedEnvironment[$Name]" in text
    for forbidden in ("Invoke-Expression", "ExecutionPolicy Bypass", "Set-ExecutionPolicy", "git push"):
        assert forbidden not in text


def test_matrix_delivers_native_tests_docs_and_automatic_sdist_inclusion() -> None:
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    assert "recursive-include tools *.py *.json *.ps1" in manifest
    assert "recursive-include tests *.py *.ps1" in manifest
    assert "recursive-include docs *.md" in manifest
    tests = (ROOT / "tests/powershell/LockMatrix.Tests.ps1").read_text(encoding="utf-8")
    assert "ParseFile" in tests and "missing launcher" in tests
    assert "without its Python entrypoint" in tests
    docs = (ROOT / "docs/windows-lock-matrix.md").read_text(encoding="utf-8")
    for required in ("MATRIX_INSTALLED_NOT_AUDITED", "INCOMPLETE", "results_archive",
                     "without pip", "human review", "not signatures", "NOT_VERIFIED"):
        assert required in docs


def test_generated_lock_is_not_claimed_human_reviewed() -> None:
    text = (ROOT / "tools/dependency_lock.py").read_text(encoding="utf-8")
    assert "resolved-artifact hash lock" in text
    assert "Human review: REQUIRED before integration." in text
