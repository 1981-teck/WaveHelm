from __future__ import annotations

from src.audio.audio_events import AudioEventType
from src.controller.video_controller_playback import attach_video_controller_playback_behavior
from src.controller.video_controller_state import VideoState


class DummyAdapter:
    def __init__(self):
        self.calls = []
        self.duration = 12.5
        self.position = 3.0
        self.ended = False
        self.fail = {}
        self.selected_audio_streams_result = ()
        self.prime_audio_stream_candidates_result = None
        self.stream_count = 0

    def _maybe_fail(self, name):
        exc = self.fail.get(name)
        if isinstance(exc, list):
            if not exc:
                return
            next_exc = exc.pop(0)
            if next_exc is None:
                return
            raise next_exc
        if exc is not None:
            raise exc

    def load_source(self, path):
        self._maybe_fail('load_source')
        self.calls.append(('load_source', path))

    def play(self):
        self._maybe_fail('play')
        self.calls.append(('play', None))

    def set_volume(self, value):
        self._maybe_fail('set_volume')
        self.calls.append(('set_volume', value))

    def pause(self):
        self._maybe_fail('pause')
        self.calls.append(('pause', None))

    def resume(self):
        self._maybe_fail('resume')
        self.calls.append(('resume', None))

    def stop(self):
        self._maybe_fail('stop')
        self.calls.append(('stop', None))

    def get_duration(self):
        self._maybe_fail('get_duration')
        return self.duration

    def get_position(self):
        self._maybe_fail('get_position')
        return self.position

    def seek(self, value):
        self._maybe_fail('seek')
        self.calls.append(('seek', value))

    def pump_events(self):
        self._maybe_fail('pump_events')
        self.calls.append(('pump_events', None))

    def has_ended(self):
        self._maybe_fail('has_ended')
        return self.ended

    def get_selected_audio_streams(self, candidate_stream_indices):
        self._maybe_fail('get_selected_audio_streams')
        self.calls.append(('get_selected_audio_streams', tuple(candidate_stream_indices)))
        return self.selected_audio_streams_result

    def select_audio_stream(self, stream_index, candidate_stream_indices=None):
        self._maybe_fail('select_audio_stream')
        normalized_candidates = None if candidate_stream_indices is None else tuple(candidate_stream_indices)
        self.calls.append(('select_audio_stream', int(stream_index), normalized_candidates))
        return True

    def get_number_of_streams(self):
        self._maybe_fail('get_number_of_streams')
        self.calls.append(('get_number_of_streams', None))
        return self.stream_count

    def prime_audio_stream_candidates(self, candidate_stream_indices):
        self._maybe_fail('prime_audio_stream_candidates')
        normalized_candidates = tuple(candidate_stream_indices)
        self.calls.append(('prime_audio_stream_candidates', normalized_candidates))
        return self.prime_audio_stream_candidates_result


class DummyController:
    def __init__(self, adapter=None):
        self._shutting_down = False
        self._adapter = adapter
        self._state = VideoState.IDLE
        self._volume = 0.5
        self._current_hwnd = 123
        self._current_path = None
        self._current_duration_hint = 0.0
        self._loop_enabled = False
        self._current_audio_track_candidates = ()
        self._current_audio_track_descriptors = ()
        self._current_audio_stream_index = None
        self.published = []
        self.closed = 0

    def set_audio_track_descriptors(self, descriptors):
        self._current_audio_track_descriptors = tuple(dict(item) for item in tuple(descriptors or ()))
        return self._current_audio_track_descriptors

    def get_audio_track_descriptors(self):
        return tuple(dict(item) for item in self._current_audio_track_descriptors)

    def _publish_event(self, event_type, payload, require_ui_thread=False):
        self.published.append((event_type, payload, require_ui_thread))

    def _update_state(self, state):
        self._state = state

    def _close_adapter_internal(self):
        self.closed += 1


attach_video_controller_playback_behavior(DummyController)


def test_play_media_success_publishes_start_and_started():
    adapter = DummyAdapter()
    controller = DummyController(adapter)

    controller.play_media('movie.mp4', duration_hint=9.5)

    assert controller._current_path == 'movie.mp4'
    assert controller._current_duration_hint == 9.5
    assert ('load_source', 'movie.mp4') in adapter.calls
    assert ('play', None) in adapter.calls
    assert controller._state == VideoState.PLAYING
    assert controller.published[0][0] == AudioEventType.VIDEO_PLAYBACK_STARTING
    assert controller.published[-1][0] == AudioEventType.VIDEO_PLAYBACK_STARTED


def test_play_media_error_updates_error_state_and_event():
    adapter = DummyAdapter()
    adapter.fail['play'] = RuntimeError('boom')
    controller = DummyController(adapter)

    controller.play_media('movie.mp4')

    assert controller._state == VideoState.ERROR
    assert controller.published[-1][0] == AudioEventType.VIDEO_PLAYBACK_ERROR
    assert controller.published[-1][1]['error'] == 'boom'


