from __future__ import annotations

import threading

from src.video.imf_media_engine_adapter_playback import (
    _apply_settings_core,
    _can_issue_playback_commands,
    _is_engine_initialized,
    _require_active_engine,
    bind_hwnd,
    close,
    get_duration,
    get_position,
    get_selected_audio_streams,
    get_video_size,
    has_ended,
    has_video,
    load_source,
    load_video,
    pause,
    play,
    prime_audio_stream_candidates,
    refresh_video_window,
    resume,
    seek,
    select_audio_stream,
    set_hwnd,
    set_loop,
    set_loop_enabled,
    set_muted,
    set_target_hwnd,
    set_video_window,
    set_volume,
    shutdown,
    startup,
    stop,
)


class DummyCore:
    def __init__(self):
        self._media_engine = object()
        self.ensure_calls = []
        self.stop_calls = 0
        self.shutdown_calls = 0
        self.update_video_calls = []
        self.volume_calls = []
        self.muted_calls = []
        self.loop_calls = []
        self.load_calls = []
        self.play_calls = 0
        self.pause_calls = 0
        self.seek_calls = []
        self.raise_on = {}
        self.position = 12.0
        self.duration = 99.0
        self.ended = False
        self.video_size = (640, 360)
        self.video_present = True
        self.selected_audio_streams_result = ()
        self.select_audio_stream_calls = []
        self.select_audio_stream_result = True
        self.prime_audio_stream_candidates_calls = []
        self.prime_audio_stream_candidates_result = None

    def ensure_engine(self, hwnd):
        if 'ensure_engine' in self.raise_on:
            raise self.raise_on['ensure_engine']
        self.ensure_calls.append(hwnd)

    def shutdown(self):
        self.shutdown_calls += 1
        if 'shutdown' in self.raise_on:
            raise self.raise_on['shutdown']

    def stop(self):
        self.stop_calls += 1

    def update_video_stream(self, width, height):
        self.update_video_calls.append((width, height))
        if 'update_video_stream' in self.raise_on:
            raise self.raise_on['update_video_stream']
        return True

    def set_volume(self, value):
        self.volume_calls.append(value)
        if 'set_volume' in self.raise_on:
            raise self.raise_on['set_volume']

    def set_muted(self, value):
        self.muted_calls.append(value)
        if 'set_muted' in self.raise_on:
            raise self.raise_on['set_muted']

    def set_loop(self, value):
        self.loop_calls.append(value)
        if 'set_loop' in self.raise_on:
            raise self.raise_on['set_loop']

    def load_source(self, source):
        self.load_calls.append(source)
        if 'load_source' in self.raise_on:
            raise self.raise_on['load_source']

    def play(self):
        self.play_calls += 1
        if 'play' in self.raise_on:
            raise self.raise_on['play']

    def pause(self):
        self.pause_calls += 1

    def seek(self, seconds):
        self.seek_calls.append(seconds)

    def get_position(self):
        if 'get_position' in self.raise_on:
            raise self.raise_on['get_position']
        return self.position

    def get_duration(self):
        if 'get_duration' in self.raise_on:
            raise self.raise_on['get_duration']
        return self.duration

    def is_ended(self):
        if 'is_ended' in self.raise_on:
            raise self.raise_on['is_ended']
        return self.ended

    def get_video_size(self):
        if 'get_video_size' in self.raise_on:
            raise self.raise_on['get_video_size']
        return self.video_size

    def has_video(self):
        if 'has_video' in self.raise_on:
            raise self.raise_on['has_video']
        return self.video_present

    def get_selected_audio_streams(self, candidate_stream_indices):
        if 'get_selected_audio_streams' in self.raise_on:
            raise self.raise_on['get_selected_audio_streams']
        return self.selected_audio_streams_result

    def select_audio_stream(self, stream_index, candidate_stream_indices=None):
        if 'select_audio_stream' in self.raise_on:
            raise self.raise_on['select_audio_stream']
        self.select_audio_stream_calls.append((stream_index, candidate_stream_indices))
        return self.select_audio_stream_result

    def prime_audio_stream_candidates(self, candidate_stream_indices):
        if 'prime_audio_stream_candidates' in self.raise_on:
            raise self.raise_on['prime_audio_stream_candidates']
        self.prime_audio_stream_candidates_calls.append(candidate_stream_indices)
        return self.prime_audio_stream_candidates_result


