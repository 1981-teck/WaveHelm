from __future__ import annotations

import pytest

import src.video.adapter_factory as factory


class FakeAdapter:
    def __init__(self):
        self.shutdown_calls = 0
        self.close_calls = 0
        self._closed = False

    def shutdown(self):
        self.shutdown_calls += 1
        self._closed = True

    def close(self):
        self.close_calls += 1
        self._closed = True


class CloseOnlyAdapter:
    def __init__(self):
        self.close_calls = 0
        self._closed = False

    def close(self):
        self.close_calls += 1
        self._closed = True


@pytest.fixture(autouse=True)
def reset_factory_state():
    with factory._adapter_lock:
        factory._state.instance = None
        factory._state.created = False
        factory._state.creating = False
        factory._state.creator_tid = None
        factory._state.last_error = None
        factory._state.creation_seq = 0
        factory._state.creation_callback = None
        factory._state.callback_running = False
        factory._state.callback_tid = None
    yield
    with factory._adapter_lock:
        factory._state.instance = None
        factory._state.created = False
        factory._state.creating = False
        factory._state.creator_tid = None
        factory._state.last_error = None
        factory._state.creation_callback = None
        factory._state.callback_running = False
        factory._state.callback_tid = None


def test_is_adapter_closed_best_effort_handles_known_states():
    class BrokenAdapter:
        def is_closed(self):
            raise RuntimeError('boom')

    assert factory._is_adapter_closed(None) is True
    assert factory._is_adapter_closed(type('Closed', (), {'_closed': True})()) is True
    assert factory._is_adapter_closed(type('Shutdown', (), {'_shutdown_requested': True})()) is True
    assert factory._is_adapter_closed(BrokenAdapter()) is False


def test_create_best_video_adapter_reuses_singleton_and_ignores_callback_failure(monkeypatch):
    monkeypatch.setattr(factory, 'create_imf_media_engine_adapter', lambda event_bus=None: FakeAdapter())

    with factory._adapter_lock:
        factory._state.creation_callback = lambda adapter: (_ for _ in ()).throw(RuntimeError('callback fail'))

    first = factory.create_best_video_adapter(event_bus=object())
    second = factory.create_best_video_adapter(event_bus=object())

    assert first is second
    status = factory.get_adapter_status()
    assert status['created'] is True
    assert status['callback_running'] is False


def test_create_best_video_adapter_records_last_error_on_failure(monkeypatch):
    monkeypatch.setattr(
        factory,
        'create_imf_media_engine_adapter',
        lambda event_bus=None: (_ for _ in ()).throw(factory.MediaEngineError('create fail')),
    )

    with pytest.raises(factory.MediaEngineError, match='create fail'):
        factory.create_best_video_adapter()

    assert 'create fail' in str(factory.get_adapter_status()['last_error'])


def test_shutdown_video_adapter_uses_shutdown_or_close():
    adapter = FakeAdapter()
    with factory._adapter_lock:
        factory._state.instance = adapter
        factory._state.created = True

    factory.shutdown_video_adapter()
    assert adapter.shutdown_calls == 1

    close_only = CloseOnlyAdapter()
    with factory._adapter_lock:
        factory._state.instance = close_only
        factory._state.created = True

    factory.shutdown_video_adapter()
    assert close_only.close_calls == 1


def test_reset_adapter_returns_false_when_recreate_fails(monkeypatch):
    monkeypatch.setattr(factory, 'shutdown_video_adapter', lambda: None)
    monkeypatch.setattr(
        factory,
        'create_best_video_adapter',
        lambda event_bus=None: (_ for _ in ()).throw(RuntimeError('boom')),
    )

    assert factory.reset_adapter() is False
