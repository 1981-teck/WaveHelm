"""Real handler/operation paths with test-owned COM tables; no native DLL execution."""
from __future__ import annotations
import gc
from types import SimpleNamespace
import weakref
import pytest

from tests.test_native_seek_admission import rig
from src.video.media_engine_seek_events import BoundSeekEvents, create_bound_notify
from src.video.seek_receipt import SeekPhase as P
from src.video.media_engine_events import _MediaEngineNotifyCOM


def bridge(rig):
    rig.events = []
    rig.adapter.on_media_engine_event = lambda *args: rig.events.append(args)
    return BoundSeekEvents(rig.core, rig.adapter, 1)


def test_real_command_needs_ordered_callback_pair_and_worker_reply(rig):
    bound = bridge(rig)
    assert rig.controller.seek(2.0)
    rig.manager._drain_tasks_com_thread()
    assert not rig.controller.seek(3.0)
    bound.on_media_engine_event(17, 0, 0)
    assert rig.core.get_seek_receipt().phase is P.NATIVE_ACCEPTED_UNCONFIRMED
    bound.on_media_engine_event(16, 0, 0)
    bound.on_media_engine_event(17, 0, 0)
    assert rig.core.get_seek_receipt().phase is P.NATIVE_COMPLETED
    assert rig.controller.seek(3.0)
    assert rig.events == [(17, 0, 0), (16, 0, 0), (17, 0, 0)]


@pytest.mark.parametrize('change', ['core', 'generation', 'source', 'closed', 'shutdown', 'cancel'])
def test_delayed_notify_cannot_complete_replaced_or_cancelled_intent(rig, change):
    bound = bridge(rig)
    assert rig.controller.seek(2.0)
    rig.manager._drain_tasks_com_thread()
    operation = rig.core._seek_slot.current()
    if change == 'core': rig.adapter._core = None
    if change == 'generation': rig.core._engine_generation = 2
    if change == 'source': rig.core._requested_source = 'next.mp4'
    if change == 'closed': rig.adapter._closed = True
    if change == 'shutdown': rig.core._shutdown_requested = True
    if change == 'cancel': rig.core._seek_slot.invalidate('stop')
    bound.on_media_engine_event(16, 0, 0)
    bound.on_media_engine_event(17, 0, 0)
    assert operation.snapshot().completed_at is None


def test_callback_before_command_execution_is_not_retained(rig):
    bound = bridge(rig)
    assert rig.controller.seek(2.0)
    bound.on_media_engine_event(16, 0, 0)
    bound.on_media_engine_event(17, 0, 0)
    rig.manager._drain_tasks_com_thread()
    assert rig.core.get_seek_receipt().phase is P.NATIVE_ACCEPTED_UNCONFIRMED


def test_reentrant_callbacks_during_set_current_time_complete_only_on_worker_reply(rig):
    bound = bridge(rig)
    observed = []
    def during():
        bound.on_media_engine_event(16, 0, 0)
        bound.on_media_engine_event(17, 0, 0)
        observed.append(rig.core.get_seek_receipt().phase)
    rig.after = during
    assert rig.controller.seek(2.0)
    rig.manager._drain_tasks_com_thread()
    assert observed == [P.RUNNING]
    assert rig.core.get_seek_receipt().phase is P.NATIVE_COMPLETED
    assert rig.owned.references == ['AddRef', 'Release']


def test_callback_does_not_query_native_clock_and_forwards_non_seek_events(rig):
    bound = bridge(rig)
    assert rig.controller.seek(2.0)
    rig.manager._drain_tasks_com_thread()
    rig.core._call_engine_ptr_method = lambda *args: pytest.fail('callback queried COM')
    for event in (16, 17, 5, 18, 19):
        bound.on_media_engine_event(event, 7, 9)
    assert [entry[0] for entry in rig.events] == [16, 17, 5, 18, 19]
    assert rig.core.get_seek_receipt().phase is P.NATIVE_COMPLETED


def test_factory_keeps_weak_callback_owner_alive(rig):
    bridge(rig)
    rig.core._engine_generation = 0
    handler = create_bound_notify(rig.core, rig.adapter,
                                  lambda owner: SimpleNamespace(reference=weakref.ref(owner)))
    gc.collect()
    assert handler.reference() is handler._wavehelm_seek_event_owner
    rig.core._engine_generation = 1
    handler.reference().on_media_engine_event(5, 0, 0)
    assert rig.events == [(5, 0, 0)]


def test_real_notify_boundary_routes_through_bound_owner(rig):
    bridge(rig)
    rig.core._engine_generation = 0
    handler = create_bound_notify(rig.core, rig.adapter, _MediaEngineNotifyCOM)
    rig.core._engine_generation = 1
    assert rig.controller.seek(2.0)
    rig.manager._drain_tasks_com_thread()
    # The native callback representation differs between optional comtypes and
    # the portable ctypes definition; this calls its actual Python ABI boundary.
    for event in (16, 17):
        assert handler.EventNotify(event, 0, 0) == 0
    assert rig.core.get_seek_receipt().phase is P.NATIVE_COMPLETED


def test_factory_failure_never_falls_back_to_unbound_notify(rig):
    with pytest.raises(RuntimeError, match='no handler'):
        create_bound_notify(rig.core, rig.adapter, lambda owner: None)


def test_released_engine_notify_does_not_forward_old_error(rig):
    bound = bridge(rig)
    rig.core._media_engine = None
    bound.on_media_engine_event(5, 0, 0)
    assert rig.events == []


def test_initial_creation_may_forward_events_but_not_confirm_seek(rig):
    rig.events = []
    rig.adapter.on_media_engine_event = lambda *args: rig.events.append(args)
    rig.core._engine_generation = 0; rig.core._media_engine = None
    bound = BoundSeekEvents(rig.core, rig.adapter, 1)
    bound.on_media_engine_event(5, 7, 9)
    assert rig.events == [(5, 7, 9)]
    assert rig.core.get_seek_receipt() is None
