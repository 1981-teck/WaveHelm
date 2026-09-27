"""Actual core/adapter methods across test-owned COM dispatch and native returns."""
from __future__ import annotations

import ctypes
from dataclasses import replace
from types import SimpleNamespace
from typing import Callable

import pytest

from src.playback_observation import ClockObservation, ClockOrigin, ReadingStatus
from src.video.media_engine_clock import read_video_clock
from src.video.component_base.definitions_abi import IMFMediaEngine, IUnknownVtbl
from src.video.media_engine_core import MediaEngineCore
from src.video.imf_media_engine_adapter import IMFMediaEngineAdapter


class CheckedLock:
    held = False

    def __enter__(self) -> None:
        assert not self.held
        self.held = True

    def __exit__(self, *args: object) -> None:
        self.held = False


class TestCore:
    __test__ = False

    def __init__(self) -> None:
        self._state_lock = CheckedLock()
        self.references: list[str] = []
        self.vtable = IUnknownVtbl()
        self.callbacks = []
        for method in ("AddRef", "Release"):
            callback = dict(IUnknownVtbl._fields_)[method](
                lambda ptr, method=method: self.references.append(method) or 1)
            self.callbacks.append(callback)
            setattr(self.vtable, method, callback)
        self.engine_object = IMFMediaEngine(ctypes.pointer(self.vtable))
        self._media_engine = ctypes.pointer(self.engine_object)
        self._engine_generation = 1
        self._source = self._requested_source = self._active_source = "clip.mp4"
        self._shutdown_requested = False
        self.before_dispatch: Callable[[], None] = lambda: None
        self.after_read: Callable[[], None] = lambda: None
        self.calls: list[tuple[object, str]] = []
        self.dispatches = 0
        self.position: object = 0.0
        self.duration: object = 10.0
        self.adapter = SimpleNamespace(call_on_com_thread=self.dispatch)

    def _adapter_ref(self) -> object:
        return self.adapter

    def dispatch(self, name: str, task: Callable[[], ClockObservation]) -> ClockObservation:
        assert not self._state_lock.held
        self.dispatches += 1
        self.before_dispatch()
        return task()

    def _call_engine_ptr_method(self, engine: object, name: str) -> object:
        assert not self._state_lock.held
        self.calls.append((engine, name))
        result = self.position if name == "GetCurrentTime" else self.duration
        if isinstance(result, Exception):
            raise result
        if name == "GetDuration":
            self.after_read()
        return result


def test_production_core_entry_reads_zero_and_native_duration_in_one_task() -> None:
    core = TestCore()
    result = MediaEngineCore.observe_progress(core)
    assert result.position.seconds == 0.0
    assert result.duration.seconds == 10.0
    assert result.source == "clip.mp4" and result.generation == 1
    assert core.dispatches == 1
    assert [name for _, name in core.calls] == ["GetCurrentTime", "GetDuration"]
    assert all(ctypes.cast(engine, ctypes.c_void_p).value == ctypes.addressof(core.engine_object)
               for engine, _ in core.calls)
    assert core.references == ["AddRef", "Release"]


@pytest.mark.parametrize("field,value", [
    ("_media_engine", None), ("_source", None), ("_active_source", None),
    ("_requested_source", "next.mp4"), ("_shutdown_requested", True),
])
def test_absent_or_loading_source_never_queues_native_read(field: str, value: object) -> None:
    core = TestCore()
    setattr(core, field, value)
    result = read_video_clock(core)
    assert result.position.status is ReadingStatus.UNAVAILABLE
    assert core.dispatches == 0 and core.calls == []


@pytest.mark.parametrize("where", ["before", "after"])
@pytest.mark.parametrize("field,value", [
    ("_media_engine", None), ("_engine_generation", 2),
    ("_active_source", "next.mp4"), ("_shutdown_requested", True),
])
def test_ownership_change_discards_pair(where: str, field: str, value: object) -> None:
    core = TestCore()
    hook = lambda: setattr(core, field, value)
    if where == "before":
        core.before_dispatch = hook
    else:
        core.after_read = hook
    result = read_video_clock(core)
    assert result.position.status is ReadingStatus.STALE
    assert result.duration.seconds is None
    if where == "before":
        assert core.calls == []


