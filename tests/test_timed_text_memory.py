"""Ownership regression tests; Python-owned buffers never reach a native free.

The Windows subprocess test alone uses real CoTaskMemAlloc/CoTaskMemFree.
Other tests use explicit test-owned buffers and allocator call spies.
"""
from __future__ import annotations

import ctypes
import importlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

from src.video import media_engine_core_playback as playback

memory = importlib.import_module(playback._decode_timed_text_wstr.__module__)


def _owned(text: str) -> tuple[ctypes.Array, ctypes.c_wchar_p]:
    buffer = ctypes.create_unicode_buffer(text)
    return buffer, ctypes.cast(buffer, ctypes.c_wchar_p)


@pytest.fixture
def allocators(monkeypatch: pytest.MonkeyPatch) -> tuple[list[int], list[object]]:
    task_frees: list[int] = []
    bstr_frees: list[object] = []
    monkeypatch.setattr(memory, '_CoTaskMemFree', lambda p: task_frees.append(p.value), raising=False)
    monkeypatch.setattr(memory, '_SysFreeString', lambda p: bstr_frees.append(p), raising=False)
    return task_frees, bstr_frees


@pytest.mark.parametrize('text', ['', 'it-IT', '  Français 日本語  ', 'Music 🎵'])
def test_owned_lpwstr_uses_task_allocator_and_clears_owner(text: str, allocators) -> None:
    buffer, owner = _owned(text)
    assert memory._decode_timed_text_wstr(owner) == text.strip()
    assert allocators == ([ctypes.addressof(buffer)], [])
    assert owner.value is None


@pytest.mark.parametrize('value', [None, ctypes.c_wchar_p()])
def test_null_is_empty_without_free(value, allocators) -> None:
    assert memory._decode_timed_text_wstr(value) == ''
    assert allocators == ([], [])


@pytest.mark.parametrize('value', ['borrowed Python text', b'bytes', 1, ctypes.c_void_p(1), object()])
def test_non_owned_input_is_rejected_without_read_or_free(value, allocators) -> None:
    with pytest.raises((TypeError, RuntimeError)):
        memory._decode_timed_text_wstr(value)
    assert allocators == ([], [])


def test_consumed_pointer_cannot_be_freed_twice(allocators) -> None:
    buffer, owner = _owned('label')
    memory._decode_timed_text_wstr(owner)
    assert memory._decode_timed_text_wstr(owner) == ''
    assert allocators == ([ctypes.addressof(buffer)], [])


def test_decode_exception_still_releases_exact_allocation(monkeypatch, allocators) -> None:
    buffer, owner = _owned('label')
    def unreadable(address: int) -> str:
        raise ValueError('decoder fault injection')
    monkeypatch.setattr(memory, '_copy_task_wstr', unreadable, raising=False)
    with pytest.raises(RuntimeError, match='decoder fault injection'):
        memory._decode_timed_text_wstr(owner)
    assert allocators == ([ctypes.addressof(buffer)], [])
    assert owner.value is None


def test_release_failure_is_not_silently_accepted(monkeypatch, allocators) -> None:
    buffer, owner = _owned('label')
    def unavailable(address: ctypes.c_void_p) -> None:
        raise RuntimeError('allocator fault injection')
    monkeypatch.setattr(memory, '_CoTaskMemFree', unavailable, raising=False)
    with pytest.raises(RuntimeError, match='allocator fault injection'):
        memory._decode_timed_text_wstr(owner)
    assert owner.value is None
    assert allocators[1] == []


def test_oversized_native_string_fails_without_leaking(allocators) -> None:
    buffer, owner = _owned('x' * 32769)
    with pytest.raises(RuntimeError, match='limit'):
        memory._decode_timed_text_wstr(owner)
    assert allocators == ([ctypes.addressof(buffer)], [])
    assert owner.value is None


def test_media_source_bstr_still_uses_its_original_allocator(monkeypatch) -> None:
    # Observable compatibility patch points used by the existing load_source test.
    assert playback._SysFreeString is not getattr(memory, '_CoTaskMemFree', None)


@pytest.mark.skipif(os.name != 'nt', reason='real Windows COM task allocator required')
def test_native_task_allocator_roundtrip_in_separate_process() -> None:
    root = str(Path(__file__).resolve().parents[1])
    code = '''import ctypes, sys
sys.path.insert(0, sys.argv[1])
from src.video.media_engine_core_timed_text import _decode_timed_text_wstr
ole32 = ctypes.WinDLL('ole32')
alloc = ole32.CoTaskMemAlloc
alloc.argtypes = [ctypes.c_size_t]
alloc.restype = ctypes.c_void_p
for text in ['', 'it-IT', 'Français 日本語', 'Music 🎵'] * 16:
    source = ctypes.create_unicode_buffer(text)
    address = alloc(ctypes.sizeof(source))
    if not address:
        raise MemoryError('CoTaskMemAlloc')
    ctypes.memmove(address, source, ctypes.sizeof(source))
    owned = ctypes.cast(address, ctypes.c_wchar_p)
    if _decode_timed_text_wstr(owned) != text or owned.value is not None:
        raise RuntimeError('native allocation/decoding/ownership mismatch')
print('64 native task-memory roundtrips; no Media Foundation getter invoked')
'''
    result = subprocess.run([sys.executable, '-I', '-B', '-c', code, root],
                            capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '64 native task-memory roundtrips' in result.stdout
