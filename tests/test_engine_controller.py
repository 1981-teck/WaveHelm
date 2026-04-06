from __future__ import annotations

from types import SimpleNamespace

from src.controller.component_player.engine_controller import EngineController
from src.controller.component_player.playback_state_manager import PlayerState
from src.model.media_file import MediaType


class DummyStateManager:
    def __init__(self, state=PlayerState.STOPPED):
        self.state = state
        self.update_calls = []
        self.is_video_value = False

    def update_state(self, state, payload=None):
        self.state = state
        self.update_calls.append((state, payload))

    def is_stopped(self):
        return self.state == PlayerState.STOPPED

    def is_video(self):
        return self.is_video_value


class DummyAudioEngine:
    def __init__(self):
        self.calls = []

    def play_file(self, path):
        self.calls.append(('play_file', path))

    def play(self, *args):
        self.calls.append(('play', args))

    def set_loop(self, value):
        self.calls.append(('set_loop', value))

    def pause(self):
        self.calls.append(('pause', None))

    def resume(self):
        self.calls.append(('resume', None))

    def stop(self):
        self.calls.append(('stop', None))

    def seek(self, value):
        self.calls.append(('seek', value))

    def set_volume(self, value):
        self.calls.append(('set_volume', value))




class DummyAudioEnginePlayReturnsFalse(DummyAudioEngine):
    def play_file(self, path):
        self.calls.append(('play_file', path))
        return False


class DummyAudioEngineSetFileFails(DummyAudioEngine):
    def set_file(self, path):
        self.calls.append(('set_file', path))
        return False


class DummyVideoController:
    def __init__(self):
        self.calls = []

    def set_loop(self, value):
        self.calls.append(('set_loop', value))

    def pause(self):
        self.calls.append(('pause', None))

    def resume(self):
        self.calls.append(('resume', None))

    def stop(self, close_adapter=True):
        self.calls.append(('stop', close_adapter))

    def shutdown(self):
        self.calls.append(('shutdown', None))

    def close(self):
        self.calls.append(('close', None))

    def seek(self, value):
        self.calls.append(('seek', value))

    def set_volume(self, value):
        self.calls.append(('set_volume', value))

    def ensure_video_adapter(self):
        self.calls.append(('ensure_video_adapter', None))

    def play_media(self, *args, **kwargs):
        self.calls.append(('play_media', args, kwargs))


class FactoryError(RuntimeError):
    pass



def _media(path, media_type):
    return SimpleNamespace(path=path, media_type=media_type)



def test_play_audio_starts_engine_and_updates_state():
    state = DummyStateManager()
    audio = DummyAudioEngine()
    video = DummyVideoController()
    controller = EngineController(state, audio, video)

    result = controller.play(_media('song.mp3', MediaType.AUDIO), loop=True)

    assert result is True
    assert audio.calls[:3] == [('stop', None), ('set_loop', True), ('play_file', 'song.mp3')]
    assert state.update_calls[-1] == (PlayerState.PLAYING_AUDIO, None)



def test_play_video_ensures_controller_and_enters_loading():
    state = DummyStateManager(PlayerState.PLAYING_VIDEO)
    audio = DummyAudioEngine()
    video = DummyVideoController()
    controller = EngineController(state, audio, video)

    result = controller.play(_media('movie.mp4', MediaType.VIDEO), loop=False)

    assert result is True
    assert state.state == PlayerState.LOADING



def test_play_video_uses_factory_when_controller_missing():
    state = DummyStateManager()
    audio = DummyAudioEngine()
    created = DummyVideoController()
    controller = EngineController(state, audio, None, video_controller_factory=lambda: created)

    result = controller.play(_media('movie.mp4', MediaType.VIDEO), loop=True)

    assert result is True
    assert controller.video_controller is created
    assert state.state == PlayerState.LOADING



def test_play_video_factory_error_sets_error_state():
    state = DummyStateManager()
    audio = DummyAudioEngine()
    controller = EngineController(state, audio, None, video_controller_factory=lambda: (_ for _ in ()).throw(FactoryError('boom')))

    result = controller.play(_media('movie.mp4', MediaType.VIDEO), loop=True)

    assert result is False
    assert state.update_calls[-1] == (PlayerState.ERROR, {'message': 'boom'})



