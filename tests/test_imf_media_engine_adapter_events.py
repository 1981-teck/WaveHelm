from __future__ import annotations

import logging
import queue
import threading

import pytest

import src.video.imf_media_engine_adapter_events as adapter_events
from src.video.imf_media_engine_adapter import IMFMediaEngineAdapter
from src.video.mf_base import (
    MF_MEDIA_ENGINE_EVENT_CANPLAY,
    MF_MEDIA_ENGINE_EVENT_LOADSTART,
    MF_MEDIA_ENGINE_EVENT_PAUSE,
    MF_MEDIA_ENGINE_EVENT_PLAYING,
    MF_MEDIA_ENGINE_EVENT_PURGEQUEUEDEVENTS,
)


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
        self._lock = threading.RLock()
        self._source_epoch = 0


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


class ReadyCore:
    def __init__(self, *, fail_play: bool = False, fail_detach: bool = False):
        self._media_engine = object()
        self.load_calls: list[str] = []
        self.detach_calls = 0
        self.play_calls = 0
        self.pause_switch_calls = 0
        self.fail_play = fail_play
        self.fail_detach = fail_detach

    def load_source(self, source: str | None) -> None:
        if source is None:
            self.detach_calls += 1
            if self.fail_detach:
                raise RuntimeError('detach failed')
            return
        self.load_calls.append(source)

    def play(self) -> None:
        self.play_calls += 1
        if self.fail_play:
            raise RuntimeError('play failed')

    def pause_for_source_switch(self) -> None:
        self.pause_switch_calls += 1


def _ready_adapter(
    *, fail_play: bool = False, fail_detach: bool = False
) -> tuple[IMFMediaEngineAdapter, ReadyCore]:
    adapter = IMFMediaEngineAdapter()
    core = ReadyCore(fail_play=fail_play, fail_detach=fail_detach)
    adapter._core = core
    return adapter, core


def test_play_waits_for_matching_loadstart_and_canplay():
    """CANPLAY is accepted only after LOADSTART for the active source epoch."""
    adapter, core = _ready_adapter()
    adapter.load_source('first.mp4')
    adapter.play()
    assert core.play_calls == 0

    adapter.on_media_engine_event(MF_MEDIA_ENGINE_EVENT_CANPLAY, 0, 0)
    adapter.pump_events()
    assert core.play_calls == 0

    adapter.on_media_engine_event(MF_MEDIA_ENGINE_EVENT_LOADSTART, 0, 0)
    adapter.on_media_engine_event(MF_MEDIA_ENGINE_EVENT_CANPLAY, 0, 0)
    adapter.pump_events()
    assert core.play_calls == 1


def test_stale_readiness_events_cannot_release_new_source_play():
    """Queued events from a replaced source cannot authorize the current source."""
    adapter, core = _ready_adapter()
    adapter.load_source('first.mp4')
    adapter.play()
    adapter.on_media_engine_event(MF_MEDIA_ENGINE_EVENT_LOADSTART, 0, 0)
    adapter.on_media_engine_event(MF_MEDIA_ENGINE_EVENT_CANPLAY, 0, 0)

    adapter.load_source('second.mp4')
    adapter.play()
    adapter.pump_events()
    assert core.play_calls == 0

    adapter.on_media_engine_event(MF_MEDIA_ENGINE_EVENT_PURGEQUEUEDEVENTS, 0, 0)
    adapter.pump_events()
    assert core.detach_calls == 1
    assert core.load_calls == ['first.mp4', 'second.mp4']

    adapter.on_media_engine_event(MF_MEDIA_ENGINE_EVENT_LOADSTART, 0, 0)
    adapter.on_media_engine_event(MF_MEDIA_ENGINE_EVENT_CANPLAY, 0, 0)
    adapter.pump_events()
    assert core.play_calls == 1


