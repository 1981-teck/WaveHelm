from __future__ import annotations

import pytest

import src.services.service_container as service_container_module
import src.services.service_container_factories as service_container_factories_module
from src.audio.audio_engine import AudioEngine
from src.audio.audio_events import AudioEventBus
from src.audio.effects import EffectsEngine
from src.audio.equalizer import Equalizer
from src.controller.player_controller import PlayerController
from src.controller.video_controller import VideoController
from src.model.localization_manager import LocalizationManager
from src.model.setting_manager import SettingsManager
from src.services.service_container import ServiceContainer


class FakeService:
    def __init__(self, name: str, calls: list[str], *, fail: bool = False):
        self.name = name
        self.calls = calls
        self.fail = fail

    def shutdown(self):
        self.calls.append(self.name)
        if self.fail:
            raise RuntimeError(f'{self.name} fail')


class FactoryContainerStub:
    def __init__(self, services=None, existing_services=None):
        self._services = services or {}
        self._existing_services = existing_services or {}

    def get_service(self, service_type):
        return self._services[service_type]

    def get_existing_service(self, service_type):
        return self._existing_services.get(service_type)


@pytest.fixture
def bare_container(monkeypatch):
    monkeypatch.setattr(ServiceContainer, '_register_core_services', lambda self: None)
    monkeypatch.setattr(ServiceContainer, '_register_app_services', lambda self: None)
    return ServiceContainer()


def test_service_container_get_service_caches_instances(bare_container):
    create_calls = []

    class FakeType:
        pass

    bare_container._register_service(
        FakeType,
        lambda: create_calls.append('create') or {'instance': True},
        [],
        10,
    )

    first = bare_container.get_service(FakeType)
    second = bare_container.get_service(FakeType)

    assert first is second
    assert create_calls == ['create']
    assert bare_container.has_service(FakeType) is True
    assert bare_container.get_existing_service(FakeType) is first


def test_service_container_unknown_service_raises_keyerror(bare_container):
    class Missing:
        pass

    with pytest.raises(KeyError):
        bare_container.get_service(Missing)


def test_service_container_shutdown_orders_services_and_tolerates_failures(bare_container):
    calls = []

    class High:
        pass

    class Mid:
        pass

    class Low:
        pass

    bare_container._register_service(High, lambda: FakeService('high', calls, fail=True), [], 30)
    bare_container._register_service(Mid, lambda: FakeService('mid', calls), [], 20)
    bare_container._register_service(Low, lambda: FakeService('low', calls), [], 10)

    bare_container.get_service(Low)
    bare_container.get_service(High)
    bare_container.get_service(Mid)
    bare_container.set_main_view(object())

    bare_container.shutdown_all()

    assert calls == ['high', 'mid', 'low']
    assert bare_container.get_existing_service(High) is None
    assert bare_container.get_main_view() is None


def test_global_service_container_singleton_and_shutdown(monkeypatch):
    monkeypatch.setattr(ServiceContainer, '_register_core_services', lambda self: None)
    monkeypatch.setattr(ServiceContainer, '_register_app_services', lambda self: None)
    service_container_module._service_container_instance = None

    first = service_container_module.get_service_container()
    second = service_container_module.get_service_container()
    assert first is second

    service_container_module.shutdown_service_container()
    assert service_container_module._service_container_instance is None


def test_audio_engine_factory_uses_explicit_constructor_contract(monkeypatch):
    captured = {}

    class FakeAudioEngine:
        def __init__(
            self,
            localization_manager,
            settings_manager,
            event_bus,
            effects_engine=None,
            equalizer=None,
        ):
            captured['localization_manager'] = localization_manager
            captured['settings_manager'] = settings_manager
            captured['event_bus'] = event_bus
            captured['effects_engine'] = effects_engine
            captured['equalizer'] = equalizer

    localization_manager = object()
    settings_manager = object()
    event_bus = object()
    effects_engine = object()
    equalizer = object()

    container = FactoryContainerStub(
        services={
            LocalizationManager: localization_manager,
            SettingsManager: settings_manager,
            AudioEventBus: event_bus,
        },
        existing_services={
            EffectsEngine: effects_engine,
            Equalizer: equalizer,
        },
    )

    monkeypatch.setattr(service_container_factories_module, 'AudioEngine', FakeAudioEngine)

    instance = service_container_factories_module._create_audio_engine(container)

    assert isinstance(instance, FakeAudioEngine)
    assert captured == {
        'localization_manager': localization_manager,
        'settings_manager': settings_manager,
        'event_bus': event_bus,
        'effects_engine': effects_engine,
        'equalizer': equalizer,
    }


def test_audio_engine_factory_fails_fast_when_constructor_contract_changes(monkeypatch):
    class LegacyAudioEngine:
        def __init__(self, event_bus):
            self.event_bus = event_bus

    container = FactoryContainerStub(
        services={
            LocalizationManager: object(),
            SettingsManager: object(),
            AudioEventBus: object(),
        },
    )

    monkeypatch.setattr(service_container_factories_module, 'AudioEngine', LegacyAudioEngine)

    with pytest.raises(TypeError):
        service_container_factories_module._create_audio_engine(container)


