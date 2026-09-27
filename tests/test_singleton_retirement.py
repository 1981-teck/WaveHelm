"""Guard a retired unused module without replacing active singleton mechanisms.

Edge cases: stale source/bytecode left by overlay copying; obsolete direct or
relative imports; accidental loss of active configuration reuse/reset contracts.
Unknown external and arbitrarily computed consumers are outside these guards.
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
RETIRED = "src.utils.singleton"


def test_retired_source_is_absent() -> None:
    """A clean source distribution must not retain the retired implementation."""
    assert not (ROOT / "src/utils/singleton.py").exists()


def test_no_orphan_singleton_bytecode() -> None:
    """Legacy adjacent bytecode must not resurrect the removed module."""
    assert not (ROOT / "src/utils/singleton.pyc").exists()
    assert not list((ROOT / "src/utils/__pycache__").glob("singleton.*.pyc"))


def test_retired_import_reports_exact_missing_module() -> None:
    """Exercise normal Python import resolution in a clean process, not a stub."""
    code = (
        "import importlib, sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "try:\n"
        "    importlib.import_module('src.utils.singleton')\n"
        "except ModuleNotFoundError as exc:\n"
        "    if exc.name != 'src.utils.singleton': raise\n"
        "else:\n"
        "    raise RuntimeError('retired singleton is still importable')\n"
        "print('EXPECTED_RETIRED_MODULE_ABSENCE')\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code, str(ROOT)],
        capture_output=True, text=True, encoding="utf-8", timeout=15, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "EXPECTED_RETIRED_MODULE_ABSENCE" in result.stdout


def _is_retired_reference(node: ast.AST) -> bool:
    """Recognize explicit imports, type names and full-path dynamic literals."""
    if isinstance(node, ast.ImportFrom):
        return node.module in {RETIRED, "utils.singleton"} or (
            node.module == "src.utils" and any(a.name == "singleton" for a in node.names)
        ) or (node.level > 0 and node.module == "singleton")
    if isinstance(node, ast.Import):
        return any(a.name == RETIRED for a in node.names)
    if isinstance(node, ast.Name):
        return node.id == "ThreadSafeSingleton"
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value in {RETIRED, "ThreadSafeSingleton", "src/utils/singleton.py"}
    return False


@pytest.mark.parametrize("location", ["src", "tools", "main.py"])
def test_no_explicit_application_consumer(location: str) -> None:
    """No runtime/tool consumer should retain the obsolete import or metaclass."""
    root = ROOT / location
    paths = [root] if root.is_file() else sorted(root.rglob("*.py"))
    findings: list[str] = []
    for path in paths:
        if path == ROOT / "src/utils/singleton.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if _is_retired_reference(node):
                findings.append(f"{path.relative_to(ROOT)}:{getattr(node, 'lineno', 0)}")
    assert not findings, findings


def test_utils_package_did_not_reexport_retired_class() -> None:
    """The utils package marker keeps its existing minimal namespace."""
    package = importlib.import_module("src.utils")
    assert not hasattr(package, "ThreadSafeSingleton")


def test_public_helpers_remain_importable() -> None:
    """Retiring an unused module must not remove unrelated utility APIs."""
    from src.utils.helpers import format_file_size

    assert format_file_size(1024 ** 4) == "1.00 TB"


def test_audio_config_still_reuses_its_own_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real audio configuration uses its own implementation, not the metaclass."""
    from src.audio import audio_config

    monkeypatch.setattr(audio_config, "_audio_config_instance", None)
    first = audio_config.get_audio_config()
    assert audio_config.get_audio_config() is first
    assert isinstance(first, audio_config.AudioEngineConfig)
    assert type(audio_config.AudioEngineConfig) is type


def test_audio_config_reset_still_recreates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset only the test-owned configuration instance, preserving normal defaults."""
    from src.audio import audio_config

    monkeypatch.setattr(audio_config, "_audio_config_instance", None)
    first = audio_config.get_audio_config()
    audio_config.reset_audio_config()
    second = audio_config.get_audio_config()
    assert first is not second
    assert first.frequency == second.frequency == 44100


def test_audio_config_still_rejects_invalid_frequency() -> None:
    """An unrelated validation contract is not weakened by the retirement."""
    from src.audio.audio_config import AudioEngineConfig

    with pytest.raises(ValueError, match="Invalid frequency"):
        AudioEngineConfig(frequency=0)


def test_database_class_has_no_retired_metaclass() -> None:
    """Inspect the real class without opening a user database."""
    from src.model.component_database.db_core import DbCore

    assert type(DbCore) is type
    assert "ThreadSafeSingleton" not in {base.__name__ for base in DbCore.__mro__}
