"""Guard private alias cleanup while retaining real runtime and optional providers.

Edge cases: missing DLLs must fail in strict mode, missing function proxies must
raise when called, and private facade removal must not erase canonical providers.
Native loading failures are injected only at the test boundary; no device is used.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import ModuleType

import pytest

from src.video import mf_base
from src.video import media_engine_core_setup as setup
from src.video import media_engine_timed_text_backend as text_backend
from src.video.component_adapter import com_thread_manager, media_engine_events
from src.video.component_base import definitions, definitions_runtime as runtime

PRIVATE_ALIASES = (
    '_MissingDLL', '_STRICT_DLL_LOAD', '_kernel32',
    '_load_windll', '_oleaut32', '_user32',
)
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('name', PRIVATE_ALIASES)
def test_private_alias_is_removed_only_from_definitions(name: str) -> None:
    """The six reviewed names cease to be incidental attributes of the facade."""
    assert name not in vars(definitions)


@pytest.mark.parametrize('name', PRIVATE_ALIASES)
def test_canonical_runtime_provider_remains_present(name: str) -> None:
    """Do not remove the loader, policy, DLL owners or failure type themselves."""
    assert name in vars(runtime)


@pytest.mark.parametrize('name', PRIVATE_ALIASES)
def test_reviewed_names_are_not_public_mf_exports(name: str) -> None:
    """None of the removed private aliases belonged to the supported MF facade."""
    assert name not in mf_base.__all__
    assert name not in vars(mf_base)


@pytest.mark.parametrize('name', [
    '_IsWindow', '_SysAllocString', '_SysFreeString', '_mfplat', '_ole32',
    'MF_VERSION', 'MF_VERSION_PARSE_EXCEPTIONS',
])
def test_required_runtime_binding_identity_is_preserved(name: str) -> None:
    """Keep required private and public bindings with their real canonical owner."""
    assert getattr(definitions, name) is getattr(runtime, name)


@pytest.mark.parametrize('module', [setup, text_backend, com_thread_manager, media_engine_events])
def test_real_loader_consumers_use_canonical_runtime(module: ModuleType) -> None:
    """Existing native consumers do not depend on the retired definitions alias."""
    assert module._load_windll is runtime._load_windll


def test_missing_dll_proxy_reports_failure() -> None:
    error = OSError('missing-test-library')
    proxy = runtime._MissingDLL('review-library', error)
    assert not proxy
    assert proxy.error is error
    with pytest.raises(AttributeError, match='review-library'):
        getattr(proxy, 'MissingExport')


def _failing_loader(name: str, *, use_last_error: bool) -> object:
    """Simulate only a DLL acquisition failure, never a successful native service."""
    raise OSError(f'controlled missing DLL: {name}, last_error={use_last_error}')


def test_strict_required_dll_failure_is_not_suppressed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime.ctypes, 'WinDLL', _failing_loader, raising=False)
    monkeypatch.setattr(runtime, '_STRICT_DLL_LOAD', True)
    with pytest.raises(OSError, match='Impossibile caricare'):
        runtime._load_windll('review-library')


@pytest.mark.parametrize(('strict', 'required'), [(False, True), (True, False), (False, False)])
def test_permitted_missing_library_stays_explicit(monkeypatch: pytest.MonkeyPatch,
                                                 strict: bool, required: bool) -> None:
    monkeypatch.setattr(runtime.ctypes, 'WinDLL', _failing_loader, raising=False)
    monkeypatch.setattr(runtime, '_STRICT_DLL_LOAD', strict)
    proxy = runtime._load_windll('review-library', required=required)
    assert isinstance(proxy, runtime._MissingDLL)
    assert not proxy
    assert isinstance(proxy.error, OSError)


def test_missing_export_still_raises_on_execution() -> None:
    proxy = runtime._MissingDLL('review-library', OSError('unavailable'))
    function = runtime._bind_function(proxy, 'review-library', 'Absent', argtypes=[], restype=None)
    with pytest.raises(RuntimeError, match='review-library!Absent'):
        function()


def test_optional_win32api_block_is_retained() -> None:
    """This AST check does not claim execution of Windows package initialization."""
    tree = ast.parse((ROOT / 'src/controller/app_controller.py').read_bytes())
    blocks = [node for node in tree.body if isinstance(node, ast.Try)]
    block = next(node for node in blocks if any(
        isinstance(item, ast.Import) and any(alias.name == 'win32api' for alias in item.names)
        for item in node.body))
    assert len(block.handlers) == 1
    assert isinstance(block.handlers[0].type, ast.Name)
    assert block.handlers[0].type.id == 'ImportError'
    assignment = block.handlers[0].body[0]
    assert isinstance(assignment, ast.Assign)
    assert isinstance(assignment.value, ast.Constant) and assignment.value.value is None


def test_pillow_module_alias_is_retained() -> None:
    """Keep the intentional legacy alias without fabricating an external caller."""
    from PIL import Image
    from src.model import media_loader
    assert media_loader.Image is Image
    with Image.new('RGB', (1, 1), (12, 34, 56)) as image:
        assert image.getpixel((0, 0)) == (12, 34, 56)
