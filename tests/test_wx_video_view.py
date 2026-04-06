from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from src.audio.audio_event_models import AudioEventType
from src.model.media_file import MediaType
from src.ui_wx.main_view import MainView
from src.ui_wx.video_view import ExternalVideoWindow, NativeVideoSurface, VideoView
from tests.wx_fakes import FakeApp, FakeCloseEvent, FakeWxModule


class DummyLocalizationManager:
    def __init__(self) -> None:
        self.language_callbacks = []

    def register_language_change_callback(self, callback):
        self.language_callbacks.append(callback)

    def unregister_language_change_callback(self, callback):
        if callback in self.language_callbacks:
            self.language_callbacks.remove(callback)

    def get_text(self, key, default=None, **kwargs):
        mapping = {
            'nav_library': 'Library',
            'nav_playlists': 'Playlist',
            'nav_favorites': 'Favorites',
            'nav_equalizer': 'Equalizer',
            'nav_effects': 'Effects',
            'tab_visualizer': 'Visualizer',
            'nav_ambient': 'Ambient',
            'nav_settings': 'Settings',
            'nav_readmi': 'Readmi',
            'nav_about': 'About',
        }
        text = mapping.get(key, default or key)
        return text.format(**kwargs) if kwargs else text


class DummyThemeManager:
    def __init__(self) -> None:
        self.theme_callbacks = []

    def register_theme_change_callback(self, callback):
        self.theme_callbacks.append(callback)

    def unregister_theme_change_callback(self, callback):
        if callback in self.theme_callbacks:
            self.theme_callbacks.remove(callback)

    def get_current_theme_colors(self):
        return {
            'bg_color': '#111111',
            'panel_bg': '#1b1b1b',
            'text_color': '#f5f5f5',
            'button_color': '#222222',
        }


class DummyVideoController:
    def __init__(self, duration: float = 120.0, *, is_playing: bool = True) -> None:
        self.duration = float(duration)
        self.position = 0.0
        self.is_playing = bool(is_playing)

    def get_duration(self) -> float:
        return self.duration

    def get_position(self) -> float:
        return self.position


class DummyPlayerController:
    def __init__(self, duration: float = 120.0) -> None:
        self.actions: list[str] = []
        self.video_controller = DummyVideoController(duration)
        self.current_track = SimpleNamespace(media_type=MediaType.VIDEO)
        self._state_payload = {
            'state': 'PLAYING_VIDEO',
            'playing': True,
            'paused': False,
            'stopped': False,
            'is_video': True,
            'is_audio': False,
        }

    def play_action(self):
        self.actions.append('play_action')

    def pause(self):
        self.actions.append('pause')

    def stop(self):
        self.actions.append('stop')

    def next(self):
        self.actions.append('next')

    def previous(self):
        self.actions.append('previous')

    def get_duration(self) -> float:
        return self.video_controller.get_duration()

    def get_position(self) -> float:
        return self.video_controller.get_position()

    def get_current_player_state_payload(self) -> dict[str, object]:
        return dict(self._state_payload)

    def set_runtime_state_payload(self, **updates: object) -> None:
        self._state_payload.update(updates)

    def is_video(self) -> bool:
        return bool(self._state_payload.get('is_video'))


class DummyEventBus:
    def __init__(self) -> None:
        self.subscriptions = {}
        self.published = []

    def subscribe(self, event_type, callback):
        self.subscriptions.setdefault(event_type, []).append(callback)
        return callback

    def unsubscribe(self, event_type, subscription=None, callback=None):
        target = subscription if subscription is not None else callback
        callbacks = self.subscriptions.get(event_type, [])
        if target in callbacks:
            callbacks.remove(target)
            return True
        return False

    def publish(self, event_type, payload):
        self.published.append((event_type, payload))
        for callback in list(self.subscriptions.get(event_type, [])):
            callback(payload)


