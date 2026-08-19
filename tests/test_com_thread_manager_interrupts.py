from __future__ import annotations

import queue

import pytest

import src.video.component_adapter.com_thread_manager as com_thread_manager


def test_com_thread_main_sets_ready_event_and_wraps_interrupt(monkeypatch):
    manager = com_thread_manager.ComThreadManager(thread_name='TestCOM')

    monkeypatch.setattr(com_thread_manager, '_GetCurrentThreadId', lambda: 77)
    monkeypatch.setattr(com_thread_manager._ole32, 'CoInitializeEx', lambda *args: 0, raising=False)
    monkeypatch.setattr(com_thread_manager, '_hr_ok', lambda hr: True)
    monkeypatch.setattr(com_thread_manager._ole32, 'CoUninitialize', lambda: None, raising=False)
    monkeypatch.setattr(com_thread_manager, 'MFStartup', lambda flags: None)
    monkeypatch.setattr(com_thread_manager, 'MFShutdown', lambda: None)
    monkeypatch.setattr(com_thread_manager, '_CreateEventW', lambda *args: 1)
    monkeypatch.setattr(com_thread_manager, '_CloseHandle', lambda handle: True)
    monkeypatch.setattr(
        manager,
        '_com_thread_loop',
        lambda: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    with pytest.raises(KeyboardInterrupt):
        manager._com_thread_main()

    assert manager._com_ready_event.is_set()
    assert isinstance(manager._com_init_error, RuntimeError)
    assert 'interrotta' in str(manager._com_init_error)


def test_drain_tasks_com_thread_tolerates_full_response_queue_on_success():
    manager = com_thread_manager.ComThreadManager(thread_name='TestCOM')
    response_q = queue.Queue(maxsize=1)
    response_q.put_nowait('occupied')

    manager._task_queue.put(com_thread_manager._ComTask('ok', lambda: 42, (), {}, response_q))
    manager._drain_tasks_com_thread()

    assert response_q.get_nowait() == 'occupied'
