from __future__ import annotations

from pathlib import Path

from src.audio.audio_events import AudioEventType
from src.controller.component_player.engine_controller import EngineController
from src.controller.component_player.playback_state_manager import PlaybackStateManager, PlayerState
from src.controller.component_player.queue_manager import QueueManager
from src.controller.player_controller import PlayerController
from src.model.media_file import MediaFile, MediaType


class RecordingEventBus:
    def __init__(self):
        self.published = []
        self._ui_dispatcher = lambda callback: callback()

    def publish(self, event_type, payload):
        self.published.append((event_type, payload))


class DummyAudioEngine:
    def __init__(self):
        self.play_file_calls = []
        self.stop_calls = 0
        self.pause_calls = 0
        self.resume_calls = 0
        self.set_loop_calls = []
        self.set_volume_calls = []
        self.seek_calls = []

    def play_file(self, path):
        self.play_file_calls.append(path)

    def stop(self):
        self.stop_calls += 1

    def pause(self):
        self.pause_calls += 1

    def resume(self):
        self.resume_calls += 1

    def set_loop(self, value):
        self.set_loop_calls.append(bool(value))

    def set_volume(self, value):
        self.set_volume_calls.append(value)

    def seek(self, value):
        self.seek_calls.append(value)


class DummyVideoController:
    def __init__(self):
        self.stop_calls = []
        self.set_loop_calls = []
        self.set_volume_calls = []
        self.pause_calls = 0
        self.resume_calls = 0

    def stop(self, close_adapter=True):
        self.stop_calls.append(close_adapter)

    def set_loop(self, value):
        self.set_loop_calls.append(bool(value))

    def set_volume(self, value):
        self.set_volume_calls.append(value)

    def pause(self):
        self.pause_calls += 1

    def resume(self):
        self.resume_calls += 1


class DummyProgressTracker:
    def __init__(self):
        self.started = 0
        self.stopped = 0

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1

    def get_duration(self):
        return 10.0

    def get_position(self):
        return 2.5


def _track(name: str, media_type: MediaType) -> MediaFile:
    suffix = '.mp4' if media_type == MediaType.VIDEO else '.wav'
    return MediaFile(
        path=str(Path('media') / f'{name}{suffix}'),
        title=name,
        media_type=media_type,
        duration=12.0,
    )


def _build_controller():
    event_bus = RecordingEventBus()
    state = PlaybackStateManager(event_bus=event_bus)
    queue = QueueManager()
    audio = DummyAudioEngine()
    video = DummyVideoController()
    engine = EngineController(state, audio, video)
    progress = DummyProgressTracker()
    controller = PlayerController(state, queue, engine, progress, event_bus)
    return controller, state, queue, engine, audio, video, event_bus


def test_audio_playback_flow_moves_between_tracks_and_loops_current_track():
    controller, state, queue, engine, audio, video, event_bus = _build_controller()
    first = _track('first', MediaType.AUDIO)
    second = _track('second', MediaType.AUDIO)

    controller.set_playback_context([first, second], 0, autoplay=True)
    assert state.state == PlayerState.PLAYING_AUDIO
    assert state.current_track.path == first.path
    assert queue.current_track.path == first.path
    assert audio.play_file_calls == [first.path]
    assert any(event_type == AudioEventType.PLAYLIST_CHANGED for event_type, _ in event_bus.published)
    assert not any(event_type == AudioEventType.PREPARE_VIDEO_PLAYBACK for event_type, _ in event_bus.published)

    controller.next()
    assert state.current_track.path == second.path
    assert queue.current_track.path == second.path
    assert audio.play_file_calls[-1] == second.path
    assert audio.stop_calls >= 1

    controller.toggle_loop()
    before_replay_calls = len(audio.play_file_calls)
    controller._handle_track_end()
    assert len(audio.play_file_calls) == before_replay_calls + 1
    assert audio.play_file_calls[-1] == first.path
    assert state.current_track.path == first.path
    assert state.loop_enabled is True
    assert engine.state_manager.state == PlayerState.PLAYING_AUDIO

    controller.play_action(second)
    queue._index = 1
    state.set_context(queue.playlist, queue.index, second)
    audio._current_play_uses_native_loop = True
    before_replay_calls = len(audio.play_file_calls)
    controller._handle_track_end()
    assert len(audio.play_file_calls) == before_replay_calls + 1
    assert audio.play_file_calls[-1] == second.path
    assert state.current_track.path == second.path


def test_switching_from_audio_to_video_keeps_context_and_requests_video_prepare():
    controller, state, queue, _engine, audio, video, event_bus = _build_controller()
    audio_track = _track('song', MediaType.AUDIO)
    video_track = _track('clip', MediaType.VIDEO)

    controller.set_playback_context([audio_track], 0, autoplay=True)
    audio_stop_before = audio.stop_calls

    controller.set_playback_context([video_track], 0, autoplay=True)

    assert queue.current_track.path == video_track.path
    assert state.current_track.path == video_track.path
    assert state.state == PlayerState.LOADING
    assert audio.stop_calls > audio_stop_before
    assert any(event_type == AudioEventType.PREPARE_VIDEO_PLAYBACK and payload.get('path') == video_track.path for event_type, payload in event_bus.published)
    assert state.is_video() is True
