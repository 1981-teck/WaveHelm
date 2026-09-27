"""Test-owned COM-shaped fixtures: not native Media Foundation activation evidence."""
from __future__ import annotations

import ctypes
from types import SimpleNamespace
import threading

import pytest

from src.video import media_engine_core_timed_text as timed


def _track(text: str, status: int = 0, *, null: bool = False) -> tuple[SimpleNamespace, ctypes.Array]:
    buffer = ctypes.create_unicode_buffer(text)
    def getter(_this: object, output: object) -> int:
        if not null:
            ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.addressof(buffer)
        return status
    table = SimpleNamespace(GetLabel=getter, GetLanguage=getter, GetId=lambda _p: 9,
                            GetTrackKind=lambda _p: 1, IsActive=lambda _p: 1, IsInBand=lambda _p: 0)
    return SimpleNamespace(contents=SimpleNamespace(lpVtbl=SimpleNamespace(contents=table))), buffer


@pytest.mark.parametrize('field', ['GetLabel', 'GetLanguage'])
@pytest.mark.parametrize('text', ['', 'it-IT', '日本語'])
def test_successful_getter_copies_and_frees_exact_out_pointer(monkeypatch, field: str, text: str) -> None:
    track, buffer = _track(text)
    freed: list[int] = []
    monkeypatch.setattr(timed, '_CoTaskMemFree', lambda p: freed.append(p.value))
    assert timed._read_track_string(track, field) == text
    assert freed == [ctypes.addressof(buffer)]


@pytest.mark.parametrize('status', [0, 0x80004005])
def test_null_getter_output_never_calls_the_allocator(monkeypatch, status: int) -> None:
    track, _ = _track('', status, null=True)
    freed: list[int] = []
    monkeypatch.setattr(timed, '_CoTaskMemFree', lambda p: freed.append(p.value))
    assert timed._read_track_string(track, 'GetLabel') == ''
    assert freed == []


def test_failed_hresult_with_non_null_output_refuses_to_guess_ownership(monkeypatch) -> None:
    track, _ = _track('provider broke the COM failure contract', 0x80004005)
    freed: list[int] = []
    monkeypatch.setattr(timed, '_CoTaskMemFree', lambda p: freed.append(p.value))
    with pytest.raises(timed.TimedTextMemoryError, match='ownership unknown'):
        timed._read_track_string(track, 'GetLabel')
    assert freed == []


def test_getter_exception_is_not_reported_as_success(monkeypatch) -> None:
    track, _ = _track('')
    def broken(_this: object, _output: object) -> int:
        raise OSError('getter unavailable')
    track.contents.lpVtbl.contents.GetLabel = broken
    with pytest.raises(timed.TimedTextMemoryError, match='before ownership transfer'):
        timed._read_track_string(track, 'GetLabel')


def test_descriptor_preserves_labels_and_consumes_each_allocation(monkeypatch) -> None:
    track, buffer = _track('Subtitles')
    freed: list[int] = []
    monkeypatch.setattr(timed, '_CoTaskMemFree', lambda p: freed.append(p.value))
    descriptor = timed._read_timed_text_track_descriptor_on_com_thread(SimpleNamespace(), track)
    assert descriptor == {'track_id': 9, 'kind': 1, 'kind_label': 'kind-1', 'language': 'Subtitles',
                          'label': 'Subtitles', 'raw_label': 'Subtitles', 'is_active': True, 'is_in_band': False}
    # The test getter reuses a test buffer; each successful call owns one separate contract result.
    assert freed == [ctypes.addressof(buffer), ctypes.addressof(buffer)]


def test_descriptor_null_metadata_uses_explicit_display_fallback(monkeypatch) -> None:
    track, _ = _track('', 0x80004005, null=True)
    descriptor = timed._read_timed_text_track_descriptor_on_com_thread(SimpleNamespace(), track)
    assert descriptor['label'] == 'Subtitle 9'
    assert descriptor['raw_label'] == descriptor['language'] == ''


