from __future__ import annotations

from types import SimpleNamespace

from src.controller.video_controller import VideoController
from src.ui_wx import video_view as video_view_module
from src.ui_wx.video_view import ExternalVideoWindow
from tests.test_wx_video_view import DummyEventBus, DummyLocalizationManager, DummyThemeManager
from tests.wx_fakes import FakeWxModule


def _window(monkeypatch, controller):
    monkeypatch.setenv('WAVEHELM_VIDEO_BACKEND', 'wic')
    captured = {}

    class Driver:
        def __init__(self, wx, panel, *, pipeline_resolver, error_reporter):
            captured['resolver'] = pipeline_resolver
            captured['reporter'] = error_reporter

        def close(self):
            return None

    monkeypatch.setattr(video_view_module, 'WicSurfaceDriver', Driver)
    player = SimpleNamespace(engine_controller=SimpleNamespace(video_controller=controller))
    window = ExternalVideoWindow(
        FakeWxModule,
        player_controller=player,
        event_bus=DummyEventBus(),
        localization_manager=DummyLocalizationManager(),
        theme_manager=DummyThemeManager(),
    )
    return window, captured


def test_wic_window_resolves_lazy_engine_video_controller(monkeypatch):
    service = object()
    calls = []
    errors = []

    class Controller:
        def get_frame_pipeline(self, hwnd):
            calls.append(int(hwnd))
            return service

        def report_frame_error(self, error):
            errors.append(error)

    window, captured = _window(monkeypatch, Controller())
    hwnd = window.video_surface.get_hwnd()

    assert captured['resolver'](hwnd) is service
    assert calls == [hwnd]
    failure = RuntimeError('frame failed')
    captured['reporter'](failure)
    assert errors == [failure]


def test_wic_window_does_not_depend_on_adapter_factory_singleton(monkeypatch):
    controller = SimpleNamespace(
        get_frame_pipeline=lambda hwnd: 'live-service',
        report_frame_error=lambda error: None,
    )
    window, captured = _window(monkeypatch, controller)

    assert captured['resolver'](window.video_surface.get_hwnd()) == 'live-service'


def test_video_controller_exposes_only_its_owned_adapter_pipeline():
    bus = DummyEventBus()
    controller = VideoController(bus)
    service = object()
    errors = []
    controller._adapter = SimpleNamespace(
        get_frame_pipeline=lambda hwnd: service if hwnd == 77 else None,
        report_frame_error=errors.append,
    )

    assert controller.get_frame_pipeline(77) is service
    assert controller.get_frame_pipeline(78) is None
    failure = RuntimeError('presentation failed')
    controller.report_frame_error(failure)
    assert errors == [failure]
