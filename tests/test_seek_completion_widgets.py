"""Both real widget timers consume confirmed clocks, never the requested target."""
import pytest

from tests.test_ui_cached_progress import rig, tick, flush
from src.video.seek_receipt import SeekSlot, NativeSeekResult, SeekPhase as P
from src.playback_observation import ReadingStatus
import time


@pytest.fixture
def clock(rig, monkeypatch):
    """Control both producer and presentation time after constructing their rig.

    Equal ticks remain legal, pre-completion samples must not confirm a seek, and
    strictly later samples must thaw both playing and paused displays. No sleeps.
    """
    now = [time.monotonic()]
    read = lambda: now[0]
    monkeypatch.setattr(time, 'monotonic', read)
    rig.widget._presentation._clock = read
    rig.widget._presentation._motion.reset()
    return now


def operation(rig):
    slot = SeekSlot()
    op = slot.reserve('clip.mp4', 1, 0, 8.0)
    op.mark_queued(); assert op.begin_native()
    op.put_nowait(NativeSeekResult(P.NATIVE_ACCEPTED_UNCONFIRMED, 0))
    rig.backend.get_seek_receipt = op.snapshot
    return op


def test_pending_and_completed_seek_preserve_correct_pair_on_both_bars(rig, clock):
    assert rig.widget.progress_slider.GetValue() == 400
    op = operation(rig)
    rig.readings.position = 8.0
    rig.tracker._poll_once(); tick(rig); flush(rig)
    assert rig.widget.progress_slider.GetValue() == 400
    op.observe_event(16, 1, 0, 'clip.mp4'); op.observe_event(17, 1, 0, 'clip.mp4')
    tick(rig)
    assert rig.widget.progress_slider.GetValue() == 400  # Cached pre-completion sample.
    assert op.snapshot().completed_at == clock[0]
    clock[0] += 0.25  # This observation, not its scheduling order, is strictly later.
    rig.readings.position = 7.8  # Native observation differs from requested target.
    rig.tracker._poll_once(); tick(rig); flush(rig)
    assert rig.widget.progress_slider.GetValue() == 780


def test_paused_native_completion_gets_new_clock_without_resuming(rig, clock):
    from src.controller.component_player.playback_state_manager import PlayerState
    op = operation(rig)
    rig.manager.update_state(PlayerState.PAUSED_VIDEO)
    rig.backend.pump_pending_events = lambda: True
    op.observe_event(16, 1, 0, 'clip.mp4'); op.observe_event(17, 1, 0, 'clip.mp4')
    clock[0] += 0.25
    rig.readings.position = 2.0
    rig.tracker._poll_once(); tick(rig); flush(rig)
    assert rig.widget.progress_slider.GetValue() == 200
    assert rig.manager.state is PlayerState.PAUSED_VIDEO


def test_errors_during_a_pending_seek_do_not_call_native_getters_on_gui(rig):
    operation(rig)
    def broken(): raise OSError('receipt unavailable')
    rig.backend.get_seek_receipt = broken
    tick(rig); flush(rig)
    assert rig.widget.progress_slider.GetValue() == 400
    assert rig.readings.calls == 1


@pytest.mark.parametrize('paused', [False, True])
def test_equal_completion_tick_does_not_confirm_position_until_later_sample(rig, clock, paused):
    from src.controller.component_player.playback_state_manager import PlayerState
    op = operation(rig)
    if paused:
        rig.manager.update_state(PlayerState.PAUSED_VIDEO)
        rig.backend.pump_pending_events = lambda: True
    op.observe_event(16, 1, 0, 'clip.mp4')
    op.observe_event(17, 1, 0, 'clip.mp4')
    rig.readings.position = 7.8
    rig.tracker._poll_once(); tick(rig); flush(rig)
    assert rig.tracker.get_progress_snapshot().clock.started_at == op.snapshot().completed_at
    assert rig.widget._display_reading.status is not ReadingStatus.KNOWN
    assert rig.widget.progress_slider.GetValue() != 780
    clock[0] += 0.25
    rig.tracker._poll_once(); tick(rig); flush(rig)
    assert rig.widget._display_reading.status is ReadingStatus.KNOWN
    assert rig.widget.progress_slider.GetValue() == 780
    expected = PlayerState.PAUSED_VIDEO if paused else PlayerState.PLAYING_VIDEO
    assert rig.manager.state is expected
