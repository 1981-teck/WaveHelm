"""Deterministic handoff tests, including real concurrent producer completion."""
from __future__ import annotations

import ctypes as C
import threading
from types import SimpleNamespace

import pytest

from src.video import wic_pipeline as p
from src.video.wic_native import BitmapInfo, U8, WicError
from src.video.wic_renderer import Frame


class FakeRenderer:
    def __init__(self) -> None:
        self.calls: list[tuple[int,int,int]] = []
        self.closed = 0
        self.no_frame = False
        self.action = None
        self.pool = ((U8 * 16)(), (U8 * 16)())

    def render(self, width: int, height: int, revision: int) -> Frame | None:
        self.calls.append((width,height,revision))
        if self.action is not None:
            self.action()
        if self.no_frame:
            return None
        pixels = self.pool[(len(self.calls)-1) % 2]
        return Frame(revision, len(self.calls), pixels, BitmapInfo(40,width,-height,1,32))

    def close(self) -> None:
        self.closed += 1


class Queue:
    def __init__(self) -> None:
        self.items: list[tuple[object,object]] = []
        self.result = True
        self.error = None
        self.last = None

    def __call__(self, name, callback, response) -> bool:
        assert name == 'wic_frame'
        self.items.append((callback,response))
        if self.error:
            raise self.error
        return self.result

    def run(self) -> None:
        callback, response = self.items.pop(0)
        self.last = response
        try:
            result = callback()
        except (ValueError, RuntimeError, OSError) as error:
            result = error
        response.put_nowait(result)


def fixture():
    renderer, queue = FakeRenderer(), Queue()
    state = SimpleNamespace(ready=True)
    pipeline = p.FramePipeline(renderer, queue, lambda: state.ready, 7)
    return pipeline, renderer, queue, state


@pytest.mark.parametrize('value,expected', [('wic', True), ('legacy_hwnd', False)])
def test_backend_explicit_selection(monkeypatch, value, expected) -> None:
    monkeypatch.setenv('WAVEHELM_VIDEO_BACKEND', value)
    assert p.wic_selected() is expected


@pytest.mark.parametrize('value', ['', 'auto', 'WIC', 'wic,legacy_hwnd'])
def test_invalid_backend_never_falls_back(monkeypatch, value) -> None:
    monkeypatch.setenv('WAVEHELM_VIDEO_BACKEND',value)
    with pytest.raises(ValueError):
        p.wic_selected()


@pytest.mark.parametrize('platform,expected', [('nt', True), ('posix', False)])
def test_unset_backend_uses_platform_default(monkeypatch, platform, expected) -> None:
    monkeypatch.delenv('WAVEHELM_VIDEO_BACKEND', raising=False)
    monkeypatch.setattr(p.os, 'name', platform)
    assert p.wic_selected() is expected


def test_one_flight_and_completion_must_be_consumed() -> None:
    service, renderer, queue, _ = fixture()
    assert service.request(2,2)
    for _ in range(1000):
        assert not service.request(2,2)
    assert len(queue.items) == 1
    queue.run()
    assert not service.request(2,2)
    first = service.consume()
    assert first is not None and service.current() is first
    assert service.request(2,2)
    queue.run()
    assert service.current() is first
    second = service.consume()
    assert second is not first and second.pixels is not first.pixels
    assert service.request(2,2)
    queue.run()
    assert service.current() is second
    assert service.consume().pixels is first.pixels
    assert len(renderer.calls) == 3


@pytest.mark.parametrize('action', ['invalidate', 'stop', 'not_ready', 'rebind'])
def test_queued_old_work_never_renders(action) -> None:
    service, renderer, queue, state = fixture()
    assert service.request(2,2)
    if action == 'not_ready':
        state.ready = False
    elif action == 'rebind':
        service.rebind(9)
    else:
        getattr(service,action)()
    queue.run()
    assert not renderer.calls and service.consume() is None


@pytest.mark.parametrize('action', ['invalidate', 'stop', 'rebind'])
def test_native_completion_during_revision_change_is_discarded(action) -> None:
    service, renderer, queue, _ = fixture()
    renderer.action = (lambda: service.rebind(9)) if action == 'rebind' else getattr(service,action)
    assert service.request(2,2)
    worker = threading.Thread(target=queue.run)
    worker.start(); worker.join(timeout=2)
    assert not worker.is_alive()
    assert service.consume() is None and service.discarded == 1
    assert service.error() is None


