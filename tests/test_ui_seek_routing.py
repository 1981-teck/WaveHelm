"""Exercise real widget handlers -> helper -> facade -> engine -> owned backend.

wx widgets and the backend are test-owned. No native GUI/COM operation is claimed.
Cases cover pending EOS, loading, shutdown, invalid inputs and refused admission.
"""
from __future__ import annotations

import sys
import threading
import time
from types import SimpleNamespace

import pytest

from src.controller.component_player.engine_controller import EngineController, SeekDispatch
from src.controller.component_player.playback_state_manager import PlaybackStateManager, PlayerState
from src.controller.player_controller import PlayerController
from src.controller.playback_status import seek_to
from src.model.media_file import MediaType
from src.ui_wx.mini_player import MiniPlayer
from src.ui_wx.video_overlay_controls import ExternalVideoControlOverlay
from tests.wx_fakes import FakeMouseEvent, FakeWxModule
from tests.gesture_fakes import GestureEvent
from src.playback_observation import ClockObservation, ClockValue, ClockOrigin, ProgressSnapshot


def make_player(state: PlayerState, result: object = True):
    manager = PlaybackStateManager()
    media_type = MediaType.AUDIO if state in (PlayerState.PLAYING_AUDIO, PlayerState.PAUSED_AUDIO) else MediaType.VIDEO
    track = SimpleNamespace(path='same-file.mp4', media_type=media_type, duration=10.0)
    manager.set_context([track], 0, track)
    manager.update_state(state)
    calls: list[tuple[float, int]] = []

    def seek(seconds: float) -> object:
        calls.append((seconds, manager.playback_revision))
        return result

    backend = SimpleNamespace(seek=seek)
    engine = EngineController(manager, backend, backend)
    queue = SimpleNamespace(current_track=track, index=0)
    player = PlayerController(manager, queue, engine, None, None)
    jobs, next_calls = [], []
    player._dispatch_to_ui_thread = lambda job: jobs.append(job) or True
    player.next = lambda: next_calls.append('next')
    return player, calls, jobs, next_calls


def make_widget(monkeypatch, surface: str, player: PlayerController):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    sequence = [0]
    def observed_cache():
        # Test-owned cached clock; real facade/state and native command chain remain.
        sequence[0] += 1
        now = time.monotonic()
        state, queue = player.state_manager, player.queue_manager
        path = queue.current_track.path
        clock = ClockObservation(ClockValue.read(2.0), ClockValue.read(10.0, duration=True),
                                 now, now, ClockOrigin.VIDEO_NATIVE, path, 1)
        return ProgressSnapshot('gesture-routing-fixture', sequence[0], state.playback_revision,
                                state.state.name, path, queue.index, clock)
    monkeypatch.setattr(player, 'get_progress_snapshot', observed_cache)
    parent = FakeWxModule.Frame(None)
    if surface == 'mini':
        widget = MiniPlayer(parent, player_controller=player, audio_engine=None)
    else:
        widget = ExternalVideoControlOverlay(FakeWxModule, parent, player_controller=player)
    widget._presentation._clock = time.monotonic  # Same explicit test clock as sample/receipt.
    widget._presentation.repaint()
    widget.progress_slider.SetClientSize((201, 24))
    widget.progress_slider.SetValue(800)
    return widget


def invoke_handler(widget, gesture: str) -> None:
    if gesture == 'slider':
        widget._on_progress_key_down(GestureEvent(key=FakeWxModule.WXK_RIGHT))
        widget._on_progress_slider_changed(GestureEvent(position=800))
    else:
        widget._on_progress_slider_pointer_down(FakeMouseEvent(x=160))
        widget._on_progress_slider_pointer_up(FakeMouseEvent(x=160))


def enqueue_end(player: PlayerController) -> None:
    outcomes = []
    worker = threading.Thread(target=lambda: outcomes.append(player._handle_track_end()))
    worker.start()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert outcomes == [True]