def test_pause_and_resume_change_state_and_publish():
    adapter = DummyAdapter()
    controller = DummyController(adapter)
    controller._state = VideoState.PLAYING

    controller.pause()
    assert controller._state == VideoState.PAUSED
    assert controller.published[-1][0] == AudioEventType.VIDEO_PLAYBACK_PAUSED

    controller.resume()
    assert controller._state == VideoState.PLAYING
    assert controller.published[-1][0] == AudioEventType.VIDEO_PLAYBACK_RESUMED


def test_stop_close_adapter_short_circuits_stop_flow():
    adapter = DummyAdapter()
    controller = DummyController(adapter)
    controller._state = VideoState.PLAYING

    controller.stop(close_adapter=True)

    assert controller.closed == 1
    assert ('stop', None) not in adapter.calls


def test_set_volume_clamps_and_handles_invalid_values():
    adapter = DummyAdapter()
    controller = DummyController(adapter)

    controller.set_volume(2.0)
    controller.set_volume('bad')

    assert controller._volume == 1.0
    assert ('set_volume', 1.0) in adapter.calls


def test_get_duration_and_position_fallbacks():
    adapter = DummyAdapter()
    controller = DummyController(adapter)
    controller._current_duration_hint = 7.0

    assert controller.get_duration() == 12.5
    assert controller.get_position() == 3.0

    adapter.fail['get_duration'] = RuntimeError('duration')
    adapter.fail['get_position'] = RuntimeError('position')
    assert controller.get_duration() == 7.0
    assert controller.get_position() == 0.0


def test_seek_returns_false_on_adapter_error():
    adapter = DummyAdapter()
    controller = DummyController(adapter)

    assert controller.seek(15.0) is True
    adapter.fail['seek'] = RuntimeError('seek failed')
    assert controller.seek(15.0) is False


def test_poll_end_handles_loop_and_close_paths():
    adapter = DummyAdapter()
    controller = DummyController(adapter)

    assert controller.poll_end() is False

    adapter.ended = True
    controller._current_path = 'movie.mp4'
    controller._current_duration_hint = 8.0
    assert controller.poll_end() is True
    assert controller.closed == 1
    assert controller.published[-1][0] == AudioEventType.VIDEO_PLAYBACK_ENDED

    controller = DummyController(adapter)
    controller._loop_enabled = True
    adapter.ended = True
    assert controller.poll_end() is False


def test_audio_track_candidate_helpers_normalize_and_bridge_adapter_state():
    adapter = DummyAdapter()
    adapter.selected_audio_streams_result = ('3', 5, 'bad')
    controller = DummyController(adapter)

    assert controller.set_audio_track_candidates([3, '5', 5, -1, 'bad']) == (3, 5)
    assert controller.get_audio_track_candidates() == (3, 5)
    assert controller.get_selected_audio_streams() == (3, 5)
    assert controller.select_audio_stream('5') is True
    assert ('select_audio_stream', 5, (3, 5)) in adapter.calls
    assert controller._current_audio_stream_index == 5


def test_play_media_retries_alternative_audio_stream_candidates_deterministically():
    adapter = DummyAdapter()
    adapter.prime_audio_stream_candidates_result = 3
    adapter.fail['play'] = [RuntimeError('primary stream failed'), None]
    controller = DummyController(adapter)
    controller.set_audio_track_candidates([3, 5])

    controller.play_media('movie.mkv', duration_hint=11.0)

    assert ('load_source', 'movie.mkv') in adapter.calls
    assert ('prime_audio_stream_candidates', (3, 5)) in adapter.calls
    assert ('select_audio_stream', 5, (3, 5)) in adapter.calls
    assert adapter.calls.count(('play', None)) == 1
    assert ('stop', None) in adapter.calls
    assert controller._current_audio_stream_index == 5
    assert controller._state == VideoState.PLAYING
    assert controller.published[-1][0] == AudioEventType.VIDEO_PLAYBACK_STARTED


def test_play_media_bootstraps_generic_runtime_audio_candidates_without_probe_metadata():
    adapter = DummyAdapter()
    adapter.stream_count = 4
    adapter.prime_audio_stream_candidates_result = 1
    controller = DummyController(adapter)

    controller.play_media('movie.mkv')

    assert ('get_number_of_streams', None) in adapter.calls
    assert controller.get_audio_track_candidates() == (1, 2, 3)
    assert controller._current_audio_stream_index == 1
    descriptors = controller.get_audio_track_descriptors()
    assert tuple(item['stream_index'] for item in descriptors) == (1, 2, 3)
    assert descriptors[0]['label'] == 'Track 1 — stream #1'
    assert ('prime_audio_stream_candidates', (1, 2, 3)) in adapter.calls
    assert controller._state == VideoState.PLAYING
