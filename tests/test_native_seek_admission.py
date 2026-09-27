"""Production facade/adapter/core/manager with owned COM-shaped callback tables.

No Windows DLL or actual media is used. Return codes, time and worker delivery are
controlled boundaries; retention and dispatch use the production implementations.
"""
from __future__ import annotations
import ctypes
from contextlib import contextmanager
from types import SimpleNamespace
import threading
import pytest

from src.video.component_base.definitions_abi import IMFMediaEngine, IUnknownVtbl
from src.video.component_adapter.com_thread_manager import ComThreadManager
from src.video.imf_media_engine_adapter import IMFMediaEngineAdapter
from src.video.media_engine_core import MediaEngineCore
from src.video import media_engine_seek as native
from src.video import seek_receipt as receipt
from src.video.seek_receipt import SeekPhase as P
from src.controller.video_controller_playback import attach_video_controller_playback_behavior
from src.controller.component_player.engine_controller import EngineController, SeekDispatch
from src.controller.component_player.playback_state_manager import PlaybackStateManager, PlayerState
from src.controller.player_controller import PlayerController
from src.model.media_file import MediaType
from tests.test_ui_seek_routing import make_widget, invoke_handler


class OwnedEngine:
    """Test owns all pointed-to memory; never free these buffers through Windows."""
    def __init__(self):
        self.references=[];self.callbacks=[];self.table=IUnknownVtbl()
        for method in ('AddRef','Release'):
            callback=dict(IUnknownVtbl._fields_)[method](
                lambda address, method=method:self.references.append(method) or 1)
            self.callbacks.append(callback);setattr(self.table,method,callback)
        self.engine=IMFMediaEngine(ctypes.pointer(self.table))
        self.pointer=ctypes.pointer(self.engine)


class Controller:
    pass


attach_video_controller_playback_behavior(Controller)


@pytest.fixture
def rig(monkeypatch):
    now=[100.0];monkeypatch.setattr(receipt.time,'monotonic',lambda:now[0])
    manager=ComThreadManager()
    manager._com_thread=SimpleNamespace(is_alive=lambda:True)
    manager._com_ready_event.set()
    monkeypatch.setattr(manager,'_is_on_com_thread',lambda:True)
    monkeypatch.setattr(manager,'_wake_com_thread',lambda:None)
    adapter=IMFMediaEngineAdapter.__new__(IMFMediaEngineAdapter)
    adapter._lock=threading.RLock();adapter._closed=adapter._shutdown_requested=False
    adapter._source='clip.mp4';adapter._com_thread_manager=manager
    core=MediaEngineCore(adapter);adapter._core=core
    owned=OwnedEngine();core._media_engine=owned.pointer
    core._source=core._active_source=core._requested_source='clip.mp4'
    core._engine_generation=1
    obj=SimpleNamespace(now=now,manager=manager,adapter=adapter,core=core,owned=owned,
                        hr=0,calls=[],after=lambda:None)
    def call(engine,name,*args):
        obj.calls.append((ctypes.cast(engine,ctypes.c_void_p).value,name,args[0].value))
        obj.after()
        if isinstance(obj.hr,Exception): raise obj.hr
        return obj.hr
    monkeypatch.setattr(core,'_call_engine_ptr_method',call)
    ctrl=Controller();ctrl._adapter=adapter;ctrl._shutting_down=False;obj.controller=ctrl
    state=PlaybackStateManager()
    track=SimpleNamespace(path='clip.mp4',media_type=MediaType.VIDEO,duration=10.0)
    state.set_context([track],0,track);state.update_state(PlayerState.PLAYING_VIDEO)
    engine=EngineController(state,None,ctrl)
    obj.player=PlayerController(state,SimpleNamespace(current_track=track,index=0),engine,None,None)
    yield obj
    core._shutdown_requested=True;adapter._closed=True
    manager._com_thread=None


def test_exact_queue_admission_then_hresult_evidence_not_completed_seek(rig):
    assert rig.player.seek(2.0) is SeekDispatch.FORWARDED_UNCONFIRMED
    result=rig.controller.get_seek_receipt()
    assert result.phase is P.QUEUED and result.queued and not result.native_call_started
    assert rig.calls==[] and rig.owned.references==[]
    rig.manager._drain_tasks_com_thread()
    result=rig.controller.get_seek_receipt()
    assert result.phase is P.NATIVE_ACCEPTED_UNCONFIRMED and result.hresult==0
    assert result.worker_replied and result.native_call_started
    assert rig.calls==[(ctypes.addressof(rig.owned.engine),'SetCurrentTime',2.0)]
    assert rig.owned.references==['AddRef','Release']


