"""Mixer boundary fixtures exercise real audio acquisition, no fake pygame module."""
from types import SimpleNamespace
from typing import Callable
import threading

import pytest

from src.audio.audio_engine_shared import read_audio_clock
from src.playback_observation import ClockOrigin, ReadingStatus


def owner() -> SimpleNamespace:
    return SimpleNamespace(_current_file="clip.mp3", _playback_source_file="render.wav",
                           _seek_base=0.0, _progress_thread=object(), _is_paused=False,
                           _current_play_uses_native_loop=False,
                           _progress_stop_event=threading.Event(), _total_length=10.0)


class Mixer:
    def __init__(self, elapsed: int = 0) -> None:
        self.init: object = (44100, -16, 2)
        self.elapsed = elapsed
        self.hook: Callable[[], None] = lambda: None
        self.calls = 0
        self.music = SimpleNamespace(get_pos=self.get_pos)

    def get_init(self) -> object:
        return self.init

    def get_pos(self) -> int:
        self.calls += 1
        self.hook()
        return self.elapsed


@pytest.mark.parametrize("elapsed,base,expected", [(0, 0.0, 0.0), (10, 0.0, .01),
                                                    (1500, 2.0, 3.5)])
def test_raw_position_plus_seek_base(elapsed: int, base: float, expected: float) -> None:
    current = owner()
    current._seek_base = base
    result = read_audio_clock(current, Mixer(elapsed))
    assert result.position.seconds == expected
    assert result.duration.seconds == 10.0
    assert result.origin is ClockOrigin.AUDIO_MIXER and result.source == "clip.mp3"


@pytest.mark.parametrize("field,value", [("_current_file", None), ("_progress_thread", None)])
def test_absent_lifecycle_does_not_query_mixer(field: str, value: object) -> None:
    current, mixer = owner(), Mixer()
    setattr(current, field, value)
    assert read_audio_clock(current, mixer).position.status is ReadingStatus.UNAVAILABLE
    assert mixer.calls == 0


def test_stopped_or_uninitialized_is_unknown_not_zero() -> None:
    current, mixer = owner(), Mixer()
    current._progress_stop_event.set()
    assert read_audio_clock(current, mixer).position.seconds is None
    current._progress_stop_event.clear()
    mixer.init = None
    assert read_audio_clock(current, mixer).position.status is ReadingStatus.UNAVAILABLE
    assert mixer.calls == 0


@pytest.mark.parametrize("initialized", [False, (0, -16, 2), (44100, 0, 2), (44100, -16, 0),
                                         (True, -16, 2), [44100, -16, 2]])
def test_invalid_mixer_initialization_is_not_read(initialized: object) -> None:
    mixer = Mixer()
    mixer.init = initialized
    with pytest.raises(ValueError, match="initialization"):
        read_audio_clock(owner(), mixer)
    assert mixer.calls == 0


@pytest.mark.parametrize("field,value", [
    ("_current_file", "next.mp3"), ("_playback_source_file", "new-render.wav"),
    ("_progress_thread", object()), ("_seek_base", 5.0), ("_is_paused", True),
    ("_total_length", 20.0), ("_current_play_uses_native_loop", True),
])
def test_concurrent_reload_seek_pause_and_duration_change_invalidate_pair(field: str, value: object) -> None:
    current, mixer = owner(), Mixer()
    mixer.hook = lambda: setattr(current, field, value)
    result = read_audio_clock(current, mixer)
    assert result.position.status is result.duration.status is ReadingStatus.STALE


def test_stop_or_mixer_change_during_query_invalidates() -> None:
    current, mixer = owner(), Mixer()
    mixer.hook = current._progress_stop_event.set
    assert read_audio_clock(current, mixer).position.status is ReadingStatus.STALE
    current._progress_stop_event.clear()
    mixer.hook = lambda: setattr(mixer, "init", (48000, -16, 2))
    assert read_audio_clock(current, mixer).position.status is ReadingStatus.STALE


def test_paused_clock_is_read_without_asking_completion() -> None:
    current = owner()
    current._is_paused = True
    assert read_audio_clock(current, Mixer(2000)).position.seconds == 2.0


def test_elapsed_negative_sentinel_and_unknown_metadata_do_not_fake_zero() -> None:
    current = owner()
    current._total_length = float("nan")
    result = read_audio_clock(current, Mixer(-1))
    assert result.position.status is result.duration.status is ReadingStatus.UNAVAILABLE


@pytest.mark.parametrize("elapsed", [True, -2, 0.0, "100"])
def test_malformed_elapsed_is_not_coerced(elapsed: object) -> None:
    mixer = Mixer()
    mixer.elapsed = elapsed
    with pytest.raises(ValueError, match="milliseconds"):
        read_audio_clock(owner(), mixer)


def test_native_loop_position_is_scoped_to_own_duration() -> None:
    current = owner()
    current._current_play_uses_native_loop = True
    assert read_audio_clock(current, Mixer(12500)).position.seconds == 2.5


def test_production_audio_wrapper_catches_real_pygame_error(monkeypatch: pytest.MonkeyPatch) -> None:
    pygame = pytest.importorskip("pygame", reason="real pygame boundary unavailable")
    from src.audio import audio_engine_playback
    from src.audio.audio_engine import AudioEngine
    def fail() -> object:
        raise pygame.error("mixer failed")
    monkeypatch.setattr(pygame.mixer, "get_init", fail)
    result = AudioEngine.observe_progress(owner())
    assert result.position.status is ReadingStatus.ERROR
    assert result.position.seconds is None
    assert AudioEngine.observe_progress is audio_engine_playback.observe_progress
