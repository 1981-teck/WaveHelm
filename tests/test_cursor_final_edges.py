"""Drawing-only history, owner changes and typed terminal markers at edge boundaries."""
from dataclasses import replace
from types import SimpleNamespace
import pytest
from tests.cursor_chain_fakes import ui, rig, tick
from tests.test_ui_progress_motion import motion_ui, start
from src.playback_observation import ReadingStatus


def test_transient_refusal_freezes_last_painted_not_newer_undrawn_endpoint(motion_ui):
    r = motion_ui; start(r)
    r.now[0] += .1; r.tick()
    assert r.owner.progress_slider.GetValue() == 410
    getter = r.player.get_playback_view
    r.player.get_playback_view = lambda: None
    r.now[0] += .1; r.tick()
    assert r.owner.progress_slider.GetValue() == 410
    assert r.owner._display_reading.position is None
    r.player.get_playback_view = getter
    r.now[0] += .1; r.tick()
    assert r.owner.progress_slider.GetValue() == 425


def test_controller_replaced_during_read_cannot_keep_old_owner_pixels(ui, monkeypatch):
    r = ui; original = r.player.get_playback_view
    def changed():
        view = original()
        replacement = SimpleNamespace(get_playback_view=lambda: None)
        if r.surface == 'mini':
            r.widget.player_controller = replacement
        else:
            r.widget._player_controller = replacement
        return view
    monkeypatch.setattr(r.player, 'get_playback_view', changed)
    tick(r)
    assert r.widget.progress_slider.GetValue() == 0
    assert not r.widget._presentation.input_is_current
    assert r.widget._display_reading.position is None


@pytest.mark.parametrize('invalid', [None, 0, 1, 'true'])
def test_terminal_flag_cannot_be_forged_by_truthy_scalar(ui, invalid):
    with pytest.raises(TypeError, match='terminal'):
        replace(ui.tracker.get_progress_snapshot(), terminal=invalid)


def test_old_sequence_cannot_promote_a_terminal_record(ui):
    r = ui; prior = r.tracker.get_progress_snapshot()
    r.position = 5.; r.now[0] += .25; r.tracker._poll_once(); tick(r)
    current_view = r.player.get_playback_view()
    r.player.get_playback_view = lambda: replace(current_view, sample=replace(prior, terminal=True))
    tick(r)
    assert not r.widget._display_reading.terminal
    assert r.widget.progress_slider.GetValue() != 1000
    assert not r.widget._presentation.input_is_current
