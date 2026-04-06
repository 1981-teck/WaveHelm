from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

sys.modules.setdefault('pygame', SimpleNamespace(error=RuntimeError, mixer=SimpleNamespace(get_init=lambda: False, quit=lambda: None, init=lambda *args, **kwargs: None, music=SimpleNamespace(set_volume=lambda *args, **kwargs: None))))
sys.modules.setdefault('soundfile', SimpleNamespace())

class _VideoControllerType:
    pass

sys.modules.setdefault('src.controller.video_controller', SimpleNamespace(VideoController=_VideoControllerType))

from src.audio.audio_events import AudioEventBus
from src.audio.audio_engine import AudioEngine
from src.audio.effects import EffectsEngine
from src.audio.equalizer import Equalizer
from src.controller import app_controller as app_mod
from src.controller.app_controller import AppController
from src.controller.effects_controller import EffectsController
from src.controller.equalizer_controller import EqualizerController
from src.controller.library_controller import LibraryController
from src.controller.player_controller import PlayerController
from src.controller.playlist_controller import PlaylistController
from src.controller.settings_controller import SettingsController
from src.controller.video_controller import VideoController
from src.model.ambient_manager import AmbientManager
from src.model.database_manager import DatabaseManager
from src.model.localization_manager import LocalizationManager
from src.model.media_loader import MediaLoader
from src.model.profile_manager import ProfileManager
from src.model.setting_manager import SettingsManager
from src.model.theme_manager import ThemeManager


class DummyQtRoot:
    def __init__(self, result=0, error=None):
        self.result = result
        self.error = error
        self.exec_calls = 0
        self.quit_calls = 0

    def exec(self):
        self.exec_calls += 1
        if self.error is not None:
            raise self.error
        return self.result

    def quit(self):
        self.quit_calls += 1


class DummyWxRoot:
    def __init__(self, result=0, error=None):
        self.result = result
        self.error = error
        self.main_loop_calls = 0
        self.exit_main_loop_calls = 0
        self.top_window = None

    def MainLoop(self):
        self.main_loop_calls += 1
        if self.error is not None:
            raise self.error
        return self.result

    def ExitMainLoop(self):
        self.exit_main_loop_calls += 1

    def SetTopWindow(self, window):
        self.top_window = window


class DummyEventBus:
    def __init__(self):
        self.dispatcher = None
        self.close_calls = 0

    def set_ui_dispatcher(self, dispatcher):
        self.dispatcher = dispatcher

    def close(self):
        self.close_calls += 1


class DummySignal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def emit(self):
        for callback in list(self.callbacks):
            callback()


class DummyPlayerController:
    def __init__(self):
        self.main_view = None
        self.video_factory = None
        self.shutdown_calls = 0

    def set_main_view(self, main_view):
        self.main_view = main_view

    def set_video_controller_factory(self, factory):
        self.video_factory = factory

    def shutdown(self):
        self.shutdown_calls += 1


class DummyVideoController:
    def __init__(self):
        self.shutdown_calls = 0

    def ensure_video_adapter(self):
        return True

    def play_media(self, *args, **kwargs):
        return True

    def stop(self):
        return True

    def shutdown(self):
        self.shutdown_calls += 1


class DummyMainView:
    def __init__(self, master=None, **dependencies):
        self.master = master
        self.dependencies = dependencies
        self.shutdown_calls = 0
        self.show_calls = 0
        self.shutdown_requested = DummySignal()

    def show(self):
        self.show_calls += 1

    def shutdown(self):
        self.shutdown_calls += 1


class DummyServiceContainer:
    def __init__(self, services):
        self.services = dict(services)
        self.main_view = None
        self.close_services_calls = 0

    def get_service(self, service_type):
        if service_type not in self.services:
            raise RuntimeError(f'missing service: {service_type}')
        return self.services[service_type]

    def get_existing_service(self, service_type):
        if service_type not in self.services:
            raise RuntimeError(f'missing existing service: {service_type}')
        return self.services[service_type]

    def set_main_view(self, main_view):
        self.main_view = main_view

    def close_services(self):
        self.close_services_calls += 1


