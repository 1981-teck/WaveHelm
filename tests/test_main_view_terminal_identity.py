"""Terminal identity with real MainView/event bus and test-owned wx controls.

No native window, media, network request or successful adapter is created. The
path admission contract deliberately does not invent a shared playback revision.
"""
from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator
from copy import deepcopy
import inspect
import sys

import pytest

from src.audio.audio_event_bus import AudioEventBus
from src.audio.audio_event_models import AudioEventType
from src.ui_wx.main_view import MainView
from src.ui_wx.video_view import ExternalVideoWindow
from tests.gesture_fakes import install_progress_fakes
from tests.test_wx_video_view import (
    DummyLocalizationManager, DummyPlayerController, DummyThemeManager,
)
from tests.wx_fakes import FakeApp, FakeWxModule

EVENTS = [
    AudioEventType.VIDEO_PLAYBACK_ERROR,
    AudioEventType.VIDEO_PLAYBACK_ENDED,
    AudioEventType.VIDEO_PLAYBACK_STOPPED,
]
METHODS = {
    EVENTS[0]: '_on_video_playback_error',
    EVENTS[1]: '_on_video_playback_ended',
    EVENTS[2]: '_on_video_playback_stopped',
}
EVENT_IDS = ['error', 'ended', 'stopped']
DISTINCT = [
    ('current.mp4', 'previous.mp4'),
    ('https://streams.example/Live.m3u8', 'https://streams.example/live.m3u8'),
    ('https://streams.example/live?id=AB12', 'https://streams.example/live?id=ab12'),
    ('https://User@streams.example/live', 'https://user@streams.example/live'),
    ('https://streams.example/live#Part', 'https://streams.example/live#part'),
    ('custom:Current', 'custom:current'),
]
DISTINCT_IDS = ['local', 'path-case', 'query-case', 'userinfo', 'fragment', 'opaque']
MATCHING = [
    ('current.mp4', 'current.mp4'),
    (r'C:\Videos\Current.MP4', r'c:\videos\current.mp4'),
    (r'\\Server\Share\Current.MP4', r'\\server\share\current.mp4'),
    ('HTTPS://STREAMS.EXAMPLE/Live?id=AB12', 'https://streams.example/Live?id=AB12'),
]


class _Timer:
    def __init__(self):
        self.stops = 0

    def Stop(self):
        self.stops += 1


@contextmanager
def _opened(
    monkeypatch: pytest.MonkeyPatch, path: str = 'current.mp4', started: bool = False,
) -> Iterator[tuple[MainView, AudioEventBus, ExternalVideoWindow]]:
    """Generic lifecycle fixture, not a native WIC/GDI integration test.

    Edges: inherited WIC cannot reach native GDI; platform defaults cannot change
    this fixture's legacy contract; pytest restores the environment after each test.
    Dedicated WIC/default-selection tests retain their explicit backend coverage.
    """
    monkeypatch.setenv('WAVEHELM_VIDEO_BACKEND', 'legacy_hwnd')
    install_progress_fakes(monkeypatch)
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    bus = AudioEventBus(enable_async_processing=False)
    view = None
    try:
        view = MainView(
            FakeApp(), event_bus=bus, player_controller=DummyPlayerController(),
            theme_manager=DummyThemeManager(),
            localization_manager=DummyLocalizationManager(),
        )
        bus.publish(AudioEventType.PREPARE_VIDEO_PLAYBACK, {'path': path, 'loop': False})
        assert view._external_video_window is not None
        if started:
            bus.publish(AudioEventType.VIDEO_PLAYBACK_STARTED, {'path': path})
            assert view._pending_video_request is None
        yield view, bus, view._external_video_window
    finally:
        try:
            if view is not None:
                view.shutdown()
        finally:
            bus.shutdown()