def test_deferred_play_failure_is_published_fail_closed(caplog):
    """A native Play failure after CANPLAY is visible and does not retry silently."""
    caplog.set_level(logging.ERROR, logger=adapter_events.logger.name)
    adapter, core = _ready_adapter(fail_play=True)
    bus = PublishBus()
    adapter._event_bus = bus
    adapter.load_source('broken.mp4')
    adapter.play()
    adapter.on_media_engine_event(MF_MEDIA_ENGINE_EVENT_LOADSTART, 0, 0)
    adapter.on_media_engine_event(MF_MEDIA_ENGINE_EVENT_CANPLAY, 0, 0)
    adapter.pump_events()

    assert core.play_calls == 1
    assert 'Deferred Play failed after CANPLAY' in caplog.text
    assert len(bus.calls) == 2
    assert bus.calls[0][0] == 'VIDEO_ERROR'

def _start_playing_source(adapter: IMFMediaEngineAdapter, core: ReadyCore, path: str) -> None:
    adapter.load_source(path)
    adapter.play()
    adapter.on_media_engine_event(MF_MEDIA_ENGINE_EVENT_LOADSTART, 0, 0)
    adapter.on_media_engine_event(MF_MEDIA_ENGINE_EVENT_CANPLAY, 0, 0)
    adapter.on_media_engine_event(MF_MEDIA_ENGINE_EVENT_PLAYING, 0, 0)
    adapter.pump_events()
    assert core.play_calls >= 1


def test_active_source_replacement_waits_for_pause_before_setsource():
    """An active decoder is quiesced before the next SetSource is admitted."""
    adapter, core = _ready_adapter()
    _start_playing_source(adapter, core, 'first.mp4')

    adapter.load_source('second.mp4')
    adapter.play()

    assert core.pause_switch_calls == 1
    assert core.load_calls == ['first.mp4']
    assert adapter._source == 'first.mp4'
    assert adapter._source_epoch == 1
    assert adapter._pending_source_epoch == 2
    assert adapter._pending_source_path == 'second.mp4'

    # Readiness arriving before PAUSE belongs to the retiring source and is ignored.
    adapter.on_media_engine_event(MF_MEDIA_ENGINE_EVENT_LOADSTART, 0, 0)
    adapter.on_media_engine_event(MF_MEDIA_ENGINE_EVENT_CANPLAY, 0, 0)
    adapter.pump_events()
    assert core.load_calls == ['first.mp4']
    assert core.play_calls == 1

    adapter.on_media_engine_event(MF_MEDIA_ENGINE_EVENT_PAUSE, 0, 0)
    adapter.pump_events()
    assert core.detach_calls == 1
    assert core.load_calls == ['first.mp4']
    assert adapter._source == 'first.mp4'
    assert adapter._source_epoch == 1

    adapter.on_media_engine_event(MF_MEDIA_ENGINE_EVENT_PURGEQUEUEDEVENTS, 0, 0)
    adapter.pump_events()
    assert core.load_calls == ['first.mp4', 'second.mp4']
    assert adapter._source == 'second.mp4'
    assert adapter._source_epoch == 2

    adapter.on_media_engine_event(MF_MEDIA_ENGINE_EVENT_LOADSTART, 0, 0)
    adapter.on_media_engine_event(MF_MEDIA_ENGINE_EVENT_CANPLAY, 0, 0)
    adapter.pump_events()
    assert core.play_calls == 2


def test_already_paused_source_replacement_does_not_wait_for_second_pause():
    """A source already confirmed paused can be replaced directly without a dead wait."""
    adapter, core = _ready_adapter()
    _start_playing_source(adapter, core, 'first.mp4')
    adapter.on_media_engine_event(MF_MEDIA_ENGINE_EVENT_PAUSE, 0, 0)
    adapter.pump_events()

    adapter.load_source('second.mp4')

    assert core.pause_switch_calls == 0
    assert core.detach_calls == 1
    assert core.load_calls == ['first.mp4']
    assert adapter._pending_source_epoch == 2
    adapter.on_media_engine_event(MF_MEDIA_ENGINE_EVENT_PURGEQUEUEDEVENTS, 0, 0)
    adapter.pump_events()
    assert core.load_calls == ['first.mp4', 'second.mp4']
    assert adapter._pending_source_epoch is None