class BrokenStateCore:
    @property
    def _media_engine(self):
        raise RuntimeError('broken state')


class DummyAdapter:
    def __init__(self, core=None):
        self._core = core or DummyCore()
        self._lock = threading.RLock()
        self._shutdown_requested = False
        self._closed = False
        self._loop_enabled = False
        self._volume = 1.0
        self._muted = False
        self._hwnd = None
        self._source = None
        self.com_manager_calls = []

    def _get_com_thread_manager(self, ensure_started=False):
        self.com_manager_calls.append(ensure_started)
        if ensure_started == 'fail':
            raise RuntimeError('startup fail')
        return 'manager'


for name, fn in {
    '_is_engine_initialized': _is_engine_initialized,
    '_can_issue_playback_commands': _can_issue_playback_commands,
    '_require_active_engine': _require_active_engine,
    'startup': startup,
    'shutdown': shutdown,
    'close': close,
    'stop': stop,
    'set_hwnd': set_hwnd,
    'bind_hwnd': bind_hwnd,
    'set_target_hwnd': set_target_hwnd,
    'set_video_window': set_video_window,
    'set_volume': set_volume,
    'set_loop': set_loop,
    'set_loop_enabled': set_loop_enabled,
    'set_muted': set_muted,
    'resume': resume,
    'load_source': load_source,
    'load_video': load_video,
    'get_selected_audio_streams': get_selected_audio_streams,
    'select_audio_stream': select_audio_stream,
    'prime_audio_stream_candidates': prime_audio_stream_candidates,
    'play': play,
    'pause': pause,
    'seek': seek,
    'get_position': get_position,
    'get_duration': get_duration,
    'has_ended': has_ended,
    'get_video_size': get_video_size,
    'has_video': has_video,
    'refresh_video_window': refresh_video_window,
    '_apply_settings_core': _apply_settings_core,
}.items():
    setattr(DummyAdapter, name, fn)


def test_engine_state_and_command_guards_handle_broken_state_cleanly():
    adapter = DummyAdapter(core=BrokenStateCore())
    assert adapter._is_engine_initialized() is False
    assert adapter._can_issue_playback_commands() is False

    adapter = DummyAdapter()
    adapter._shutdown_requested = True
    assert adapter._can_issue_playback_commands() is False


def test_startup_shutdown_and_close_are_best_effort_and_idempotent():
    adapter = DummyAdapter()
    adapter.startup()
    assert adapter.com_manager_calls == [True]

    adapter._get_com_thread_manager = lambda ensure_started=False: (_ for _ in ()).throw(RuntimeError('boom'))
    adapter.startup()

    adapter._core.raise_on['shutdown'] = RuntimeError('shutdown fail')
    adapter.shutdown()
    adapter.shutdown()
    assert adapter._closed is True
    assert adapter._core.shutdown_calls == 1

    second = DummyAdapter()
    second.close()
    assert second._closed is True


def test_set_hwnd_aliases_and_refresh_video_window_behaviour():
    adapter = DummyAdapter()
    adapter.set_hwnd(77)
    assert adapter._hwnd == 77
    assert adapter._core.ensure_calls == [77]

    alias = DummyAdapter()
    alias.bind_hwnd(88)
    alias.set_target_hwnd(99)
    alias.set_video_window(111)
    assert alias._core.ensure_calls == [88, 99, 111]

    assert adapter.refresh_video_window(120, 640, 360) is True
    assert adapter._hwnd == 120
    assert adapter._core.update_video_calls[-1] == (640, 360)

    adapter._shutdown_requested = True
    assert adapter.refresh_video_window(0, 10, 10) is False