def _arm(view, monkeypatch):
    timer = _Timer()
    status = []
    view._hwnd_retry_timer = timer
    view._deferred_external_window_close = True
    view._deferred_external_window_close_on_user_interrupt = True
    view._close_external_window_on_stop_request = True
    monkeypatch.setattr(view, '_update_video_status', status.append)
    return timer, status


def _snapshot(view, window, timer, status):
    return (
        view._external_video_window is window, window.frame.destroy_calls,
        deepcopy(view._pending_video_request), view._last_video_ready_signature,
        view._last_video_surface_signature, view._video_hwnd,
        view._video_session_active, view._deferred_external_window_close,
        view._deferred_external_window_close_on_user_interrupt,
        view._close_external_window_on_stop_request,
        view._hwnd_retry_timer is timer, timer.stops, tuple(status),
    )


@pytest.mark.parametrize('event', EVENTS, ids=EVENT_IDS)
@pytest.mark.parametrize('current,old', DISTINCT, ids=DISTINCT_IDS)
@pytest.mark.parametrize('started', [False, True], ids=['pending', 'ready'])
def test_stale_terminal_preserves_all_current_window_state(monkeypatch, event, current, old, started):
    with _opened(monkeypatch, current, started) as (view, bus, window):
        timer, status = _arm(view, monkeypatch)
        before = _snapshot(view, window, timer, status)
        payload = {'path': old, 'error': 'synthetic stale error', 'playback_revision': 1}
        original = dict(payload)
        bus.publish(event, payload)
        assert _snapshot(view, window, timer, status) == before
        assert payload == original
        assert bus.get_stats()['errors'] == 0


@pytest.mark.parametrize('event', EVENTS, ids=EVENT_IDS)
@pytest.mark.parametrize('current,old', DISTINCT[:3], ids=DISTINCT_IDS[:3])
def test_queued_terminal_rechecks_identity_at_delivery(monkeypatch, event, current, old):
    with _opened(monkeypatch, old) as (view, bus, window):
        queued = []
        bus.set_ui_dispatcher(queued.append)
        assert bus.publish(event, {'path': old}, require_ui_thread=True)
        assert queued
        bus.publish(AudioEventType.PREPARE_VIDEO_PLAYBACK, {'path': current})
        timer, status = _arm(view, monkeypatch)
        before = _snapshot(view, window, timer, status)
        for callback in queued:
            callback()
        assert _snapshot(view, window, timer, status) == before
        assert bus.get_stats()['errors'] == 0


@pytest.mark.parametrize('event', EVENTS, ids=EVENT_IDS)
@pytest.mark.parametrize('current,path', MATCHING, ids=['exact', 'drive', 'unc', 'scheme-host'])
@pytest.mark.parametrize('started', [False, True], ids=['pending', 'ready'])
def test_matching_terminal_still_completes_deferred_close_once(monkeypatch, event, current, path, started):
    with _opened(monkeypatch, current, started) as (view, bus, window):
        timer, _ = _arm(view, monkeypatch)
        closed = []
        bus.subscribe(AudioEventType.VIDEO_WINDOW_CLOSED, closed.append)
        bus.publish(event, {'path': path, 'error': 'synthetic current error'})
        bus.publish(event, {'path': path})
        assert view._external_video_window is None
        assert window.frame.destroy_calls == 1 and timer.stops == 1
        assert view._pending_video_request is None and not view._video_session_active
        assert not view._deferred_external_window_close and closed == []
        assert bus.get_stats()['errors'] == 0


@pytest.mark.parametrize('event', EVENTS, ids=EVENT_IDS)
@pytest.mark.parametrize('payload', [None, {}, {'path': ''}, {'path': '  '}, []],
                         ids=['none', 'no-key', 'empty', 'space', 'legacy-sequence'])
def test_pathless_terminal_retains_declared_legacy_behavior(monkeypatch, event, payload):
    with _opened(monkeypatch) as (view, bus, window):
        _arm(view, monkeypatch)
        bus.publish(event, payload)
        assert view._external_video_window is None and window.frame.destroy_calls == 1
        assert bus.get_stats()['errors'] == 0