def test_resize_invalidates_front_and_does_not_duplicate_inflight() -> None:
    service, renderer, queue, _ = fixture()
    assert service.request(2,2); queue.run(); service.consume()
    assert service.request(2,2)
    assert not service.request(3,3)
    assert service.current() is None
    queue.run()
    assert service.consume() is None
    assert service.request(3,3)
    queue.run()
    assert service.consume().info.width == 3


def test_worker_native_failure_is_latched_exactly_without_fallback() -> None:
    service, renderer, queue, _ = fixture()
    error = WicError('TransferVideoFrame(WIC) HRESULT=0x80004005')
    def fail():
        raise error
    renderer.action = fail
    assert service.request(2,2); queue.run()
    assert service.error() is error and service.consume() is None
    assert not service.request(2,2)
    assert not queue.items


@pytest.mark.parametrize('receipt', [0, True, 'frame', object()])
def test_invalid_worker_receipt_stops_the_pipeline(receipt) -> None:
    service, _, queue, _ = fixture()
    service.request(2,2)
    _, ticket = queue.items.pop()
    ticket.put_nowait(receipt)
    assert isinstance(service.error(), WicError)
    assert not service.request(2,2)


def test_stale_failure_is_not_silently_labeled_success() -> None:
    service, _, queue, _ = fixture()
    service.request(2,2); service.invalidate()
    _, ticket = queue.items.pop()
    error = WicError('Uncertain native state')
    ticket.put_nowait(error)
    assert service.error() is error


def test_duplicate_receipt_cannot_overwrite_next_ticket() -> None:
    service, _, queue, _ = fixture()
    service.request(2,2); queue.run(); service.consume()
    old = queue.last
    assert service.request(2,2)
    old.put_nowait(RuntimeError('duplicate'))
    assert service.error() is None and service.snapshot()['in_flight']
    queue.run(); assert service.consume().pts == 2


@pytest.mark.parametrize('kind', ['refused', 'uncertain_admission'])
def test_no_retry_when_admission_is_refused_or_uncertain(kind) -> None:
    service, _, queue, _ = fixture()
    queue.result = False
    if kind == 'uncertain_admission':
        queue.error = RuntimeError('enqueued but wake failed')
    assert not service.request(2,2)
    assert service.error() is not None
    assert not service.request(2,2)
    queue.run()
    assert service.consume() is None


def test_worker_timeout_retains_ticket_no_second_native_request(monkeypatch) -> None:
    service, _, queue, _ = fixture()
    monkeypatch.setattr(p.time,'monotonic',lambda:10.0)
    service.request(2,2)
    monkeypatch.setattr(p.time,'monotonic',lambda:14.0)
    assert service.consume() is None
    assert isinstance(service.error(),TimeoutError)
    assert not service.request(2,2) and len(queue.items) == 1
    queue.run(); assert service.consume() is None


def test_s_false_retains_current_front_for_paused_repaint() -> None:
    service, renderer, queue, _ = fixture()
    service.request(2,2); queue.run(); first=service.consume()
    renderer.no_frame=True
    service.request(2,2); queue.run()
    assert service.consume() is first and service.current() is first


def test_second_ui_thread_is_rejected() -> None:
    service, _, _, _ = fixture()
    service.consume()
    errors=[]
    def wrong_owner():
        try:
            service.consume()
        except WicError as error:
            errors.append(error)
    worker=threading.Thread(target=wrong_owner); worker.start(); worker.join(timeout=2)
    assert len(errors)==1 and not worker.is_alive()


@pytest.mark.parametrize('size', [(0,10),(10,0),(-2,10)])
def test_minimized_output_invalidates_without_submission(size) -> None:
    service, _, queue, _ = fixture()
    assert not service.request(*size) and not queue.items


def test_shutdown_prevents_late_admission_and_releases_on_ordered_owner() -> None:
    service, renderer, queue, _ = fixture()
    service.request(2,2); service.stop()
    queue.run()
    assert service.consume() is None
    service.close_native()
    assert renderer.closed == 1 and not service.request(2,2)