@pytest.mark.parametrize('hr',[0x80004005,-2147467259,1,True,None,'0',1<<33])
def test_eventual_hresult_failure_is_not_lost(rig,hr):
    rig.hr=hr
    assert rig.controller.seek(3.0) is True  # Queue admission only.
    rig.manager._drain_tasks_com_thread()
    value=rig.controller.get_seek_receipt()
    assert value.phase is P.FAILED and value.worker_replied
    assert rig.owned.references==['AddRef','Release']
    if type(hr) is int and -(1<<31)<=hr<=0xFFFFFFFF: assert value.hresult==hr&0xFFFFFFFF
    else: assert value.hresult is None


@pytest.mark.parametrize('error',[OSError('native failure'),RuntimeError('call failed'),ValueError('bad native input')])
def test_original_worker_exception_reaches_bounded_receipt_and_reference_is_released(rig,error):
    rig.hr=error
    assert rig.controller.seek(1.0)
    rig.manager._drain_tasks_com_thread()
    value=rig.core.get_seek_receipt()
    assert value.phase is P.FAILED and value.error_type==type(error).__name__
    assert rig.owned.references==['AddRef','Release']


@pytest.mark.parametrize('case',['worker-missing','worker-not-ready','worker-closing','adapter-closing'])
def test_facade_observes_refused_insertion(rig,case):
    if case=='worker-missing':rig.manager._com_thread=None
    if case=='worker-not-ready':rig.manager._com_ready_event.clear()
    if case=='worker-closing':rig.manager._shutdown_requested=True
    if case=='adapter-closing':rig.adapter._shutdown_requested=True
    assert rig.player.seek(1.0) is SeekDispatch.REJECTED
    assert rig.manager._task_queue.empty() and not rig.calls


@pytest.mark.parametrize('field,value',[('_engine_generation',2),('_source','next.mp4'),
    ('_active_source','next.mp4'),('_requested_source','next.mp4'),('_shutdown_requested',True),('_media_engine',None)])
def test_changed_engine_or_source_cancels_queued_seek_before_dereference(rig,field,value):
    assert rig.controller.seek(4.0)
    setattr(rig.core,field,value)
    rig.manager._drain_tasks_com_thread()
    assert rig.core.get_seek_receipt().phase is P.CANCELLED
    assert rig.calls==[] and rig.owned.references==[]


def test_reentrant_change_during_call_retains_and_releases_original_reference(rig):
    assert rig.controller.seek(1.0)
    rig.after=lambda:setattr(rig.core,'_media_engine',None)
    rig.manager._drain_tasks_com_thread()
    value=rig.core.get_seek_receipt()
    assert value.phase is P.CANCELLED and value.hresult==0 and value.native_call_started
    assert rig.owned.references==['AddRef','Release']


def test_second_pending_request_is_refused_without_growing_queue(rig):
    assert rig.controller.seek(1.0)
    first=rig.core.get_seek_receipt().request_id
    for _ in range(40): assert rig.controller.seek(2.0) is False
    assert rig.manager._task_queue.qsize()==1
    rig.manager._drain_tasks_com_thread()
    assert rig.controller.seek(3.0) is False
    operation = rig.core._seek_slot.current()
    assert operation.observe_event(16, 1, 0, 'clip.mp4')
    assert operation.observe_event(17, 1, 0, 'clip.mp4')
    assert rig.controller.seek(3.0)
    assert rig.core.get_seek_receipt().request_id==first+1


def test_expired_task_never_executes_and_keeps_slot_until_worker_reply(rig):
    assert rig.controller.seek(1.0)
    rig.now[0]=111.0
    assert rig.core.get_seek_receipt().phase is P.EXPIRED_UNCONFIRMED
    assert rig.controller.seek(2.0) is False
    rig.manager._drain_tasks_com_thread()
    assert not rig.calls and not rig.owned.references
    assert rig.core.get_seek_receipt().worker_replied
    assert rig.controller.seek(3.0)