def test_descriptor_memory_failure_is_not_swallowed(monkeypatch) -> None:
    track, _ = _track('label')
    monkeypatch.setattr(timed, '_CoTaskMemFree', lambda _p: None)
    def broken(_address: int) -> str:
        raise ValueError('invalid native text')
    monkeypatch.setattr(timed, '_copy_task_wstr', broken)
    with pytest.raises(timed.TimedTextMemoryError, match='invalid native text'):
        timed._read_timed_text_track_descriptor_on_com_thread(SimpleNamespace(), track)


def test_enumeration_releases_track_when_descriptor_ownership_fails(monkeypatch) -> None:
    track = ctypes.pointer(timed.IMFTimedTextTrack())
    def get_track(_this: object, _index: object, output: object) -> int:
        ctypes.cast(output, ctypes.POINTER(timed.TrackPointer))[0] = track
        return 0
    table = SimpleNamespace(GetLength=lambda _p: 1, GetTrack=get_track)
    track_list = SimpleNamespace(contents=SimpleNamespace(lpVtbl=SimpleNamespace(contents=table)))
    released: list[object] = []
    monkeypatch.setattr(timed, 'safe_release', lambda p, _name: released.append(p))
    def broken(_core: object, _track: object) -> None:
        raise timed.TimedTextMemoryError('unsafe ownership')
    monkeypatch.setattr(timed, '_read_timed_text_track_descriptor_on_com_thread', broken)
    with pytest.raises(timed.TimedTextMemoryError, match='unsafe ownership'):
        timed._enumerate_timed_text_track_list_on_com_thread(SimpleNamespace(), track_list)
    assert len(released) == 1
    assert ctypes.cast(released[0], ctypes.c_void_p).value == ctypes.cast(track, ctypes.c_void_p).value


def test_oversize_track_list_fails_before_any_acquisition() -> None:
    table = SimpleNamespace(GetLength=lambda _p: timed.MAX_TIMED_TEXT_TRACKS + 1)
    track_list = SimpleNamespace(contents=SimpleNamespace(lpVtbl=SimpleNamespace(contents=table)))
    with pytest.raises(timed.TimedTextMemoryError, match='1024-track limit'):
        timed._enumerate_timed_text_track_list_on_com_thread(SimpleNamespace(), track_list)


def test_core_query_does_not_convert_memory_failure_to_empty_success() -> None:
    def failure(_name: str, _callback: object) -> None:
        raise timed.TimedTextMemoryError('ownership failure')
    core = SimpleNamespace(_adapter_ref=lambda: SimpleNamespace(call_on_com_thread=failure),
                           _media_engine=object(), _state_lock=threading.RLock(), _shutdown_requested=False)
    with pytest.raises(timed.TimedTextMemoryError, match='ownership failure'):
        timed.get_text_track_descriptors(core)


def _unsupported_core() -> SimpleNamespace:
    def call(_name: str, callback):
        return callback()
    return SimpleNamespace(_adapter_ref=lambda: SimpleNamespace(call_on_com_thread=call),
                           _media_engine=object(), _state_lock=threading.RLock(), _shutdown_requested=False)


def test_missing_native_getter_is_explicitly_unavailable() -> None:
    with pytest.raises(timed.TimedTextUnavailableError, match='not implemented'):
        timed._get_text_track_descriptors_on_com_thread(_unsupported_core())


def test_missing_backend_cannot_report_successful_disable() -> None:
    assert timed.disable_text_tracks(_unsupported_core()) is False


def test_missing_backend_cannot_report_successful_selection() -> None:
    assert timed.select_text_track(_unsupported_core(), 1) is False


def test_public_query_retains_empty_compatibility_but_logs_missing_capability(caplog) -> None:
    with caplog.at_level('DEBUG', logger=timed.__name__):
        assert timed.get_text_track_descriptors(_unsupported_core()) == ()
    assert 'unavailable or failed' in caplog.text
