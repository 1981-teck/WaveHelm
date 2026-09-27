"""Protect compatibility exports without hiding genuine provider conflicts.

Edge cases: fresh/repeated imports, an unexpected replacement provider, and
proxy-local monkeypatches must preserve the bridge and standard-library boundary.
No Win32 API or physical media device is exercised by these local tests.
"""
from __future__ import annotations

import ctypes
import importlib
import json
import logging
from pathlib import Path
import subprocess
import sys
from types import ModuleType

import pytest

from src.video import mf_base
from src.video.component_base import com_helpers, definitions, mf_helpers, utils


def test_fresh_import_has_no_ctypes_collision() -> None:
    """The real compatibility module must compose its exports without a conflict."""
    command = (
        "import json; from src.video import mf_base; "
        "from src.video.component_base import com_helpers; "
        "print(json.dumps({'exports': mf_base.__all__, "
        "'proxy_preserved': mf_base.ctypes is com_helpers.ctypes}))"
    )
    result = subprocess.run(
        [sys.executable, '-B', '-c', command],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, encoding='utf-8', timeout=30, check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload['proxy_preserved'] is True
    assert payload['exports'] == mf_base.__all__
    assert "Collisione export 'ctypes'" not in result.stderr


def test_reload_has_no_ctypes_collision(caplog: pytest.LogCaptureFixture) -> None:
    """A repeat composition must not temporarily replace the canonical proxy."""
    caplog.set_level(logging.WARNING, logger=mf_base.__name__)
    importlib.reload(mf_base)
    assert mf_base.ctypes is com_helpers.ctypes
    assert "Collisione export 'ctypes'" not in caplog.text


def test_public_export_roster_is_unchanged() -> None:
    """Retain the historical union, including the legacy ctypes attribute."""
    modules = (definitions, com_helpers, mf_helpers, utils)
    expected = sorted({name for mod in modules for name in mf_base._public_names_from_module(mod)})
    assert mf_base.__all__ == expected
    assert 'ctypes' in expected
    assert 'ctypes' in mf_base._public_names_from_module(definitions)


@pytest.mark.parametrize('name,owner', [
    ('ctypes', com_helpers), ('ComPtr', com_helpers),
    ('describe_hresult', com_helpers), ('GUID', definitions),
    ('HRESULT', definitions), ('IUnknown', definitions),
    ('_hr_ok', definitions),
])
def test_required_export_identity(name: str, owner: ModuleType) -> None:
    """Identity, not merely similar callable behavior, is the compatibility contract."""
    assert getattr(mf_base, name) is getattr(owner, name)


def test_proxy_delegates_real_ctypes_types() -> None:
    assert mf_base.ctypes is not ctypes
    assert mf_base.ctypes.c_void_p is ctypes.c_void_p
    assert mf_base.ctypes.c_uint32 is ctypes.c_uint32
    assert mf_base.ctypes.c_void_p(123).value == 123


def test_proxy_patch_does_not_modify_stdlib(monkeypatch: pytest.MonkeyPatch) -> None:
    """The proxy is retained because its WinDLL patch point is intentionally local."""
    sentinel = object()
    stdlib_before = getattr(ctypes, 'WinDLL', sentinel)
    replacement = object()
    monkeypatch.setattr(com_helpers.ctypes, 'WinDLL', replacement)
    assert mf_base.ctypes.WinDLL is replacement
    assert getattr(ctypes, 'WinDLL', sentinel) is stdlib_before


def test_real_collision_still_warns(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    name = '_wavehelm_test_export_collision'
    original, replacement = object(), object()
    source = ModuleType('wavehelm_test_export_provider')
    setattr(source, name, replacement)
    monkeypatch.setattr(mf_base, name, original, raising=False)
    caplog.set_level(logging.WARNING, logger=mf_base.__name__)
    mf_base._export(source, [name], source_label='unexpected-provider')
    assert getattr(mf_base, name) is replacement
    assert name in caplog.text
    assert 'unexpected-provider' in caplog.text


def test_unexpected_ctypes_provider_is_not_suppressed(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    replacement = object()
    source = ModuleType('wavehelm_test_foreign_ctypes')
    source.ctypes = replacement
    monkeypatch.setattr(mf_base, 'ctypes', com_helpers.ctypes)
    caplog.set_level(logging.WARNING, logger=mf_base.__name__)
    mf_base._export(source, ['ctypes'], source_label='foreign-provider')
    assert mf_base.ctypes is replacement
    assert "Collisione export 'ctypes'" in caplog.text


def test_identical_export_is_idempotent(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    name = '_wavehelm_test_identical_export'
    value = object()
    source = ModuleType('wavehelm_test_same_export')
    setattr(source, name, value)
    monkeypatch.setattr(mf_base, name, value, raising=False)
    caplog.set_level(logging.WARNING, logger=mf_base.__name__)
    mf_base._export(source, [name, name], source_label='same-provider')
    assert getattr(mf_base, name) is value
    assert not caplog.records


def test_foreign_logger_cannot_replace_bridge_logger(caplog: pytest.LogCaptureFixture) -> None:
    original = mf_base.logger
    source = ModuleType('wavehelm_test_logger_export')
    source.logger = logging.getLogger('wavehelm_test_foreign_logger')
    caplog.set_level(logging.WARNING, logger=mf_base.__name__)
    mf_base._export(source, ['logger'], source_label='logger-provider')
    assert mf_base.logger is original
    assert not caplog.records