def test_native_call_past_deadline_keeps_success_code_but_not_timely_acceptance(rig):
    rig.after=lambda:rig.now.__setitem__(0,111.0)
    assert rig.controller.seek(1.0)
    rig.manager._drain_tasks_com_thread()
    result=rig.core.get_seek_receipt()
    assert result.phase is P.EXPIRED_UNCONFIRMED and result.hresult==0
    assert result.native_call_started and rig.owned.references==['AddRef','Release']


def test_dispatcher_exception_is_uncertainty_not_fake_no_effect(rig,monkeypatch):
    original=rig.manager.submit_to_com_thread
    def exception_after_insertion(*args):
        original(*args)
        raise RuntimeError('lost admission response')
    monkeypatch.setattr(rig.manager,'submit_to_com_thread',exception_after_insertion)
    assert rig.controller.seek(1.0) is False
    assert rig.core.get_seek_receipt().phase is P.SUBMISSION_UNCERTAIN
    assert rig.controller.seek(2.0) is False
    rig.manager._drain_tasks_com_thread()
    assert not rig.calls and rig.core.get_seek_receipt().worker_replied


def test_shutdown_drains_to_receipt_without_dereferencing(rig):
    assert rig.controller.seek(1.0)
    rig.manager._reject_pending_tasks('shutdown')
    result=rig.core.get_seek_receipt()
    assert result.phase is P.FAILED and result.worker_replied
    assert not rig.calls and not rig.owned.references


@pytest.mark.parametrize('action',['stop','load_source','shutdown'])
def test_actual_transport_lifecycle_invalidates_same_source_intent(rig,monkeypatch,action):
    assert rig.controller.seek(1.0)
    # Lifecycle native work is not under test; execute its production admission/epoch path.
    monkeypatch.setattr(rig.adapter,'call_on_com_thread',lambda name,callback:None)
    # Explicitly refuse the new teardown boundary; this seek test runs no native cleanup.
    monkeypatch.setattr(rig.adapter,'call_shutdown_on_com_thread',
                        lambda name,callback,response:response.set_admission(False))
    if action=='load_source': rig.core.load_source('clip.mp4')
    else:getattr(rig.core,action)()
    rig.manager._drain_tasks_com_thread()
    assert rig.core.get_seek_receipt().phase is P.CANCELLED
    assert not rig.calls
    if action == 'shutdown':
        assert rig.core.get_shutdown_snapshot().execution == 'REJECTED'


def test_native_retention_failure_is_reported_not_accepted(rig,monkeypatch):
    @contextmanager
    def failed_retention(address):
        raise ValueError('AddRef contract unavailable')
        yield address
    monkeypatch.setattr(native,'_held_engine',failed_retention)
    assert rig.controller.seek(1.0)
    rig.manager._drain_tasks_com_thread()
    value=rig.core.get_seek_receipt()
    assert value.phase is P.FAILED and not value.native_call_started and not rig.calls


def test_release_failure_does_not_become_unqualified_success(rig,monkeypatch):
    @contextmanager
    def failed_release(address):
        yield rig.owned.pointer
        raise OSError('Release boundary failed')
    monkeypatch.setattr(native,'_held_engine',failed_release)
    assert rig.controller.seek(1.0)
    rig.manager._drain_tasks_com_thread()
    value=rig.core.get_seek_receipt()
    assert value.phase is P.FAILED and value.native_call_started and value.error_type=='OSError'


@pytest.mark.parametrize('surface',['mini','overlay'])
@pytest.mark.parametrize('gesture',['slider','pointer'])
def test_real_bar_to_facade_to_native_queue_propagates_refusal_and_later_failure(rig,monkeypatch,surface,gesture):
    widget=make_widget(monkeypatch,surface,rig.player)
    rig.manager._com_ready_event.clear()
    invoke_handler(widget,gesture)
    assert rig.manager._task_queue.empty() and rig.calls==[]
    rig.manager._com_ready_event.set();rig.hr=0x80004005
    invoke_handler(widget,gesture)
    assert rig.manager._task_queue.qsize()==1
    assert widget._display_reading.position is None  # Revision changed: no confirmed clock.
    assert widget._display_reading.seek_phase is P.QUEUED
    assert widget._current_position != 8.0  # Requested target is never measured data.
    rig.manager._drain_tasks_com_thread()
    assert rig.controller.get_seek_receipt().phase is P.FAILED
    widget.close()


