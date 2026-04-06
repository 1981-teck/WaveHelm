from __future__ import annotations

from src.audio.audio_events import AudioEventType
from src.controller.component_player.playback_state_manager import PlaybackStateManager, PlayerState
from src.model.media_file import MediaFile, MediaType


class DummyEventBus:
    def __init__(self, fail_publish: bool = False):
        self.fail_publish = fail_publish
        self.calls = []

    def publish(self, event_type, payload):
        if self.fail_publish:
            raise RuntimeError('publish fail')
        self.calls.append((event_type, payload))


class BadTrack:
    path = 'bad-video.mp4'
    media_type = MediaType.VIDEO

    def to_dict(self):
        raise RuntimeError('serialize fail')


def test_playback_state_manager_publishes_state_and_flags():
    event_bus = DummyEventBus()
    manager = PlaybackStateManager(event_bus=event_bus)
    audio_track = MediaFile(path='song.wav', title='Song', media_type=MediaType.AUDIO, duration=42.0)

    manager.set_context([audio_track], 0, audio_track)
    assert event_bus.calls[-1][0] == AudioEventType.PLAYER_STATE_CHANGED
    assert event_bus.calls[-1][1]['current_track_path'] == 'song.wav'

    manager.update_state(PlayerState.PLAYING_AUDIO, {'source': 'unit'})
    payload = event_bus.calls[-1][1]
    assert payload['state'] == 'PLAYING_AUDIO'
    assert payload['playing'] is True
    assert payload['paused'] is False
    assert payload['is_audio'] is True
    assert payload['is_video'] is False
    assert payload['source'] == 'unit'

    assert manager.toggle_loop() is True
    assert manager.toggle_shuffle() is True
    assert event_bus.calls[-1][1]['shuffle_enabled'] is True


def test_playback_state_manager_handles_bad_context_and_serialization_failures():
    event_bus = DummyEventBus()
    manager = PlaybackStateManager(event_bus=event_bus)

    class BadSequence:
        def __iter__(self):
            raise TypeError('bad sequence')

    bad_track = BadTrack()
    manager.set_context(BadSequence(), 'bad-index', bad_track)
    payload = event_bus.calls[-1][1]
    assert manager.playlist == []
    assert manager.index == 0
    assert payload['current_track'] is None
    assert payload['path'] == 'bad-video.mp4'
    assert payload['is_video'] is True

    manager.update_state('not-a-state')
    assert event_bus.calls[-1][1]['state'] == 'IDLE'


def test_playback_state_manager_swallow_publish_failures():
    manager = PlaybackStateManager(event_bus=DummyEventBus(fail_publish=True))
    video_track = MediaFile(path='movie.mp4', title='Movie', media_type=MediaType.VIDEO, duration=10.0)

    manager.set_context([video_track], 0, video_track)
    manager.update_state(PlayerState.PLAYING_VIDEO)
    manager.set_loop(True)
    manager.set_shuffle(True)