@pytest.mark.parametrize('event', EVENTS, ids=EVENT_IDS)
def test_pending_identity_wins_over_an_older_ready_signature(monkeypatch, event):
    with _opened(monkeypatch) as (view, bus, window):
        timer, status = _arm(view, monkeypatch)
        hwnd = view._last_video_ready_signature[0]
        view._last_video_ready_signature = (hwnd, 'old.mp4', False)
        before = _snapshot(view, window, timer, status)
        bus.publish(event, {'path': 'old.mp4'})
        assert _snapshot(view, window, timer, status) == before
        bus.publish(event, {'path': 'current.mp4'})
        assert view._external_video_window is None and window.frame.destroy_calls == 1


@pytest.mark.parametrize('event', EVENTS, ids=EVENT_IDS)
@pytest.mark.parametrize('active', [False, True], ids=['inactive', 'active'])
def test_missing_known_path_keeps_existing_active_state_policy(monkeypatch, event, active):
    with _opened(monkeypatch) as (view, bus, window):
        view._pending_video_request = None
        view._last_video_ready_signature = None
        view._video_session_active = active
        timer, status = _arm(view, monkeypatch)
        before = _snapshot(view, window, timer, status)
        bus.publish(event, {'path': 'other.mp4'})
        if active:
            assert _snapshot(view, window, timer, status) == before
        else:
            assert view._external_video_window is None and window.frame.destroy_calls == 1


@pytest.mark.parametrize('event', EVENTS, ids=EVENT_IDS)
def test_queued_pathless_callback_after_shutdown_is_inert(monkeypatch, event):
    with _opened(monkeypatch) as (view, bus, window):
        callbacks = []
        bus.set_ui_dispatcher(callbacks.append)
        assert bus.publish(event, {}, require_ui_thread=True)
        assert callbacks
        view.shutdown()
        timer, status = _arm(view, monkeypatch)
        before = _snapshot(view, window, timer, status)
        for callback in callbacks:
            callback()
        assert _snapshot(view, window, timer, status) == before
        assert bus.get_stats()['errors'] == 0


def test_stale_error_rejected_before_reading_its_message(monkeypatch):
    class Message:
        def __str__(self):
            raise RuntimeError('Stale message must not be rendered')
    with _opened(monkeypatch) as (view, bus, window):
        timer, status = _arm(view, monkeypatch)
        before = _snapshot(view, window, timer, status)
        view._on_video_playback_error({'path': 'old.mp4', 'error': Message()})
        assert _snapshot(view, window, timer, status) == before
        assert bus.get_stats()['errors'] == 0


@pytest.mark.parametrize('event', EVENTS, ids=EVENT_IDS)
@pytest.mark.parametrize('error_type', [RuntimeError, KeyboardInterrupt], ids=['runtime', 'interrupt'])
def test_matching_destroy_failure_still_propagates(monkeypatch, event, error_type):
    with _opened(monkeypatch) as (view, _, window):
        _arm(view, monkeypatch)
        original = window.destroy
        error = error_type('synthetic destroy error')
        def fail():
            raise error
        monkeypatch.setattr(window, 'destroy', fail)
        try:
            with pytest.raises(error_type) as caught:
                getattr(view, METHODS[event])({'path': 'current.mp4'})
            assert caught.value is error
            assert not view._deferred_external_window_close
        finally:
            monkeypatch.setattr(window, 'destroy', original)


@pytest.mark.parametrize('event', EVENTS, ids=EVENT_IDS)
def test_close_reentry_and_legacy_refresh_keyword_remain_idempotent(monkeypatch, event):
    with _opened(monkeypatch) as (view, bus, window):
        _arm(view, monkeypatch)
        original = window.destroy
        calls = []
        def reenter():
            calls.append(view._close_external_video_window_if_deferred(
                reason='nested', status_message='nested', refresh_force=False))
            original()
        monkeypatch.setattr(window, 'destroy', reenter)
        bus.publish(event, {'path': 'current.mp4'})
        assert calls == [False] and window.frame.destroy_calls == 1
        parameter = inspect.signature(MainView._close_external_video_window_if_deferred).parameters['refresh_force']
        assert parameter.default is True and parameter.kind is inspect.Parameter.KEYWORD_ONLY


