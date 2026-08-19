from __future__ import annotations

from types import SimpleNamespace

from src.audio.audio_events import AudioEventType
from src.controller.component_player.playback_state_manager import PlayerState
from src.controller.component_player.progress_tracker import ProgressTracker


class DummyStateManager:
    def __init__(self, state: PlayerState, *, playing: bool = True, loop: bool = False):
        self.state = state
        self._playing = playing
        self._loop = loop

    def is_playing(self):
        return self._playing


class DummyQueueManager:
    def __init__(self, path: str = 'song.wav'):
        self.current_track = SimpleNamespace(path=path)


class ControlledStopEvent:
    def __init__(self, break_after: int = 2):
        self.break_after = break_after
        self.calls = 0
        self.flag = False

    def wait(self, timeout):
        self.calls += 1
        if self.calls >= self.break_after:
            self.flag = True
            return True
        return False

    def is_set(self):
        return self.flag

    def set(self):
        self.flag = True

    def clear(self):
        self.flag = False


def test_get_duration_and_position_handle_audio_video_and_failures():
    tracker = ProgressTracker(
        DummyStateManager(PlayerState.PLAYING_AUDIO),
        DummyQueueManager(),
        SimpleNamespace(
            audio_engine=SimpleNamespace(get_duration=lambda: 3.5, get_position=lambda: (_ for _ in ()).throw(ValueError('bad pos'))),
            video_controller=SimpleNamespace(get_duration=lambda: 8.0, get_position=lambda: 2.0),
        ),
        lambda *_args, **_kwargs: None,
        lambda: None,
    )

    assert tracker.get_duration() == 3.5
    assert tracker.get_position() == 0.0

    tracker.state_manager.state = PlayerState.PLAYING_VIDEO
    assert tracker.get_duration() == 8.0
    assert tracker.get_position() == 2.0


def test_publish_progress_clamps_and_handles_publisher_failure():
    calls = []
    tracker = ProgressTracker(
        DummyStateManager(PlayerState.PLAYING_AUDIO),
        DummyQueueManager('track.flac'),
        SimpleNamespace(audio_engine=None, video_controller=None),
        lambda event_type, payload: calls.append((event_type, payload)),
        lambda: None,
    )

    tracker._publish_progress(15.0, 10.0)
    assert calls[-1][0] == AudioEventType.PLAYBACK_PROGRESS
    assert calls[-1][1]['progress_percent'] == 100.0
    assert calls[-1][1]['path'] == 'track.flac'

    tracker._publish_event = lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError('boom'))
    tracker._publish_progress(5.0, 10.0)


def test_poll_worker_handles_video_end_and_callback_failure():
    callback_calls = []

    def failing_callback():
        callback_calls.append('called')
        raise RuntimeError('boom')

    tracker = ProgressTracker(
        DummyStateManager(PlayerState.PLAYING_VIDEO),
        DummyQueueManager(),
        SimpleNamespace(video_controller=SimpleNamespace(poll_end=lambda: True, get_duration=lambda: 10.0, get_position=lambda: 9.9)),
        lambda *_args, **_kwargs: None,
        failing_callback,
    )
    tracker._polling_active = True
    tracker._stop_event = ControlledStopEvent(break_after=2)

    tracker._poll_worker()

    assert callback_calls == ['called']
    assert tracker._stop_event.is_set() is True


def test_poll_worker_tolerates_publish_failures_for_audio_progress():
    tracker = ProgressTracker(
        DummyStateManager(PlayerState.PLAYING_AUDIO),
        DummyQueueManager(),
        SimpleNamespace(audio_engine=SimpleNamespace(get_duration=lambda: 10.0, get_position=lambda: 5.0), video_controller=None),
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError('publish failed')),
        lambda: None,
    )
    tracker._polling_active = True
    tracker._stop_event = ControlledStopEvent(break_after=2)

    tracker._poll_worker()

    assert tracker._stop_event.is_set() is True


def test_poll_worker_restarts_current_track_when_loop_enabled_but_current_play_is_not_native():
    callback_calls = []
    tracker = ProgressTracker(
        DummyStateManager(PlayerState.PLAYING_AUDIO, loop=True),
        DummyQueueManager(),
        SimpleNamespace(
            audio_engine=SimpleNamespace(
                get_duration=lambda: 10.0,
                get_position=lambda: 9.9,
                _current_play_uses_native_loop=False,
            ),
            video_controller=None,
        ),
        lambda *_args, **_kwargs: None,
        lambda: callback_calls.append('ended'),
    )
    tracker._polling_active = True
    tracker._stop_event = ControlledStopEvent(break_after=2)

    tracker._poll_worker()

    assert callback_calls == ['ended']


def test_poll_worker_suppresses_track_end_when_native_audio_loop_is_active():
    callback_calls = []
    tracker = ProgressTracker(
        DummyStateManager(PlayerState.PLAYING_AUDIO, loop=True),
        DummyQueueManager(),
        SimpleNamespace(
            audio_engine=SimpleNamespace(
                get_duration=lambda: 10.0,
                get_position=lambda: 9.9,
                _current_play_uses_native_loop=True,
            ),
            video_controller=None,
        ),
        lambda *_args, **_kwargs: None,
        lambda: callback_calls.append('ended'),
    )
    tracker._polling_active = True
    tracker._stop_event = ControlledStopEvent(break_after=2)

    tracker._poll_worker()

    assert callback_calls == []
    assert tracker._stop_event.is_set() is True


def test_poll_worker_keeps_native_audio_loop_even_if_toggle_was_disabled_mid_track():
    callback_calls = []
    tracker = ProgressTracker(
        DummyStateManager(PlayerState.PLAYING_AUDIO, loop=False),
        DummyQueueManager(),
        SimpleNamespace(
            audio_engine=SimpleNamespace(
                get_duration=lambda: 10.0,
                get_position=lambda: 9.9,
                _current_play_uses_native_loop=True,
            ),
            video_controller=None,
        ),
        lambda *_args, **_kwargs: None,
        lambda: callback_calls.append('ended'),
    )
    tracker._polling_active = True
    tracker._stop_event = ControlledStopEvent(break_after=2)

    tracker._poll_worker()

    assert callback_calls == []
    assert tracker._stop_event.is_set() is True