def build_main_view(monkeypatch, player_controller=None):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    event_bus = DummyEventBus()
    main_view = MainView(
        FakeApp(),
        event_bus=event_bus,
        localization_manager=DummyLocalizationManager(),
        theme_manager=DummyThemeManager(),
        player_controller=player_controller,
    )
    return main_view, event_bus


def test_wx_video_view_stub_fails_fast_after_external_only_migration():
    assert VideoView.external_only_mode is True
    with pytest.raises(RuntimeError, match='removed from the app'):
        VideoView()


def test_wx_native_video_surface_notifies_ready_and_deduplicates_surface_changes(monkeypatch):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    parent = FakeWxModule.Panel(None)
    hwnd_notifications: list[int] = []
    surface_notifications: list[dict[str, int]] = []

    surface = NativeVideoSurface(
        FakeWxModule,
        parent,
        hwnd_ready_callback=lambda hwnd: hwnd_notifications.append(int(hwnd)),
        surface_changed_callback=lambda payload: surface_notifications.append(dict(payload)),
    )

    surface.request_hwnd_ready()

    assert hwnd_notifications and hwnd_notifications[-1] > 0
    assert len(surface_notifications) == 1
    assert surface_notifications[-1]['hwnd'] == hwnd_notifications[-1]

    surface.request_hwnd_ready()
    assert len(hwnd_notifications) == 1
    assert len(surface_notifications) == 1

    surface.panel.SetSize((640, 360))

    assert surface_notifications[-1]['width'] == 640
    assert surface_notifications[-1]['height'] == 360
    assert len(surface_notifications) == 2


def test_wx_external_video_window_requests_surface_and_emits_close_callback(monkeypatch):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    hwnd_notifications: list[int] = []
    surface_notifications: list[dict[str, int]] = []
    close_notifications: list[str] = []

    window = ExternalVideoWindow(
        FakeWxModule,
        hwnd_ready_callback=lambda hwnd: hwnd_notifications.append(int(hwnd)),
        surface_changed_callback=lambda payload: surface_notifications.append(dict(payload)),
        close_callback=lambda: close_notifications.append('closed'),
        player_controller=DummyPlayerController(),
        event_bus=DummyEventBus(),
        localization_manager=DummyLocalizationManager(),
        theme_manager=DummyThemeManager(),
    )

    window.show_window()
    window.request_hwnd_ready(force=True)

    assert window.frame.show_calls[-1] is True
    assert window.frame.raise_calls == 1
    assert window.frame.activate_calls == 1
    assert hwnd_notifications and hwnd_notifications[-1] > 0
    assert surface_notifications[-1]['hwnd'] == hwnd_notifications[-1]

    close_event = FakeCloseEvent(can_veto=False)
    window.frame.trigger(FakeWxModule.EVT_CLOSE, close_event)

    assert close_notifications == ['closed']
    assert close_event.skipped is True


def test_wx_main_view_external_only_prepare_video_playback_opens_external_window_and_publishes_ready(monkeypatch):
    main_view, event_bus = build_main_view(monkeypatch, player_controller=DummyPlayerController())

    main_view.set_use_external_video_window(False)
    event_bus.publish(AudioEventType.PREPARE_VIDEO_PLAYBACK, {'path': 'movie.mp4', 'loop': True})

    ready_events = [payload for event_type, payload in event_bus.published if event_type == AudioEventType.VIDEO_PLAYBACK_READY]

    assert main_view.current_view == 'library'
    assert main_view.get_view('video') is None
    assert main_view._use_external_video_window is True
    assert isinstance(main_view._external_video_window, ExternalVideoWindow)
    assert main_view._pending_video_request == {'path': 'movie.mp4', 'loop': True}
    assert ready_events and ready_events[-1]['path'] == 'movie.mp4'
    assert ready_events[-1]['loop'] is True
    assert int(ready_events[-1]['hwnd']) > 0


