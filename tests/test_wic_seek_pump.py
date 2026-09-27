"""Tick-only seek liveness with actual Python paths and explicit native doubles.

These tests do not claim Windows reproduction. Missing callbacks never authorize
pixels; source/owner changes invalidate queued work; HRESULT/timeouts latch errors.
The ctypes test raises SEEKED from a synthetic tick to test the dependency cycle.
"""
from __future__ import annotations

import ctypes as C
import threading
from types import SimpleNamespace

import pytest

from src.video import wic_native as n
from src.video.imf_media_engine_adapter import IMFMediaEngineAdapter
from src.video.seek_receipt import NativeSeekResult, SeekPhase, SeekSlot
from src.video.wic_pipeline import FramePipeline
from tests.test_wic_integration import adapter_shape
from tests.test_wic_native import NativeFixture
from tests.test_wic_pipeline import FakeRenderer, Queue


class TickRenderer(FakeRenderer):
    """Endpoint double: no codec, Windows, GDI or native timing claims."""

    def __init__(self) -> None:
        super().__init__()
        self.ticks = 0
        self.tick_error: RuntimeError | None = None

    def pump_seek(self) -> None:
        if self.tick_error is not None:
            raise self.tick_error
        self.ticks += 1


def pending_pipeline() -> tuple[FramePipeline, TickRenderer, Queue, SimpleNamespace, IMFMediaEngineAdapter]:
    adapter = adapter_shape()
    state = SimpleNamespace(phase=SeekPhase.NATIVE_ACCEPTED_UNCONFIRMED)
    adapter._core.get_seek_receipt = lambda: state
    renderer, queue = TickRenderer(), Queue()
    pipeline = FramePipeline(renderer, queue, adapter.frame_ready, 7,
                             pump_ready=adapter.frame_pump_ready)
    return pipeline, renderer, queue, state, adapter


@pytest.mark.parametrize('phase', list(SeekPhase))
def test_pump_permission_never_weakens_presentation_permission(phase: SeekPhase) -> None:
    adapter = adapter_shape()
    adapter._core.get_seek_receipt = lambda: SimpleNamespace(phase=phase)
    presentable = phase in (SeekPhase.NATIVE_COMPLETED, SeekPhase.REJECTED, SeekPhase.CANCELLED)
    assert adapter.frame_ready() is presentable
    assert adapter.frame_pump_ready() is (presentable or phase is SeekPhase.NATIVE_ACCEPTED_UNCONFIRMED)


@pytest.mark.parametrize('field,value', [('_closed', True), ('_shutdown_requested', True),
                                        ('_pending_source_epoch', 3), ('_source', None),
                                        ('_ready_epoch', 1)])
def test_source_readiness_exclusions_also_block_seek_ticks(field: str, value: object) -> None:
    pipeline, renderer, queue, _state, adapter = pending_pipeline()
    setattr(adapter, field, value)
    assert not adapter.frame_ready() and not adapter.frame_pump_ready()
    assert not pipeline.request(2, 2)
    assert not queue.items and renderer.ticks == 0


def test_pending_seek_ticks_do_not_render_or_publish_and_remain_single_flight() -> None:
    pipeline, renderer, queue, state, _adapter = pending_pipeline()
    for _ in range(5):
        assert pipeline.request(2, 2)
        assert not pipeline.request(2, 2)
        queue.run()
        assert pipeline.consume() is None and pipeline.current() is None
    assert renderer.ticks == 5 and not renderer.calls
    assert pipeline.snapshot()['seek_pumps'] == 5
    state.phase = SeekPhase.NATIVE_COMPLETED
    assert pipeline.request(2, 2)
    queue.run()
    assert pipeline.consume() is not None
    assert len(renderer.calls) == 1 and renderer.ticks == 5


@pytest.mark.parametrize('change', ['invalidate', 'stop', 'rebind', 'closed', 'expired', 'failed'])
def test_stale_or_uncertain_pending_job_never_touches_native_tick(change: str) -> None:
    pipeline, renderer, queue, state, adapter = pending_pipeline()
    assert pipeline.request(2, 2)
    if change == 'closed':
        adapter._closed = True
    elif change == 'expired':
        state.phase = SeekPhase.EXPIRED_UNCONFIRMED
    elif change == 'failed':
        state.phase = SeekPhase.FAILED
    elif change == 'rebind':
        pipeline.rebind(9)
    else:
        getattr(pipeline, change)()
    queue.run()
    assert renderer.ticks == 0 and not renderer.calls
    assert pipeline.consume() is None and pipeline.snapshot()['seek_pumps'] == 0


