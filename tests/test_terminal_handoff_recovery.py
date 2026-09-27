"""Cache failures and native ownership changes must not become silent end success."""
from types import SimpleNamespace
import threading
import pytest
from tests.cursor_chain_fakes import ui, rig, tick
from tests.test_terminal_cursor_delivery import finish_in_worker, wire_actual_bus
from src.controller.component_player.playback_state_manager import PlayerState


def test_transient_final_view_refusal_retries_without_next_or_native_repoll(ui, monkeypatch):
    r = ui; bus, order = wire_actual_bus(r, monkeypatch)
    try:
        r.position = 10.; r.ended = True; r.now[0] += .25
        finish_in_worker(r)
        read = r.player.get_playback_view
        monkeypatch.setattr(r.player, 'get_playback_view', lambda: None)
        r.jobs.pop(0)()
        assert not order and not r.next_calls
        assert r.controller._adapter is r.adapter
        monkeypatch.setattr(r.player, 'get_playback_view', read)
        monkeypatch.setattr(r.controller, 'observe_end', lambda: (_ for _ in ()).throw(AssertionError('repoll')))
        r.now[0] += .25
        worker = threading.Thread(target=r.tracker._poll_once)
        worker.start(); worker.join(2)
        assert not worker.is_alive() and len(r.jobs) == 1
        r.jobs.pop(0)()
        assert order == [('paint', 1000), ('release', None), ('destroy', None), ('next', None)]
    finally:
        r.widget.close(); bus.shutdown()


@pytest.mark.parametrize('drift', ['generation', 'epoch', 'core'])
def test_old_cache_is_unavailable_after_native_owner_replacement(ui, drift):
    r = ui
    if drift == 'generation':
        r.core._engine_generation += 1
    elif drift == 'epoch':
        r.core._seek_slot.invalidate('reload-same-file')
    else:
        r.adapter._core = SimpleNamespace(_engine_generation=1, _seek_slot=r.core._seek_slot)
    assert r.tracker.get_progress_snapshot() is None


def test_owner_change_after_clock_before_end_never_tags_old_measurement_terminal(ui, monkeypatch):
    r = ui; read = r.controller.observe_progress
    def changed():
        clock = read()
        r.core._engine_generation += 1
        return clock
    monkeypatch.setattr(r.controller, 'observe_progress', changed)
    r.ended = True; r.position = 10.; r.now[0] += .25
    assert r.tracker._poll_once() is False
    assert not r.jobs and not r.next_calls
    assert r.tracker.get_progress_snapshot() is None


def test_stable_stop_is_not_hidden_by_failed_cached_clock(ui, monkeypatch):
    r = ui
    r.player.state_manager.update_state(PlayerState.STOPPED)
    monkeypatch.setattr(r.player, 'get_progress_snapshot', lambda: (_ for _ in ()).throw(RuntimeError('clock')))
    tick(r)
    assert r.widget.progress_slider.GetValue() == 0
    assert r.widget._display_reading.position is None
    assert r.widget._presentation._view.state is PlayerState.STOPPED


def test_final_view_retry_budget_does_not_loop_forever(ui, monkeypatch):
    r = ui; r.position = 10.; r.ended = True; r.now[0] += .25
    finish_in_worker(r)
    monkeypatch.setattr(r.player, 'get_playback_view', lambda: None)
    executions = 0
    for _ in range(8):
        if r.jobs:
            r.jobs.pop(0)(); executions += 1
        worker = threading.Thread(target=r.tracker._poll_once)
        worker.start(); worker.join(2)
        assert not worker.is_alive()
    assert executions == 4  # Original handoff and three bounded read-only retries.
    assert not r.jobs and not r.next_calls and r.controller._adapter is r.adapter


def test_old_final_retry_cannot_revive_after_seek_revision(ui, monkeypatch):
    r = ui; r.position = 10.; r.ended = True; r.now[0] += .25
    finish_in_worker(r)
    read = r.player.get_playback_view
    monkeypatch.setattr(r.player, 'get_playback_view', lambda: None)
    r.jobs.pop(0)()
    monkeypatch.setattr(r.player, 'get_playback_view', read)
    r.player.state_manager.invalidate_end_observation(); r.ended = False
    r.now[0] += .25; r.tracker._poll_once()
    assert not r.jobs and not r.next_calls
    assert not r.tracker.get_progress_snapshot().terminal