def test_pause_and_resume_audio_and_video():
    state = DummyStateManager(PlayerState.PLAYING_AUDIO)
    audio = DummyAudioEngine()
    video = DummyVideoController()
    controller = EngineController(state, audio, video)

    controller.pause()
    assert ('pause', None) in audio.calls
    assert state.state == PlayerState.PAUSED_AUDIO

    controller.resume()
    assert ('resume', None) in audio.calls
    assert state.state == PlayerState.PLAYING_AUDIO

    state.state = PlayerState.PLAYING_VIDEO
    controller.pause()
    assert ('pause', None) in video.calls
    assert state.state == PlayerState.PAUSED_VIDEO

    controller.resume()
    assert ('resume', None) in video.calls
    assert state.state == PlayerState.PLAYING_VIDEO



def test_stop_stops_audio_video_and_sets_stopped():
    state = DummyStateManager(PlayerState.PLAYING_VIDEO)
    audio = DummyAudioEngine()
    video = DummyVideoController()
    controller = EngineController(state, audio, video)

    controller.stop()

    assert ('stop', None) in audio.calls
    assert ('stop', True) in video.calls
    assert state.state == PlayerState.STOPPED



def test_seek_routes_to_active_engine_and_ignores_loading():
    state = DummyStateManager(PlayerState.PLAYING_AUDIO)
    audio = DummyAudioEngine()
    video = DummyVideoController()
    controller = EngineController(state, audio, video)

    controller.seek(12.5)
    assert ('seek', 12.5) in audio.calls

    state.state = PlayerState.PLAYING_VIDEO
    controller.seek(30.0)
    assert ('seek', 30.0) in video.calls

    state.state = PlayerState.LOADING
    before = len(video.calls)
    controller.seek(99.0)
    assert len(video.calls) == before



def test_set_volume_clamps_and_updates_video_when_state_is_video():
    state = DummyStateManager(PlayerState.PLAYING_VIDEO)
    state.is_video_value = True
    audio = DummyAudioEngine()
    video = DummyVideoController()
    controller = EngineController(state, audio, video)

    controller.set_volume(2.5)
    controller.set_volume('bad')

    assert ('set_volume', 1.0) in audio.calls
    assert ('set_volume', 1.0) in video.calls



def test_shutdown_is_idempotent_and_calls_stop_once():
    state = DummyStateManager(PlayerState.PLAYING_AUDIO)
    audio = DummyAudioEngine()
    video = DummyVideoController()
    controller = EngineController(state, audio, video)

    controller.shutdown()
    controller.shutdown()

    stop_calls = [call for call in audio.calls if call[0] == 'stop']
    assert len(stop_calls) == 1
    assert controller._is_shutting_down is True


def test_play_audio_fails_when_engine_returns_false_without_exception():
    state = DummyStateManager()
    audio = DummyAudioEnginePlayReturnsFalse()
    video = DummyVideoController()
    controller = EngineController(state, audio, video)

    result = controller.play(_media('broken.mp3', MediaType.AUDIO), loop=False)

    assert result is False
    assert ('play_file', 'broken.mp3') in audio.calls
    assert state.state == PlayerState.ERROR
    assert state.update_calls[-1] == (
        PlayerState.ERROR,
        {'message': 'Audio playback did not start correctly.'},
    )
    assert (PlayerState.PLAYING_AUDIO, None) not in state.update_calls


def test_play_audio_fails_closed_when_set_file_rejects_media():
    state = DummyStateManager()
    audio = DummyAudioEngineSetFileFails()
    video = DummyVideoController()
    controller = EngineController(state, audio, video)

    result = controller.play(_media('invalid.mp3', MediaType.AUDIO), loop=True)

    assert result is False
    assert ('set_loop', True) in audio.calls
    assert ('set_file', 'invalid.mp3') in audio.calls
    assert not any(call[0] == 'play' for call in audio.calls)
    assert state.state == PlayerState.ERROR
    assert state.update_calls[-1] == (
        PlayerState.ERROR,
        {'message': 'Audio playback did not start correctly.'},
    )
    assert (PlayerState.PLAYING_AUDIO, None) not in state.update_calls
