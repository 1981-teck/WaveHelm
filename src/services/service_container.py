from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Type

from src.audio.audio_engine import AudioEngine
from src.audio.audio_events import AudioEventBus
from src.audio.effects import EffectsEngine
from src.audio.equalizer import Equalizer
from src.controller.equalizer_controller import EqualizerController
from src.controller.effects_controller import EffectsController
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
from src.services.service_container_factories import (
    attach_service_container_factory_behavior as _attach_service_container_factory_behavior,
)

logger = logging.getLogger(__name__)

SERVICE_CONTAINER_FACTORY_EXCEPTIONS = (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError)
SERVICE_CONTAINER_SHUTDOWN_EXCEPTIONS = (AttributeError, OSError, RuntimeError, TypeError, ValueError)


@dataclass(frozen=True)
class ServiceInfo:
    service_type: Type[Any]
    factory: Callable[[], Any]
    dependencies: List[Type[Any]]
    shutdown_priority: int


class ServiceContainer:
    """
    DI Container con creazione lazy.

    NOTE DI COMPATIBILITÀ:
    - main.py si aspetta: get_service_container()
    - AppController si aspetta: set_main_view(), close_services()
    """

    def __init__(self) -> None:
        self._services: Dict[Type[Any], Any] = {}
        self._service_info: Dict[Type[Any], ServiceInfo] = {}
        self._lock = threading.RLock()
        self._main_view: Any = None

        self._register_core_services()
        self._register_app_services()

    def set_main_view(self, main_view: Any) -> None:
        with self._lock:
            self._main_view = main_view

    def get_main_view(self) -> Any:
        with self._lock:
            return self._main_view

    def close_services(self) -> None:
        self.shutdown_all()

    def has_service(self, service_type: Type[Any]) -> bool:
        return service_type in self._service_info

    def get_existing_service(self, service_type: Type[Any]) -> Optional[Any]:
        with self._lock:
            return self._services.get(service_type)

    def get_service(self, service_type: Type[Any]) -> Any:
        with self._lock:
            if service_type in self._services:
                return self._services[service_type]

            info = self._service_info.get(service_type)
            if not info:
                raise KeyError(f"Service not registered: {service_type}")

            service = self._create_service_instance(info)
            self._services[service_type] = service
            return service

    def _register_core_services(self) -> None:
        core_services = [
            (AudioEventBus, self._create_event_bus, [], 100),
            (LocalizationManager, self._create_localization_manager, [], 95),
            (ThemeManager, self._create_theme_manager, [], 90),
            (DatabaseManager, self._create_database_manager, [], 85),
            (ProfileManager, self._create_profile_manager, [], 80),
            (SettingsManager, self._create_settings_manager, [], 75),
            (AudioEngine, self._create_audio_engine, [], 70),
            (MediaLoader, self._create_media_loader, [], 65),
            (AmbientManager, self._create_ambient_manager, [], 60),
        ]
        for service_type, factory, deps, priority in core_services:
            self._register_service(service_type, factory, deps, priority)

    def _register_app_services(self) -> None:
        app_services = [
            (LibraryController, self._create_library_controller, [AudioEventBus, DatabaseManager], 55),
            (PlaylistController, self._create_playlist_controller, [DatabaseManager, LocalizationManager, AudioEventBus, LibraryController], 50),
            (PlayerController, self._create_player_controller, [AudioEngine, AudioEventBus, SettingsManager], 45),
            (Equalizer, self._create_equalizer, [DatabaseManager, LocalizationManager, AudioEventBus, SettingsManager], 40),
            (EqualizerController, self._create_equalizer_controller, [Equalizer, ProfileManager, LocalizationManager, AudioEventBus], 35),
            (EffectsEngine, self._create_effects_engine, [AudioEventBus, LocalizationManager, SettingsManager], 34),
            (EffectsController, self._create_effects_controller, [EffectsEngine, ProfileManager, LocalizationManager, AudioEventBus], 33),
            (SettingsController, self._create_settings_controller, [SettingsManager, LocalizationManager, ThemeManager, AudioEventBus, AudioEngine], 30),
            (VideoController, self._create_video_controller, [AudioEventBus], 15),
        ]
        for service_type, factory, deps, priority in app_services:
            self._register_service(service_type, factory, deps, priority)

    def _register_service(
        self,
        service_type: Type[Any],
        factory: Callable[[], Any],
        dependencies: List[Type[Any]],
        shutdown_priority: int,
    ) -> None:
        self._service_info[service_type] = ServiceInfo(
            service_type=service_type,
            factory=factory,
            dependencies=dependencies,
            shutdown_priority=int(shutdown_priority),
        )

    def _create_service_instance(self, info: ServiceInfo) -> Any:
        try:
            instance = info.factory()
            logger.info("Service %s created successfully", info.service_type.__name__)
            return instance
        except SERVICE_CONTAINER_FACTORY_EXCEPTIONS:
            logger.error("Failed to create service %s", info.service_type.__name__, exc_info=True)
            raise

    def shutdown_all(self) -> None:
        with self._lock:
            logger.info("Starting service shutdown...")

            ordered = sorted(
                self._services.items(),
                key=lambda kv: self._service_info.get(kv[0], ServiceInfo(kv[0], lambda: None, [], 0)).shutdown_priority,
                reverse=True,
            )

            for service_type, instance in ordered:
                try:
                    self._shutdown_service_instance(service_type, instance)
                except SERVICE_CONTAINER_SHUTDOWN_EXCEPTIONS:
                    logger.error("Error shutting down service %s", service_type.__name__, exc_info=True)

            self._services.clear()
            self._main_view = None
            logger.info("All services shut down successfully")

    def _shutdown_service_instance(self, service_type: Type[Any], instance: Any) -> None:
        for method_name in ("shutdown", "close", "stop"):
            m = getattr(instance, method_name, None)
            if callable(m):
                try:
                    m()
                except SERVICE_CONTAINER_SHUTDOWN_EXCEPTIONS:
                    logger.debug(
                        "Service %s.%s failed during shutdown",
                        service_type.__name__,
                        method_name,
                        exc_info=True,
                    )
                break


