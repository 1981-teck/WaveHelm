"""Cache-only facade consumer contracts; no native services are substituted globally."""
from dataclasses import replace
from types import SimpleNamespace
import time

import pytest

from src.controller.playback_view import PlaybackView
from src.controller.player_controller import PlayerController
from src.controller.component_player.playback_state_manager import PlayerState, PlaybackStateManager
from src.model.media_file import MediaType
from src.playback_observation import ClockObservation, ClockValue, ClockOrigin, ProgressSnapshot


def make_source(state=PlayerState.PLAYING_VIDEO):
    manager = PlaybackStateManager()
    track = SimpleNamespace(path='clip.mp4', title='Clip', media_type=MediaType.VIDEO)
    manager.set_context([track], 0, track)
    manager.update_state(state)
    queue = SimpleNamespace(current_track=track, index=0)
    tracker = SimpleNamespace(get_progress_snapshot=lambda: None)
    player = PlayerController(manager, queue, None, tracker, None)
    return player


def make_sample(player, position=4.0, duration=10.0, sequence=1, stamp=None, stream='stream:1'):
    now = time.monotonic() if stamp is None else stamp
    clock = ClockObservation(ClockValue.read(position), ClockValue.read(duration, duration=True),
                             now, now, ClockOrigin.VIDEO_NATIVE, player.current_track.path, 1)
    return ProgressSnapshot(stream, sequence, player.state_manager.playback_revision,
                            player.state_manager.state.name, player.current_track.path,
                            player.queue_manager.index, clock)


def test_view_never_queries_native_scalar_getters():
    player = make_source()
    sample = make_sample(player)
    player.progress_tracker.get_progress_snapshot = lambda: sample
    def forbidden():
        raise AssertionError('GUI called a native getter')
    player.get_position = player.get_duration = forbidden
    view = player.get_playback_view()
    assert view.sample is sample and view.title == 'Clip'


@pytest.mark.parametrize('field,value', [('revision', 77), ('path', 'old.mp4'),
                                        ('index', 3), ('state', 'PAUSED_VIDEO')])
def test_mismatched_sample_refused(field, value):
    player = make_source()
    sample = replace(make_sample(player), **{field:value})
    player.progress_tracker.get_progress_snapshot = lambda: sample
    assert player.get_playback_view() is None


@pytest.mark.parametrize('mutation', ['seek', 'state', 'queue', 'shutdown', 'title'])
def test_mutation_during_cache_read_refused(mutation):
    player = make_source()
    sample = make_sample(player)
    def change():
        if mutation == 'seek': player.state_manager.invalidate_end_observation()
        if mutation == 'state': player.state_manager.update_state(PlayerState.PAUSED_VIDEO)
        if mutation == 'queue': player.queue_manager.current_track = SimpleNamespace(path='next.mp4')
        if mutation == 'shutdown': player._is_shutdown = True
        if mutation == 'title': player.current_track.title = 'New title'
        return sample
    player.progress_tracker.get_progress_snapshot = change
    assert player.get_playback_view() is None


@pytest.mark.parametrize('state', list(PlayerState))
def test_empty_cache_keeps_current_state_without_inventing_position(state):
    player = make_source(state)
    view = player.get_playback_view()
    assert view.state is state and view.sample is None


@pytest.mark.parametrize('value', [object(), {}, 0, False])
def test_advertised_cache_invalid_result_refused(value):
    player = make_source()
    player.progress_tracker.get_progress_snapshot = lambda: value
    assert player.get_playback_view() is None


def test_shutdown_refuses_even_with_a_cached_sample():
    player = make_source(); player._is_shutdown = True
    assert player.get_playback_view() is None


def test_view_rejects_foreign_clock_source():
    player = make_source(); sample = make_sample(player)
    sample = replace(sample, clock=replace(sample.clock, source='other.mp4'))
    player.progress_tracker.get_progress_snapshot = lambda: sample
    assert player.get_playback_view() is None


@pytest.mark.parametrize('field,value', [('revision',True), ('revision',-1), ('index',False),
 ('state','PLAYING_VIDEO'),('title',42),('loop',1),('path',''),('path','x\0y')])
def test_invalid_view_fields_refused(field,value):
    player=make_source(); view=player.get_playback_view()
    with pytest.raises((ValueError, TypeError)):
        replace(view, **{field:value})


@pytest.mark.parametrize('title', [None, ''])
def test_blank_title_uses_filename_without_native_or_filesystem_query(title):
    player = make_source(); player.current_track.title = title
    assert player.get_playback_view().title == 'clip'
