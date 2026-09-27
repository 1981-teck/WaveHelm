"""Queued UI work is rejected after seek, same-path replay, pause or stop."""
from types import SimpleNamespace
import threading

import pytest

from src.controller.player_controller import PlayerController
from src.controller.component_player.playback_state_manager import PlaybackStateManager, PlayerState


def player():
    controller = object.__new__(PlayerController)
    controller._is_shutdown = False
    controller._track_end_dispatch_pending = False
    controller.state_manager = PlaybackStateManager()
    controller.state_manager.update_state(PlayerState.PLAYING_VIDEO)
    controller.queue_manager = SimpleNamespace(current_track=SimpleNamespace(path="clip.mp4"), index=0)
    controller.engine_controller = SimpleNamespace(should_repeat_current_track=lambda: False)
    calls, jobs = [], []
    controller.next = lambda: calls.append("next")
    controller._dispatch_to_ui_thread = lambda job: jobs.append(job) or True
    return controller, calls, jobs


def enqueue(controller):
    results = []
    thread = threading.Thread(target=lambda: results.append(controller._handle_track_end()))
    thread.start()
    thread.join(timeout=2)
    assert not thread.is_alive()
    return results[0]


@pytest.mark.parametrize("mutation", ["seek", "replay", "pause", "stop", "same-context"])
def test_delayed_end_does_not_apply_to_new_transport_revision(mutation):
    controller, calls, jobs = player()
    assert enqueue(controller) is True
    state = controller.state_manager
    if mutation == "seek":
        state.invalidate_end_observation()
    elif mutation == "replay":
        state.update_state(PlayerState.PLAYING_VIDEO)
    elif mutation == "pause":
        state.update_state(PlayerState.PAUSED_VIDEO)
    elif mutation == "stop":
        state.update_state(PlayerState.STOPPED)
    else:
        state.set_context([controller.queue_manager.current_track], 0, controller.queue_manager.current_track)
    jobs.pop()()
    assert calls == []
    assert controller._track_end_dispatch_pending is False


def test_current_job_advances_once_and_coalesced_notification_is_declined():
    controller, calls, jobs = player()
    assert enqueue(controller) is True
    assert enqueue(controller) is False
    assert len(jobs) == 1
    jobs.pop()()
    assert calls == ["next"]
    assert controller._track_end_dispatch_pending is False


def test_shutdown_and_missing_context_do_not_accept_an_end():
    controller, calls, jobs = player()
    controller._is_shutdown = True
    assert controller._handle_track_end() is False
    controller._is_shutdown = False
    controller.queue_manager.current_track = None
    assert controller._handle_track_end() is False
    assert calls == jobs == []
