from __future__ import annotations

import threading
import weakref

import src.video.media_engine_core_playback as media_engine_core_playback


class DummyAdapter:
    def __init__(self, *, fail_call=False):
        self.fail_call = fail_call
        self.call_requests = []
        self.post_requests = []

    def call_on_com_thread(self, name, callback):
        self.call_requests.append(name)
        if self.fail_call:
            raise RuntimeError('call fail')
        return callback()

    def post_to_com_thread(self, name, callback):
        self.post_requests.append(name)
        callback()


class DummyCore:
    def __init__(self, adapter=None):
        self._state_lock = threading.RLock()
        self._shutdown_requested = False
        self._media_engine = object()
        self._media_engine_ex = None
        self._source = None
        self._requested_source = None
        self._active_source = None
        self.adapter = adapter or DummyAdapter()
        self.calls = []
        self.fail_names = set()
        self.values = {
            'GetCurrentTime': 12.5,
            'GetDuration': 88.0,
            'IsEnded': 1,
            'HasVideo': 1,
            'GetNativeVideoSize': 0,
        }

    def _adapter_ref(self):
        return self.adapter

    def _call_vtable_method(self, name, *args):
        self.calls.append((name, args))
        if name in self.fail_names:
            raise RuntimeError(name)
        if name == 'GetNativeVideoSize':
            args[0]._obj.value = 640
            args[1]._obj.value = 360
            return self.values.get(name, 0)
        return self.values.get(name, 0)

    def _call_engine_ptr_method(self, engine_ptr, name, *args):
        self.calls.append((name, args))
        if name in self.fail_names:
            raise RuntimeError(name)
        return 0

    def _sanitize_nonneg_float(self, value, default=0.0):
        if value in ('bad', None):
            raise ValueError('bad value')
        value = float(value)
        return value if value >= 0 else default

    def _query_media_engine_ex_on_com_thread(self, engine):
        self.calls.append(('query_engine_ex', (engine,)))
        if 'query_engine_ex' in self.fail_names:
            raise RuntimeError('query fail')
        return object()

    def _stop_engine_ptr_playback(self, engine):
        self.calls.append(('stop_engine_ptr_playback', (engine,)))



def test_load_source_uses_com_thread_and_updates_active_source(monkeypatch):
    core = DummyCore()
    freed = []
    monkeypatch.setattr(media_engine_core_playback, '_SysAllocString', lambda src: f'bstr:{src}')
    monkeypatch.setattr(media_engine_core_playback, '_SysFreeString', lambda bstr: freed.append(bstr))

    media_engine_core_playback.load_source(core, 'movie.mp4')

    assert core._source == 'movie.mp4'
    assert core._requested_source == 'movie.mp4'
    assert core._active_source == 'movie.mp4'
    assert core.adapter.call_requests == ['load']
    assert freed == ['bstr:movie.mp4']
    assert [name for name, _ in core.calls[:2]] == ['SetSource', 'Load']



def test_stop_and_update_video_stream_handle_failures_cleanly():
    core = DummyCore()
    media_engine_core_playback.stop(core)
    assert core.adapter.call_requests == ['stop']
    assert ('stop_engine_ptr_playback', (core._media_engine,)) in core.calls

    failing_adapter = DummyAdapter(fail_call=True)
    failing_core = DummyCore(adapter=failing_adapter)
    media_engine_core_playback.stop(failing_core)

    assert media_engine_core_playback.update_video_stream(core, 640, 360) is True
    assert any(name == 'query_engine_ex' for name, _ in core.calls)
    assert any(name == 'UpdateVideoStream' for name, _ in core.calls)

    assert media_engine_core_playback.update_video_stream(core, 0, 360) is False
    failing_update = DummyCore(adapter=DummyAdapter(fail_call=True))
    assert media_engine_core_playback.update_video_stream(failing_update, 640, 360) is False