def test_volume_muted_load_and_playback_helpers_fall_back_cleanly():
    adapter = DummyAdapter()
    adapter.set_volume('bad')
    assert adapter._volume == 1.0
    assert adapter._core.volume_calls == [1.0]

    adapter._core.raise_on['set_volume'] = RuntimeError('mute fail')
    adapter.set_volume(0.25)
    assert adapter._volume == 0.25

    adapter._core.raise_on['set_muted'] = RuntimeError('muted fail')
    adapter.set_muted(True)
    assert adapter._muted is True

    adapter.load_source('video.mp4')
    assert adapter._source == 'video.mp4'
    assert adapter._core.load_calls == ['video.mp4']

    assert adapter.load_video('clip.mp4', loop=True) is True
    assert adapter._loop_enabled is True
    assert adapter._core.play_calls >= 1

    failing = DummyAdapter()
    failing._core.raise_on['load_source'] = RuntimeError('load fail')
    assert failing.load_video('broken.mp4') is False


def test_getters_and_apply_settings_core_return_safe_defaults():
    adapter = DummyAdapter()
    assert adapter.get_position() == 12.0
    assert adapter.get_duration() == 99.0
    assert adapter.has_ended() is False
    assert adapter.get_video_size() == (640, 360)
    assert adapter.has_video() is True

    for key in ('get_position', 'get_duration', 'is_ended', 'get_video_size', 'has_video'):
        adapter._core.raise_on[key] = RuntimeError(key)

    assert adapter.get_position() == 0.0
    assert adapter.get_duration() == 0.0
    assert adapter.has_ended() is False
    assert adapter.get_video_size() == (0, 0)
    assert adapter.has_video() is False

    apply_adapter = DummyAdapter()
    apply_adapter._loop_enabled = True
    apply_adapter._volume = 0.5
    apply_adapter._muted = True
    apply_adapter._apply_settings_core()
    assert apply_adapter._core.loop_calls == [True]
    assert apply_adapter._core.volume_calls == [0.5]
    assert apply_adapter._core.muted_calls == [True]

    apply_adapter._core.raise_on['set_loop'] = RuntimeError('loop fail')
    apply_adapter._core.raise_on['set_volume'] = RuntimeError('volume fail')
    apply_adapter._core.raise_on['set_muted'] = RuntimeError('muted fail')
    apply_adapter._apply_settings_core()


def test_audio_stream_helpers_bridge_core_selection_deterministically():
    adapter = DummyAdapter()
    adapter._core.selected_audio_streams_result = ('3', 5, 'bad')
    assert adapter.get_selected_audio_streams([1, 3, 5]) == (3, 5)

    assert adapter.select_audio_stream('5', [1, 3, 5]) is True
    assert adapter._core.select_audio_stream_calls == [(5, [1, 3, 5])]

    adapter._core.prime_audio_stream_candidates_result = '3'
    assert adapter.prime_audio_stream_candidates([3, 5]) == 3
    assert adapter._core.prime_audio_stream_candidates_calls == [[3, 5]]


def test_audio_stream_helpers_fail_cleanly_when_engine_or_core_state_is_invalid():
    adapter = DummyAdapter()
    adapter._shutdown_requested = True
    assert adapter.get_selected_audio_streams([1, 2]) == ()

    adapter = DummyAdapter()
    adapter._core.raise_on['get_selected_audio_streams'] = RuntimeError('probe fail')
    assert adapter.get_selected_audio_streams([1, 2]) == ()

    adapter._core.raise_on['select_audio_stream'] = RuntimeError('select fail')
    assert adapter.select_audio_stream(2, [1, 2]) is False

    adapter._core.raise_on['prime_audio_stream_candidates'] = RuntimeError('prime fail')
    assert adapter.prime_audio_stream_candidates([1, 2]) is None

    inactive = DummyAdapter(core=BrokenStateCore())
    try:
        inactive.select_audio_stream(2, [1, 2])
    except RuntimeError:
        pass
    else:
        raise AssertionError('select_audio_stream should fail fast without an initialized engine')

    try:
        adapter.select_audio_stream('bad', [1, 2])
    except ValueError:
        pass
    else:
        raise AssertionError('select_audio_stream should reject invalid stream indices')
