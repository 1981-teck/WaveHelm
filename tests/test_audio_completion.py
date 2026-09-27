"""Strict audio activity boundary; spies do not prove real device playback."""
import ast
from pathlib import Path
from types import SimpleNamespace
import threading

import pytest

from src.audio.audio_engine_shared import poll_audio_completion


def owner():
    return SimpleNamespace(_current_file="clip.wav", _playback_source_file="clip.wav",
        _seek_base=0.0, _progress_thread=object(), _is_paused=False,
        _current_play_uses_native_loop=False, _progress_stop_event=threading.Event())


def mixer(busy):
    return SimpleNamespace(get_init=lambda: (44100, -16, 2),
                           music=SimpleNamespace(get_busy=lambda: busy))


@pytest.mark.parametrize("busy", [False, True])
def test_audio_end_uses_actual_mixer_activity_not_metadata(busy):
    state = owner()
    state._current_position = 2.01
    state._total_length = 2.26
    assert poll_audio_completion(state, mixer(busy)) is (not busy)


@pytest.mark.parametrize("field,value", [("_current_file", None), ("_progress_thread", None),
    ("_is_paused", True), ("_current_play_uses_native_loop", True)])
def test_inactive_or_native_looping_lifecycle_never_completes(field, value):
    state = owner()
    setattr(state, field, value)
    native = mixer(False)
    native.music.get_busy = lambda: pytest.fail("inactive state reached mixer")
    assert poll_audio_completion(state, native) is False


def test_stop_and_dsp_reload_inhibit_completion():
    state = owner()
    state._progress_stop_event.set()
    assert poll_audio_completion(state, mixer(False)) is False
    state._progress_thread = None
    state._progress_stop_event.clear()
    assert poll_audio_completion(state, mixer(False)) is False


def test_uninitialized_mixer_is_unknown_not_idle():
    native = mixer(False)
    native.get_init = lambda: None
    assert poll_audio_completion(owner(), native) is None


@pytest.mark.parametrize("busy", [None, 0, 1, "false"])
def test_malformed_activity_never_becomes_an_end(busy):
    with pytest.raises(TypeError, match="boolean"):
        poll_audio_completion(owner(), mixer(busy))


@pytest.mark.parametrize("field,value", [("_current_file", "new.wav"),
    ("_playback_source_file", "new-render.wav"), ("_seek_base", 2.0),
    ("_progress_thread", object()), ("_is_paused", True),
    ("_current_play_uses_native_loop", True)])
def test_reentrant_playback_changes_invalidate_idle(field, value):
    state = owner()
    native = mixer(False)

    def mutate():
        setattr(state, field, value)
        return False

    native.music.get_busy = mutate
    assert poll_audio_completion(state, native) is None


def test_reentrant_stop_or_mixer_shutdown_is_unknown():
    state = owner()
    native = mixer(False)
    native.music.get_busy = lambda: state._progress_stop_event.set() or False
    assert poll_audio_completion(state, native) is None
    state = owner()
    native = mixer(False)
    initialized = iter([(44100, -16, 2), None])
    native.get_init = lambda: next(initialized)
    assert poll_audio_completion(state, native) is None


@pytest.mark.parametrize("field", ["_is_paused", "_current_play_uses_native_loop"])
def test_non_boolean_lifecycle_flags_are_rejected(field):
    state = owner()
    setattr(state, field, "false")
    with pytest.raises(TypeError, match="flags"):
        poll_audio_completion(state, mixer(False))


def test_idle_short_clip_needs_no_previous_busy_sample_or_known_duration():
    state = owner()
    state._total_length = 0.0
    state._was_playing = False
    assert poll_audio_completion(state, mixer(False)) is True


def test_query_exception_is_not_converted_to_completion_in_shared_helper():
    native = mixer(False)

    def fail():
        raise RuntimeError("native query failed")

    native.music.get_busy = fail
    with pytest.raises(RuntimeError, match="native query"):
        poll_audio_completion(owner(), native)


def test_production_audio_engine_wires_the_strict_query():
    # Wiring assertion is structural, not a substitute for importing the engine below.
    path = Path(__file__).resolve().parents[1] / "src/audio/audio_engine.py"
    module = ast.parse(path.read_bytes())
    assert any(isinstance(node, ast.Assign) and
               any(ast.unparse(target) == "AudioEngine.poll_end" for target in node.targets)
               and ast.unparse(node.value) == "playback.poll_end" for node in module.body)


def test_real_audio_engine_method_maps_pygame_failure_to_unknown(monkeypatch):
    pygame = pytest.importorskip("pygame", reason="real pygame required for assembled audio boundary")
    from src.audio.audio_engine import AudioEngine
    from src.audio import audio_engine_playback
    assert AudioEngine.poll_end is audio_engine_playback.poll_end
    state = owner()
    monkeypatch.setattr(pygame.mixer, "get_init", lambda: (44100, -16, 2))
    monkeypatch.setattr(pygame.mixer.music, "get_busy", lambda: False)
    assert AudioEngine.poll_end(state) is True

    def fail():
        raise pygame.error("native query failure")

    monkeypatch.setattr(pygame.mixer.music, "get_busy", fail)
    assert AudioEngine.poll_end(state) is None


def test_production_tracker_uses_shared_audio_completion_without_early_fallback():
    from src.controller.component_player.playback_state_manager import PlaybackStateManager, PlayerState
    from src.controller.component_player.progress_tracker import ProgressTracker
    state = owner()
    native = mixer(True)
    state.get_duration = lambda: 2.26
    state.get_position = lambda: 2.01
    state.poll_end = lambda: poll_audio_completion(state, native)
    manager = PlaybackStateManager()
    manager.update_state(PlayerState.PLAYING_AUDIO)
    ended = []
    tracker = ProgressTracker(manager, SimpleNamespace(current_track=object(), index=0),
        SimpleNamespace(audio_engine=state), lambda *_: None, lambda: ended.append(True))
    assert tracker._poll_once() is False
    native.music.get_busy = lambda: False
    assert tracker._poll_once() is True
    assert ended == [True]


@pytest.mark.parametrize("invalid", [False, 0, (), (44100, -16), (0, -16, 2),
                                   (44100, 0, 2), (44100, -16, False)])
def test_malformed_mixer_initialization_is_unknown(invalid):
    native = mixer(False)
    native.get_init = lambda: invalid
    assert poll_audio_completion(owner(), native) is None