@pytest.mark.parametrize('surface', ['mini', 'overlay'])
@pytest.mark.parametrize('gesture', ['slider', 'pointer'])
@pytest.mark.parametrize('state', [PlayerState.PLAYING_AUDIO, PlayerState.PLAYING_VIDEO])
def test_real_bar_seek_invalidates_previously_queued_end(monkeypatch, surface, gesture, state):
    player, calls, jobs, next_calls = make_player(state)
    widget = make_widget(monkeypatch, surface, player)
    enqueue_end(player)
    revision = player.state_manager.playback_revision
    invoke_handler(widget, gesture)
    # Assert observable routing BEFORE any return-type/API check.
    assert calls == [(8.0, revision + 1)]
    assert len(jobs) == 1
    jobs.pop()()
    assert next_calls == []
    assert player._track_end_dispatch_pending is False


@pytest.mark.parametrize('surface', ['mini', 'overlay'])
@pytest.mark.parametrize('gesture', ['slider', 'pointer'])
@pytest.mark.parametrize('state', [PlayerState.LOADING, PlayerState.STOPPED,
                                   PlayerState.IDLE, PlayerState.ERROR])
def test_real_bar_cannot_bypass_inactive_state_guard(monkeypatch, surface, gesture, state):
    player, calls, _, _ = make_player(state)
    widget = make_widget(monkeypatch, surface, player)
    revision = player.state_manager.playback_revision
    invoke_handler(widget, gesture)
    assert calls == []
    assert player.state_manager.playback_revision == revision


@pytest.mark.parametrize('surface', ['mini', 'overlay'])
@pytest.mark.parametrize('state', [PlayerState.PAUSED_AUDIO, PlayerState.PAUSED_VIDEO])
def test_paused_seek_is_forwarded_without_resuming(monkeypatch, surface, state):
    player, calls, _, _ = make_player(state)
    widget = make_widget(monkeypatch, surface, player)
    invoke_handler(widget, 'slider')
    assert calls == [(8.0, 3)]
    assert player.state_manager.state is state


@pytest.mark.parametrize('surface', ['mini', 'overlay'])
def test_closed_facade_refuses_bar_request(monkeypatch, surface):
    player, calls, _, _ = make_player(PlayerState.PLAYING_VIDEO)
    player._is_shutdown = True
    widget = make_widget(monkeypatch, surface, player)
    invoke_handler(widget, 'slider')
    assert calls == []


@pytest.mark.parametrize('result', [SeekDispatch.REJECTED, False, None, 1, 'ok'])
def test_helper_never_falls_back_after_refused_or_unknown_facade_result(result):
    direct, canonical = [], []
    backend = SimpleNamespace(seek=lambda seconds: direct.append(seconds) or True)
    player = SimpleNamespace(video_controller=backend, audio_engine=backend,
                             current_track=SimpleNamespace(media_type=MediaType.VIDEO),
                             seek=lambda seconds: canonical.append(seconds) or result)
    assert seek_to(player, 3.0) is False
    assert canonical == [3.0]
    assert direct == []


@pytest.mark.parametrize('missing', [None, 'not callable'])
def test_missing_facade_cannot_dispatch_directly(missing):
    calls = []
    backend = SimpleNamespace(seek=lambda seconds: calls.append(seconds) or True)
    player = SimpleNamespace(seek=missing, video_controller=backend, audio_engine=backend,
                             current_track=SimpleNamespace(media_type=MediaType.VIDEO))
    assert seek_to(player, 3.0) is False
    assert calls == []


@pytest.mark.parametrize('value', [float('nan'), float('inf'), True, '1.0', None, 10**400],
                         ids=['nan', 'inf', 'bool', 'string', 'none', 'huge-integer'])
def test_helper_rejects_invalid_input_before_facade(value):
    calls = []
    player = SimpleNamespace(seek=lambda seconds: calls.append(seconds) or True)
    assert seek_to(player, value) is False
    assert calls == []


def test_sync_refusal_still_cancels_old_end_via_real_helper():
    player, calls, jobs, next_calls = make_player(PlayerState.PLAYING_VIDEO, False)
    enqueue_end(player)
    assert seek_to(player, 2.0) is False
    assert calls == [(2.0, 3)]
    jobs.pop()()
    assert next_calls == []
