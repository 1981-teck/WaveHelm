from __future__ import annotations

from types import SimpleNamespace

from src.audio.audio_events import AudioEventType
from src.controller.component_player.player_event_handler_video import attach_player_event_handler_video_behavior
from src.controller.component_player.playback_state_manager import PlayerState


class DummyEventBus:
    def __init__(self, fail_publish=False):
        self.fail_publish = fail_publish
        self.calls = []

    def publish(self, event_type, payload, require_ui_thread=False):
        if self.fail_publish:
            raise ValueError('publish fail')
        self.calls.append((event_type, payload, require_ui_thread))


class DummyStateManager:
    def __init__(self, state=PlayerState.STOPPED):
        self.state = state
        self.updates = []

    def update_state(self, state, payload=None):
        self.state = state
        self.updates.append((state, payload))

    def is_video(self):
        return self.state in (PlayerState.PLAYING_VIDEO, PlayerState.PAUSED_VIDEO, PlayerState.LOADING)


class DummyVideoController:
    def __init__(self, *, ensure_result=True, fail_ensure=False, fail_play=False, fail_set_volume=False, fail_set_audio_candidates=False):
        self.ensure_result = ensure_result
        self.fail_ensure = fail_ensure
        self.fail_play = fail_play
        self.fail_set_volume = fail_set_volume
        self.fail_set_audio_candidates = fail_set_audio_candidates
        self.ensure_calls = []
        self.play_calls = []
        self.volume_calls = []
        self.audio_candidate_calls = []

    def ensure_video_adapter(self, hwnd, loop_enabled=False):
        self.ensure_calls.append((hwnd, loop_enabled))
        if self.fail_ensure:
            raise RuntimeError('ensure fail')
        return self.ensure_result

    def play_media(self, path, duration_hint=0.0):
        self.play_calls.append((path, duration_hint))
        if self.fail_play:
            raise RuntimeError('play fail')

    def set_volume(self, value):
        self.volume_calls.append(value)
        if self.fail_set_volume:
            raise RuntimeError('volume fail')

    def set_audio_track_candidates(self, candidates):
        normalized = tuple(int(index) for index in tuple(candidates or ()))
        self.audio_candidate_calls.append(normalized)
        if self.fail_set_audio_candidates:
            raise RuntimeError('audio candidates fail')
        return normalized


class DummyEngineController:
    def __init__(self, video_controller=None, audio_engine=None, factory=None, fail_stop=False):
        self.video_controller = video_controller
        self.audio_engine = audio_engine
        self._video_controller_factory = factory
        self.fail_stop = fail_stop
        self.stop_calls = 0

    def stop(self):
        self.stop_calls += 1
        if self.fail_stop:
            raise RuntimeError('stop fail')


class DummyHandler:
    def __init__(self):
        self._is_shutting_down = False
        self._last_video_start_signature = None
        self.event_bus = DummyEventBus()
        self.state_manager = DummyStateManager()
        self.queue_manager = SimpleNamespace(current_track=None)
        self.engine_controller = DummyEngineController()
        self._coerce_bool = lambda value: bool(value)


attach_player_event_handler_video_behavior(DummyHandler)


def _make_track(path='video.mp4', duration=12.5, metadata=None):
    return SimpleNamespace(path=path, duration=duration, metadata=metadata or {})


def test_matches_current_track_path_supports_direct_and_resolved_stream_url():
    track = _make_track(path='VIDEO.MP4', metadata={'_resolved_stream_url': 'https://cdn.example/stream'})
    assert DummyHandler._matches_current_track_path(track, 'video.mp4') is True
    assert DummyHandler._matches_current_track_path(track, 'https://cdn.example/stream') is True
    assert DummyHandler._matches_current_track_path(track, 'other.mp4') is False


def test_on_video_playback_error_ignores_stale_paths_and_handles_matching_errors():
    handler = DummyHandler()
    handler.queue_manager.current_track = _make_track(path='current.mp4', metadata={'_resolved_stream_url': 'https://cdn/current'})
    handler._last_video_start_signature = ('current.mp4', 55, False)

    handler._on_video_playback_error({'path': 'stale.mp4', 'error': 'boom'})
    assert handler.engine_controller.stop_calls == 0
    assert handler.state_manager.updates == []
    assert handler._last_video_start_signature == ('current.mp4', 55, False)

    handler._on_video_playback_error({'path': 'https://cdn/current', 'error': 'decoder failed'})
    assert handler.engine_controller.stop_calls == 1
    assert handler._last_video_start_signature is None
    assert [call[0] for call in handler.event_bus.calls] == [
        AudioEventType.CANCEL_VIDEO_PLAYBACK,
        AudioEventType.FEEDBACK_MESSAGE,
        AudioEventType.PLAYER_ERROR,
    ]
    assert handler.state_manager.updates[-1][0] == PlayerState.STOPPED
    assert handler.state_manager.updates[-1][1]['recoverable_error'] is True


