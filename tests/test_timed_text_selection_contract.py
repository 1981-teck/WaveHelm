"""Exercise production hook routing with test-owned native-shaped track objects."""
from __future__ import annotations

import ctypes as ct
from types import MethodType

import pytest

from test_timed_text_backend import NativeFixture
from src.video.component_base.definitions_abi import IMFTimedTextTrack, IMFTimedTextTrackVtbl, IMFTimedTextTrackList, IMFTimedTextTrackListVtbl
from src.video import media_engine_core_timed_text as timed
from src.video import media_engine_timed_text_backend as backend


class TrackWorld(NativeFixture):
    def __init__(self, monkeypatch):
        super().__init__(monkeypatch)
        self.ids = [0, 7]
        self.active = {7}
        self.nodes, self.tables, self.buffers = [], [], []
        self.freed = []
        self.fail_index = None
        self.null_index = None
        self.fail_id = None
        self.ignore_selection = False
        self.change_on_selection = False
        self.bad_length = None
        self.length_reads = 0
        self.length_drift = False
        for index in range(2):
            self._make_track(index)
        self.active_vt = IMFTimedTextTrackListVtbl()
        self.active_list = IMFTimedTextTrackList(ct.pointer(self.active_vt))
        self._bind(self.list_vt, 'GetLength', lambda p: self._length(False))
        self._bind(self.list_vt, 'GetTrack', lambda p, i, out: self._track(i, out, False))
        self._bind(self.active_vt, 'GetLength', lambda p: self._length(True))
        self._bind(self.active_vt, 'GetTrack', lambda p, i, out: self._track(i, out, True))
        self._bind(self.active_vt, 'Release', lambda p: self._release('active-list'))
        monkeypatch.setattr(timed, '_CoTaskMemFree', lambda p: self.freed.append(p.value))
        self.adapter.call_on_com_thread = lambda name, callback: callback()
        for name in ('_get_text_tracks_on_com_thread', '_get_active_timed_text_tracks_on_com_thread',
                     '_call_timed_text_method_on_com_thread'):
            setattr(self.core, name, MethodType(getattr(backend, name), self.core))

    def _make_track(self, index):
        vt = IMFTimedTextTrackVtbl()
        self._bind(vt, 'GetId', lambda p: self.ids[index])
        self._bind(vt, 'GetTrackKind', lambda p: 1)
        self._bind(vt, 'IsActive', lambda p: int(self.ids[index] in self.active))
        self._bind(vt, 'IsInBand', lambda p: 1)
        self._bind(vt, 'GetLabel', lambda p, out: self._string(f'Track {index}', out))
        self._bind(vt, 'GetLanguage', lambda p, out: self._string('it-IT', out))
        self._bind(vt, 'Release', lambda p: self._release(f'track-{index}'))
        self.tables.append(vt)
        self.nodes.append(IMFTimedTextTrack(ct.pointer(vt)))

    def _string(self, text, out):
        buffer = ct.create_unicode_buffer(text)
        self.buffers.append(buffer)
        ct.cast(out, ct.POINTER(ct.c_void_p))[0] = ct.addressof(buffer)
        return 0

    def _indices(self, active):
        return [i for i, track_id in enumerate(self.ids) if not active or track_id in self.active]

    def _length(self, active):
        self.length_reads += 1
        if self.bad_length is not None:
            return self.bad_length
        return len(self._indices(active)) + int(self.length_drift and self.length_reads > 1)

    def _track(self, index, out, active):
        if index == self.fail_index:
            return -2147467259
        if index != self.null_index:
            out[0] = ct.pointer(self.nodes[self._indices(active)[index]])
        return 0

    def _list(self, out, active):
        self.events.append(('list', active))
        if self.list_output:
            out[0] = ct.pointer(self.active_list if active else self.track_list)
        return self.list_result

    def _select(self, pointer, track_id, selected):
        self.events.append(('select', int(track_id), int(selected)))
        if track_id == self.fail_id:
            return -2147467259
        if not self.ignore_selection:
            if selected:
                self.active.add(track_id)
            else:
                self.active.discard(track_id)
        if self.change_on_selection:
            self.core._engine_generation += 1
        return 0


@pytest.fixture
def world(monkeypatch) -> TrackWorld:
    return TrackWorld(monkeypatch)


