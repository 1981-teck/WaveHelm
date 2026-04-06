from __future__ import annotations

import logging
import queue

import pytest

import src.video.component_adapter.com_thread_manager as com_thread_manager



def test_is_on_com_thread_handles_thread_id_lookup_failure(monkeypatch):
    manager = com_thread_manager.ComThreadManager()
    manager._com_thread_id = 123

    monkeypatch.setattr(com_thread_manager, '_GetCurrentThreadId', lambda: (_ for _ in ()).throw(ValueError('boom')))
    assert manager._is_on_com_thread() is False



def test_wake_com_thread_is_best_effort_when_setevent_fails(monkeypatch):
    manager = com_thread_manager.ComThreadManager()
    manager._com_thread_wakeup = object()

    monkeypatch.setattr(com_thread_manager, '_SetEvent', lambda handle: (_ for _ in ()).throw(RuntimeError('fail')))
    manager._wake_com_thread()



def test_reject_pending_tasks_handles_full_response_queue(caplog):
    caplog.set_level(logging.DEBUG, logger=com_thread_manager.logger.name)
    manager = com_thread_manager.ComThreadManager()
    full_q = queue.Queue(maxsize=1)
    full_q.put_nowait('occupied')
    manager._task_queue.put(com_thread_manager._ComTask('t1', lambda: None, (), {}, full_q))

    manager._reject_pending_tasks('shutdown requested')
    assert manager._task_queue.empty()
    assert full_q.get_nowait() == 'occupied'
    assert "Response queue full while rejecting task 't1'" in caplog.text



def test_drain_tasks_com_thread_returns_results_and_exceptions():
    manager = com_thread_manager.ComThreadManager(thread_name='TestCOM')
    ok_q = queue.Queue(maxsize=1)
    err_q = queue.Queue(maxsize=1)

    manager._task_queue.put(com_thread_manager._ComTask('ok', lambda: 42, (), {}, ok_q))

    def failing_task():
        raise ValueError('bad task')

    manager._task_queue.put(com_thread_manager._ComTask('bad', failing_task, (), {}, err_q))
    manager._drain_tasks_com_thread()

    assert ok_q.get_nowait() == 42
    err = err_q.get_nowait()
    assert isinstance(err, ValueError)
    assert str(err) == 'bad task'



def test_drain_tasks_com_thread_re_raises_interrupts():
    manager = com_thread_manager.ComThreadManager(thread_name='TestCOM')
    q = queue.Queue(maxsize=1)

    def interrupting_task():
        raise KeyboardInterrupt()

    manager._task_queue.put(com_thread_manager._ComTask('interrupt', interrupting_task, (), {}, q))
    with pytest.raises(KeyboardInterrupt):
        manager._drain_tasks_com_thread()