def test_duplicate_pause_event_releases_deferred_source_once():
    """Duplicate PAUSE notifications cannot issue SetSource twice."""
    adapter, core = _ready_adapter()
    _start_playing_source(adapter, core, 'first.mp4')
    adapter.load_source('second.mp4')
    adapter.play()

    adapter.on_media_engine_event(MF_MEDIA_ENGINE_EVENT_PAUSE, 0, 0)
    adapter.on_media_engine_event(MF_MEDIA_ENGINE_EVENT_PAUSE, 0, 0)
    adapter.pump_events()

    assert core.detach_calls == 1
    assert core.load_calls == ['first.mp4']
    adapter.on_media_engine_event(MF_MEDIA_ENGINE_EVENT_PURGEQUEUEDEVENTS, 0, 0)
    adapter.pump_events()
    assert core.load_calls == ['first.mp4', 'second.mp4']
    assert adapter._pending_source_epoch is None
    assert adapter._pending_source_path is None


def test_pause_admission_failure_clears_pending_source_fail_closed():
    """A failed native Pause cannot leave a replacement armed implicitly."""
    adapter, core = _ready_adapter()
    _start_playing_source(adapter, core, 'first.mp4')

    def fail_pause() -> None:
        raise RuntimeError('pause admission failed')

    core.pause_for_source_switch = fail_pause
    with pytest.raises(RuntimeError, match='pause admission failed'):
        adapter.load_source('second.mp4')

    assert core.load_calls == ['first.mp4']
    assert adapter._source == 'first.mp4'
    assert adapter._source_epoch == 1
    assert adapter._pending_source_epoch is None
    assert adapter._pending_source_path is None


def test_rapid_pending_replacement_commits_only_latest_source():
    """A second request before PAUSE supersedes the uncommitted path without another Pause."""
    adapter, core = _ready_adapter()
    _start_playing_source(adapter, core, 'first.mp4')

    adapter.load_source('second.mp4')
    adapter.play()
    adapter.load_source('third.mp4')
    adapter.play()

    assert core.pause_switch_calls == 1
    assert core.load_calls == ['first.mp4']
    assert adapter._source == 'first.mp4'
    assert adapter._source_epoch == 1
    assert adapter._pending_source_epoch == 2
    assert adapter._pending_source_path == 'third.mp4'

    adapter.on_media_engine_event(MF_MEDIA_ENGINE_EVENT_PAUSE, 0, 0)
    adapter.pump_events()
    assert core.detach_calls == 1
    assert core.load_calls == ['first.mp4']
    adapter.on_media_engine_event(MF_MEDIA_ENGINE_EVENT_PURGEQUEUEDEVENTS, 0, 0)
    adapter.pump_events()
    assert core.load_calls == ['first.mp4', 'third.mp4']
    assert adapter._source == 'third.mp4'
    assert adapter._source_epoch == 2

    adapter.on_media_engine_event(MF_MEDIA_ENGINE_EVENT_LOADSTART, 0, 0)
    adapter.on_media_engine_event(MF_MEDIA_ENGINE_EVENT_CANPLAY, 0, 0)
    adapter.on_media_engine_event(MF_MEDIA_ENGINE_EVENT_PLAYING, 0, 0)
    adapter.pump_events()
    assert core.play_calls == 2