def test_full_public_query_uses_service_and_returns_pointer_free_metadata(world) -> None:
    result = timed.get_text_track_descriptors(world.core)
    assert [d['track_id'] for d in result] == [0, 7]
    assert result[0]['language'] == 'it-IT'
    assert result[0]['label'] == 'Track 0'
    assert result[1]['is_active'] is True
    assert len(world.freed) == 4
    assert world.events.count(('release', 'list')) == 1
    assert world.events.count(('release', 'track-0')) == 1
    assert world.events.count(('release', 'track-1')) == 1


def test_active_query_routes_get_active_tracks(world) -> None:
    assert timed.get_active_text_track_ids(world.core) == (7,)
    assert ('list', True) in world.events
    assert world.events[-1] == ('release', 'active-list')


def test_select_zero_then_disable_roundtrips_with_readback(world) -> None:
    assert timed.select_text_track(world.core, 0) is True
    assert world.active == {0}
    assert ('select', 0, 1) in world.events
    assert timed.disable_text_tracks(world.core) is True
    assert not world.active
    assert ('list', True) in world.events


@pytest.mark.parametrize('value', [True, False, -1, 2**32, '7', 7.5, None])
def test_invalid_ids_never_dispatch_or_deselect(world, value) -> None:
    assert timed.select_text_track(world.core, value) is False
    assert world.events == []
    assert world.active == {7}


def test_unknown_id_does_not_mutate_existing_selection(world) -> None:
    assert timed.select_text_track(world.core, 123) is False
    assert not any(e[0] == 'select' for e in world.events)
    assert world.active == {7}


@pytest.mark.parametrize('failure', ['failed-entry', 'null-entry', 'duplicate-id', 'length-drift', 'over-budget'])
def test_incomplete_enumeration_prevents_every_mutation(world, failure) -> None:
    if failure == 'failed-entry':
        world.fail_index = 1
    elif failure == 'null-entry':
        world.null_index = 1
    elif failure == 'duplicate-id':
        world.ids[1] = 0
    elif failure == 'length-drift':
        world.length_drift = True
    else:
        world.bad_length = 1025
    if failure == 'over-budget':
        with pytest.raises(timed.TimedTextMemoryError):
            timed.select_text_track(world.core, 0)
    else:
        assert timed.select_text_track(world.core, 0) is False
    assert not any(e[0] == 'select' for e in world.events)
    assert world.events[-1] == ('release', 'list')


def test_failed_first_selection_does_not_disable_the_old_track(world) -> None:
    world.fail_id = 0
    assert timed.select_text_track(world.core, 0) is False
    assert world.active == {7}
    assert [e for e in world.events if e[0] == 'select'] == [('select', 0, 1)]


def test_later_failure_is_not_reported_as_atomic_success(world) -> None:
    world.fail_id = 7
    assert timed.select_text_track(world.core, 0) is False
    assert world.active == {0, 7}  # Partial native state is explicit, not fake rollback.


def test_success_hresult_without_effect_fails_readback(world) -> None:
    world.ignore_selection = True
    assert timed.select_text_track(world.core, 0) is False
    assert world.active == {7}
    assert ('list', True) in world.events


def test_engine_change_stops_further_mutation(world) -> None:
    world.change_on_selection = True
    assert timed.select_text_track(world.core, 0) is False
    assert [e for e in world.events if e[0] == 'select'] == [('select', 0, 1)]


def test_unavailable_service_is_not_successful_disable(world) -> None:
    world.service_result, world.service_output = -2147467262, False
    assert timed.disable_text_tracks(world.core) is False
    assert not any(e[0] == 'select' for e in world.events)


def test_shutdown_after_dispatch_is_refused_without_native_access(world) -> None:
    world.adapter.call_on_com_thread = lambda name, fn: (setattr(world.core, '_shutdown_requested', True), fn())[1]
    assert timed.disable_text_tracks(world.core) is False
    assert world.events == []


def test_foreign_thread_public_selection_cannot_bypass_affinity(world) -> None:
    world.on_thread = False
    assert timed.select_text_track(world.core, 0) is False
    assert world.events == []


def test_failed_enumeration_releases_preceding_tracks_and_list(world) -> None:
    world.fail_index = 1
    with pytest.raises(backend.TimedTextBackendError, match='GetTrack'):
        timed._get_text_track_descriptors_on_com_thread(world.core)
    assert world.events.count(('release', 'track-0')) == 1
    assert world.events.count(('release', 'track-1')) == 0
    assert world.events[-1] == ('release', 'list')