_SERVICE_CONTAINER_ATTACHERS: tuple[tuple[str, Any], ...] = (
    ("factories", _attach_service_container_factory_behavior),
)


def attach_service_container_behavior(cls: type[Any]) -> None:
    """Attach service container factory blocks in one central coordinator.

    Edge cases handled:
    1. A non-class target would receive runtime attributes unexpectedly.
    2. A missing or invalid split installer would leave the container partially wired.
    3. Re-import or reload could repeat the factory attachment path without an idempotent guard.
    """
    if not isinstance(cls, type):
        raise TypeError("service container target must be a class")
    if getattr(cls, '_service_container_behavior_attached', False):
        return

    for group_name, attacher in _SERVICE_CONTAINER_ATTACHERS:
        if not callable(attacher):
            raise TypeError(f"service container attacher must be callable: {group_name}")
        attacher(cls)

    setattr(cls, '_service_container_behavior_attached', True)


attach_service_container_behavior(ServiceContainer)


_service_container_instance: Optional[ServiceContainer] = None
_service_container_lock = threading.RLock()


def get_service_container() -> ServiceContainer:
    """Ottiene l'istanza singleton del ServiceContainer."""
    global _service_container_instance
    with _service_container_lock:
        if _service_container_instance is None:
            _service_container_instance = ServiceContainer()
        return _service_container_instance


def shutdown_service_container() -> None:
    """Chiude il ServiceContainer globale."""
    global _service_container_instance
    with _service_container_lock:
        if _service_container_instance is not None:
            _service_container_instance.shutdown_all()
            _service_container_instance = None