def test_native_tick_failure_is_latched_without_retry_or_legacy_fallback() -> None:
    pipeline, renderer, queue, _state, _adapter = pending_pipeline()
    renderer.tick_error = n.WicError('Synthetic OnVideoStreamTick failure')
    assert pipeline.request(2, 2)
    queue.run()
    assert pipeline.error() is renderer.tick_error
    assert pipeline.consume() is None and not pipeline.request(2, 2)
    assert pipeline.snapshot()['seek_pumps'] == 0


def test_pending_frame_timeout_remains_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.video import wic_pipeline as module
    pipeline, renderer, queue, _state, _adapter = pending_pipeline()
    monkeypatch.setattr(module.time, 'monotonic', lambda: 10.0)
    assert pipeline.request(2, 2)
    monkeypatch.setattr(module.time, 'monotonic', lambda: 14.0)
    assert pipeline.consume() is None
    assert isinstance(pipeline.error(), TimeoutError)
    assert not pipeline.request(2, 2)
    queue.run()
    assert renderer.ticks == 0


def test_legacy_pipeline_constructor_retains_its_original_gate() -> None:
    renderer, queue = TickRenderer(), Queue()
    pipeline = FramePipeline(renderer, queue, lambda: False, 7)
    assert not pipeline.request(2, 2) and not queue.items


@pytest.mark.parametrize('tick_hr', [0, 1])
def test_native_tick_only_does_not_allocate_transfer_or_change_revision(tick_hr: int) -> None:
    native = NativeFixture()
    renderer = native.renderer()
    native.tick_hr = tick_hr
    before = (renderer.revision, renderer.last_pts, renderer.index, renderer.size)
    assert renderer.pump_seek() is None
    assert native.engine.calls == [44]
    assert native.bitmap.calls == [] and renderer.buffers is None
    assert (renderer.revision, renderer.last_pts, renderer.index, renderer.size) == before
    assert renderer.frames == renderer.resizes == 0
    renderer.close()


@pytest.mark.parametrize('tick_hr,pts', [(-2147467259, 10), (0, -1), (2, 10)])
def test_tick_only_native_failure_never_converts_to_frame_success(tick_hr: int, pts: int) -> None:
    native = NativeFixture()
    renderer = native.renderer()
    native.tick_hr, native.pts = tick_hr, pts
    with pytest.raises(n.WicError):
        renderer.pump_seek()
    assert native.engine.calls == [44] and renderer.frames == 0
    renderer.close()


def test_tick_only_rejects_wrong_thread_and_closed_renderer() -> None:
    native = NativeFixture()
    renderer = native.renderer()
    errors: list[n.WicError] = []

    def other_thread() -> None:
        try:
            renderer.pump_seek()
        except n.WicError as error:
            errors.append(error)

    worker = threading.Thread(target=other_thread)
    worker.start()
    worker.join(timeout=2)
    assert not worker.is_alive() and len(errors) == 1 and not native.engine.calls
    renderer.close()
    with pytest.raises(n.WicError):
        renderer.pump_seek()


@pytest.mark.parametrize('complete_on_tick', [False, True])
def test_synthetic_tick_callback_breaks_cycle_without_fabricating_seek_completion(complete_on_tick: bool) -> None:
    native = NativeFixture()
    slot = SeekSlot()
    operation = slot.reserve('old.mp4', 1, slot.epoch, 75.0)
    assert operation is not None and operation.begin_native()
    operation.put_nowait(NativeSeekResult(SeekPhase.NATIVE_ACCEPTED_UNCONFIRMED, 0))
    adapter = adapter_shape()
    adapter._core.get_seek_receipt = operation.snapshot

    def synthetic_tick(_this: int | None, pts: C.c_void_p) -> int:
        C.cast(pts, C.POINTER(C.c_int64)).contents.value = 750000000
        if complete_on_tick:
            assert operation.observe_event(16, 1, slot.epoch, 'old.mp4')
            assert operation.observe_event(17, 1, slot.epoch, 'old.mp4')
        return 1

    native.engine.add(44, n.I32, (C.POINTER(C.c_int64),), synthetic_tick)
    renderer, queue = native.renderer(), Queue()
    pipeline = FramePipeline(renderer, queue, adapter.frame_ready, 7,
                             pump_ready=adapter.frame_pump_ready)
    assert pipeline.request(64, 36)
    queue.run()
    assert pipeline.consume() is None and renderer.frames == 0
    expected = SeekPhase.NATIVE_COMPLETED if complete_on_tick else SeekPhase.NATIVE_ACCEPTED_UNCONFIRMED
    assert operation.snapshot().phase is expected
    if complete_on_tick:
        # Install an ordinary paused S_FALSE tick, not another synthetic callback.
        renderer._tick = lambda _engine, _pts: 1
        assert pipeline.request(64, 36)
        queue.run()
        frame = pipeline.consume()
        assert frame is not None and renderer.frames == 1
        assert frame.revision == pipeline._revision
    assert pipeline.snapshot()['seek_pumps'] == 1
    renderer.close()
