from __future__ import annotations

import threading
from types import SimpleNamespace

from src.audio.audio_events import AudioEventType
from src.controller.video_controller_core import attach_video_controller_core_behavior
from src.controller.video_controller_state import VideoState
import src.controller.video_controller_core as core


class DummyBus:
    def __init__(self):
        self.subscribed = []
        self.published = []

    def subscribe(self, event_type, callback):
        self.subscribed.append((event_type, callback))

    def publish(self, event_type, payload, require_ui_thread=False):
        self.published.append((event_type, payload, require_ui_thread))


class DummyAdapter:
    def __init__(self):
        self.calls = []
        self.raise_on = {}

    def _maybe_raise(self, name):
        exc = self.raise_on.get(name)
        if exc is not None:
            raise exc

    def set_hwnd(self, hwnd):
        self._maybe_raise('set_hwnd')
        self.calls.append(('set_hwnd', hwnd))

    def set_loop(self, enabled):
        self._maybe_raise('set_loop')
        self.calls.append(('set_loop', enabled))

    def stop(self):
        self._maybe_raise('stop')
        self.calls.append(('stop', None))

    def close(self):
        self._maybe_raise('close')
        self.calls.append(('close', None))


class DummyController:
    def __init__(self):
        self._event_bus = DummyBus()
        self._state = VideoState.IDLE
        self._adapter = None
        self._current_hwnd = None
        self._current_path = 'movie.mp4'
        self._current_duration_hint = 10.0
        self._last_surface_signature = ('old',)
        self._last_error_info = 'err'
        self._loop_enabled = False
        self._shutting_down = False
        self._close_lock = threading.RLock()
        self._closing = False
        self.play_media_calls = []
        self._on_fullscreen_toggle_requested = lambda data=None: None
        self._on_video_window_moved = lambda data=None: None
        self._on_video_playback_error = lambda data=None: None

    def play_media(self, path):
        self.play_media_calls.append(path)


attach_video_controller_core_behavior(DummyController)


def test_setup_event_subscriptions_registers_callbacks():
    controller = DummyController()
    core._setup_event_subscriptions(controller)

    event_types = [event_type for event_type, _ in controller._event_bus.subscribed]
    assert event_types == [
        AudioEventType.FULLSCREEN_TOGGLE_REQUESTED,
        AudioEventType.VIDEO_WINDOW_MOVED,
        AudioEventType.VIDEO_PLAYBACK_ERROR,
    ]



def test_publish_event_and_validate_hwnd():
    controller = DummyController()
    core._publish_event(controller, AudioEventType.VIDEO_PLAYBACK_STARTED, {'x': 1}, require_ui_thread=True)
    assert controller._event_bus.published[-1] == (AudioEventType.VIDEO_PLAYBACK_STARTED, {'x': 1}, True)

    assert core._validate_hwnd(controller, 123) is True
    assert core._validate_hwnd(controller, 0) is False
    assert controller._state == VideoState.ERROR



def test_safe_adapter_call_sets_error_on_failure():
    controller = DummyController()
    adapter = DummyAdapter()
    adapter.raise_on['set_loop'] = RuntimeError('boom')
    controller._adapter = adapter

    result = core._safe_adapter_call(controller, adapter.set_loop, True)

    assert result is None
    assert controller._state == VideoState.ERROR



def test_ensure_video_adapter_creates_binds_and_sets_ready(monkeypatch):
    controller = DummyController()
    adapter = DummyAdapter()
    monkeypatch.setattr(core, 'create_imf_media_engine_adapter', lambda event_bus=None: adapter)

    ok = core.ensure_video_adapter(controller, 555, True)

    assert ok is True
    assert controller._adapter is adapter
    assert controller._current_hwnd == 555
    assert controller._state == VideoState.READY
    assert ('set_hwnd', 555) in adapter.calls
    assert ('set_loop', True) in adapter.calls
    assert controller._event_bus.published[-1][0] == AudioEventType.VIDEO_PLAYBACK_STARTING



def test_ensure_video_adapter_handles_factory_or_bind_failure(monkeypatch):
    controller = DummyController()
    monkeypatch.setattr(core, 'create_imf_media_engine_adapter', lambda event_bus=None: (_ for _ in ()).throw(RuntimeError('factory fail')))
    assert core.ensure_video_adapter(controller, 555, False) is False
    assert controller._state == VideoState.ERROR

    controller = DummyController()
    adapter = DummyAdapter()
    adapter.raise_on['set_hwnd'] = RuntimeError('bind fail')
    monkeypatch.setattr(core, 'create_imf_media_engine_adapter', lambda event_bus=None: adapter)
    assert core.ensure_video_adapter(controller, 555, False) is False
    assert controller._state == VideoState.ERROR



def test_on_video_playback_ready_and_set_loop(monkeypatch):
    controller = DummyController()
    adapter = DummyAdapter()
    controller._adapter = adapter
    calls = []
    controller.ensure_video_adapter = lambda hwnd, loop: calls.append((hwnd, loop)) or True

    core._on_video_playback_ready(controller, {'hwnd': 321, 'path': 'movie.mp4', 'loop': True})
    assert calls == [(321, True)]
    assert controller.play_media_calls == ['movie.mp4']

    core.set_loop(controller, False)
    assert controller._loop_enabled is False
    assert ('set_loop', False) in adapter.calls



def test_close_adapter_internal_and_shutdown_cleanup():
    controller = DummyController()
    adapter = DummyAdapter()
    controller._adapter = adapter
    controller._state = VideoState.ERROR

    core._close_adapter_internal(controller)

    assert controller._adapter is None
    assert controller._current_hwnd is None
    assert controller._current_path is None
    assert controller._state == VideoState.STOPPED
    assert ('stop', None) in adapter.calls
    assert ('close', None) in adapter.calls
    assert controller._event_bus.published[0][0] == AudioEventType.VIDEO_PLAYBACK_STOPPED

    controller = DummyController()
    controller._adapter = DummyAdapter()
    core.shutdown(controller)
    assert controller._shutting_down is True
    assert controller._state == VideoState.STOPPED