@pytest.mark.parametrize('event', EVENTS, ids=EVENT_IDS)
def test_same_path_revision_is_not_fabricated_from_unbound_fields(monkeypatch, event):
    """Document a limit: PREPARE does not bind revision; this is not session proof."""
    with _opened(monkeypatch) as (view, bus, window):
        bus.publish(AudioEventType.PREPARE_VIDEO_PLAYBACK,
                    {'path': 'current.mp4', 'playback_revision': 2})
        assert set(view._pending_video_request) == {'path', 'loop'}
        _arm(view, monkeypatch)
        bus.publish(event, {'path': 'current.mp4', 'playback_revision': 1})
        assert view._external_video_window is None and window.frame.destroy_calls == 1


@pytest.mark.parametrize('event', EVENTS, ids=EVENT_IDS)
@pytest.mark.parametrize('path', ['https:///missing', 'https://streams.example:99999/x'],
                         ids=['missing-host', 'invalid-port'])
def test_invalid_nonempty_stream_identity_cannot_match_itself(monkeypatch, event, path):
    with _opened(monkeypatch, path) as (view, bus, window):
        timer, status = _arm(view, monkeypatch)
        before = _snapshot(view, window, timer, status)
        bus.publish(event, {'path': path})
        assert _snapshot(view, window, timer, status) == before


@pytest.mark.parametrize('payload', [None, {}, {'path': 'clip.mp4'}],
                         ids=['none', 'empty', 'identified'])
def test_legacy_partial_shell_has_no_fabricated_current_identity(payload):
    """Existing terminal handoff fixtures use an uninitialized MainView shell."""
    view = MainView.__new__(MainView)
    before = dict(vars(view))
    assert not view._should_ignore_video_playback_stopped(payload)
    assert vars(view) == before


@pytest.mark.parametrize('inherited_backend', [None, 'wic'], ids=['unset', 'wic'])
def test_prepare_source_switch_reuses_external_window_without_focus_steal(
    monkeypatch: pytest.MonkeyPatch, inherited_backend: str | None,
) -> None:
    """Keep one external host active across source switches without re-activation.

    Edge cases:
        1. The first PREPARE must still show, raise, and activate the new external window.
        2. A later PREPARE for another video must reuse the same window and HWND.
        3. The second source must still publish a fresh VIDEO_PLAYBACK_READY receipt.
    """
    if inherited_backend is None:
        monkeypatch.delenv('WAVEHELM_VIDEO_BACKEND', raising=False)
    else:
        monkeypatch.setenv('WAVEHELM_VIDEO_BACKEND', inherited_backend)
    with _opened(monkeypatch, 'first.mp4') as (view, bus, window):
        first_hwnd = view.get_video_hwnd(allow_cached=False)
        assert window.frame.show_calls == [True]
        assert window.frame.raise_calls == 1
        assert window.frame.activate_calls == 1

        ready: list[dict[str, object]] = []
        bus.subscribe(AudioEventType.VIDEO_PLAYBACK_READY, ready.append)
        bus.publish(AudioEventType.PREPARE_VIDEO_PLAYBACK, {'path': 'second.mp4', 'loop': False})

        assert view._external_video_window is window
        assert view.get_video_hwnd(allow_cached=False) == first_hwnd
        assert window.frame.show_calls == [True]
        assert window.frame.raise_calls == 1
        assert window.frame.activate_calls == 1
        assert ready[-1]['path'] == 'second.mp4'
        assert ready[-1]['hwnd'] == first_hwnd