def test_player_controller_factory_builds_current_facade_wiring(monkeypatch):
    import src.controller.component_player.engine_controller as engine_controller_module
    import src.controller.component_player.playback_state_manager as playback_state_manager_module
    import src.controller.component_player.player_event_handler as player_event_handler_module
    import src.controller.component_player.progress_tracker as progress_tracker_module
    import src.controller.component_player.queue_manager as queue_manager_module

    class FakeEventBus:
        def __init__(self):
            self.published = []

        def publish(self, *args, **kwargs):
            self.published.append((args, kwargs))

    class FakePlaybackStateManager:
        def __init__(self, event_bus):
            self.event_bus = event_bus

    class FakeQueueManager:
        def __init__(self):
            self.created = True

    class FakeEngineController:
        def __init__(
            self,
            state_manager,
            audio_engine,
            video_controller,
            video_controller_factory=None,
        ):
            self.state_manager = state_manager
            self.audio_engine = audio_engine
            self.video_controller = video_controller
            self.video_controller_factory = video_controller_factory

    class FakeProgressTracker:
        def __init__(
            self,
            state_manager,
            queue_manager,
            engine_controller,
            event_publisher,
            on_track_end_callback,
        ):
            self.state_manager = state_manager
            self.queue_manager = queue_manager
            self.engine_controller = engine_controller
            self.event_publisher = event_publisher
            self.on_track_end_callback = on_track_end_callback
            self.started = False

        def start(self):
            self.started = True

    class FakePlayerEventHandler:
        def __init__(
            self,
            state_manager,
            queue_manager,
            engine_controller,
            event_bus,
            player_controller_facade,
        ):
            self.state_manager = state_manager
            self.queue_manager = queue_manager
            self.engine_controller = engine_controller
            self.event_bus = event_bus
            self.player_controller_facade = player_controller_facade

        def setup_event_subscriptions(self):
            return None

    class FakePlayerController:
        def __init__(
            self,
            state_manager,
            queue_manager,
            engine_controller,
            progress_tracker,
            event_bus,
        ):
            self.state_manager = state_manager
            self.queue_manager = queue_manager
            self.engine_controller = engine_controller
            self.progress_tracker = progress_tracker
            self.event_bus = event_bus
            self.bound_handler = None
            self.track_end_calls = 0

        def set_event_handler(self, handler):
            self.bound_handler = handler
            self.progress_tracker.start()

        def _handle_track_end(self):
            self.track_end_calls += 1

    event_bus = FakeEventBus()
    audio_engine = object()
    video_controller = object()
    container = FactoryContainerStub(
        services={
            AudioEventBus: event_bus,
            AudioEngine: audio_engine,
            VideoController: video_controller,
        }
    )

    monkeypatch.setattr(playback_state_manager_module, 'PlaybackStateManager', FakePlaybackStateManager)
    monkeypatch.setattr(queue_manager_module, 'QueueManager', FakeQueueManager)
    monkeypatch.setattr(engine_controller_module, 'EngineController', FakeEngineController)
    monkeypatch.setattr(progress_tracker_module, 'ProgressTracker', FakeProgressTracker)
    monkeypatch.setattr(player_event_handler_module, 'PlayerEventHandler', FakePlayerEventHandler)
    monkeypatch.setattr(service_container_factories_module, 'PlayerController', FakePlayerController)

    instance = service_container_factories_module._create_player_controller(container)

    assert isinstance(instance, FakePlayerController)
    assert isinstance(instance.state_manager, FakePlaybackStateManager)
    assert isinstance(instance.queue_manager, FakeQueueManager)
    assert isinstance(instance.engine_controller, FakeEngineController)
    assert isinstance(instance.progress_tracker, FakeProgressTracker)
    assert instance.event_bus is event_bus
    assert instance.engine_controller.audio_engine is audio_engine
    assert instance.engine_controller.video_controller is None
    assert instance.engine_controller.video_controller_factory() is video_controller
    assert isinstance(instance.bound_handler, FakePlayerEventHandler)
    assert instance.bound_handler.player_controller_facade is instance
    assert instance.progress_tracker.started is True

    instance.progress_tracker.on_track_end_callback()
    assert instance.track_end_calls == 1


