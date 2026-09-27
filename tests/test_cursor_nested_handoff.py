"""Nested ownership, coarse-clock and paused-recovery regressions on actual routes."""
import threading
import pytest
from tests.cursor_chain_fakes import ui, rig, tick
from tests.gesture_fakes import GestureEvent
from src.controller.component_player.playback_state_manager import PlayerState
from src.playback_observation import ReadingStatus


@pytest.mark.parametrize('mutation', ['generation', 'same-source-load'])
def test_end_observation_rejects_native_owner_change_inside_query(ui, monkeypatch, mutation):
    r = ui
    def ended():
        if mutation == 'generation':
            r.core._engine_generation += 1
        else:
            r.core._seek_slot.invalidate('same-source-load')
        return True
    monkeypatch.setattr(r.adapter, 'has_ended', ended)
    assert r.controller.observe_end() is False
    assert r.controller._adapter is r.adapter


def test_same_source_reload_after_end_cannot_be_closed_by_previous_ticket(ui):
    r = ui; r.ended = True
    assert r.controller.observe_end() is True
    r.core._seek_slot.invalidate('same-source-load')
    assert r.controller.finalize_video_end() is False
    assert r.controller._adapter is r.adapter


def test_native_end_on_seek_completion_tick_is_terminal_not_a_fake_clock(ui):
    r = ui
    r.widget._seek_from_progress_ratio(.8)
    r.manager._drain_tasks_com_thread()
    op = r.core._seek_slot.current()
    assert op.observe_event(16, 1, 0, 'clip.mp4')
    assert op.observe_event(17, 1, 0, 'clip.mp4')
    r.ended = True; r.position = 10.0
    outcomes = []
    worker = threading.Thread(target=lambda: outcomes.append(r.tracker._poll_once()))
    worker.start(); worker.join(2)
    assert not worker.is_alive() and outcomes == [True]
    tick(r)
    assert r.widget.progress_slider.GetValue() == 1000
    assert r.widget._display_reading.terminal
    assert r.widget._display_reading.status is not ReadingStatus.KNOWN
    assert r.widget._display_reading.position is None


def test_terminal_latch_still_pumps_native_errors_while_ui_is_delayed(ui, monkeypatch):
    r = ui; r.ended = True; r.position = 10.0
    outcomes = []
    t = threading.Thread(target=lambda: outcomes.append(r.tracker._poll_once()))
    t.start(); t.join(2)
    assert outcomes == [True] and not t.is_alive()
    pumps = []
    monkeypatch.setattr(r.adapter, 'pump_events', lambda: pumps.append(True))
    r.now[0] += .25
    assert not r.tracker._poll_once()
    assert pumps == [True]
    assert r.tracker.get_progress_snapshot().terminal


def test_refused_paused_seek_can_recover_input_without_resuming(ui):
    r = ui; state = r.player.state_manager
    state.update_state(PlayerState.PAUSED_VIDEO); tick(r)
    r.manager._com_ready_event.clear()
    assert r.widget._seek_from_progress_ratio(.8) is False
    assert r.widget.progress_slider.GetValue() == 400
    r.manager._com_ready_event.set(); r.now[0] += .25
    assert r.tracker._poll_once() is False
    tick(r)
    assert state.state is PlayerState.PAUSED_VIDEO
    assert r.widget._display_reading.position == 4.0
    assert r.widget._display_reading.status is ReadingStatus.KNOWN
    assert r.widget._seek_from_progress_ratio(.2) is True
    assert state.state is PlayerState.PAUSED_VIDEO and not r.next_calls


def test_pending_new_core_cannot_inherit_old_generation_visual_history(ui):
    r = ui
    r.core._engine_generation += 1
    r.core._seek_slot.invalidate('different-generation')
    assert not r.widget._seek_from_progress_ratio(.8)  # Old-generation cache cannot admit input.
    from src.controller.component_player.engine_controller import SeekDispatch
    assert r.player.seek(8.) is SeekDispatch.FORWARDED_UNCONFIRMED
    tick(r)  # An external canonical command still exposes its new-generation receipt.
    assert r.widget.progress_slider.GetValue() == 0
    assert r.widget._display_reading.position is None
