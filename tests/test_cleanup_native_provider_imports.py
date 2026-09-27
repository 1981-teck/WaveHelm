"""Protect provider cleanup without creating or releasing a native COM device.

Edge cases: deferred annotations need their real provider; a NULL engine must fail
before dereference; missing native exports cannot start MF; release failures must
remain visible. Absence checks are implementation details; others protect contracts.
"""
from __future__ import annotations

import ctypes
import inspect
from types import ModuleType
from typing import get_type_hints

import pytest

from src.video import media_engine_core_playback as playback
from src.video import media_engine_core_vtable as vtable
from src.video import mf_base
from src.video.component_base import com_helpers, definitions, mf_helpers, utils
from src.video.media_engine_core import MediaEngineCore
from src.video.media_engine_core_shared import MediaEngineError


@pytest.mark.parametrize(('module', 'name'), [
    (mf_helpers, '_hr_to_hex'), (playback, 'safe_release'), (vtable, 'IUnknown'),
])
def test_reviewed_incidental_bindings_are_absent(module: ModuleType, name: str) -> None:
    """Remove only the audited module attributes, never their canonical providers."""
    assert name not in vars(module)


@pytest.mark.parametrize(('module', 'name', 'provider'), [
    (mf_helpers, 'ComPtr', com_helpers), (mf_helpers, '_check_hr', com_helpers),
    (playback, 'is_success', utils), (playback, '_hr_to_hex', com_helpers),
    (playback, '_SysAllocString', definitions), (playback, '_SysFreeString', definitions),
    (vtable, 'safe_release', utils), (vtable, 'IMFMediaEngine', definitions),
    (vtable, '_IMF_MEDIA_ENGINE_VTBL_SPECS', definitions),
    (vtable, '_hr_to_hex', com_helpers),
])
def test_required_providers_retain_identity(module: ModuleType, name: str,
                                          provider: ModuleType) -> None:
    """Keep native allocators, release hooks, HRESULT checks and declaration types."""
    assert getattr(module, name) is getattr(provider, name)


@pytest.mark.parametrize(('module', 'count'), [
    (mf_helpers, 8), (playback, 20), (vtable, 10),
])
def test_all_owned_function_annotations_resolve(module: ModuleType, count: int) -> None:
    """Resolve annotations in the real defining namespace, including pointer types."""
    functions = [value for value in vars(module).values()
                 if inspect.isfunction(value) and value.__module__ == module.__name__]
    assert len(functions) == count
    for function in functions:
        assert set(get_type_hints(function)) == set(function.__annotations__)


@pytest.mark.parametrize(('module', 'table'), [
    (playback, '_MEDIA_ENGINE_CORE_PLAYBACK_METHODS'),
    (vtable, '_MEDIA_ENGINE_CORE_VTABLE_METHODS'),
])
def test_all_owner_attachments_keep_function_identity(module: ModuleType, table: str) -> None:
    """Static descriptors must keep their original function, not an accidental wrapper."""
    for name, method in getattr(module, table):
        function = method.__func__ if isinstance(method, staticmethod) else method
        assert getattr(MediaEngineCore, name) is function


def test_public_facade_still_exposes_canonical_native_providers() -> None:
    """The incidental removals must not shrink supported COM/MF facade exports."""
    assert mf_base.IUnknown is definitions.IUnknown is com_helpers.IUnknown
    assert mf_base.safe_release is utils.safe_release is vtable.safe_release
    assert mf_base.ComPtr is com_helpers.ComPtr is mf_helpers.ComPtr
    assert mf_helpers.__all__ == [
        'MFStartup', 'MFShutdown', 'MFCreateAttributes', 'MFCreateMediaEngine',
        'MFCreateMediaEngineClassFactory', 'CLSID_MFMediaEngineClassFactory',
        'IID_IMFMediaEngineClassFactory', 'CLSCTX_INPROC_SERVER',
    ]
    for name in mf_helpers.__all__:
        assert getattr(mf_base, name) is getattr(mf_helpers, name)


@pytest.mark.parametrize(('value', 'expected'), [
    (0, '0x00000000'), (0x80004005, '0x80004005'), (-1, '0xFFFFFFFF'),
])
def test_retained_hresult_formatter_still_formats_values(value: int, expected: str) -> None:
    """Exercise the canonical formatter instead of its deleted mf_helpers alias."""
    assert playback._hr_to_hex(value) == vtable._hr_to_hex(value) == expected


def test_hresult_check_still_rejects_failure() -> None:
    """Removing an unused formatter binding must not weaken the actual checker."""
    with pytest.raises(RuntimeError, match='cleanup-check failed: hr=0x80004005'):
        mf_helpers._check_hr(0x80004005, 'cleanup-check')


def test_missing_startup_export_does_not_change_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail before touching refcount when the native export is unavailable."""
    monkeypatch.setattr(mf_helpers, '_MFStartup', None)
    monkeypatch.setattr(mf_helpers, '_mf_refcount', 0)
    monkeypatch.setattr(mf_helpers, '_mf_started', False)
    with pytest.raises(RuntimeError, match='MFStartup non disponibile'):
        mf_helpers.MFStartup()
    assert mf_helpers._mf_refcount == 0
    assert mf_helpers._mf_started is False


def test_missing_factory_providers_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """No available export or COM factory must never become successful construction."""
    monkeypatch.setattr(mf_helpers, '_MFCreateMediaEngineClassFactory', None)
    monkeypatch.setattr(mf_helpers, '_CoCreateInstance', None)
    with pytest.raises(RuntimeError, match='CoCreateInstance non disponibile'):
        mf_helpers.MFCreateMediaEngine()


def test_null_notification_never_calls_release(monkeypatch: pytest.MonkeyPatch) -> None:
    """A NULL notification must exit without attempting release."""
    calls: list[tuple[object, str]] = []
    monkeypatch.setattr(vtable, 'safe_release', lambda value, name: calls.append((value, name)))
    vtable._release_notify_iunknown(None)
    assert calls == []


def test_notification_release_uses_retained_patch_point(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the real function with a test-owned release boundary, not native COM."""
    marker = object()
    calls: list[tuple[object, str]] = []
    monkeypatch.setattr(vtable, 'safe_release', lambda value, name: calls.append((value, name)))
    vtable._release_notify_iunknown(marker)
    assert calls == [(marker, 'notify_iunknown')]


def test_notification_release_failure_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """The existing finally clause must not swallow a release error."""
    error = RuntimeError('release-boundary-failure')
    def fail_release(value: object, name: str) -> None:
        raise error
    monkeypatch.setattr(vtable, 'safe_release', fail_release)
    with pytest.raises(RuntimeError, match='release-boundary-failure') as raised:
        vtable._release_notify_iunknown(object())
    assert raised.value is error


def test_null_engine_pointer_is_rejected() -> None:
    """A NULL pointer must raise before looking up or calling a native vtable."""
    with pytest.raises(MediaEngineError, match='engine_ptr assente'):
        vtable._call_engine_ptr_method(None, 'Play')


def test_unknown_vtable_spec_is_rejected_before_dereference() -> None:
    """A real local ctypes struct with NULL vtable is safe only before dereference."""
    pointer = ctypes.pointer(definitions.IMFMediaEngine())
    with pytest.raises(MediaEngineError, match='Spec vtable mancante'):
        vtable._call_engine_ptr_method(pointer, '__unknown_cleanup_method__')