def test_wx_main_view_audio_playback_started_closes_external_window_runtime_state(monkeypatch):
    main_view, event_bus = build_main_view(monkeypatch, player_controller=DummyPlayerController())
    event_bus.publish(AudioEventType.PREPARE_VIDEO_PLAYBACK, {'path': 'movie.mp4', 'loop': False})

    window = main_view._external_video_window
    assert isinstance(window, ExternalVideoWindow)
    destroy_calls: list[str] = []
    original_destroy = window.destroy

    def _wrapped_destroy():
        destroy_calls.append('destroy')
        original_destroy()

    window.destroy = _wrapped_destroy

    event_bus.publish(AudioEventType.PLAYBACK_STARTED, {'path': 'track.mp3', 'position': 0.0})

    assert destroy_calls == ['destroy']
    assert window.frame.destroy_calls == 1
    assert main_view._external_video_window is None
    assert main_view._video_hwnd is None
    assert main_view._pending_video_request is None
    assert main_view._video_session_active is False




def test_wx_main_view_stop_requested_closes_external_window_on_video_stopped(monkeypatch):
    """Guard stop-driven teardown for the external-only video host.

    Edge cases:
        1. STOP_REQUESTED can arrive before the backend emits VIDEO_PLAYBACK_STOPPED, so teardown must wait for the confirmed stop event.
        2. Duplicate stop requests must remain idempotent and destroy the external window only once.
        3. Runtime flags must be cleared after teardown so the next video session does not inherit stale stop intent.
    """
    main_view, event_bus = build_main_view(monkeypatch, player_controller=DummyPlayerController())
    event_bus.publish(AudioEventType.PREPARE_VIDEO_PLAYBACK, {'path': 'movie.mp4', 'loop': False})

    window = main_view._external_video_window
    assert isinstance(window, ExternalVideoWindow)
    destroy_calls: list[str] = []
    original_destroy = window.destroy

    def _wrapped_destroy():
        destroy_calls.append('destroy')
        original_destroy()

    window.destroy = _wrapped_destroy

    event_bus.publish(AudioEventType.STOP_REQUESTED, {})
    assert main_view._external_video_window is window

    event_bus.publish(AudioEventType.VIDEO_PLAYBACK_STOPPED, {'path': 'movie.mp4'})

    assert destroy_calls == ['destroy']
    assert window.frame.destroy_calls == 1
    assert main_view._external_video_window is None
    assert main_view._video_hwnd is None
    assert main_view._pending_video_request is None
    assert main_view._video_session_active is False
    assert main_view._close_external_window_on_stop_request is False
    assert main_view._deferred_external_window_close_on_user_interrupt is False

def test_wx_main_view_stop_requested_closes_external_window_when_backend_already_stopped(monkeypatch):
    """Close the stale external window even when STOP_REQUESTED arrives after backend teardown.

    Edge cases:
        1. A different STOP_REQUESTED subscriber can synchronously stop the backend before MainView processes the same event.
        2. The selected queue item can remain a video track after stop, so stopped payload state must win over stale track metadata.
        3. Video-controller doubles can expose is_playing as a bool property instead of a method, and the inactivity check must still stay correct.
    """
    player_controller = DummyPlayerController()
    main_view, event_bus = build_main_view(monkeypatch, player_controller=player_controller)
    event_bus.publish(AudioEventType.PREPARE_VIDEO_PLAYBACK, {'path': 'movie.mp4', 'loop': False})

    window = main_view._external_video_window
    assert isinstance(window, ExternalVideoWindow)
    destroy_calls: list[str] = []
    original_destroy = window.destroy

    def _wrapped_destroy():
        destroy_calls.append('destroy')
        original_destroy()

    window.destroy = _wrapped_destroy
    player_controller.video_controller.is_playing = False
    player_controller.set_runtime_state_payload(
        state='STOPPED',
        playing=False,
        paused=False,
        stopped=True,
        is_video=True,
        is_audio=False,
    )
    main_view._video_session_active = False

    event_bus.publish(AudioEventType.VIDEO_PLAYBACK_STOPPED, {'path': 'movie.mp4'})
    event_bus.publish(AudioEventType.STOP_REQUESTED, {})

    assert destroy_calls == ['destroy']
    assert window.frame.destroy_calls == 1
    assert main_view._external_video_window is None
    assert main_view._close_external_window_on_stop_request is False