def test_player_controller_factory_fails_fast_when_constructor_contract_changes(monkeypatch):
    import src.controller.component_player.engine_controller as engine_controller_module
    import src.controller.component_player.playback_state_manager as playback_state_manager_module
    import src.controller.component_player.progress_tracker as progress_tracker_module
    import src.controller.component_player.queue_manager as queue_manager_module

    class FakeEventBus:
        def publish(self, *args, **kwargs):
            return None

    class FakePlaybackStateManager:
        def __init__(self, event_bus):
            self.event_bus = event_bus

    class FakeQueueManager:
        pass

    class FakeEngineController:
        def __init__(self, state_manager, audio_engine, video_controller, video_controller_factory=None):
            self.state_manager = state_manager
            self.audio_engine = audio_engine
            self.video_controller = video_controller
            self.video_controller_factory = video_controller_factory

    class FakeProgressTracker:
        def __init__(
            self,
            state_manager,
            queue_manager,
            engine_controller,
            event_publisher,
            on_track_end_callback,
        ):
            self.state_manager = state_manager
            self.queue_manager = queue_manager
            self.engine_controller = engine_controller
            self.event_publisher = event_publisher
            self.on_track_end_callback = on_track_end_callback

    class LegacyPlayerController:
        def __init__(self, event_bus):
            self.event_bus = event_bus

    container = FactoryContainerStub(
        services={
            AudioEventBus: FakeEventBus(),
            AudioEngine: object(),
            VideoController: object(),
        }
    )

    monkeypatch.setattr(playback_state_manager_module, 'PlaybackStateManager', FakePlaybackStateManager)
    monkeypatch.setattr(queue_manager_module, 'QueueManager', FakeQueueManager)
    monkeypatch.setattr(engine_controller_module, 'EngineController', FakeEngineController)
    monkeypatch.setattr(progress_tracker_module, 'ProgressTracker', FakeProgressTracker)
    monkeypatch.setattr(service_container_factories_module, 'PlayerController', LegacyPlayerController)

    with pytest.raises(TypeError):
        service_container_factories_module._create_player_controller(container)


def test_event_bus_factory_tolerates_typed_start_failure(monkeypatch):
    started = []

    class FakeEventBus:
        def start(self):
            started.append('start')
            raise RuntimeError('start fail')

    monkeypatch.setattr(service_container_factories_module, 'AudioEventBus', FakeEventBus)

    instance = service_container_factories_module._create_event_bus(object())

    assert isinstance(instance, FakeEventBus)
    assert started == ['start']


def test_player_controller_track_end_callback_tolerates_typed_handler_failure(monkeypatch):
    import src.controller.component_player.engine_controller as engine_controller_module
    import src.controller.component_player.playback_state_manager as playback_state_manager_module
    import src.controller.component_player.player_event_handler as player_event_handler_module
    import src.controller.component_player.progress_tracker as progress_tracker_module
    import src.controller.component_player.queue_manager as queue_manager_module

    class FakeEventBus:
        def publish(self, *args, **kwargs):
            return None

    class FakePlaybackStateManager:
        def __init__(self, event_bus):
            self.event_bus = event_bus

    class FakeQueueManager:
        pass

    class FakeEngineController:
        def __init__(self, state_manager, audio_engine, video_controller, video_controller_factory=None):
            self.state_manager = state_manager
            self.audio_engine = audio_engine
            self.video_controller = video_controller
            self.video_controller_factory = video_controller_factory

    class FakeProgressTracker:
        def __init__(
            self,
            state_manager,
            queue_manager,
            engine_controller,
            event_publisher,
            on_track_end_callback,
        ):
            self.on_track_end_callback = on_track_end_callback

        def start(self):
            return None

    class FakePlayerEventHandler:
        def __init__(self, **kwargs):
            return None

        def setup_event_subscriptions(self):
            return None

    class FailingPlayerController:
        def __init__(self, **kwargs):
            self.state_manager = kwargs.get('state_manager')
            self.queue_manager = kwargs.get('queue_manager')
            self.engine_controller = kwargs.get('engine_controller')
            self.progress_tracker = kwargs.get('progress_tracker')
            self.event_bus = kwargs.get('event_bus')
            self.handler = None
            self.track_end_calls = 0

        def set_event_handler(self, handler):
            self.handler = handler

        def _handle_track_end(self):
            self.track_end_calls += 1
            raise RuntimeError('track end fail')

    container = FactoryContainerStub(
        services={
            AudioEventBus: FakeEventBus(),
            AudioEngine: object(),
            VideoController: object(),
        }
    )

    monkeypatch.setattr(playback_state_manager_module, 'PlaybackStateManager', FakePlaybackStateManager)
    monkeypatch.setattr(queue_manager_module, 'QueueManager', FakeQueueManager)
    monkeypatch.setattr(engine_controller_module, 'EngineController', FakeEngineController)
    monkeypatch.setattr(progress_tracker_module, 'ProgressTracker', FakeProgressTracker)
    monkeypatch.setattr(player_event_handler_module, 'PlayerEventHandler', FakePlayerEventHandler)
    monkeypatch.setattr(service_container_factories_module, 'PlayerController', FailingPlayerController)

    instance = service_container_factories_module._create_player_controller(container)

    instance.progress_tracker.on_track_end_callback()

    assert instance.track_end_calls == 1
