# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any, Callable, Optional


try:
    import win32api  # type: ignore
except ImportError:  # pragma: no cover
    win32api = None  # type: ignore

if TYPE_CHECKING:
    from src.services.service_container import ServiceContainer
    from src.ui_wx.main_view import MainView

from src.audio.audio_events import AudioEventBus
from src.audio.audio_engine import AudioEngine
from src.audio.effects import EffectsEngine
from src.audio.equalizer import Equalizer
from src.controller.effects_controller import EffectsController
from src.controller.ui_dispatcher import build_ui_dispatcher
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
logger = logging.getLogger(__name__)


def _get_main_view_class(ui_backend: Optional[str] = None):
    del ui_backend
    from src.ui_wx.main_view import MainView as MainViewClass

    return MainViewClass



DISPATCHER_EXCEPTIONS = (AttributeError, ImportError, RuntimeError, TypeError, ValueError)
SERVICE_EXCEPTIONS = (
    AttributeError,
    KeyError,
    LookupError,
    RuntimeError,
    TypeError,
    ValueError,
    OSError,
)
LIFECYCLE_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError, OSError)


class AppController:
    """Controller principale dell'app."""

    def __init__(
        self,
        root: Any = None,
        service_container: Optional["ServiceContainer"] = None,
        ui_backend: Optional[str] = None,
    ) -> None:
        if service_container is None:
            from src.services.service_container import get_service_container

            service_container = get_service_container()

        self.root = root
        self.ui_backend = 'wx' if ui_backend is None else ui_backend
        self.service_container: ServiceContainer = service_container
        self.main_view: Optional[MainView] = None
        self.event_bus: Optional[AudioEventBus] = None
        self._video_controller_instance: Optional[VideoController] = None
        self._shutdown_lock = threading.RLock()
        self._is_shutting_down = False

    def _build_ui_dispatcher(self) -> Callable[[Callable[[], Any]], None]:
        """Build the UI dispatcher used by the event bus and view callbacks.

        Edge cases handled deterministically:
        1. The runtime always targets the wx dispatcher after the final cutover.
        2. Missing framework modules fail fast instead of registering a partially working dispatcher.
        3. Dispatcher creation errors are converted into a stable ``RuntimeError`` for bootstrap callers.
        """
        try:
            return build_ui_dispatcher(self.root, ui_backend=self.ui_backend)
        except DISPATCHER_EXCEPTIONS as exc:
            raise RuntimeError('Unable to build the UI dispatcher bridge.') from exc

    def _set_event_bus_dispatcher(self) -> None:
        if self.event_bus is None:
            return
        try:
            self.event_bus.set_ui_dispatcher(self._build_ui_dispatcher())
        except DISPATCHER_EXCEPTIONS:
            logger.debug('UI dispatcher registration failed (best effort).', exc_info=True)

    def _set_container_main_view(self) -> None:
        try:
            self.service_container.set_main_view(self.main_view)
        except SERVICE_EXCEPTIONS:
            logger.debug('ServiceContainer.set_main_view failed (best effort).', exc_info=True)

    def _show_main_view_if_supported(self) -> None:
        if self.main_view is None:
            return

        show = getattr(self.main_view, 'show', None)
        if not callable(show):
            return

        try:
            show()
        except LIFECYCLE_EXCEPTIONS:
            logger.debug('MainView.show() failed (best effort).', exc_info=True)

    def _wire_main_view_shutdown(self) -> None:
        if self.main_view is None:
            return

        try:
            setattr(self.main_view, '_request_app_shutdown', self.shutdown)
        except LIFECYCLE_EXCEPTIONS:
            logger.debug('MainView shutdown callback wiring failed (best effort).', exc_info=True)

        shutdown_signal = getattr(self.main_view, 'shutdown_requested', None)
        connect = getattr(shutdown_signal, 'connect', None)
        if not callable(connect):
            return

        try:
            connect(self.shutdown)
        except LIFECYCLE_EXCEPTIONS:
            logger.debug('MainView shutdown signal wiring failed (best effort).', exc_info=True)

    def initialize_app(self) -> None:
        logger.info("Inizializzazione dell'applicazione...")

        self.event_bus = self.service_container.get_service(AudioEventBus)
        self._set_event_bus_dispatcher()

        dependencies = {
            'event_bus': self.event_bus,
            'localization_manager': self.service_container.get_service(LocalizationManager),
            'theme_manager': self.service_container.get_service(ThemeManager),
            'database_manager': self.service_container.get_service(DatabaseManager),
            'profile_manager': self.service_container.get_service(ProfileManager),
            'settings_manager': self.service_container.get_service(SettingsManager),
            'audio_engine': self.service_container.get_service(AudioEngine),
            'ambient_manager': self.service_container.get_service(AmbientManager),
            'player_controller': self.service_container.get_service(PlayerController),
            'library_controller': self.service_container.get_service(LibraryController),
            'playlist_controller': self.service_container.get_service(PlaylistController),
            'equalizer': self.service_container.get_service(Equalizer),
            'equalizer_controller': self.service_container.get_service(EqualizerController),
            'effects_engine': self.service_container.get_service(EffectsEngine),
            'effects_controller': self.service_container.get_service(EffectsController),
            'settings_controller': self.service_container.get_service(SettingsController),
            'media_loader': self.service_container.get_service(MediaLoader),
        }

        main_view_class = _get_main_view_class(self.ui_backend)
        self.main_view = main_view_class(master=self.root, **dependencies)
        self._wire_main_view_shutdown()
        self._show_main_view_if_supported()
        logger.info('MainView initialized')

        player_controller = dependencies.get('player_controller')
        self._wire_player_controller(player_controller)
        self._set_container_main_view()
        self._warm_up_video_controller()

        logger.info('AppController inizializzato con successo.')

    def initialize_application(self) -> None:
        self.initialize_app()

    def _wire_player_controller(self, player_controller: Any) -> None:
        if player_controller is None:
            raise RuntimeError('PlayerController service is not available during application bootstrap.')

        if self.main_view is None:
            raise RuntimeError('MainView must exist before wiring the PlayerController.')

        set_main_view = getattr(player_controller, 'set_main_view', None)
        if not callable(set_main_view):
            raise AttributeError('PlayerController does not expose set_main_view().')

        set_video_controller_factory = getattr(player_controller, 'set_video_controller_factory', None)
        if not callable(set_video_controller_factory):
            raise AttributeError('PlayerController does not expose set_video_controller_factory().')

        set_main_view(self.main_view)
        set_video_controller_factory(self.jit_create_video_controller)
        logger.info('PlayerController wired to MainView and VideoController factory.')

    def _warm_up_video_controller(self) -> None:
        video_controller = self.jit_create_video_controller()
        if video_controller is None:
            raise RuntimeError('VideoController service could not be created during application bootstrap.')

        required_methods = ('ensure_video_adapter', 'play_media', 'stop', 'shutdown')
        missing_methods = [
            method_name
            for method_name in required_methods
            if not callable(getattr(video_controller, method_name, None))
        ]
        if missing_methods:
            missing_list = ', '.join(missing_methods)
            raise TypeError(f'VideoController is missing required methods: {missing_list}')

        logger.info('VideoController warm-up completed successfully.')

    def jit_create_video_controller(self) -> Optional[VideoController]:
        if self._video_controller_instance is not None:
            return self._video_controller_instance

        try:
            video_controller = self.service_container.get_service(VideoController)
        except SERVICE_EXCEPTIONS as exc:
            logger.error('Errore durante il recupero JIT del VideoController: %s', exc, exc_info=True)
            return None

        self._video_controller_instance = video_controller
        return video_controller

    def run_main_loop(self) -> int:
        if self.root is not None:
            main_loop = getattr(self.root, 'MainLoop', None)
            if callable(main_loop):
                return int(main_loop() or 0)

            exec_method = getattr(self.root, 'exec', None)
            if callable(exec_method):
                return int(exec_method())  # type: ignore[misc]

        raise RuntimeError('Application root does not expose MainLoop() or exec().')

    def _get_existing_service_safe(self, service_type: Any) -> Any | None:
        try:
            return self.service_container.get_existing_service(service_type)
        except SERVICE_EXCEPTIONS:
            return None

    def _shutdown_component(self, target: Any, method_names: tuple[str, ...], label: str) -> None:
        if target is None:
            return
        for method_name in method_names:
            method = getattr(target, method_name, None)
            if not callable(method):
                continue
            try:
                method()
            except LIFECYCLE_EXCEPTIONS:
                logger.debug('%s shutdown failed (best effort).', label, exc_info=True)
            return

    def _request_ui_app_quit(self) -> None:
        app = self.root
        quit_method = getattr(app, 'quit', None)
        if callable(quit_method):
            try:
                quit_method()
                return
            except LIFECYCLE_EXCEPTIONS:
                logger.debug('Root quit() failed (best effort).', exc_info=True)

        exit_main_loop = getattr(app, 'ExitMainLoop', None)
        if callable(exit_main_loop):
            try:
                exit_main_loop()
                return
            except LIFECYCLE_EXCEPTIONS:
                logger.debug('Root ExitMainLoop() failed (best effort).', exc_info=True)

    def shutdown(self) -> None:
        with self._shutdown_lock:
            if self._is_shutting_down:
                return
            self._is_shutting_down = True

        logger.info("Arresto dell'applicazione...")

        player_controller = self._get_existing_service_safe(PlayerController)
        self._shutdown_component(player_controller, ('shutdown', 'close', 'stop'), 'PlayerController')

        self._shutdown_component(self.main_view, ('shutdown', 'close', 'destroy'), 'MainView')

        video_controller = self._video_controller_instance
        if video_controller is None:
            video_controller = self._get_existing_service_safe(VideoController)
        self._shutdown_component(video_controller, ('shutdown', 'close'), 'VideoController')

        event_bus = self._get_existing_service_safe(AudioEventBus)
        self._shutdown_component(event_bus, ('close',), 'AudioEventBus')

        try:
            self.service_container.close_services()
        except SERVICE_EXCEPTIONS:
            logger.debug('ServiceContainer.close_services failed (best effort).', exc_info=True)

        self._request_ui_app_quit()