def test_cached_receipt_read_does_not_invoke_com_or_advance_work(rig):
    assert rig.controller.seek(1.0)
    for _ in range(100): assert rig.controller.get_seek_receipt().phase is P.QUEUED
    assert rig.calls==[] and rig.manager._task_queue.qsize()==1


def test_fast_worker_completion_survives_admission_return(rig,monkeypatch):
    monkeypatch.setattr(rig.manager,'_wake_com_thread',lambda:rig.manager._drain_tasks_com_thread())
    assert rig.controller.seek(2.0)
    result=rig.core.get_seek_receipt()
    assert result.phase is P.NATIVE_ACCEPTED_UNCONFIRMED and result.queued and result.worker_replied


def test_retention_reentrancy_cancels_before_setcurrenttime(rig,monkeypatch):
    original=native._held_engine
    @contextmanager
    def reentrant(address):
        with original(address) as engine:
            rig.core._engine_generation += 1
            yield engine
    monkeypatch.setattr(native,'_held_engine',reentrant)
    assert rig.controller.seek(1.0)
    rig.manager._drain_tasks_com_thread()
    assert rig.core.get_seek_receipt().phase is P.CANCELLED
    assert not rig.calls and rig.owned.references==['AddRef','Release']


def test_retention_delay_past_deadline_never_calls_setcurrenttime(rig,monkeypatch):
    original=native._held_engine
    @contextmanager
    def delayed(address):
        with original(address) as engine:
            rig.now[0] += 11.0
            yield engine
    monkeypatch.setattr(native,'_held_engine',delayed)
    assert rig.controller.seek(1.0)
    rig.manager._drain_tasks_com_thread()
    assert rig.core.get_seek_receipt().phase is P.EXPIRED_UNCONFIRMED
    assert not rig.calls and rig.owned.references==['AddRef','Release']


def test_controller_receipt_never_queries_a_replaced_adapter(rig,monkeypatch):
    assert rig.controller.seek(1.0)
    original=rig.adapter.get_seek_receipt
    def changed():
        value=original();rig.controller._adapter=object();return value
    monkeypatch.setattr(rig.adapter,'get_seek_receipt',changed)
    with pytest.raises(RuntimeError, match="adapter changed"):
        rig.controller.get_seek_receipt()


def test_receipt_invalidates_after_same_source_engine_generation_change(rig):
    assert rig.controller.seek(1.0)
    rig.manager._drain_tasks_com_thread()
    rig.core._engine_generation += 1
    assert rig.core.get_seek_receipt().phase is P.CANCELLED


@pytest.mark.parametrize('value',[float('nan'),float('inf'),-float('inf'),True,None,'2',10**400],
                         ids=['nan','inf','negative-inf','bool','none','string','overflow'])
def test_direct_native_route_rejects_invalid_targets_before_queue(rig,value):
    assert rig.core.seek(value) is False
    assert rig.manager._task_queue.empty() and rig.core.get_seek_receipt() is None


def test_native_calls_run_outside_core_and_adapter_state_locks(rig,monkeypatch):
    from tests.test_video_clock_observation import CheckedLock
    rig.core._state_lock=CheckedLock();rig.adapter._lock=CheckedLock()
    original=rig.core._call_engine_ptr_method
    def checked(*args):
        assert not rig.core._state_lock.held and not rig.adapter._lock.held
        return original(*args)
    monkeypatch.setattr(rig.core,'_call_engine_ptr_method',checked)
    assert rig.controller.seek(1.0)
    rig.manager._drain_tasks_com_thread()
    assert rig.core.get_seek_receipt().phase is P.NATIVE_ACCEPTED_UNCONFIRMED


@pytest.mark.parametrize('action',['stop','load_source','shutdown'])
def test_missing_ownership_state_cannot_silently_skip_invalidation(rig,monkeypatch,action):
    rig.core._seek_slot=None
    calls=[]
    monkeypatch.setattr(rig.adapter,'call_on_com_thread',lambda *args:calls.append(args))
    monkeypatch.setattr(rig.adapter,'call_shutdown_on_com_thread',lambda *args:calls.append(args))
    with pytest.raises(TypeError,match='ownership slot'):
        if action=='load_source':rig.core.load_source('clip.mp4')
        else:getattr(rig.core,action)()
    assert calls==[]
