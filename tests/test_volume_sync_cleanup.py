"""Volume guard cleanup at the real MiniPlayer/test-owned wx boundary.

Widget errors must propagate unchanged; nested writes must preserve the outer
synchronization state; subsequent user input must not be silently dropped.
The controls/controllers below are the existing repository fixtures, not native
Windows or real mixer substitutes. No physical audio/video device is started.
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest

from src.audio.audio_event_models import AudioEventType
from src.ui_wx.mini_player import MiniPlayer
from tests.test_wx_mini_player import (
    DummyAudioEngine, DummyEventBus, DummyPlayerController, build_mini_player,
)

Player = tuple[MiniPlayer, DummyPlayerController, DummyEventBus, DummyAudioEngine]


@pytest.fixture
def player(monkeypatch: pytest.MonkeyPatch) -> Iterator[Player]:
    built = build_mini_player(monkeypatch)
    try:
        yield built
    finally:
        built[0].close()


@pytest.mark.parametrize('prior', [False, True])
@pytest.mark.parametrize('error_type', [RuntimeError, ValueError, OSError,
                                      KeyboardInterrupt, SystemExit])
def test_widget_failure_restores_prior_guard_and_propagates(
    player: Player, monkeypatch: pytest.MonkeyPatch,
    prior: bool, error_type: type[BaseException],
) -> None:
    view, controller, _, engine = player
    failure = error_type('synthetic slider failure')
    calls: list[int] = []
    view._volume_sync = prior

    def write(value: int) -> None:
        calls.append(value)
        assert view._volume_sync is True
        raise failure

    monkeypatch.setattr(view.volume_slider, 'SetValue', write)
    with pytest.raises(error_type) as caught:
        view._handle_volume_event({'volume': 0.65})
    assert caught.value is failure
    assert calls == [65] and view._volume_sync is prior
    assert view._last_nonzero_volume == 0.65  # Existing nontransactional display state.
    assert not view._closed and controller.volume_calls == []
    assert engine.set_mute_calls == [] and engine.seek_calls == []


@pytest.mark.parametrize('muted', [False, True])
@pytest.mark.parametrize('user_value', [0, 25, 100])
def test_first_user_gesture_after_failure_is_delivered_once(
    player: Player, monkeypatch: pytest.MonkeyPatch, muted: bool, user_value: int,
) -> None:
    view, controller, _, engine = player
    original = view.volume_slider.SetValue
    view._muted = muted
    engine.muted = muted

    def fail_once(_value: int) -> None:
        raise RuntimeError('transient widget error')

    monkeypatch.setattr(view.volume_slider, 'SetValue', fail_once)
    with pytest.raises(RuntimeError, match='transient widget error'):
        view._handle_volume_event({'volume': 0.65})
    monkeypatch.setattr(view.volume_slider, 'SetValue', original)
    original(user_value)
    view._on_volume_slider_changed()
    assert controller.volume_calls == [user_value / 100.0]
    assert engine.set_mute_calls == ([False] if muted and user_value > 0 else [])
    assert view._muted is (muted and user_value == 0)
    assert view._last_nonzero_volume == (user_value / 100.0 if user_value else 0.65)
    assert view._volume_sync is False and engine.seek_calls == []


@pytest.mark.parametrize('prior', [False, True])
@pytest.mark.parametrize('value, expected', [(0.0, 0), (0.005, 0), (0.015, 2),
                                            (0.35, 35), (1.0, 100)])
def test_programmatic_write_suppresses_callback_and_keeps_rounding(
    player: Player, monkeypatch: pytest.MonkeyPatch,
    prior: bool, value: float, expected: int,
) -> None:
    view, controller, _, engine = player
    previous_last = view._last_nonzero_volume
    original = view.volume_slider.SetValue
    guards: list[bool] = []
    view._volume_sync = prior

    def write(position: int) -> None:
        original(position)
        guards.append(view._volume_sync)
        view._on_volume_slider_changed()

    monkeypatch.setattr(view.volume_slider, 'SetValue', write)
    assert view._set_volume_slider(value) is None
    assert guards == [True] and view._volume_sync is prior
    assert view.volume_slider.GetValue() == expected
    assert view._last_nonzero_volume == (value if value > 0 else previous_last)
    assert controller.volume_calls == [] and engine.set_mute_calls == []


@pytest.mark.parametrize('prior', [False, True])
def test_nested_success_does_not_release_the_outer_guard(
    player: Player, monkeypatch: pytest.MonkeyPatch, prior: bool,
) -> None:
    view, controller, _, _ = player
    original = view.volume_slider.SetValue
    guards: list[bool] = []
    calls: list[int] = []
    view._volume_sync = prior

    def write(position: int) -> None:
        calls.append(position)
        guards.append(view._volume_sync)
        if position == 70:
            view._set_volume_slider(0.2)
            guards.append(view._volume_sync)
        original(position)
        view._on_volume_slider_changed()

    monkeypatch.setattr(view.volume_slider, 'SetValue', write)
    view._set_volume_slider(0.7)
    assert calls == [70, 20] and guards == [True, True, True]
    assert view._volume_sync is prior and controller.volume_calls == []
    assert view.volume_slider.GetValue() == 70
    assert view._last_nonzero_volume == 0.2  # Preserve reentrant update order.


@pytest.mark.parametrize('prior', [False, True])
@pytest.mark.parametrize('recover_inner', [False, True])
def test_nested_failure_keeps_outer_guard_until_outer_exit(
    player: Player, monkeypatch: pytest.MonkeyPatch, prior: bool, recover_inner: bool,
) -> None:
    view, controller, _, _ = player
    original = view.volume_slider.SetValue
    failure = RuntimeError('nested widget failure')
    view._volume_sync = prior
    after_inner: list[bool] = []

    def write(position: int) -> None:
        assert view._volume_sync is True
        if position == 20:
            raise failure
        if recover_inner:
            with pytest.raises(RuntimeError) as caught:
                view._set_volume_slider(0.2)
            assert caught.value is failure
            after_inner.append(view._volume_sync)
            original(position)
            view._on_volume_slider_changed()
        else:
            view._set_volume_slider(0.2)

    monkeypatch.setattr(view.volume_slider, 'SetValue', write)
    if recover_inner:
        view._set_volume_slider(0.7)
        assert after_inner == [True]
    else:
        with pytest.raises(RuntimeError) as caught:
            view._set_volume_slider(0.7)
        assert caught.value is failure
    assert view._volume_sync is prior and controller.volume_calls == []


@pytest.mark.parametrize('prior', [False, True])
@pytest.mark.parametrize('value, error_type', [(float('nan'), ValueError),
                                             (float('inf'), OverflowError),
                                             (-float('inf'), OverflowError)],
                         ids=['nan', 'positive-inf', 'negative-inf'])
def test_conversion_failure_releases_guard_without_widget_write(
    player: Player, monkeypatch: pytest.MonkeyPatch,
    prior: bool, value: float, error_type: type[Exception],
) -> None:
    view, controller, _, _ = player
    calls: list[int] = []
    view._volume_sync = prior
    monkeypatch.setattr(view.volume_slider, 'SetValue', calls.append)
    with pytest.raises(error_type):
        view._set_volume_slider(value)
    assert calls == [] and controller.volume_calls == []
    assert view._volume_sync is prior


@pytest.mark.parametrize('close_then_fail', [False, True])
def test_close_during_write_preserves_cleanup_and_blocks_late_delivery(
    player: Player, monkeypatch: pytest.MonkeyPatch, close_then_fail: bool,
) -> None:
    view, controller, event_bus, _ = player
    calls: list[int] = []
    failure = RuntimeError('closed during widget write')

    def write(position: int) -> None:
        calls.append(position)
        view.close()
        if close_then_fail:
            raise failure

    monkeypatch.setattr(view.volume_slider, 'SetValue', write)
    if close_then_fail:
        with pytest.raises(RuntimeError) as caught:
            view._handle_volume_event({'volume': 0.4})
        assert caught.value is failure
    else:
        view._handle_volume_event({'volume': 0.4})
    assert view._closed and view._volume_sync is False
    view._handle_volume_event({'volume': 0.8})
    event_bus.publish(AudioEventType.VOLUME_CHANGED, {'volume': 0.9})
    assert calls == [40] and controller.volume_calls == []
    assert view._subscriptions == [] and view._progress_timer is None


def test_queued_volume_event_is_ignored_after_close(
    player: Player, monkeypatch: pytest.MonkeyPatch,
) -> None:
    view, controller, _, _ = player
    queued: list[object] = []
    calls: list[int] = []
    monkeypatch.setattr(view._wx, 'CallAfter', queued.append)
    monkeypatch.setattr(view.volume_slider, 'SetValue', calls.append)
    view._handle_volume_event({'volume': 0.8})
    assert len(queued) == 1
    view.close()
    delivery = queued[0]
    assert callable(delivery)
    delivery()
    assert calls == [] and controller.volume_calls == []
    assert view._volume_sync is False


def test_event_bus_recovers_without_an_extra_programmatic_refresh(
    player: Player, monkeypatch: pytest.MonkeyPatch,
) -> None:
    view, controller, event_bus, _ = player
    original = view.volume_slider.SetValue

    def fail(_position: int) -> None:
        raise RuntimeError('event-bus widget failure')

    monkeypatch.setattr(view.volume_slider, 'SetValue', fail)
    with pytest.raises(RuntimeError, match='event-bus widget failure'):
        event_bus.publish(AudioEventType.VOLUME_CHANGED, {'volume': 0.6})
    monkeypatch.setattr(view.volume_slider, 'SetValue', original)
    original(25)
    view._on_volume_slider_changed()
    assert view._volume_sync is False and controller.volume_calls == [0.25]
    assert len(event_bus.subscriptions[AudioEventType.VOLUME_CHANGED]) == 1


@pytest.mark.parametrize('muted', [False, True])
@pytest.mark.parametrize('fail_widget', [False, True])
def test_video_mute_keeps_existing_backend_and_label_commit_order(
    player: Player, monkeypatch: pytest.MonkeyPatch, muted: bool, fail_widget: bool,
) -> None:
    from src.model.media_file import MediaType

    view, controller, _, engine = player
    controller.current_track.media_type = MediaType.VIDEO
    view._muted = muted
    previous_last = view._last_nonzero_volume
    expected = previous_last if muted else 0.0
    original = view.volume_slider.SetValue

    def write(position: int) -> None:
        assert view._volume_sync is True
        if fail_widget:
            raise RuntimeError('video mute drawing failure')
        original(position)
        view._on_volume_slider_changed()

    monkeypatch.setattr(view.volume_slider, 'SetValue', write)
    if fail_widget:
        with pytest.raises(RuntimeError, match='video mute drawing failure'):
            view._on_mute_clicked()
        assert view._muted is muted  # Backend request precedes drawing, as before.
    else:
        view._on_mute_clicked()
        assert view._muted is not muted
        assert view.volume_slider.GetValue() == int(round(expected * 100))
    assert controller.volume_calls == [expected]
    assert view._last_nonzero_volume == previous_last
    assert engine.set_mute_calls == [] and view._volume_sync is False