@pytest.mark.parametrize("field", ["position", "duration"])
@pytest.mark.parametrize("error", [OSError("native failed"), RuntimeError("dispatcher failed")])
def test_native_failure_is_not_a_partial_pair_or_fake_zero(field: str, error: Exception) -> None:
    core = TestCore()
    setattr(core, field, error)
    result = read_video_clock(core)
    assert result.position.status is result.duration.status is ReadingStatus.ERROR
    assert result.position.seconds is result.duration.seconds is None
    assert result.position.error_type == type(error).__name__


@pytest.mark.parametrize("duration", [None, 0.0, float("nan"), float("inf")])
def test_unavailable_native_duration_is_not_replaced_by_other_metadata(duration: object) -> None:
    core = TestCore()
    core.duration = duration
    result = read_video_clock(core)
    assert result.position.seconds == 0.0
    assert result.duration.status is ReadingStatus.UNAVAILABLE


def test_adapter_change_after_dispatch_discards_old_clock() -> None:
    core = TestCore()
    core.after_read = lambda: setattr(core, "adapter", SimpleNamespace())
    assert read_video_clock(core).position.status is ReadingStatus.STALE


def test_missing_adapter_and_invalid_dispatch_return_are_explicit() -> None:
    core = TestCore()
    core.adapter = None
    assert read_video_clock(core).position.status is ReadingStatus.UNAVAILABLE
    core.adapter = SimpleNamespace(call_on_com_thread=lambda *_: 0.0)
    assert read_video_clock(core).position.status is ReadingStatus.ERROR


def test_real_adapter_guard_and_query_do_not_hold_adapter_lock() -> None:
    core = TestCore()
    adapter = IMFMediaEngineAdapter.__new__(IMFMediaEngineAdapter)
    adapter._lock = CheckedLock()
    adapter._source = "clip.mp4"
    adapter._closed = adapter._shutdown_requested = False
    def observe() -> ClockObservation:
        assert not adapter._lock.held
        return read_video_clock(core)
    adapter._core = SimpleNamespace(observe_progress=observe)
    result = adapter.observe_progress()
    assert result.position.seconds == 0.0
    def changed() -> ClockObservation:
        result = observe()
        adapter._source = "next.mp4"
        return result
    adapter._core.observe_progress = changed
    assert adapter.observe_progress().position.status is ReadingStatus.STALE
    adapter._closed = True
    assert adapter.observe_progress().position.status is ReadingStatus.UNAVAILABLE


def test_first_getter_reentrancy_stops_before_second_and_releases_retained_ref() -> None:
    core = TestCore()
    raw = core._call_engine_ptr_method
    def reentrant(engine: object, name: str) -> object:
        result = raw(engine, name)
        core._shutdown_requested = True
        return result
    core._call_engine_ptr_method = reentrant
    result = read_video_clock(core)
    assert result.position.status is ReadingStatus.STALE
    assert [name for _, name in core.calls] == ["GetCurrentTime"]
    assert core.references == ["AddRef", "Release"]


def test_query_failure_releases_owned_ref_once_on_each_read_at_same_address() -> None:
    core = TestCore()
    core.duration = OSError("native getter failed")
    for _ in range(3):
        assert read_video_clock(core).position.status is ReadingStatus.ERROR
    assert core.references == ["AddRef", "Release"] * 3


@pytest.mark.parametrize("missing", ["AddRef", "Release"])
def test_null_ownership_method_never_attempts_getters(missing: str) -> None:
    core = TestCore()
    setattr(core.vtable, missing, dict(IUnknownVtbl._fields_)[missing]())
    assert read_video_clock(core).position.status is ReadingStatus.ERROR
    assert core.calls == [] and core.references == []