def _build_services():
    event_bus = DummyEventBus()
    player = DummyPlayerController()
    video = DummyVideoController()
    services = {
        AudioEventBus: event_bus,
        LocalizationManager: SimpleNamespace(),
        ThemeManager: SimpleNamespace(),
        DatabaseManager: SimpleNamespace(),
        ProfileManager: SimpleNamespace(),
        SettingsManager: SimpleNamespace(),
        AudioEngine: SimpleNamespace(),
        AmbientManager: SimpleNamespace(),
        PlayerController: player,
        LibraryController: SimpleNamespace(),
        PlaylistController: SimpleNamespace(),
        Equalizer: SimpleNamespace(),
        EqualizerController: SimpleNamespace(),
        EffectsEngine: SimpleNamespace(),
        EffectsController: SimpleNamespace(),
        SettingsController: SimpleNamespace(),
        MediaLoader: SimpleNamespace(),
        VideoController: video,
    }
    return services, event_bus, player, video


def test_initialize_app_builds_main_view_and_wires_player(monkeypatch):
    monkeypatch.setattr(app_mod, '_get_main_view_class', lambda ui_backend=None: DummyMainView)
    services, event_bus, player, video = _build_services()
    root = DummyQtRoot()
    container = DummyServiceContainer(services)
    controller = AppController(root=root, service_container=container)

    class FakeQtDispatcher:
        def __init__(self):
            self.calls = []

        def __call__(self, callback):
            self.calls.append(callback)
            callback()

    fake_dispatcher = FakeQtDispatcher()
    monkeypatch.setattr(app_mod, 'build_ui_dispatcher', lambda root, ui_backend=None: fake_dispatcher)

    controller.initialize_app()

    assert isinstance(controller.main_view, DummyMainView)
    assert controller.main_view.master is root
    assert player.main_view is controller.main_view
    assert callable(player.video_factory)
    assert player.video_factory() is video
    assert container.main_view is controller.main_view
    assert controller.main_view._request_app_shutdown == controller.shutdown

    dispatched = []
    event_bus.dispatcher(lambda: dispatched.append('ok'))
    assert dispatched == ['ok']



def test_initialize_app_builds_wx_dispatcher_when_requested(monkeypatch):
    monkeypatch.setattr(app_mod, '_get_main_view_class', lambda ui_backend=None: DummyMainView)
    services, event_bus, player, video = _build_services()
    container = DummyServiceContainer(services)
    calls = []

    class FakeWxModule:
        @staticmethod
        def CallAfter(callback):
            calls.append('wx-callafter')
            callback()

    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    controller = AppController(root=object(), service_container=container, ui_backend='wx')

    controller.initialize_app()
    dispatched = []
    event_bus.dispatcher(lambda: dispatched.append('wx'))

    assert dispatched == ['wx']
    assert calls == ['wx-callafter']


def test_jit_create_video_controller_returns_none_when_service_lookup_fails():
    services, event_bus, player, video = _build_services()
    services.pop(VideoController)
    controller = AppController(root=None, service_container=DummyServiceContainer(services))

    assert controller.jit_create_video_controller() is None


def test_run_main_loop_rejects_non_qt_root():
    services, event_bus, player, video = _build_services()
    root = object()
    controller = AppController(root=root, service_container=DummyServiceContainer(services))

    with pytest.raises(RuntimeError, match='exec'):
        controller.run_main_loop()


def test_run_main_loop_uses_root_exec_when_available():
    services, event_bus, player, video = _build_services()
    root = DummyQtRoot(result=7)
    controller = AppController(root=root, service_container=DummyServiceContainer(services))

    result = controller.run_main_loop()

    assert result == 7
    assert root.exec_calls == 1


def test_run_main_loop_propagates_exec_failures():
    services, event_bus, player, video = _build_services()
    root = DummyQtRoot(error=RuntimeError("exec failed"))
    controller = AppController(root=root, service_container=DummyServiceContainer(services))

    with pytest.raises(RuntimeError, match="exec failed"):
        controller.run_main_loop()


