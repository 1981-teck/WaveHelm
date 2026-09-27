"""Controller/adapter/core clock route, with only native returns dispatched in tests."""
from dataclasses import replace
from types import SimpleNamespace
from typing import Callable
import threading

import pytest

from src.controller.video_controller import VideoController
from src.controller.video_controller_state import VideoState
from src.playback_observation import ClockObservation, ClockOrigin, ClockValue, ReadingStatus
from src.video.imf_media_engine_adapter import IMFMediaEngineAdapter
from src.video.media_engine_core import MediaEngineCore


def sample() -> ClockObservation:
    return ClockObservation(ClockValue.read(0), ClockValue.read(10, duration=True),
                            1.0, 1.1, ClockOrigin.VIDEO_NATIVE, "clip.mp4", 3)


def controller(callback: Callable[[], ClockObservation] = sample) -> VideoController:
    current = VideoController.__new__(VideoController)
    current._adapter = SimpleNamespace(observe_progress=callback)
    current._current_path = "clip.mp4"
    current._state = VideoState.PLAYING
    current._closing = current._shutting_down = False
    return current


def test_controller_keeps_valid_zero_and_does_not_query_scalar_getters() -> None:
    current = controller()
    current.get_position = lambda: pytest.fail("legacy position must not be read")
    current.get_duration = lambda: pytest.fail("metadata duration must not be read")
    assert current.observe_progress().position.seconds == 0.0


@pytest.mark.parametrize("state", [VideoState.IDLE, VideoState.STOPPED, VideoState.ERROR])
def test_inactive_controller_does_not_query_adapter(state: VideoState) -> None:
    current = controller(lambda: pytest.fail("inactive adapter read"))
    current._state = state
    assert current.observe_progress().position.status is ReadingStatus.UNAVAILABLE


@pytest.mark.parametrize("field,value", [("_closing", True), ("_shutting_down", True),
                                         ("_adapter", None)])
def test_closing_or_absent_adapter_is_unavailable(field: str, value: object) -> None:
    current = controller()
    setattr(current, field, value)
    assert current.observe_progress().position.status is ReadingStatus.UNAVAILABLE


@pytest.mark.parametrize("field,value", [
    ("_current_path", "next.mp4"), ("_adapter", object()),
    ("_state", VideoState.PAUSED), ("_closing", True), ("_shutting_down", True),
])
def test_change_during_read_is_stale(field: str, value: object) -> None:
    current = controller()
    def change() -> ClockObservation:
        setattr(current, field, value)
        return sample()
    current._adapter.observe_progress = change
    assert current.observe_progress().position.status is ReadingStatus.STALE


@pytest.mark.parametrize("result", [0.0, None, {}, replace(sample(), origin=ClockOrigin.LEGACY)])
def test_invalid_adapters_fail_without_scalar_fallback(result: object) -> None:
    current = controller(lambda: result)
    assert current.observe_progress().position.status is ReadingStatus.ERROR


def test_adapter_exception_and_wrong_source_are_not_zero() -> None:
    def fail() -> ClockObservation:
        raise OSError("native read failed")
    assert controller(fail).observe_progress().position.status is ReadingStatus.ERROR
    wrong = replace(sample(), source="other.mp4")
    assert controller(lambda: wrong).observe_progress().position.status is ReadingStatus.STALE


def test_actual_three_layer_route_reaches_only_one_com_task() -> None:
    current = controller()
    adapter = IMFMediaEngineAdapter.__new__(IMFMediaEngineAdapter)
    adapter._lock = threading.RLock()
    adapter._closed = adapter._shutdown_requested = False
    adapter._source = "clip.mp4"
    core = MediaEngineCore(adapter)
    from test_video_clock_observation import TestCore
    native_fixture = TestCore()
    core._media_engine = native_fixture._media_engine
    core._source = core._active_source = core._requested_source = "clip.mp4"
    calls = []
    tasks = []
    def raw(engine: object, name: str) -> object:
        calls.append(name)
        return 0.0 if name == "GetCurrentTime" else 10.0
    def dispatch(name: str, task: Callable[[], ClockObservation]) -> ClockObservation:
        tasks.append(name)
        return task()
    core._call_engine_ptr_method = raw
    adapter.call_on_com_thread = dispatch
    adapter._core = core
    current._adapter = adapter
    try:
        result = current.observe_progress()
        assert result.position.seconds == 0.0 and result.duration.seconds == 10.0
        assert calls == ["GetCurrentTime", "GetDuration"]
        assert tasks == ["observe_progress"]
    finally:
        core._shutdown_requested = True  # Test-owned COM-shaped vtable, not a native engine.
