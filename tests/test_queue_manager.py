from __future__ import annotations

from types import SimpleNamespace

from src.controller.component_player.queue_manager import QueueManager
from src.model.media_file import MediaFile, MediaType


def _track(index: int) -> MediaFile:
    return MediaFile(
        path=f'track_{index}.wav',
        title=f'Track {index}',
        media_type=MediaType.AUDIO,
        duration=10.0 + index,
    )


def test_queue_manager_set_context_clamps_and_wraps_without_shuffle():
    manager = QueueManager()
    items = [_track(0), _track(1), _track(2)]

    current = manager.set_context(items, 99)
    assert current.path == 'track_2.wav'
    assert manager.index == 2
    assert manager.next().path == 'track_0.wav'
    assert manager.previous().path == 'track_2.wav'


def test_queue_manager_shuffle_history_and_previous_navigation():
    manager = QueueManager()
    items = [_track(0), _track(1), _track(2), _track(3)]
    manager.set_context(items, 1)
    manager._rng.seed(123)
    manager.set_shuffle(True)

    visited = [manager.next().path for _ in range(3)]
    assert set(visited) == {'track_0.wav', 'track_2.wav', 'track_3.wav'}
    assert len(set(visited)) == 3

    previous_track = manager.previous()
    assert previous_track.path == visited[-2]


def test_queue_manager_avoids_repeating_previous_shuffle_cycle_when_possible(monkeypatch):
    manager = QueueManager()
    manager._playlist = [_track(0), _track(1), _track(2), _track(3)]
    manager._last_cycle_order = [0, 1, 2, 3]

    monkeypatch.setattr(manager._rng, 'shuffle', lambda seq: None)
    cycle = manager._build_shuffle_cycle(exclude_first=0)

    assert cycle != [0, 1, 2, 3]
    assert cycle[0] != 0


def test_queue_manager_get_playlist_info_handles_bad_track_serialization():
    manager = QueueManager()
    bad_track = SimpleNamespace(path='broken.wav', to_dict=lambda: (_ for _ in ()).throw(RuntimeError('fail')))
    manager._playlist = [bad_track]
    manager._index = 0

    info = manager.get_playlist_info()
    assert info['current_index'] == 0
    assert info['total_tracks'] == 1
    assert info['current_track'] is None
