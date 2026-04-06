from __future__ import annotations

import threading

import pytest

import src.video.imf_media_engine_adapter_threading as adapter_threading


class DummyManager:
    def __init__(self, *, start_exc=None, post_exc=None, call_result=42):
        self.start_exc = start_exc
        self.post_exc = post_exc
        self.call_result = call_result
        self.calls = []

    def start(self):
        self.calls.append('start')
        if self.start_exc is not None:
            raise self.start_exc

    def post_to_com_thread(self, name, func):
        self.calls.append(('post', name))
        if self.post_exc is not None:
            raise self.post_exc

    def call_on_com_thread(self, name, func):
        self.calls.append(('call', name))
        return self.call_result


class DummyAdapter:
    def __init__(self, manager=None):
        self._lock = threading.RLock()
        self._com_thread_manager = manager
        self._shutdown_requested = False
        self._closed = False


adapter_threading.attach_imf_media_engine_adapter_threading_behavior(DummyAdapter)


def test_get_com_thread_manager_starts_existing_manager():
    manager = DummyManager()
    adapter = DummyAdapter(manager)

    result = adapter._get_com_thread_manager()

    assert result is manager
    assert manager.calls == ['start']


def test_post_to_com_thread_is_best_effort_for_runtime_failures():
    adapter = DummyAdapter()
    adapter._get_com_thread_manager = lambda ensure_started=False: (_ for _ in ()).throw(RuntimeError('boom'))

    adapter.post_to_com_thread('play', lambda: None)


def test_post_to_com_thread_surfaces_programming_errors():
    class BadManager:
        pass

    adapter = DummyAdapter()
    adapter._get_com_thread_manager = lambda ensure_started=False: BadManager()

    with pytest.raises(AttributeError):
        adapter.post_to_com_thread('play', lambda: None)


def test_call_on_com_thread_returns_manager_result():
    manager = DummyManager(call_result='done')
    adapter = DummyAdapter(manager)

    assert adapter.call_on_com_thread('seek', lambda: None) == 'done'
