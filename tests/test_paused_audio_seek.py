"""Real transport functions with explicit mixer/wx test boundaries, not native audio.

Cases cover paused/playing seeks, two consecutive GUI gestures, rejected input,
backend failures, and stale cross-seek clock reads. No simulated Windows claim.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from dataclasses import replace
import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest

from src.audio.audio_engine_shared import read_audio_clock
from src.audio.audio_events import AudioEventType
from src.controller.component_player.engine_controller import EngineController
from src.controller.component_player.playback_state_manager import PlayerState
from src.model.media_file import MediaType
from src.playback_observation import ReadingStatus, ClockOrigin
from tests.gesture_fakes import GestureEvent
from tests.test_ui_cached_progress import rig, tick


class MixerError(RuntimeError):
    """Explicit fake exception at the pygame boundary."""


class Music:
    def __init__(self) -> None:
        self.paused = False
        self.elapsed = 4000
        self.volume = 0.625
        self.calls: list[tuple[str, object]] = []
        self.fail = ''
        self.on_play = lambda: None
        self.on_pos = lambda: None

    def play(self, *, loops=0, start=0.0) -> None:
        self.calls.append(('play', (loops, start, self.volume)))
        if self.fail == 'play':
            raise MixerError('play failed')
        self.paused, self.elapsed = False, 0
        self.on_play()

    def pause(self) -> None:
        self.calls.append(('pause', None))
        if self.fail == 'pause':
            raise MixerError('pause failed')
        self.paused = True

    def stop(self) -> None:
        self.calls.append(('stop', None))
        self.elapsed = -1

    def get_volume(self) -> float:
        return self.volume

    def set_volume(self, value: float) -> None:
        self.calls.append(('volume', value))
        self.volume = value

    def get_pos(self) -> int:
        self.on_pos()
        return self.elapsed

    def get_busy(self) -> bool:
        return not self.paused and self.elapsed >= 0


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError('Cannot load test-owned module')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def audio(monkeypatch):
    """Execute original source modules; only pygame I/O is replaced per test."""
    music = Music()
    pygame = ModuleType('pygame')
    pygame.error = MixerError
    pygame.mixer = SimpleNamespace(music=music, get_init=lambda: (44100, -16, 2))
    monkeypatch.setitem(sys.modules, 'pygame', pygame)
    directory = Path(__file__).resolve().parents[1] / 'src' / 'audio'
    support = _load('src.audio._test_seek_support', directory / 'audio_engine_playback_support.py')
    monkeypatch.setitem(sys.modules, 'src.audio.audio_engine_playback_support', support)
    playback = _load('src.audio._test_seek_playback', directory / 'audio_engine_playback.py')
    events = []
    stop = threading.Event()
    owner = SimpleNamespace(
        _current_file='clip.mp3', _playback_source_file=None, _seek_base=0.0,
        _progress_thread=object(), _progress_stop_event=stop, _is_paused=False,
        _current_position=4.0, _total_length=10.0, _current_play_uses_native_loop=False,
        _loop_enabled=False, _is_music_loaded=True, _volume=0.625, _is_muted=False,
        _audio_seek_serial=0, event_bus=SimpleNamespace(publish=lambda e, p: events.append((e, p))),
    )
    def start():
        owner._progress_thread = object()
        stop.clear()
    def halt():
        owner._progress_thread = None
        stop.set()
    owner._start_progress_loop, owner._stop_progress_loop = start, halt
    return SimpleNamespace(music=music, owner=owner, playback=playback, support=support,
                           mixer=pygame.mixer, events=events)


def _pause(audio) -> None:
    audio.playback.pause(audio.owner)
    audio.music.calls.clear()
    audio.events.clear()


@pytest.mark.parametrize('target', [0.0, 2.0, 8.0])
def test_paused_seek_preserves_pause_and_mutes_internal_restart(audio, target):
    _pause(audio)
    assert audio.playback.seek(audio.owner, target) is True
    assert audio.music.paused and audio.owner._is_paused
    assert audio.owner._current_position == audio.owner._seek_base == target
    assert audio.owner._progress_thread is None
    assert audio.owner._progress_stop_event.is_set()
    assert audio.music.volume == 0.625
    assert ('play', (0, target, 0.0)) in audio.music.calls
    assert audio.events == [(AudioEventType.SEEK, {'position': target})]


def test_playing_seek_does_not_pause_or_change_volume(audio):
    assert audio.playback.seek(audio.owner, 8.0) is True
    assert not audio.music.paused
    assert audio.music.calls == [('play', (0, 8.0, 0.625))]
    assert audio.owner._progress_thread is not None


@pytest.mark.parametrize('target', [True, '3', float('nan'), float('inf')],
                         ids=['bool', 'text', 'nan', 'infinity'])
def test_invalid_seek_has_no_native_side_effect_or_success_event(audio, target):
    _pause(audio)
    assert audio.playback.seek(audio.owner, target) is False
    assert audio.music.calls == [] and audio.events == []
    assert audio.owner._seek_base == 0.0


@pytest.mark.parametrize('failure', ['play', 'pause'])
def test_native_failure_is_rejected_without_silent_restart_at_zero(audio, failure):
    _pause(audio)
    audio.music.fail = failure
    assert audio.playback.seek(audio.owner, 8.0) is False
    assert audio.owner._seek_base == 0.0 and audio.owner._current_position == 4.0
    assert audio.music.elapsed == -1
    assert audio.music.volume == 0.625 and audio.events == []
    assert len([call for call in audio.music.calls if call[0] == 'play']) == 1
    assert audio.owner._audio_seek_serial % 2 == 0


def test_audio_clock_can_read_pause_after_progress_worker_stops(audio):
    _pause(audio)
    result = read_audio_clock(audio.owner, audio.mixer)
    assert result.position.seconds == 4.0 and result.duration.seconds == 10.0


def test_audio_clock_refuses_an_inflight_seek(audio):
    _pause(audio)
    values = []
    audio.music.on_play = lambda: values.append(read_audio_clock(audio.owner, audio.mixer))
    audio.playback.seek(audio.owner, 8.0)
    assert len(values) == 1 and values[0].position.status is ReadingStatus.UNAVAILABLE


def test_same_target_seek_crossing_clock_read_invalidates_sample(audio):
    audio.music.on_pos = lambda: setattr(audio.owner, '_audio_seek_serial', 2)
    assert read_audio_clock(audio.owner, audio.mixer).position.status is ReadingStatus.STALE


@pytest.mark.parametrize('paused', [PlayerState.PAUSED_AUDIO, PlayerState.PAUSED_VIDEO])
def test_paused_tracker_samples_without_polling_end(rig, paused):
    rig.tracker.engine_controller.audio_engine = rig.backend
    if paused is PlayerState.PAUSED_AUDIO:
        previous = rig.backend.observe_progress
        rig.backend.observe_progress = lambda: replace(previous(), origin=ClockOrigin.AUDIO_MIXER)
    rig.manager.update_state(paused)
    rig.backend.poll_end = lambda: pytest.fail('Pause must not query EOS')
    rig.tracker._poll_once()
    sample = rig.tracker.get_progress_snapshot()
    assert sample is not None and sample.state == paused.name and not sample.terminal
    assert sample.clock.position.seconds == 4.0


@pytest.mark.parametrize('rig', ['mini'], indirect=True)
def test_two_paused_audio_gestures_refresh_actual_cache_without_resuming(rig, audio):
    track = SimpleNamespace(path='clip.mp3', title='Clip', media_type=MediaType.AUDIO)
    rig.queue.current_track = track
    rig.manager.set_context([track], 0, track)
    rig.manager.update_state(PlayerState.PLAYING_AUDIO)
    engine = EngineController.__new__(EngineController)
    engine.state_manager, engine.audio_engine = rig.manager, audio.owner
    engine.video_controller, engine._is_shutting_down = None, False
    audio.owner.observe_progress = lambda: read_audio_clock(audio.owner, audio.mixer)
    audio.owner.seek = lambda target: audio.playback.seek(audio.owner, target)
    rig.player.engine_controller = rig.tracker.engine_controller = engine
    rig.tracker._poll_once()
    tick(rig)
    _pause(audio)
    rig.manager.update_state(PlayerState.PAUSED_AUDIO)
    rig.tracker._poll_once()
    tick(rig)
    slider = rig.widget.progress_slider
    slider.SetClientSize((201, 24))
    for x, expected in [(160, 8.0), (40, 2.0)]:
        slider.trigger('EVT_LEFT_DOWN', GestureEvent(x=x))
        slider.trigger('EVT_LEFT_UP', GestureEvent(x=x, down=False))
        rig.tracker._poll_once()
        tick(rig)
        assert rig.widget._current_position == expected
        assert slider.GetValue() == int(expected * 100)
        assert rig.manager.state is PlayerState.PAUSED_AUDIO and audio.music.paused
        assert not slider.HasCapture()