def test_shutdown_closes_components_once(monkeypatch):
    monkeypatch.setattr(app_mod, '_get_main_view_class', lambda ui_backend=None: DummyMainView)
    services, event_bus, player, video = _build_services()
    root = DummyQtRoot()
    container = DummyServiceContainer(services)
    controller = AppController(root=root, service_container=container)
    controller.initialize_app()

    controller.shutdown()
    controller.shutdown()

    assert player.shutdown_calls == 1
    assert video.shutdown_calls == 1
    assert controller.main_view.shutdown_calls == 1
    assert event_bus.close_calls == 1
    assert controller.root.quit_calls == 1
    assert container.close_services_calls == 1
    assert root.quit_calls == 1


def test_initialize_app_wires_main_view_shutdown_signal(monkeypatch):
    monkeypatch.setattr(app_mod, '_get_main_view_class', lambda ui_backend=None: DummyMainView)
    services, event_bus, player, video = _build_services()
    controller = AppController(root=DummyQtRoot(), service_container=DummyServiceContainer(services))

    controller.initialize_app()
    controller.main_view.shutdown_requested.emit()

    assert player.shutdown_calls == 1
    assert video.shutdown_calls == 1
    assert controller.main_view.shutdown_calls == 1
    assert event_bus.close_calls == 1


class OrderedComponent:
    def __init__(self, label, calls):
        self.label = label
        self.calls = calls

    def shutdown(self):
        self.calls.append(self.label)

    def close(self):
        self.calls.append(self.label)


def test_shutdown_stops_player_then_closes_main_view_then_video_then_event_bus():
    calls = []
    controller = AppController(root=DummyQtRoot(), service_container=DummyServiceContainer({}))
    controller.main_view = OrderedComponent('main_view', calls)
    controller._video_controller_instance = OrderedComponent('video_controller', calls)

    player = OrderedComponent('player_controller', calls)
    event_bus = OrderedComponent('event_bus', calls)

    def _get_existing(service_type):
        mapping = {
            PlayerController: player,
            AudioEventBus: event_bus,
        }
        if service_type not in mapping:
            raise RuntimeError('missing')
        return mapping[service_type]

    container = DummyServiceContainer({})
    container.get_existing_service = _get_existing
    controller.service_container = container

    quit_calls = []
    controller._request_ui_app_quit = lambda: quit_calls.append('quit')

    controller.shutdown()

    assert calls == ['player_controller', 'main_view', 'video_controller', 'event_bus']
    assert container.close_services_calls == 1
    assert quit_calls == ['quit']


def test_run_main_loop_uses_wx_main_loop_when_available():
    services, event_bus, player, video = _build_services()
    root = DummyWxRoot(result=11)
    controller = AppController(root=root, service_container=DummyServiceContainer(services), ui_backend='wx')

    result = controller.run_main_loop()

    assert result == 11
    assert root.main_loop_calls == 1


def test_shutdown_requests_wx_exit_main_loop(monkeypatch):
    monkeypatch.setattr(app_mod, '_get_main_view_class', lambda ui_backend=None: DummyMainView)
    services, event_bus, player, video = _build_services()
    root = DummyWxRoot()
    container = DummyServiceContainer(services)
    controller = AppController(root=root, service_container=container, ui_backend='wx')

    controller.initialize_app()
    controller.shutdown()

    assert root.exit_main_loop_calls == 1
    assert controller.main_view.show_calls == 1


def test_initialize_app_uses_backend_specific_main_view(monkeypatch):
    class DummyWxMainView(DummyMainView):
        pass

    monkeypatch.setattr(app_mod, '_get_main_view_class', lambda ui_backend=None: DummyWxMainView if ui_backend == 'wx' else DummyMainView)
    services, event_bus, player, video = _build_services()
    controller = AppController(root=DummyWxRoot(), service_container=DummyServiceContainer(services), ui_backend='wx')

    controller.initialize_app()

    assert isinstance(controller.main_view, DummyWxMainView)
    assert controller.main_view.show_calls == 1
