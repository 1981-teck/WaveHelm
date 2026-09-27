"""Seek admission: malformed input, missing guard, shutdown and explicit refusal.

The backend is test-owned. A forwarded command is not an actual native seek.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.controller.component_player import engine_controller as module
from src.controller.component_player.engine_controller import EngineController
from src.controller.component_player.playback_state_manager import PlaybackStateManager, PlayerState
from src.controller.player_controller import PlayerController


STATES = (PlayerState.PLAYING_AUDIO, PlayerState.PAUSED_AUDIO,
          PlayerState.PLAYING_VIDEO, PlayerState.PAUSED_VIDEO)


def make_engine(state: PlayerState = PlayerState.PLAYING_VIDEO, result: object = True):
    manager = PlaybackStateManager()
    manager.update_state(state)
    calls: list[tuple[float, int]] = []

    def seek(value: float) -> object:
        calls.append((value, manager.playback_revision))
        return result

    backend = SimpleNamespace(seek=seek)
    engine = EngineController(manager, backend, backend)
    return engine, manager, backend, calls


@pytest.mark.parametrize('state', STATES)
@pytest.mark.parametrize('result', [True, None])
def test_forwarding_is_explicitly_unconfirmed_and_invalidates_first(state, result):
    engine, manager, _, calls = make_engine(state, result)
    before = manager.playback_revision
    actual = engine.seek(2.0)
    assert actual is module.SeekDispatch.FORWARDED_UNCONFIRMED
    assert calls == [(2.0, before + 1)]
    assert manager.state is state
    with pytest.raises(TypeError, match='completion'):
        bool(actual)


@pytest.mark.parametrize('value', [False, 0, 1, 'ok', [], object()])
def test_refused_or_malformed_backend_return_does_not_claim_forwarding(value):
    engine, manager, _, calls = make_engine(result=value)
    assert engine.seek(1.0) is module.SeekDispatch.REJECTED
    assert len(calls) == 1
    assert manager.playback_revision == 2


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf'),
                                   True, False, None, '2', object(), 10**400],
                         ids=['nan', 'inf', 'negative-inf', 'true', 'false',
                              'none', 'string', 'object', 'huge-integer'])
def test_invalid_position_has_no_effect_on_backend_or_revision(value):
    engine, manager, _, calls = make_engine()
    assert engine.seek(value) is module.SeekDispatch.REJECTED
    assert calls == []
    assert manager.playback_revision == 1


@pytest.mark.parametrize('state', [PlayerState.IDLE, PlayerState.STOPPED,
                                   PlayerState.LOADING, PlayerState.ERROR])
def test_inactive_states_cannot_dispatch_a_seek(state):
    engine, manager, _, calls = make_engine(state)
    assert engine.seek(2.0) is module.SeekDispatch.REJECTED
    assert manager.playback_revision == 1
    assert calls == []


def test_negative_finite_position_clamps_to_zero_without_losing_zero():
    engine, _, _, calls = make_engine()
    assert engine.seek(-4.0) is module.SeekDispatch.FORWARDED_UNCONFIRMED
    assert engine.seek(0.0) is module.SeekDispatch.FORWARDED_UNCONFIRMED
    assert calls == [(0.0, 2), (0.0, 3)]


@pytest.mark.parametrize('failure', [RuntimeError, ValueError, OSError])
def test_backend_exception_keeps_old_end_invalidated(failure):
    engine, manager, backend, _ = make_engine()

    def fail(_: float) -> None:
        raise failure('backend seek refused')

    backend.seek = fail
    assert engine.seek(2.0) is module.SeekDispatch.REJECTED
    assert manager.playback_revision == 2


@pytest.mark.parametrize('missing', ['backend', 'seek', 'guard', 'revision'])
def test_missing_contract_is_not_forwarded(missing):
    engine, manager, backend, calls = make_engine()
    if missing == 'backend':
        engine.video_controller = None
    elif missing == 'seek':
        backend.seek = None
    elif missing == 'guard':
        manager.invalidate_end_observation = None
    else:
        manager._playback_revision = None
    assert engine.seek(2.0) is module.SeekDispatch.REJECTED
    assert calls == []


def test_guard_failure_does_not_fall_through_to_backend():
    engine, manager, _, calls = make_engine()

    def fail() -> None:
        raise RuntimeError('guard unavailable')

    manager.invalidate_end_observation = fail
    assert engine.seek(2.0) is module.SeekDispatch.REJECTED
    assert calls == []


def test_guard_must_advance_revision_and_preserve_state():
    engine, manager, _, calls = make_engine()
    manager.invalidate_end_observation = lambda: None
    assert engine.seek(2.0) is module.SeekDispatch.REJECTED
    assert calls == []
    manager.invalidate_end_observation = lambda: manager.update_state(PlayerState.LOADING)
    assert engine.seek(2.0) is module.SeekDispatch.REJECTED
    assert calls == []


def test_facade_returns_explicit_dispatch_and_shutdown_refuses():
    engine, _, _, calls = make_engine()
    facade = PlayerController(engine.state_manager, SimpleNamespace(), engine, None, None)
    assert facade.seek(2.0) is module.SeekDispatch.FORWARDED_UNCONFIRMED
    facade._is_shutdown = True
    assert facade.seek(3.0) is module.SeekDispatch.REJECTED
    assert len(calls) == 1
    engine._is_shutting_down = True
    assert engine.seek(3.0) is module.SeekDispatch.REJECTED
    assert len(calls) == 1


@pytest.mark.parametrize('mutation', ['state', 'revision', 'backend'])
def test_context_change_during_backend_call_cannot_be_acknowledged(mutation):
    engine, manager, backend, _ = make_engine()

    def mutate(_: float) -> bool:
        if mutation == 'state':
            manager.update_state(PlayerState.STOPPED)
        elif mutation == 'revision':
            manager.invalidate_end_observation()
        else:
            engine.video_controller = object()
        return True

    backend.seek = mutate
    assert engine.seek(2.0) is module.SeekDispatch.REJECTED