def test_wx_main_view_cancel_video_playback_closes_external_window_when_switching_to_audio(monkeypatch):
    """Close the stale external window during a video-to-audio handoff before audio start arrives.

    Edge cases:
        1. EngineController.stop() can finish the video backend before CANCEL_VIDEO_PLAYBACK is delivered to MainView.
        2. The queue can already target audio while the previously selected video track remains cached elsewhere.
        3. Audio playback can start slightly later, so cancellation alone must already be sufficient to dismiss the old external host.
    """
    player_controller = DummyPlayerController()
    main_view, event_bus = build_main_view(monkeypatch, player_controller=player_controller)
    event_bus.publish(AudioEventType.PREPARE_VIDEO_PLAYBACK, {'path': 'movie.mp4', 'loop': False})

    window = main_view._external_video_window
    assert isinstance(window, ExternalVideoWindow)
    destroy_calls: list[str] = []
    original_destroy = window.destroy

    def _wrapped_destroy():
        destroy_calls.append('destroy')
        original_destroy()

    window.destroy = _wrapped_destroy
    player_controller.current_track = SimpleNamespace(media_type=MediaType.AUDIO)
    player_controller.video_controller.is_playing = False
    player_controller.set_runtime_state_payload(
        state='STOPPED',
        playing=False,
        paused=False,
        stopped=True,
        is_video=False,
        is_audio=True,
    )
    main_view._video_session_active = False

    event_bus.publish(AudioEventType.CANCEL_VIDEO_PLAYBACK, {'reason': 'switch_to_audio'})

    assert destroy_calls == ['destroy']
    assert window.frame.destroy_calls == 1
    assert main_view._external_video_window is None
    assert main_view._video_hwnd is None
    assert main_view._pending_video_request is None

def test_wx_main_view_video_playback_ended_closes_external_window_runtime_state(monkeypatch):
    main_view, event_bus = build_main_view(monkeypatch, player_controller=DummyPlayerController())
    event_bus.publish(AudioEventType.PREPARE_VIDEO_PLAYBACK, {'path': 'movie.mp4', 'loop': False})

    window = main_view._external_video_window
    assert isinstance(window, ExternalVideoWindow)
    destroy_calls: list[str] = []
    original_destroy = window.destroy

    def _wrapped_destroy():
        destroy_calls.append('destroy')
        original_destroy()

    window.destroy = _wrapped_destroy

    event_bus.publish(AudioEventType.VIDEO_PLAYBACK_ENDED, {'path': 'movie.mp4', 'duration': 12.0})

    assert destroy_calls == ['destroy']
    assert window.frame.destroy_calls == 1
    assert main_view._external_video_window is None
    assert main_view._video_hwnd is None
    assert main_view._pending_video_request is None
    assert main_view._video_session_active is False


def test_wx_external_video_window_close_event_resets_main_view_runtime_and_publishes_closed(monkeypatch):
    main_view, event_bus = build_main_view(monkeypatch, player_controller=DummyPlayerController())
    event_bus.publish(AudioEventType.PREPARE_VIDEO_PLAYBACK, {'path': 'movie.mp4', 'loop': False})

    window = main_view._external_video_window
    assert isinstance(window, ExternalVideoWindow)

    close_event = FakeCloseEvent(can_veto=False)
    window.frame.trigger(FakeWxModule.EVT_CLOSE, close_event)

    closed_events = [payload for event_type, payload in event_bus.published if event_type == AudioEventType.VIDEO_WINDOW_CLOSED]

    assert close_event.skipped is True
    assert main_view._external_video_window is None
    assert main_view._video_hwnd is None
    assert main_view._pending_video_request is None
    assert main_view._video_session_active is False
    assert closed_events and closed_events[-1]['source'] == 'wx_external_window'