def test_start_video_playback_handles_loading_success_duplicate_and_error_paths():
    handler = DummyHandler()
    handler.queue_manager.current_track = _make_track(path='clip.mp4', duration=21.0)
    controller = DummyVideoController(ensure_result=False)
    handler.engine_controller = DummyEngineController(video_controller=controller)

    handler._start_video_playback(hwnd=44, path='clip.mp4', loop=True, source='test')
    assert controller.ensure_calls == [(44, True)]
    assert handler.state_manager.updates[-1][0] == PlayerState.LOADING

    success_handler = DummyHandler()
    success_handler.queue_manager.current_track = _make_track(path='clip.mp4', duration=21.0)
    success_controller = DummyVideoController()
    success_handler.engine_controller = DummyEngineController(
        video_controller=success_controller,
        audio_engine=SimpleNamespace(get_volume=lambda: 0.42),
    )

    success_handler._start_video_playback(hwnd=77, path='clip.mp4', loop=False, source='test')
    assert success_controller.ensure_calls == [(77, False)]
    assert success_controller.volume_calls == [0.42]
    assert success_controller.play_calls == [('clip.mp4', 21.0)]
    assert success_handler._last_video_start_signature == ('clip.mp4', 77, False)
    assert success_handler.state_manager.updates[-1][0] == PlayerState.PLAYING_VIDEO

    success_handler._start_video_playback(hwnd=77, path='clip.mp4', loop=False, source='test')
    assert success_controller.play_calls == [('clip.mp4', 21.0)]

    failing_handler = DummyHandler()
    failing_handler.queue_manager.current_track = _make_track(path='clip.mp4')
    failing_handler.engine_controller = DummyEngineController(video_controller=DummyVideoController(fail_play=True))
    failing_handler._start_video_playback(hwnd=10, path='clip.mp4', loop=False, source='test')
    assert failing_handler.state_manager.updates[-1][0] == PlayerState.ERROR


def test_start_video_playback_can_create_video_controller_jit_or_fail_cleanly():
    created = DummyVideoController()
    handler = DummyHandler()
    handler.queue_manager.current_track = _make_track(path='jit.mp4', duration=6.0)
    handler.engine_controller = DummyEngineController(factory=lambda: created)

    handler._start_video_playback(hwnd=88, path='jit.mp4', loop=True, source='factory')
    assert handler.engine_controller.video_controller is created
    assert created.play_calls == [('jit.mp4', 6.0)]

    broken = DummyHandler()
    broken.queue_manager.current_track = _make_track(path='jit.mp4')
    broken.engine_controller = DummyEngineController(factory=lambda: (_ for _ in ()).throw(RuntimeError('factory fail')))
    broken._start_video_playback(hwnd=88, path='jit.mp4', loop=True, source='factory')
    assert broken.state_manager.updates[-1][0] == PlayerState.ERROR
    assert broken.state_manager.updates[-1][1]['message'] == 'VideoController not available'


def test_ready_handlers_and_window_closed_behave_as_expected():
    handler = DummyHandler()
    handler.queue_manager.current_track = _make_track(path='ready.mp4')
    controller = DummyVideoController()
    handler.engine_controller = DummyEngineController(video_controller=controller)

    handler._on_video_playback_ready({'hwnd': '123', 'path': 'ready.mp4', 'loop': 1})
    assert controller.ensure_calls[-1] == (123, True)

    handler._on_video_playback_ready_signal(321, 'ready.mp4', False)
    assert controller.ensure_calls[-1] == (321, False)

    handler.state_manager.state = PlayerState.PLAYING_VIDEO
    handler._last_video_start_signature = ('ready.mp4', 321, False)
    handler._on_video_window_closed()
    assert handler.engine_controller.stop_calls == 1
    assert handler._last_video_start_signature is None
    assert handler.state_manager.updates[-1][0] == PlayerState.STOPPED


def test_error_publish_and_stop_failures_are_best_effort():
    handler = DummyHandler()
    handler.event_bus = DummyEventBus(fail_publish=True)
    handler.engine_controller = DummyEngineController(fail_stop=True)
    handler.queue_manager.current_track = _make_track(path='x.mp4')
    handler._last_video_start_signature = ('x.mp4', 1, False)

    handler._on_video_playback_error({'path': 'x.mp4', 'error': 'boom'})

    assert handler.engine_controller.stop_calls == 1
    assert handler.state_manager.updates[-1][0] == PlayerState.STOPPED


def test_extract_and_configure_audio_track_candidates_are_deterministic():
    handler = DummyHandler()
    track = _make_track(metadata={'audio_track_candidates': [3, '5', 5, -1, 'bad']})
    controller = DummyVideoController()

    assert handler._extract_audio_track_candidates_from_track(track) == (3, 5)
    assert handler._extract_audio_track_candidates_from_track(_make_track(metadata={'audio_track_candidates': object()})) == ()
    assert handler._configure_video_controller_audio_track_candidates(controller, track) == (3, 5)
    assert controller.audio_candidate_calls == [(3, 5)]

    failing_controller = DummyVideoController(fail_set_audio_candidates=True)
    assert handler._configure_video_controller_audio_track_candidates(failing_controller, track) == (3, 5)
    assert failing_controller.audio_candidate_calls == [(3, 5)]



def test_start_video_playback_pushes_current_track_audio_candidates_into_video_controller():
    handler = DummyHandler()
    handler.queue_manager.current_track = _make_track(
        path='clip.mkv',
        duration=21.0,
        metadata={'audio_track_candidates': [7, '9', 7, 'bad']},
    )
    controller = DummyVideoController()
    handler.engine_controller = DummyEngineController(
        video_controller=controller,
        audio_engine=SimpleNamespace(get_volume=lambda: 0.5),
    )

    handler._start_video_playback(hwnd=55, path='clip.mkv', loop=False, source='test')

    assert controller.audio_candidate_calls == [(7, 9)]
    assert controller.ensure_calls == [(55, False)]
    assert controller.play_calls == [('clip.mkv', 21.0)]
