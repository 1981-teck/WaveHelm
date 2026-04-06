from __future__ import annotations

import ctypes
import threading
from types import SimpleNamespace

import pytest

import src.video.media_engine_core_setup as media_engine_core_setup


class DummyCore:
    def __init__(self):
        self._state_lock = threading.RLock()
        self._shutdown_requested = False
        self._media_engine = object()
        self._media_engine_ex = None
        self._playback_hwnd = None
        self.calls = []
        self.query_calls = []

    def _call_vtable_method(self, name, hwnd):
        self.calls.append((name, hwnd))

    def _call_engine_ptr_method(self, engine_ptr, name, *args):
        self.calls.append((name, args))
        if name == 'GetNumberOfStreams':
            args[0]._obj.value = 3
            return 0
        if name == 'GetStreamSelection':
            args[1]._obj.value = 1
            return 0
        return 0

    def _query_media_engine_ex_on_com_thread(self, engine_ptr):
        self.query_calls.append(engine_ptr)
        return 'engine-ex'

    def _ensure_media_engine_ex_on_com_thread(self):
        return media_engine_core_setup._ensure_media_engine_ex_on_com_thread(self)

    def _call_media_engine_ex_method_on_com_thread(self, method_name, *args):
        return media_engine_core_setup._call_media_engine_ex_method_on_com_thread(self, method_name, *args)

    def _normalize_stream_index(self, stream_index):
        return media_engine_core_setup._normalize_stream_index(self, stream_index)


class FailingCore(DummyCore):
    def _call_vtable_method(self, name, hwnd):
        raise ValueError('fail')


class FakeVTableMethods:
    def __init__(self, *, hr64=0, hr32=0, hr_unknown=0):
        self.hr64 = hr64
        self.hr32 = hr32
        self.hr_unknown = hr_unknown
        self.calls = []

    def SetUINT64(self, attrs_ptr, guid_ptr, value):
        self.calls.append(('u64', int(value.value)))
        return self.hr64

    def SetUINT32(self, attrs_ptr, guid_ptr, value):
        self.calls.append(('u32', int(value.value)))
        return self.hr32

    def SetUnknown(self, attrs_ptr, guid_ptr, unk_ptr):
        self.calls.append(('unk', unk_ptr))
        return self.hr_unknown


class MissingVTable:
    pass


class DummyIUnknown:
    pass



def _make_attrs_ptr(vtable_contents):
    return SimpleNamespace(contents=SimpleNamespace(lpVtbl=SimpleNamespace(contents=vtable_contents)))



def test_try_rebind_video_window_on_com_thread_success_and_failure():
    core = DummyCore()
    assert media_engine_core_setup._try_rebind_video_window_on_com_thread(core, 77) is True
    assert core.calls[0][0] == 'SetVideoWindow'
    assert core.calls[0][1].value == 77
    assert core._playback_hwnd == 77

    failing = FailingCore()
    assert media_engine_core_setup._try_rebind_video_window_on_com_thread(failing, 88) is False
    assert failing._playback_hwnd is None



def test_query_media_engine_ex_returns_none_on_null_or_cast_failure(monkeypatch):
    core = object()
    assert media_engine_core_setup._query_media_engine_ex_on_com_thread(core, None) is None

    monkeypatch.setattr(media_engine_core_setup, 'cast', lambda *args, **kwargs: (_ for _ in ()).throw(TypeError('cast fail')))
    assert media_engine_core_setup._query_media_engine_ex_on_com_thread(core, object()) is None



def test_imfattributes_wrappers_use_vtable_and_raise_on_missing_or_failed_calls():
    methods = FakeVTableMethods()
    attrs_ptr = _make_attrs_ptr(methods)
    guid = ctypes.c_uint32(7)

    media_engine_core_setup._imfattributes_set_uint64(object(), attrs_ptr, guid, 42)
    media_engine_core_setup._imfattributes_set_uint32(object(), attrs_ptr, guid, 21)
    media_engine_core_setup._imfattributes_set_unknown(object(), attrs_ptr, guid, 'unk')

    assert methods.calls == [('u64', 42), ('u32', 21), ('unk', 'unk')]

    with pytest.raises(RuntimeError):
        media_engine_core_setup._imfattributes_set_uint64(object(), _make_attrs_ptr(MissingVTable()), guid, 1)

    failing64 = _make_attrs_ptr(FakeVTableMethods(hr64=5))
    with pytest.raises(OSError):
        media_engine_core_setup._imfattributes_set_uint64(object(), failing64, guid, 1)

    failing32 = _make_attrs_ptr(FakeVTableMethods(hr32=5))
    with pytest.raises(OSError):
        media_engine_core_setup._imfattributes_set_uint32(object(), failing32, guid, 1)

    failing_unknown = _make_attrs_ptr(FakeVTableMethods(hr_unknown=5))
    with pytest.raises(OSError):
        media_engine_core_setup._imfattributes_set_unknown(object(), failing_unknown, guid, DummyIUnknown())


def test_media_engine_ex_helpers_cache_query_and_dispatch_methods():
    core = DummyCore()

    engine_ex = media_engine_core_setup._ensure_media_engine_ex_on_com_thread(core)
    assert engine_ex == 'engine-ex'
    assert core._media_engine_ex == 'engine-ex'
    assert core.query_calls == [core._media_engine]

    again = media_engine_core_setup._ensure_media_engine_ex_on_com_thread(core)
    assert again == 'engine-ex'
    assert core.query_calls == [core._media_engine]

    count = media_engine_core_setup._get_number_of_streams_on_com_thread(core)
    selected = media_engine_core_setup._get_stream_selection_on_com_thread(core, 1)
    media_engine_core_setup._set_stream_selection_on_com_thread(core, 2, True)
    media_engine_core_setup._apply_stream_selections_on_com_thread(core)

    assert count == 3
    assert selected is True
    assert ('GetNumberOfStreams',) == (core.calls[0][0],)
    assert any(name == 'GetStreamSelection' for name, _args in core.calls)
    assert any(name == 'SetStreamSelection' and int(args[0].value) == 2 and bool(args[1].value) for name, args in core.calls)
    assert any(name == 'ApplyStreamSelections' for name, _args in core.calls)


def test_media_engine_ex_helpers_reject_invalid_indices_and_missing_interface():
    core = DummyCore()

    with pytest.raises(ValueError):
        media_engine_core_setup._normalize_stream_index(core, -1)

    with pytest.raises(ValueError):
        media_engine_core_setup._normalize_stream_index(core, 0x100000000)

    core._media_engine = None
    with pytest.raises(media_engine_core_setup.MediaEngineError):
        media_engine_core_setup._ensure_media_engine_ex_on_com_thread(core)

    core._media_engine = object()
    core._query_media_engine_ex_on_com_thread = lambda _engine: None
    with pytest.raises(media_engine_core_setup.MediaEngineError):
        media_engine_core_setup._ensure_media_engine_ex_on_com_thread(core)


def test_media_engine_ex_helpers_raise_on_hresult_failures():
    class HrFailingCore(DummyCore):
        def _call_engine_ptr_method(self, engine_ptr, name, *args):
            self.calls.append((name, args))
            return 0x80004005

    core = HrFailingCore()
    core._media_engine_ex = 'engine-ex'

    with pytest.raises(RuntimeError):
        media_engine_core_setup._get_number_of_streams_on_com_thread(core)

    with pytest.raises(RuntimeError):
        media_engine_core_setup._set_stream_selection_on_com_thread(core, 0, True)

    with pytest.raises(RuntimeError):
        media_engine_core_setup._apply_stream_selections_on_com_thread(core)
