from __future__ import annotations

import src.controller.video_controller_events as video_controller_events
from src.controller.video_controller_state import VideoState


class DummyAdapter:
    def refresh_video_window(self, hwnd, width, height):
        return True


class DummyController:
    def __init__(self):
        self._adapter = DummyAdapter()
        self._current_hwnd = 77
        self._state = VideoState.PLAYING
        self._last_surface_signature = None
        self._last_error_info = None
        self._shutting_down = False
        self._closing = False
        self._current_path = 'movie.mp4'
        self.refresh_calls = []
        self.published = []

    def _safe_adapter_call(self, fn, *args):
        self.refresh_calls.append(args)
        return True

    def _publish_event(self, event_type, payload):
        self.published.append((event_type, payload))

    def _update_state(self, state):
        self._state = state


video_controller_events.attach_video_controller_event_behavior(DummyController)


def test_on_video_window_moved_ignores_bad_numeric_payload():
    controller = DummyController()

    controller._on_video_window_moved({'hwnd': object(), 'width': 'bad', 'height': 480})

    assert controller.refresh_calls == []


def test_on_video_window_moved_refreshes_once_for_new_signature():
    controller = DummyController()
    payload = {'hwnd': 77, 'width': 640, 'height': 480}

    controller._on_video_window_moved(payload)
    controller._on_video_window_moved(payload)

    assert controller.refresh_calls == [(77, 640, 480)]
    assert controller._last_surface_signature == (77, 640, 480)