def test_async_playback_commands_are_posted_to_adapter():
    core = DummyCore()
    media_engine_core_playback.play(core)
    media_engine_core_playback.pause(core)
    media_engine_core_playback.seek(core, 5.0)
    media_engine_core_playback.set_loop(core, True)
    media_engine_core_playback.set_volume(core, 0.5)
    media_engine_core_playback.set_muted(core, True)

    assert core.adapter.post_requests == ['play', 'pause', 'seek', 'set_loop', 'set_volume', 'set_muted']
    posted_names = [name for name, _ in core.calls if name in {'Play', 'Pause', 'SetCurrentTime', 'SetLoop', 'SetVolume', 'SetMuted'}]
    assert posted_names == ['Play', 'Pause', 'SetCurrentTime', 'SetLoop', 'SetVolume', 'SetMuted']




def test_audio_stream_selection_helpers_are_deterministic():
    core = DummyCore()
    selected_state = {1: False, 3: True, 5: False}

    def _get_stream_selection_on_com_thread(stream_index):
        return bool(selected_state.get(int(stream_index), False))

    def _set_stream_selection_on_com_thread(stream_index, selected):
        selected_state[int(stream_index)] = bool(selected)
        core.calls.append(('set_stream_selection', (int(stream_index), bool(selected))))

    def _apply_stream_selections_on_com_thread():
        core.calls.append(('apply_stream_selections', ()))

    core._get_stream_selection_on_com_thread = _get_stream_selection_on_com_thread
    core._set_stream_selection_on_com_thread = _set_stream_selection_on_com_thread
    core._apply_stream_selections_on_com_thread = _apply_stream_selections_on_com_thread

    assert media_engine_core_playback.get_selected_audio_streams(core, [1, '3', 3, 5]) == (3,)
    assert media_engine_core_playback.select_audio_stream(core, 5, [1, 3, 5]) is True
    assert selected_state == {1: False, 3: False, 5: True}
    assert media_engine_core_playback.prime_audio_stream_candidates(core, [3, 5]) == 3
    assert selected_state[3] is True and selected_state[5] is False
    assert ('apply_stream_selections', ()) in core.calls



def test_audio_stream_selection_helpers_handle_invalid_or_unavailable_inputs():
    core = DummyCore(adapter=DummyAdapter(fail_call=True))
    core._get_stream_selection_on_com_thread = lambda stream_index: False
    core._set_stream_selection_on_com_thread = lambda stream_index, selected: None
    core._apply_stream_selections_on_com_thread = lambda: None

    assert media_engine_core_playback.get_selected_audio_streams(core, []) == ()
    assert media_engine_core_playback.select_audio_stream(core, 1, [1, 2]) is False
    assert media_engine_core_playback.prime_audio_stream_candidates(core, [1, 2]) is None

    try:
        media_engine_core_playback.get_selected_audio_streams(core, ['bad'])
    except ValueError:
        pass
    else:
        raise AssertionError('Expected ValueError for invalid stream index input')

def test_getters_fall_back_safely_and_is_ended_uses_getended_fallback():
    core = DummyCore()
    assert media_engine_core_playback.get_position(core) == 12.5
    assert media_engine_core_playback.get_duration(core) == 88.0
    assert media_engine_core_playback.is_ended(core) is True
    assert media_engine_core_playback.has_video(core) is True
    assert media_engine_core_playback.get_video_size(core) == (640, 360)

    core.fail_names.add('IsEnded')
    core.values['GetEnded'] = 0
    assert media_engine_core_playback.is_ended(core) is False

    core.fail_names.update({'GetCurrentTime', 'GetDuration', 'HasVideo', 'GetNativeVideoSize'})
    assert media_engine_core_playback.get_position(core) == 0.0
    assert media_engine_core_playback.get_duration(core) == 0.0
    assert media_engine_core_playback.has_video(core) is False
    assert media_engine_core_playback.get_video_size(core) == (0, 0)

    failing_adapter_core = DummyCore(adapter=DummyAdapter(fail_call=True))
    assert media_engine_core_playback.get_position(failing_adapter_core) == 0.0
    assert media_engine_core_playback.get_duration(failing_adapter_core) == 0.0
    assert media_engine_core_playback.is_ended(failing_adapter_core) is False
    assert media_engine_core_playback.has_video(failing_adapter_core) is False
    assert media_engine_core_playback.get_video_size(failing_adapter_core) == (0, 0)
