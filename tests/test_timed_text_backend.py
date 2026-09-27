"""Contract tests use test-owned COM-shaped objects, never Windows evidence."""
from __future__ import annotations

import ctypes as ct
import threading
from types import SimpleNamespace

import pytest

from src.video.component_base.definitions_abi import (
    GUID, IMFMediaEngine, IMFTimedText, IMFTimedTextTrackList,
    IMFTimedTextVtbl, IMFTimedTextTrackListVtbl,
)
from src.video.media_engine_core import MediaEngineCore
from src.video import media_engine_timed_text_backend as backend


class NativeFixture:
    """Own every callback/buffer for the lifetime of these synthetic interfaces."""
    def __init__(self, monkeypatch):
        self.events = []
        self.on_thread = True
        self.service_result = 0
        self.list_result = 0
        self.select_result = 0
        self.service_output = True
        self.list_output = True
        self.after_service = None
        self.service_vt = IMFTimedTextVtbl()
        self.list_vt = IMFTimedTextTrackListVtbl()
        self.track_list = IMFTimedTextTrackList(ct.pointer(self.list_vt))
        self.service = IMFTimedText(ct.pointer(self.service_vt))
        self.callbacks = []
        self._bind(self.service_vt, 'Release', lambda p: self._release('service'))
        self._bind(self.list_vt, 'Release', lambda p: self._release('list'))
        self._bind(self.service_vt, 'GetTextTracks', lambda p, out: self._list(out, False))
        self._bind(self.service_vt, 'GetActiveTracks', lambda p, out: self._list(out, True))
        self._bind(self.service_vt, 'SelectTrack', self._select)
        self.adapter = SimpleNamespace(_get_com_thread_manager=lambda **kw: self)
        self.core = SimpleNamespace(_adapter_ref=lambda: self.adapter,
                                    _state_lock=threading.RLock(), _engine_generation=1,
                                    _media_engine=ct.pointer(IMFMediaEngine()), _shutdown_requested=False)
        monkeypatch.setattr(backend, '_MFGetService', self.get_service)

    def _bind(self, vtable, name, fn):
        callback = dict(vtable._fields_)[name](fn)
        self.callbacks.append(callback)
        setattr(vtable, name, callback)

    def _release(self, name):
        self.events.append(('release', name))
        return 1  # Nonzero is a valid diagnostic refcount, not an HRESULT error.

    def _is_on_com_thread(self):
        return self.on_thread

    def get_service(self, engine, service_id, iid, out):
        self.events.append(('service', engine.value,
                            ct.string_at(service_id, 16).hex(), ct.string_at(iid, 16).hex()))
        if self.service_output:
            ct.cast(out, ct.POINTER(ct.c_void_p))[0] = ct.addressof(self.service)
        if self.after_service is not None:
            self.after_service()
        return self.service_result

    def _list(self, out, active):
        self.events.append(('list', active))
        if self.list_output:
            out[0] = ct.pointer(self.track_list)
        return self.list_result

    def _select(self, pointer, track_id, selected):
        self.events.append(('select', int(track_id), int(selected)))
        return self.select_result


@pytest.fixture
def native(monkeypatch) -> NativeFixture:
    return NativeFixture(monkeypatch)


def test_production_core_has_all_three_real_hooks() -> None:
    assert MediaEngineCore._get_text_tracks_on_com_thread is backend._get_text_tracks_on_com_thread
    assert MediaEngineCore._get_active_timed_text_tracks_on_com_thread is backend._get_active_timed_text_tracks_on_com_thread
    assert MediaEngineCore._call_timed_text_method_on_com_thread is backend._call_timed_text_method_on_com_thread


@pytest.mark.parametrize('active', [False, True])
def test_exact_service_guid_and_iid_owned_list_survives_service_release(native, active) -> None:
    output = backend._get_track_list(native.core, active)
    assert bool(output)
    assert native.events[0][2:] == (
        '11a45e80e092594e9b6e5c7d7915e64f', 'c9942a1fdfa30d439d0facd85ddc29af')
    assert native.events[-2:] == [('list', active), ('release', 'service')]
    backend.release_owned(output)
    assert not output
    assert native.events[-1] == ('release', 'list')


def test_each_acquisition_is_released_even_at_identical_address(native) -> None:
    for _ in range(3):
        output = backend._get_text_tracks_on_com_thread(native.core)
        backend.release_owned(output)
        backend.release_owned(output)
    assert native.events.count(('release', 'service')) == 3
    assert native.events.count(('release', 'list')) == 3


@pytest.mark.parametrize('kind', ['foreign-thread', 'adapter-gone', 'shutdown', 'no-engine', 'wrong-engine', 'bad-generation'])
def test_invalid_context_refuses_before_service_call(native, kind) -> None:
    if kind == 'foreign-thread':
        native.on_thread = False
    elif kind == 'adapter-gone':
        native.adapter = None
    elif kind == 'shutdown':
        native.core._shutdown_requested = True
    elif kind == 'no-engine':
        native.core._media_engine = None
    elif kind == 'wrong-engine':
        native.core._media_engine = object()
    else:
        native.core._engine_generation = True
    with pytest.raises(backend.TimedTextBackendError):
        backend._get_text_tracks_on_com_thread(native.core)
    assert native.events == []


