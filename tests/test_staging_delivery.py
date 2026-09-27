"""Static delivery contracts; these are NOT execution of PowerShell or Pester."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_wrapper_delegates_arguments_and_native_exit_without_evaluation() -> None:
    text = (ROOT / "tools/WaveHelm-LocalStaging.ps1").read_text(encoding="utf-8")
    assert "'Prepare', 'Commit', 'Cancel', 'Status'" in text
    assert "& $Executable.Source @Arguments" in text
    assert "$Code = $LASTEXITCODE" in text
    assert "exit $Code" in text
    assert "Invoke-Expression" not in text
    assert "git push" not in text


def test_source_distribution_includes_real_wrappers_tests_and_future_locks() -> None:
    text = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    assert "recursive-include tools *.py *.json *.ps1" in text
    assert "recursive-include tests *.py *.ps1" in text
    assert "recursive-include locks *.lock *.json" in text
    assert (ROOT / "tests/powershell/LocalStaging.Tests.ps1").is_file()


def test_documentation_retains_unexecuted_native_and_unimplemented_remote_scope() -> None:
    status = (ROOT / "docs/continuation-status.md").read_text(encoding="utf-8")
    assert "Publish" in status and "remote Verify" in status
    assert "NOT_RUN" in status and "NOT_VERIFIED" in status
    assert "tools/WaveHelm-LocalStaging.ps1" in status
    assert "Invoke-Pester" in status
