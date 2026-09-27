"""Current-cache presentation contracts with explicit scheduling/clock boundaries."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.controller.playback_view import PlaybackView
from src.controller.component_player.playback_state_manager import PlayerState
from src.playback_observation import ClockValue, ReadingStatus
from src.ui_wx.playback_presentation import PlaybackPresentation
from tests.test_playback_view import make_source, make_sample


def setup_presentation():
    player = make_source()
    slot = [make_sample(player, stamp=10.0)]
    player.progress_tracker.get_progress_snapshot = lambda: slot[0]
    jobs, renders, now = [], [], [10.0]
    presentation = PlaybackPresentation(lambda: player, lambda *args: renders.append(args),
                                        jobs.append, lambda: now[0])
    return player, slot, jobs, renders, now, presentation


def test_current_cache_wins_over_any_number_of_late_wakeups():
    player, slot, jobs, renders, now, p = setup_presentation()
    assert p.request()
    for _ in range(40):
        assert not p.request()
    assert len(jobs) == 1
    slot[0] = make_sample(player, position=6.0, sequence=2, stamp=10.0)
    p.refresh()
    jobs.pop()()
    assert [reading.position for _, reading in renders] == [6.0]


def test_cache_reader_never_uses_scalar_or_legacy_state_getters():
    player, slot, jobs, renders, now, p = setup_presentation()
    def forbidden():
        raise AssertionError('Native/legacy poll from presentation')
    player.get_position = player.get_duration = forbidden
    player.get_current_player_state_payload = forbidden
    p.refresh()
    assert renders[-1][1].ratio == .4


@pytest.mark.parametrize('position', [0.0, .01, 2.0])
def test_new_seek_revision_accepts_valid_backwards_and_zero(position):
    player, slot, jobs, renders, now, p = setup_presentation(); p.refresh()
    player.state_manager.invalidate_end_observation()
    slot[0] = make_sample(player, position=position, sequence=2, stamp=10.0)
    p.refresh()
    assert renders[-1][1].position == position


def test_switching_path_resets_both_values_without_old_metadata():
    player, slot, jobs, renders, now, p = setup_presentation(); p.refresh()
    track = SimpleNamespace(path='new.mp4', title='New')
    player.queue_manager.current_track = track
    player.state_manager.set_context([track], 0, track)
    slot[0] = None
    p.refresh()
    view, reading = renders[-1]
    assert view.path == 'new.mp4' and reading.position is None and reading.duration is None


@pytest.mark.parametrize('field', ['position', 'duration'])
@pytest.mark.parametrize('status', [ReadingStatus.ERROR, ReadingStatus.UNAVAILABLE, ReadingStatus.STALE])
def test_unknown_retains_only_complete_old_pair_with_nonfresh_status(field, status):
    player, slot, jobs, renders, now, p = setup_presentation(); p.refresh()
    next_sample = make_sample(player, position=9.0, duration=20.0, sequence=2, stamp=10.0)
    unknown = ClockValue(status)
    slot[0] = replace(next_sample, clock=replace(next_sample.clock, **{field: unknown}))
    p.refresh()
    reading = renders[-1][1]
    assert (reading.position, reading.duration, reading.status) == (4.0, 10.0, status)


@pytest.mark.parametrize('age', [1.01, 4.0, -0.1])
def test_expired_or_future_observation_is_not_displayed_as_fresh(age):
    player, slot, jobs, renders, now, p = setup_presentation(); p.refresh()
    now[0] += age
    p.refresh()
    assert renders[-1][1].status is ReadingStatus.STALE
    assert renders[-1][1].position == 4.0


def test_pause_retains_coherent_pair_but_seek_during_pause_invalidates_it():
    player, slot, jobs, renders, now, p = setup_presentation(); p.refresh()
    player.state_manager.update_state(PlayerState.PAUSED_VIDEO); slot[0] = None
    p.refresh()
    assert renders[-1][1].position == 4.0
    assert renders[-1][1].status is ReadingStatus.UNAVAILABLE
    player.state_manager.invalidate_end_observation(); p.refresh()
    assert renders[-1][1].position is None


@pytest.mark.parametrize('state', [PlayerState.IDLE, PlayerState.STOPPED,
                                    PlayerState.LOADING, PlayerState.ERROR])
def test_inactive_state_clears_pair_even_if_cache_has_an_observation(state):
    player, slot, jobs, renders, now, p = setup_presentation(); p.refresh()
    player.state_manager.update_state(state)
    slot[0] = make_sample(player, stamp=10.0); p.refresh()
    assert renders[-1][1].position is None and renders[-1][1].duration is None


@pytest.mark.parametrize('change', ['old_sequence', 'same_sequence_new_numbers', 'earlier_acquisition', 'foreign_epoch'])
def test_old_sample_cannot_replace_newer_numbers(change):
    player, slot, jobs, renders, now, p = setup_presentation()
    slot[0] = make_sample(player, sequence=5, stamp=10.0); p.refresh()
    seq = 4 if change == 'old_sequence' else 6 if change == 'earlier_acquisition' else 5
    slot[0] = make_sample(player, position=1.0, sequence=seq,
                          stamp=9.9 if change == 'earlier_acquisition' else 10.0,
                          stream='old:1' if change == 'foreign_epoch' else 'stream:1')
    p.refresh()
    assert renders[-1][1].position == 4.0


def test_expiry_invalidation_same_sequence_can_remove_validity_not_change_numbers():
    player, slot, jobs, renders, now, p = setup_presentation(); p.refresh()
    unknown = ClockValue(ReadingStatus.STALE)
    slot[0] = replace(slot[0], clock=replace(slot[0].clock, position=unknown, duration=unknown))
    p.refresh()
    assert renders[-1][1].status is ReadingStatus.STALE


def test_close_rejects_pending_delivery_and_future_reads():
    player, slot, jobs, renders, now, p = setup_presentation()
    p.request(); p.close(); jobs.pop()(); p.refresh()
    assert not p.request() and not renders


def test_controller_replacement_permits_its_own_lower_revision():
    player, slot, jobs, renders, now, p = setup_presentation()
    current = [player]; p._source_reader = lambda: current[0]
    player.state_manager.invalidate_end_observation()
    slot[0] = make_sample(player, position=8.0, stamp=10.0); p.refresh()
    other = make_source(); other.progress_tracker.get_progress_snapshot = lambda: make_sample(other, position=1.0, stamp=10.0)
    current[0] = other; p.refresh()
    assert renders[-1][1].position == 1.0


@pytest.mark.parametrize('bad', [None, False, {}, 42])
def test_bad_optional_reader_never_uses_legacy_fallback(bad):
    player, slot, jobs, renders, now, p = setup_presentation(); p.refresh()
    player.get_playback_view = lambda: bad
    p.refresh()
    assert renders[-1][1].position is None


def test_read_failure_is_unknown_and_recovers_on_next_fresh_view():
    player, slot, jobs, renders, now, p = setup_presentation(); p.refresh()
    reader = player.get_playback_view
    def failed(): raise RuntimeError('closed reader')
    player.get_playback_view = failed; p.refresh()
    assert renders[-1][0] is not None  # Last title/identity is display history only.
    assert renders[-1][1].position is None
    assert not p.input_is_current
    player.get_playback_view = reader; p.refresh()
    assert renders[-1][1].position == 4.0


def test_no_dispatcher_does_not_render_on_bus_thread():
    player, slot, jobs, renders, now, p = setup_presentation(); p._dispatch = None
    assert not p.request() and not renders
    p.refresh(); assert renders[-1][1].position == 4.0


def test_dispatch_error_releases_pending_slot():
    player, slot, jobs, renders, now, p = setup_presentation()
    def failed(callback): raise RuntimeError('event loop closed')
    p._dispatch = failed; assert not p.request()
    p._dispatch = jobs.append; assert p.request() and len(jobs) == 1


def test_reentrant_signal_during_render_queues_only_one_extra_refresh():
    player, slot, jobs, renders, now, p = setup_presentation()
    def render(*args):
        renders.append(args)
        p.request(); p.request()
    p._render = render; p.request(); jobs.pop(0)()
    assert len(jobs) == 1
    jobs.pop(0)()
    assert len(renders) == 1 and not jobs


def test_ignored_drag_render_is_not_marked_as_painted():
    player, slot, jobs, renders, now, p = setup_presentation()
    drag = [True]
    def render(*args):
        if drag[0]: return False
        renders.append(args)
        return True
    p._render = render; p.refresh()
    assert not renders
    drag[0] = False; p.refresh()
    assert renders[-1][1].position == 4.0


def test_null_read_does_not_forget_the_minimum_current_revision():
    player, slot, jobs, renders, now, p = setup_presentation()
    older = player.get_playback_view()
    player.state_manager.invalidate_end_observation()
    slot[0] = make_sample(player, position=1.0, stamp=10.0); p.refresh()
    player.get_playback_view = lambda: None; p.refresh()
    player.get_playback_view = lambda: older; p.refresh()
    assert renders[-1][0] is not None  # Last title/identity is display history only.
    assert renders[-1][1].position is None
    assert not p.input_is_current


def test_close_during_render_does_not_restore_painted_state():
    player, slot, jobs, renders, now, p = setup_presentation()
    p._render = lambda *args: p.close()
    p.refresh()
    assert p._painted is None and not p.request()