@pytest.mark.parametrize('result,output', [(0, False), (0x80004002, False), (0x80004005, True)])
def test_failed_or_null_service_is_not_guessed_or_dereferenced(native, result, output) -> None:
    native.service_result, native.service_output = result, output
    with pytest.raises(backend.TimedTextBackendError, match='MFGetService'):
        backend._get_text_tracks_on_com_thread(native.core)
    assert len(native.events) == 1


@pytest.mark.parametrize('result,output', [(0, False), (0x80004005, False), (0x80004005, True)])
def test_failed_or_null_list_releases_service_not_unowned_output(native, result, output) -> None:
    native.list_result, native.list_output = result, output
    with pytest.raises(backend.TimedTextBackendError, match='list acquisition'):
        backend._get_text_tracks_on_com_thread(native.core)
    assert native.events[-1] == ('release', 'service')
    assert ('release', 'list') not in native.events


def test_engine_generation_change_during_acquisition_releases_service(native) -> None:
    native.after_service = lambda: setattr(native.core, '_engine_generation', 2)
    with pytest.raises(backend.TimedTextBackendError, match='Engine changed'):
        backend._get_text_tracks_on_com_thread(native.core)
    assert native.events[-1] == ('release', 'service')
    assert not any(e[0] == 'list' for e in native.events)


def test_engine_change_during_list_call_releases_both_owned_outputs(native) -> None:
    native._bind(native.service_vt, 'GetTextTracks',
                 lambda p, out: (setattr(native.core, '_engine_generation', 2), native._list(out, False))[1])
    with pytest.raises(backend.TimedTextBackendError, match='Engine changed'):
        backend._get_text_tracks_on_com_thread(native.core)
    assert native.events[-2:] == [('release', 'service'), ('release', 'list')]


def test_service_release_failure_does_not_leak_successful_list(native, monkeypatch) -> None:
    actual_release = backend.release_owned
    def fail_service(pointer, context=''):
        actual_release(pointer, context)
        if context == 'IMFTimedText service':
            raise backend.TimedTextBackendError('injected release failure')
    monkeypatch.setattr(backend, 'release_owned', fail_service)
    with pytest.raises(backend.TimedTextBackendError, match='injected release'):
        backend._get_text_tracks_on_com_thread(native.core)
    assert native.events[-2:] == [('release', 'service'), ('release', 'list')]


@pytest.mark.parametrize('selected', [False, True])
def test_selection_dispatch_preserves_values_and_releases_service(native, selected) -> None:
    result = backend._call_timed_text_method_on_com_thread(
        native.core, 'SelectTrack', ct.wintypes.DWORD(0xFFFFFFFF), ct.wintypes.BOOL(selected))
    assert result == 0
    assert native.events[-2:] == [('select', 0xFFFFFFFF, int(selected)), ('release', 'service')]


def test_failed_select_retains_hresult_and_releases(native) -> None:
    native.select_result = -2147467259
    with pytest.raises(backend.TimedTextBackendError, match='0x80004005'):
        backend._call_timed_text_method_on_com_thread(native.core, 'SelectTrack', ct.wintypes.DWORD(2), ct.wintypes.BOOL(1))
    assert native.events[-1] == ('release', 'service')


@pytest.mark.parametrize('method,track,selected', [
    ('RemoveTrack', ct.wintypes.DWORD(1), ct.wintypes.BOOL(1)),
    ('SelectTrack', 1, ct.wintypes.BOOL(1)),
    ('SelectTrack', ct.wintypes.DWORD(1), True),
    ('SelectTrack', ct.wintypes.DWORD(1), ct.wintypes.BOOL(2)),
])
def test_invalid_dispatch_fails_before_native_access(native, method, track, selected) -> None:
    with pytest.raises(backend.TimedTextBackendError):
        backend._call_timed_text_method_on_com_thread(native.core, method, track, selected)
    assert native.events == []


def test_null_and_wrong_release_type_are_distinct() -> None:
    backend.release_owned(None)
    backend.release_owned(backend.TrackPointer())
    with pytest.raises(backend.TimedTextBackendError, match='owned typed'):
        backend.release_owned(ct.c_void_p(1))


def test_null_entry_fails_closed_and_releases_service(native) -> None:
    native.service_vt.SelectTrack = dict(IMFTimedTextVtbl._fields_)['SelectTrack']()
    with pytest.raises(backend.TimedTextBackendError, match='entry is NULL'):
        backend._call_timed_text_method_on_com_thread(native.core, 'SelectTrack', ct.wintypes.DWORD(1), ct.wintypes.BOOL(1))
    assert native.events[-1] == ('release', 'service')


def test_service_and_vtable_calls_do_not_hold_core_state_lock(native, monkeypatch) -> None:
    held = []
    original = native.get_service
    def service(*args):
        held.append(native.core._state_lock._is_owned())
        return original(*args)
    monkeypatch.setattr(backend, '_MFGetService', service)
    native._bind(native.service_vt, 'SelectTrack', lambda p, i, s: (held.append(native.core._state_lock._is_owned()), 0)[1])
    backend._call_timed_text_method_on_com_thread(native.core, 'SelectTrack', ct.wintypes.DWORD(0), ct.wintypes.BOOL(1))
    assert held == [False, False]


def test_method_install_is_idempotent_without_native_calls() -> None:
    class Core:
        pass
    backend.attach_media_engine_timed_text_backend(Core)
    first = Core._get_text_tracks_on_com_thread
    backend.attach_media_engine_timed_text_backend(Core)
    assert Core._get_text_tracks_on_com_thread is first
