"""Initial display volume: zero, bad readings and constructor side-effect contracts.

These test-owned providers and wx widgets exercise the real reader and MiniPlayer.
They do not replace pygame or claim native GUI/device qualification.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from decimal import Decimal
import logging
import math
import sys

import pytest

from src.ui_wx.mini_player import MiniPlayer
from src.ui_wx.mini_player_chrome import MiniPlayerChrome
from tests.test_wx_mini_player import (
    DummyEventBus, DummyLocalizationManager, DummyPlayerController, DummyThemeManager,
)
from tests.wx_fakes import FakeWxModule


@dataclass
class VolumeProvider:
    """Return deliberately varied boundary readings and record all backend writes."""
    value: object
    muted: bool = False
    reads: int = 0
    mute_writes: list[bool] = field(default_factory=list)
    volume_writes: list[float] = field(default_factory=list)

    def get_volume(self) -> object:
        self.reads += 1
        return self.value

    def is_muted(self) -> bool:
        return self.muted

    def set_volume(self, value: float) -> None:
        self.volume_writes.append(value)

    def set_mute(self, muted: bool) -> None:
        self.mute_writes.append(muted)
        self.muted = muted


class Reader(MiniPlayerChrome):
    """Supply only the dynamic boundary dependency of the unmodified real reader."""
    def __init__(self, engine: object) -> None:
        self.audio_engine = engine


@dataclass
class RaisingProvider:
    """Raise an exact error to check the existing exception boundary."""
    error: BaseException

    def get_volume(self) -> float:
        raise self.error


@dataclass
class FloatOnly:
    """A numeric protocol object must not be tested for truth before conversion."""
    value: float

    def __float__(self) -> float:
        return self.value

    def __bool__(self) -> bool:
        raise AssertionError('Volume reading truthiness must not be evaluated')


@pytest.mark.parametrize(
    'value, expected',
    [(0.0, 0.0), (-0.0, 0.0), (0, 0.0), (False, 0.0),
     (Decimal('0'), 0.0), ('0', 0.0), (0.35, 0.35), (1, 1.0),
     (True, 1.0), ('0.25', 0.25), (-0.2, 0.0), (1.4, 1.0)],
    ids=['zero-float', 'negative-zero', 'zero-int', 'false', 'decimal-zero',
         'text-zero', 'fraction', 'one', 'true', 'text-fraction', 'below', 'above'],
)
def test_initial_volume_preserves_zero_and_clamps(value: object, expected: float) -> None:
    provider = VolumeProvider(value)
    result = Reader(provider)._read_initial_volume()
    assert type(result) is float and result == expected
    assert provider.reads == 1
    assert provider.volume_writes == [] and provider.mute_writes == []


@pytest.mark.parametrize('value', [None, '', 'invalid', [], {}, object(), 1j, 10**400],
                         ids=['none', 'empty-text', 'text', 'list', 'dict', 'object',
                              'complex', 'overflow'])
def test_invalid_readings_keep_legacy_display_default(value: object) -> None:
    provider = VolumeProvider(value)
    assert Reader(provider)._read_initial_volume() == 1.0
    assert provider.reads == 1 and provider.volume_writes == []


@pytest.mark.parametrize('value, expected', [(float('nan'), 1.0), (float('inf'), 1.0),
                                            (-float('inf'), 0.0)])
def test_nonfinite_readings_keep_legacy_clamp(value: float, expected: float) -> None:
    result = Reader(VolumeProvider(value))._read_initial_volume()
    assert math.isfinite(result) and result == expected


@pytest.mark.parametrize('engine', [None, object(), type('NonCallable', (), {'get_volume': 0})()],
                         ids=['none', 'missing-getter', 'noncallable-getter'])
def test_missing_getter_keeps_display_default(engine: object) -> None:
    assert Reader(engine)._read_initial_volume() == 1.0


@pytest.mark.parametrize('error_type', [OverflowError, TypeError, ValueError,
                                      AttributeError, RuntimeError])
def test_expected_errors_are_logged_not_hidden(
    caplog: pytest.LogCaptureFixture, error_type: type[Exception],
) -> None:
    failure = error_type('initial volume failure')
    with caplog.at_level(logging.DEBUG, logger='src.ui_wx.mini_player_chrome'):
        assert Reader(RaisingProvider(failure))._read_initial_volume() == 1.0
    records = [r for r in caplog.records if r.message == 'wx MiniPlayer volume bootstrap failed.']
    assert len(records) == 1
    assert records[0].exc_info is not None and records[0].exc_info[1] is failure


@pytest.mark.parametrize('error_type', [OSError, KeyError, AssertionError,
                                      KeyboardInterrupt, SystemExit])
def test_unhandled_errors_still_propagate(error_type: type[BaseException]) -> None:
    failure = error_type('propagate unchanged')
    with pytest.raises(error_type) as caught:
        Reader(RaisingProvider(failure))._read_initial_volume()
    assert caught.value is failure


@pytest.mark.parametrize('value', [0.0, 0.4])
def test_conversion_does_not_probe_numeric_truthiness(value: float) -> None:
    assert Reader(VolumeProvider(FloatOnly(value)))._read_initial_volume() == value


PlayerFixture = tuple[MiniPlayer, VolumeProvider, DummyPlayerController]


@pytest.fixture
def create_player(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[object, bool], PlayerFixture]]:
    """Use the existing wx test boundary and always release callbacks/timers."""
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    widgets: list[MiniPlayer] = []

    def create(value: object, muted: bool) -> PlayerFixture:
        provider = VolumeProvider(value, muted=muted)
        controller = DummyPlayerController()
        widget = MiniPlayer(FakeWxModule.Panel(None), player_controller=controller,
                            audio_engine=provider, event_bus=DummyEventBus(),
                            localization_manager=DummyLocalizationManager(),
                            theme_manager=DummyThemeManager())
        widgets.append(widget)
        return widget, provider, controller

    yield create
    for widget in widgets:
        widget.close()


@pytest.mark.parametrize('value, expected', [(0.0, 0), (-0.0, 0), (0.35, 35), (1.0, 100),
                                            (None, 100), (-1.0, 0), (2.0, 100)],
                         ids=['zero', 'negative-zero', 'fraction', 'one', 'missing', 'below', 'above'])
@pytest.mark.parametrize('muted', [False, True], ids=['unmuted', 'muted'])
def test_real_constructor_only_initializes_display(
    create_player: Callable[[object, bool], PlayerFixture],
    value: object, expected: int, muted: bool,
) -> None:
    widget, provider, controller = create_player(value, muted)
    assert widget.volume_slider.GetValue() == expected
    assert widget._muted is muted and provider.muted is muted
    assert provider.reads == 1 and widget._volume_sync is False
    assert provider.volume_writes == [] and provider.mute_writes == []
    assert controller.volume_calls == []
    assert widget._last_nonzero_volume == (expected / 100.0 if expected else 1.0)


def test_zero_bootstrap_keeps_later_user_volume_and_unmute(
    create_player: Callable[[object, bool], PlayerFixture],
) -> None:
    widget, provider, controller = create_player(0.0, True)
    assert widget.volume_slider.GetValue() == 0
    widget.volume_slider.SetValue(40)
    widget._on_volume_slider_changed()
    assert controller.volume_calls == [0.4]
    assert provider.mute_writes == [False] and widget._muted is False
    assert widget._last_nonzero_volume == 0.4


def test_zero_bootstrap_keeps_audio_mute_toggle(
    create_player: Callable[[object, bool], PlayerFixture],
) -> None:
    widget, provider, controller = create_player(0.0, False)
    assert widget.volume_slider.GetValue() == 0
    widget._on_mute_clicked()
    widget._on_mute_clicked()
    assert provider.mute_writes == [True, False]
    assert controller.volume_calls == [] and widget.volume_slider.GetValue() == 0
