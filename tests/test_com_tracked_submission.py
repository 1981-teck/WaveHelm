"""Actual COM manager queue/drain; only the worker/Win32 boundary is test-owned."""
from __future__ import annotations
import queue
from types import SimpleNamespace
import pytest
import src.video.component_adapter.com_thread_manager as home
from src.video.imf_media_engine_adapter import IMFMediaEngineAdapter


def manager(monkeypatch):
    mgr = home.ComThreadManager()
    mgr._com_thread = SimpleNamespace(is_alive=lambda:True)
    mgr._com_ready_event.set()
    monkeypatch.setattr(mgr, '_wake_com_thread', lambda: None)
    monkeypatch.setattr(mgr, '_is_on_com_thread', lambda: True)
    return mgr


def test_admission_is_not_execution_and_worker_reports_exact_result(monkeypatch) -> None:
    mgr = manager(monkeypatch)
    results = queue.Queue(maxsize=1)
    calls = []
    assert mgr.submit_to_com_thread('seek', lambda:calls.append('native') or 42, results) is True
    assert calls == [] and results.empty()
    mgr._drain_tasks_com_thread()
    assert calls == ['native'] and results.get_nowait() == 42


@pytest.mark.parametrize('case', ['missing','dead','not-ready','stop','shutdown','full'])
def test_unavailable_manager_refuses_insertion(monkeypatch, case: str) -> None:
    mgr = manager(monkeypatch)
    if case == 'missing': mgr._com_thread = None
    if case == 'dead': mgr._com_thread.is_alive = lambda:False
    if case == 'not-ready': mgr._com_ready_event.clear()
    if case == 'stop': mgr._stop_event.set()
    if case == 'shutdown': mgr._shutdown_requested = True
    if case == 'full':
        mgr._task_queue = queue.Queue(maxsize=1)
        mgr._task_queue.put('existing')
    before = mgr._task_queue.qsize()
    assert mgr.submit_to_com_thread('seek', lambda:0, queue.Queue(1)) is False
    assert mgr._task_queue.qsize() == before


@pytest.mark.parametrize('name,callback,response', [
    ('',lambda:0,queue.Queue()), ('x'*65,lambda:0,queue.Queue()),
    (3,lambda:0,queue.Queue()), ('seek',None,queue.Queue()), ('seek',lambda:0,object()),
])
def test_invalid_dispatch_contract_is_refused(monkeypatch, name, callback, response) -> None:
    mgr = manager(monkeypatch)
    assert mgr.submit_to_com_thread(name, callback, response) is False
    assert mgr._task_queue.empty()


@pytest.mark.parametrize('case', ['stop','shutdown','replacement','wrong-thread'])
def test_queued_work_loses_ownership_before_native_call(monkeypatch, case: str) -> None:
    mgr = manager(monkeypatch)
    response = queue.Queue(1)
    calls=[]
    assert mgr.submit_to_com_thread('seek', lambda:calls.append('bad'), response)
    if case == 'stop': mgr._stop_event.set()
    if case == 'shutdown': mgr._shutdown_requested = True
    if case == 'replacement': mgr._com_thread = SimpleNamespace(is_alive=lambda:True)
    if case == 'wrong-thread': monkeypatch.setattr(mgr,'_is_on_com_thread',lambda:False)
    mgr._drain_tasks_com_thread()
    result=response.get_nowait()
    assert isinstance(result, home.MediaEngineError) and not calls


def test_real_pending_rejection_replies_without_execution(monkeypatch) -> None:
    mgr=manager(monkeypatch)
    response=queue.Queue(1)
    mgr.submit_to_com_thread('seek', lambda:pytest.fail('not expected'), response)
    mgr._reject_pending_tasks('shutdown')
    assert isinstance(response.get_nowait(),home.MediaEngineError)


def test_wake_failure_after_insertion_does_not_falsely_report_rejection(monkeypatch) -> None:
    mgr=manager(monkeypatch)
    response=queue.Queue(1)
    monkeypatch.setattr(mgr,'_wake_com_thread', lambda:(_ for _ in ()).throw(OSError('wake')))
    assert mgr.submit_to_com_thread('seek', lambda:1,response) is True
    mgr._drain_tasks_com_thread()
    assert response.get_nowait()==1


def test_worker_exception_is_forwarded_without_disguised_success(monkeypatch) -> None:
    mgr=manager(monkeypatch)
    response=queue.Queue(1)
    error=ValueError('actual failure')
    mgr.submit_to_com_thread('seek',lambda:(_ for _ in ()).throw(error),response)
    mgr._drain_tasks_com_thread()
    assert response.get_nowait() is error


@pytest.mark.parametrize('field,value', [('_closed',True),('_shutdown_requested',True),
    ('_core',object()),('_source','next'),('_com_thread_manager',None)])
def test_actual_adapter_checks_identity_at_worker_execution(monkeypatch,field,value) -> None:
    mgr=manager(monkeypatch)
    adapter=IMFMediaEngineAdapter.__new__(IMFMediaEngineAdapter)
    adapter._closed=adapter._shutdown_requested=False
    adapter._core=object();adapter._source='clip';adapter._com_thread_manager=mgr
    response=queue.Queue(1);calls=[]
    assert adapter.submit_to_com_thread('seek', lambda:calls.append('bad'),response)
    setattr(adapter,field,value)
    mgr._drain_tasks_com_thread()
    assert isinstance(response.get_nowait(),RuntimeError) and calls==[]


def test_existing_worker_is_required_no_start_or_fire_and_forget_fallback() -> None:
    adapter=IMFMediaEngineAdapter.__new__(IMFMediaEngineAdapter)
    adapter._closed=adapter._shutdown_requested=False
    adapter._core=object();adapter._source='clip';adapter._com_thread_manager=None
    assert adapter.submit_to_com_thread('seek',lambda:0,queue.Queue(1)) is False


def test_extracted_start_uses_public_owner_thread_factory(monkeypatch) -> None:
    mgr=home.ComThreadManager()
    events=[]
    class ThreadBoundary:
        def __init__(self,**kwargs): events.append(kwargs['target'])
        def start(self):
            mgr._com_thread_id=123
            mgr._com_thread_id_event.set();mgr._com_ready_event.set()
        def is_alive(self):return True
    monkeypatch.setattr(home.threading,'Thread',ThreadBoundary)
    mgr.start()
    assert events==[mgr._com_thread_main] and mgr._com_thread_id==123


def test_queue_reply_can_arrive_before_submission_returns(monkeypatch) -> None:
    mgr=manager(monkeypatch);response=queue.Queue(1)
    monkeypatch.setattr(mgr,'_wake_com_thread',lambda:mgr._drain_tasks_com_thread())
    assert mgr.submit_to_com_thread('seek',lambda:23,response) is True
    assert response.get_nowait()==23
