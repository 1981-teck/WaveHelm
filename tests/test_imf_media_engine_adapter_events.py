from __future__ import annotations

import logging
import queue

import src.video.imf_media_engine_adapter_events as adapter_events


class EmitOnlyBus:
    def __init__(self):
        self.calls = []

    def emit(self, event_name, payload):
        self.calls.append((event_name, payload))


class PublishBus:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = []

    def publish(self, event_name, payload, require_ui_thread=False):
        if self.fail:
            raise RuntimeError('publish fail')
        self.calls.append((event_name, payload, require_ui_thread))


class DummyAdapter:
    def __init__(self):
        self._event_queue = queue.Queue(maxsize=1)
        self._event_bus = None
        self._source = 'clip.mp4'


adapter_events.attach_imf_media_engine_adapter_events_behavior(DummyAdapter)


def test_on_media_engine_event_drops_when_queue_is_full(caplog):
    caplog.set_level(logging.DEBUG, logger=adapter_events.logger.name)
    adapter = DummyAdapter()
    adapter._event_queue.put_nowait(('occupied', 1, 2))

    adapter.on_media_engine_event(9, 8, 7)

    assert adapter._event_queue.get_nowait() == ('occupied', 1, 2)
    assert 'Event queue full, dropping event 9' in caplog.text


def test_publish_prefers_publish_and_ignores_runtime_errors():
    adapter = DummyAdapter()
    bus = PublishBus()
    adapter._event_bus = bus

    adapter._publish('VIDEO_READY', {'ok': True}, require_ui_thread=True)
    assert bus.calls == [('VIDEO_READY', {'ok': True}, True)]

    adapter._event_bus = PublishBus(fail=True)
    adapter._publish('VIDEO_READY', {'ok': False})


def test_publish_falls_back_to_emit_when_publish_is_missing():
    adapter = DummyAdapter()
    bus = EmitOnlyBus()
    adapter._event_bus = bus

    adapter._publish('VIDEO_READY', {'ok': True})

    assert bus.calls == [('VIDEO_READY', {'ok': True})]
