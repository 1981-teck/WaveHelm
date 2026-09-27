"""Completion freshness contracts: same-track replay, pause/stop and seek failures."""
from types import SimpleNamespace

import pytest

from src.controller.component_player.engine_controller import EngineController
from src.controller.component_player.playback_state_manager import PlaybackStateManager, PlayerState


def test_context_and_same_state_updates_invalidate_old_observation():
    state = PlaybackStateManager()
    assert state.playback_revision == 0
    track = SimpleNamespace(path="clip.mp4", media_type=None)
    state.set_context([track], 0, track)
    assert state.playback_revision == 1
    for number in (2, 3):
        state.update_state(PlayerState.PLAYING_VIDEO)
        assert state.playback_revision == number
    state.set_context([track], 0, track)
    assert state.playback_revision == 4


@pytest.mark.parametrize("target", [PlayerState.PAUSED_AUDIO, PlayerState.STOPPED, PlayerState.ERROR])
def test_pause_stop_error_and_explicit_invalidation_advance_revision(target):
    state = PlaybackStateManager()
    state.update_state(target)
    state.invalidate_end_observation()
    assert state.playback_revision == 2
    state.update_state("not a state")
    assert state.playback_revision == 2


@pytest.mark.parametrize("target", [PlayerState.PLAYING_AUDIO, PlayerState.PAUSED_AUDIO,
                                   PlayerState.PLAYING_VIDEO, PlayerState.PAUSED_VIDEO])
@pytest.mark.parametrize("failed", [False, True])
def test_seek_invalidates_before_backend_call_even_when_it_fails(target, failed):
    state = PlaybackStateManager()
    state.update_state(target)
    observed = []

    def seek(position):
        observed.append((position, state.playback_revision))
        if failed:
            raise RuntimeError("seek refused")

    backend = SimpleNamespace(seek=seek)
    engine = SimpleNamespace(_is_shutting_down=False, state_manager=state,
                             audio_engine=backend, video_controller=backend)
    EngineController.seek(engine, 2.0)
    assert observed == [(2.0, 2)]


def test_loading_seek_is_not_dispatched_or_revised():
    state = PlaybackStateManager()
    state.update_state(PlayerState.LOADING)
    engine = SimpleNamespace(_is_shutting_down=False, state_manager=state)
    EngineController.seek(engine, 2.0)
    assert state.playback_revision == 1
