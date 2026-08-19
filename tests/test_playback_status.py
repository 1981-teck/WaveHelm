from __future__ import annotations

from types import SimpleNamespace

from src.controller import playback_status
from src.model.media_file import MediaType


class ExplodingProperty:
    def __get__(self, instance, owner):
        raise RuntimeError('boom')


class BrokenCurrentTrackPlayer:
    current_track = ExplodingProperty()

    def __init__(self):
        self.queue_manager = SimpleNamespace(current_track=None)
        self._current_track = None


class DummyAudioEngine:
    def __init__(self):
        self.calls = []
        self.length_value = None
        self.duration_value = None
        self.pos_value = None

    def get_length(self):
        self.calls.append('get_length')
        return self.length_value

    def get_duration(self):
        self.calls.append('get_duration')
        return self.duration_value

    def get_pos(self):
        self.calls.append('get_pos')
        return self.pos_value

    def seek(self, value):
        self.calls.append(('seek', value))


class DummyVideoController:
    def __init__(self):
        self.calls = []
        self.duration_value = None
        self.position_value = None
        self.seek_result = True

    def get_duration(self):
        self.calls.append('get_duration')
        return self.duration_value

    def get_position(self):
        self.calls.append('get_position')
        return self.position_value

    def seek(self, value):
        self.calls.append(('seek', value))
        return self.seek_result


class DummyPlayer:
    def __init__(self, track=None, audio_engine=None, video_controller=None):
        self.current_track = track
        self.queue_manager = SimpleNamespace(current_track=None)
        self._current_track = None
        self.audio_engine = audio_engine
        self.video_controller = video_controller
        self.engine_controller = SimpleNamespace(audio_engine=audio_engine, video_controller=video_controller)



def test_get_current_track_falls_back_through_sources():
    track = SimpleNamespace(media_type=MediaType.AUDIO)
    player = DummyPlayer(track=None)
    player.queue_manager.current_track = track
    assert playback_status._get_current_track(player) is track

    player.queue_manager.current_track = None
    player._current_track = track
    assert playback_status._get_current_track(player) is track

    broken = BrokenCurrentTrackPlayer()
    assert playback_status._get_current_track(broken) is None



def test_is_video_current_reads_media_type_safely():
    assert playback_status.is_video_current(DummyPlayer(track=SimpleNamespace(media_type=MediaType.VIDEO))) is True
    assert playback_status.is_video_current(DummyPlayer(track=SimpleNamespace(media_type=MediaType.AUDIO))) is False
    assert playback_status.is_video_current(BrokenCurrentTrackPlayer()) is False



def test_get_duration_prefers_track_then_player_then_video_then_audio():
    track = SimpleNamespace(media_type=MediaType.AUDIO, duration=12.5)
    player = DummyPlayer(track=track)
    assert playback_status.get_duration(player) == 12.5

    player = DummyPlayer(track=SimpleNamespace(media_type=MediaType.AUDIO, duration=0))
    player.get_duration = lambda: 22.0
    assert playback_status.get_duration(player) == 22.0

    video = DummyVideoController()
    video.duration_value = 33.0
    player = DummyPlayer(track=SimpleNamespace(media_type=MediaType.VIDEO, duration=0), video_controller=video)
    assert playback_status.get_duration(player) == 33.0

    audio = DummyAudioEngine()
    audio.length_value = 44.0
    player = DummyPlayer(track=SimpleNamespace(media_type=MediaType.AUDIO, duration=0), audio_engine=audio)
    assert playback_status.get_duration(player) == 44.0



def test_get_position_prefers_player_then_video_then_audio():
    player = DummyPlayer(track=SimpleNamespace(media_type=MediaType.AUDIO))
    player.get_position = lambda: 5.0
    assert playback_status.get_position(player) == 5.0

    video = DummyVideoController()
    video.position_value = 6.5
    player = DummyPlayer(track=SimpleNamespace(media_type=MediaType.VIDEO), video_controller=video)
    assert playback_status.get_position(player) == 6.5

    audio = DummyAudioEngine()
    audio.pos_value = 7.5
    player = DummyPlayer(track=SimpleNamespace(media_type=MediaType.AUDIO), audio_engine=audio)
    assert playback_status.get_position(player) == 7.5



def test_seek_to_uses_video_without_audio_fallback_and_audio_for_audio_tracks():
    video = DummyVideoController()
    video.seek_result = False
    audio = DummyAudioEngine()
    player = DummyPlayer(track=SimpleNamespace(media_type=MediaType.VIDEO), audio_engine=audio, video_controller=video)
    assert playback_status.seek_to(player, 9.0) is False
    assert audio.calls == []
    assert video.calls == [('seek', 9.0)]

    audio = DummyAudioEngine()
    player = DummyPlayer(track=SimpleNamespace(media_type=MediaType.AUDIO), audio_engine=audio)
    assert playback_status.seek_to(player, -4.0) is True
    assert audio.calls == [('seek', 0.0)]



def test_failures_return_none_or_false_cleanly():
    audio = DummyAudioEngine()
    audio.get_length = lambda: (_ for _ in ()).throw(RuntimeError('len fail'))
    audio.get_duration = lambda: (_ for _ in ()).throw(RuntimeError('dur fail'))
    audio.get_pos = lambda: (_ for _ in ()).throw(RuntimeError('pos fail'))
    audio.seek = lambda value: (_ for _ in ()).throw(RuntimeError('seek fail'))
    player = DummyPlayer(track=SimpleNamespace(media_type=MediaType.AUDIO, duration=0), audio_engine=audio)

    assert playback_status.get_duration(player) is None
    assert playback_status.get_position(player) is None
    assert playback_status.seek_to(player, 'bad') is False
    assert playback_status.seek_to(player, 10.0) is False