def test_stale_duplicate_pause_cannot_clear_new_playing_epoch():
    """A duplicate PAUSE tagged with the retiring epoch cannot pause the committed source."""
    adapter, core = _ready_adapter()
    _start_playing_source(adapter, core, 'first.mp4')
    adapter.load_source('second.mp4')
    adapter.play()
    adapter.on_media_engine_event(MF_MEDIA_ENGINE_EVENT_PAUSE, 0, 0)
    adapter.on_media_engine_event(MF_MEDIA_ENGINE_EVENT_PAUSE, 0, 0)
    adapter.pump_events()
    assert core.detach_calls == 1
    adapter.on_media_engine_event(MF_MEDIA_ENGINE_EVENT_PURGEQUEUEDEVENTS, 0, 0)
    adapter.pump_events()

    adapter.on_media_engine_event(MF_MEDIA_ENGINE_EVENT_LOADSTART, 0, 0)
    adapter.on_media_engine_event(MF_MEDIA_ENGINE_EVENT_CANPLAY, 0, 0)
    adapter.on_media_engine_event(MF_MEDIA_ENGINE_EVENT_PLAYING, 0, 0)
    adapter.pump_events()

    assert adapter._source_epoch == 2
    assert adapter._playing_epoch == 2
    assert core.load_calls == ['first.mp4', 'second.mp4']

def test_stale_purge_cannot_release_source_before_detach_starts():
    """PURGE from an earlier operation cannot commit a pending replacement."""
    adapter, core = _ready_adapter()
    _start_playing_source(adapter, core, 'first.mp4')
    adapter.load_source('second.mp4')
    adapter.play()

    adapter.on_media_engine_event(MF_MEDIA_ENGINE_EVENT_PURGEQUEUEDEVENTS, 0, 0)
    adapter.pump_events()
    assert core.detach_calls == 0
    assert core.load_calls == ['first.mp4']
    assert adapter._source_epoch == 1

    adapter.on_media_engine_event(MF_MEDIA_ENGINE_EVENT_PAUSE, 0, 0)
    adapter.pump_events()
    assert core.detach_calls == 1
    adapter.on_media_engine_event(MF_MEDIA_ENGINE_EVENT_PURGEQUEUEDEVENTS, 0, 0)
    adapter.pump_events()
    assert core.load_calls == ['first.mp4', 'second.mp4']


def test_duplicate_purge_commits_pending_source_once():
    """Duplicate detach-purge notifications cannot issue SetSource twice."""
    adapter, core = _ready_adapter()
    _start_playing_source(adapter, core, 'first.mp4')
    adapter.load_source('second.mp4')
    adapter.play()
    adapter.on_media_engine_event(MF_MEDIA_ENGINE_EVENT_PAUSE, 0, 0)
    adapter.pump_events()

    adapter.on_media_engine_event(MF_MEDIA_ENGINE_EVENT_PURGEQUEUEDEVENTS, 0, 0)
    adapter.on_media_engine_event(MF_MEDIA_ENGINE_EVENT_PURGEQUEUEDEVENTS, 0, 0)
    adapter.pump_events()

    assert core.detach_calls == 1
    assert core.load_calls == ['first.mp4', 'second.mp4']
    assert adapter._source_epoch == 2

def test_detach_failure_after_pause_is_published_and_rolls_back_pending_source(caplog):
    """A failed SetSource(NULL) cannot leave a replacement armed after PAUSE."""
    caplog.set_level(logging.ERROR, logger=adapter_events.logger.name)
    adapter, core = _ready_adapter(fail_detach=True)
    bus = PublishBus()
    adapter._event_bus = bus
    _start_playing_source(adapter, core, 'first.mp4')
    bus.calls.clear()
    adapter.load_source('second.mp4')
    adapter.play()

    adapter.on_media_engine_event(MF_MEDIA_ENGINE_EVENT_PAUSE, 0, 0)
    adapter.pump_events()

    assert core.detach_calls == 1
    assert core.load_calls == ['first.mp4']
    assert adapter._source == 'first.mp4'
    assert adapter._source_epoch == 1
    assert adapter._pending_source_epoch is None
    assert adapter._pending_source_path is None
    assert adapter._detaching_source_epoch is None
    assert adapter._pending_play_epoch is None
    assert 'Native source detach failed after PAUSE' in caplog.text
    assert len(bus.calls) == 2
    assert bus.calls[0][0] == 'VIDEO_ERROR'

