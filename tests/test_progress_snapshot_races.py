"""Deterministic boundary schedules; not a native COM/GUI latency measurement."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.controller.component_player.playback_state_manager import PlayerState
from src.playback_observation import ReadingStatus
from test_progress_snapshot import setup


@pytest.mark.parametrize("mutation", ["seek", "same-file-replay", "track", "path-in-place", "index",
                                      "engine", "pause", "stop", "stop-event"])
def test_context_change_during_read_cannot_publish_old_data(mutation: str) -> None:
    tracker, backend, events, ends = setup()
    def change() -> None:
        if mutation == "seek":
            tracker.state_manager.invalidate_end_observation()
        elif mutation == "same-file-replay":
            tracker.state_manager.update_state(PlayerState.PLAYING_VIDEO)
        elif mutation == "track":
            tracker.queue_manager.current_track = SimpleNamespace(path="clip.mp4")
        elif mutation == "path-in-place":
            tracker.queue_manager.current_track.path = "changed.mp4"
        elif mutation == "index":
            tracker.queue_manager.index = 1
        elif mutation == "engine":
            tracker.engine_controller.video_controller = SimpleNamespace()
        elif mutation in ("pause", "stop"):
            tracker.state_manager.update_state(PlayerState.PAUSED_VIDEO if mutation == "pause" else PlayerState.STOPPED)
        else:
            tracker._stop_event.set()
    backend.hook = change
    tracker._poll_once()
    assert not events and not ends
    assert backend.ends == 0  # Do not consume a new context's native EOS after clock drift.
    assert tracker.get_progress_snapshot() is None


@pytest.mark.parametrize("stage", ["after-capture", "before-delivery"])
def test_delayed_bound_sample_never_gets_a_new_queue_path(stage: str) -> None:
    tracker, backend, events, _ = setup()
    context = tracker._context()
    clock = backend.observe_progress()
    if stage == "after-capture":
        tracker.queue_manager.current_track = SimpleNamespace(path="next.mp4")
        tracker._publish_observation(context, clock)
        assert not events
    else:
        def deliver(kind: object, payload: dict[str, object]) -> None:
            tracker.queue_manager.current_track = SimpleNamespace(path="next.mp4")
            events.append(payload)
        tracker._publish_event = deliver
        tracker._publish_observation(context, clock)
        assert events[0]["path"] == "clip.mp4"  # Never mislabeled with next.mp4.
        assert events[0]["progress_snapshot"]["revision"] == context.revision
        assert tracker.get_progress_snapshot() is None


def test_newer_acquisition_wins_even_if_older_read_finishes_last() -> None:
    tracker, backend, events, _ = setup()
    context = tracker._context()
    older_sequence = tracker._reserve_sample()
    old_clock = backend.observe_progress()
    newer_sequence = tracker._reserve_sample()
    backend.position = 4.0
    new_clock = backend.observe_progress()
    tracker._publish_observation(context, new_clock, newer_sequence)
    tracker._publish_observation(context, old_clock, older_sequence)
    assert len(events) == 1
    assert tracker.get_progress_snapshot().clock.position.seconds == 4.0
    assert tracker.get_progress_snapshot().sequence == newer_sequence


@pytest.mark.parametrize("change", ["seek", "replay", "pause", "stop", "track"])
def test_old_cache_is_inaccessible_immediately_after_state_change(change: str) -> None:
    tracker, _, _, _ = setup()
    tracker._poll_once()
    if change == "seek":
        tracker.state_manager.invalidate_end_observation()
    elif change == "track":
        tracker.queue_manager.current_track = SimpleNamespace(path="next.mp4")
    else:
        state = {"replay": PlayerState.PLAYING_VIDEO, "pause": PlayerState.PAUSED_VIDEO,
                 "stop": PlayerState.STOPPED}[change]
        tracker.state_manager.update_state(state)
    assert tracker.get_progress_snapshot() is None


def test_publisher_failure_does_not_destroy_the_bound_cache() -> None:
    tracker, _, _, _ = setup()
    def fail(kind: object, payload: dict[str, object]) -> None:
        raise RuntimeError("UI unavailable")
    tracker._publish_event = fail
    tracker._poll_once()
    assert tracker.get_progress_snapshot().clock.position.seconds == 0.0


def test_identity_change_between_position_and_duration_is_not_a_mixed_event() -> None:
    tracker, _, events, ends = setup()
    def position() -> float:
        tracker.queue_manager.current_track = SimpleNamespace(path="next.mp4")
        return 8.0
    tracker.engine_controller.video_controller = SimpleNamespace(
        get_position=position, get_duration=lambda: 20.0, poll_end=lambda: pytest.fail("stale EOS read"))
    tracker._poll_once()
    assert not events and not ends
    assert tracker.get_progress_snapshot() is None


def test_revision_change_while_context_is_captured_is_not_published() -> None:
    tracker, _, events, _ = setup()
    class Track:
        @property
        def path(self) -> str:
            tracker.state_manager.invalidate_end_observation()
            return "clip.mp4"
    tracker.queue_manager.current_track = Track()
    assert tracker._context().stable is False
    tracker._poll_once()
    assert not events


def test_source_mismatch_is_stale_but_native_end_does_not_depend_on_clock_value() -> None:
    tracker, backend, events, ends = setup()
    backend.source = "wrong.mp4"
    tracker._poll_once()
    assert tracker.get_progress_snapshot().clock.position.status is ReadingStatus.STALE
    assert not events and not ends
    backend.ended = True
    tracker._poll_once()
    assert ends == [True]


def test_actual_event_bus_delivers_bound_sample_without_weakening_snapshot_policy() -> None:
    from src.audio.audio_event_bus import AudioEventBus
    from src.audio.audio_events import AudioEventType
    tracker, _, _, _ = setup()
    bus = AudioEventBus()
    received = []
    bus.subscribe(AudioEventType.PLAYBACK_PROGRESS, received.append)
    tracker._publish_event = bus.publish
    try:
        tracker._poll_once()
        assert len(received) == 1
        assert received[0]["current_time"] == 0.0
        snapshot = received[0]["progress_snapshot"]
        assert type(snapshot) is dict
        assert snapshot["path"] == "clip.mp4"
        assert snapshot["clock"]["position"]["seconds"] == 0.0
        assert snapshot["revision"] == tracker.get_progress_snapshot().revision
    finally:
        bus.shutdown()


def test_stale_old_publisher_cannot_clear_a_new_context_sample() -> None:
    tracker, backend, events, _ = setup()
    old_context = tracker._context()
    old_sequence = tracker._reserve_sample()
    old_clock = backend.observe_progress()
    tracker.queue_manager.current_track = SimpleNamespace(path="next.mp4")
    tracker.state_manager.invalidate_end_observation()
    backend.source, backend.position = "next.mp4", 1.0
    tracker._poll_once()
    latest = tracker.get_progress_snapshot()
    tracker._publish_observation(old_context, old_clock, old_sequence)
    assert tracker.get_progress_snapshot() is latest
    assert len(events) == 1 and events[0]["path"] == "next.mp4"


def test_old_read_discard_cannot_clear_a_completed_new_acquisition() -> None:
    tracker, backend, events, _ = setup()
    def change_and_sample() -> None:
        backend.hook = lambda: None
        tracker.queue_manager.current_track = SimpleNamespace(path="next.mp4")
        tracker.state_manager.invalidate_end_observation()
        backend.source, backend.position = "next.mp4", 2.0
        tracker._poll_once()
    backend.hook = change_and_sample
    tracker._poll_once()
    assert tracker.get_progress_snapshot() is not None
    assert tracker.get_progress_snapshot().path == "next.mp4"
    assert len(events) == 1


def test_stopping_tracker_hides_retained_clock_even_without_a_worker() -> None:
    tracker, _, _, _ = setup()
    tracker._poll_once()
    assert tracker.get_progress_snapshot() is not None
    tracker.stop()
    assert tracker.get_progress_snapshot() is None


def test_observation_across_tracker_restart_is_invalidated() -> None:
    tracker, backend, events, _ = setup()
    def restart() -> None:
        tracker.stop()
        # Model the new start epoch without starting an unrelated worker in this test.
        tracker._run_epoch += 1
        tracker._stop_event.clear()
    backend.hook = restart
    tracker._poll_once()
    assert not events
    assert tracker.get_progress_snapshot() is None
