from __future__ import annotations

import logging
from typing import Any, Dict

from src.audio.audio_engine import AudioEngine
from src.audio.audio_events import AudioEventBus
from src.audio.effects import EffectsEngine
from src.audio.equalizer import Equalizer
from src.controller.equalizer_controller import EqualizerController
from src.controller.effects_controller import EffectsController
from src.controller.library_controller import LibraryController
from src.controller.playlist_controller import PlaylistController
from src.controller.player_controller import PlayerController
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

EVENT_BUS_START_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)
TRACK_END_FORWARD_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)


def _create_event_bus(self):
    eb = AudioEventBus()
    try:
        eb.start()
    except EVENT_BUS_START_EXCEPTIONS:
        logger.debug("AudioEventBus.start() failed (ignored)", exc_info=True)
    logger.info("AudioEventBus created and started")
    return eb


def _create_localization_manager(self):
    lm = LocalizationManager()
    logger.info("LocalizationManager created")
    return lm


def _create_theme_manager(self):
    tm = ThemeManager(
        localization_manager=self.get_service(LocalizationManager),
        event_bus=self.get_service(AudioEventBus),
    )
    logger.info("ThemeManager created")
    return tm


def _create_database_manager(self):
    dm = DatabaseManager(localization_manager=self.get_service(LocalizationManager))
    logger.info("DatabaseManager created")
    return dm


def _create_profile_manager(self):
    pm = ProfileManager(
        db_manager=self.get_service(DatabaseManager),
        localization_manager=self.get_service(LocalizationManager),
        event_bus=self.get_service(AudioEventBus),
    )
    logger.info("ProfileManager created")
    return pm


def _create_settings_manager(self):
    sm = SettingsManager(localization_manager=self.get_service(LocalizationManager))
    logger.info("SettingsManager created")
    return sm


def _create_audio_engine(self):
    ae = AudioEngine(
        localization_manager=self.get_service(LocalizationManager),
        settings_manager=self.get_service(SettingsManager),
        event_bus=self.get_service(AudioEventBus),
        effects_engine=self.get_existing_service(EffectsEngine),
        equalizer=self.get_existing_service(Equalizer),
    )
    logger.info("AudioEngine created")
    return ae


def _create_media_loader(self):
    ml = MediaLoader(localization_manager=self.get_service(LocalizationManager))
    logger.info("MediaLoader created")
    return ml


def _create_ambient_manager(self):
    am = AmbientManager(
        settings_manager=self.get_service(SettingsManager),
        event_bus=self.get_service(AudioEventBus),
        localization_manager=self.get_service(LocalizationManager),
    )
    logger.info("AmbientManager created")
    return am


def _create_library_controller(self):
    lc = LibraryController(
        event_bus=self.get_service(AudioEventBus),
        database_manager=self.get_service(DatabaseManager),
    )
    logger.info("LibraryController created")
    return lc


def _create_playlist_controller(self):
    pc = PlaylistController(
        database_manager=self.get_service(DatabaseManager),
        localization_manager=self.get_service(LocalizationManager),
        event_bus=self.get_service(AudioEventBus),
        library_controller=self.get_service(LibraryController),
    )
    logger.info("PlaylistController created")
    return pc


def _create_player_controller(self):
    from src.controller.component_player.engine_controller import EngineController
    from src.controller.component_player.playback_state_manager import PlaybackStateManager
    from src.controller.component_player.player_event_handler import PlayerEventHandler
    from src.controller.component_player.progress_tracker import ProgressTracker
    from src.controller.component_player.queue_manager import QueueManager

    event_bus = self.get_service(AudioEventBus)
    audio_engine = self.get_service(AudioEngine)
    state_manager = PlaybackStateManager(event_bus=event_bus)
    queue_manager = QueueManager()

    engine_controller = EngineController(
        state_manager=state_manager,
        audio_engine=audio_engine,
        video_controller=None,
        video_controller_factory=lambda: self.get_service(VideoController),
    )

    pc_ref: Dict[str, Any] = {"pc": None}

    def _on_track_end() -> None:
        pc_obj = pc_ref.get("pc")
        if not pc_obj:
            logger.debug("[ServiceContainer] on_track_end ignored: PlayerController not bound yet.")
            return
        try:
            pc_obj._handle_track_end()
        except TRACK_END_FORWARD_EXCEPTIONS:
            logger.debug("[ServiceContainer] PlayerController._handle_track_end failed", exc_info=True)

    progress_tracker = ProgressTracker(
        state_manager=state_manager,
        queue_manager=queue_manager,
        engine_controller=engine_controller,
        event_publisher=event_bus.publish,
        on_track_end_callback=_on_track_end,
    )

    pc = PlayerController(
        state_manager=state_manager,
        queue_manager=queue_manager,
        engine_controller=engine_controller,
        progress_tracker=progress_tracker,
        event_bus=event_bus,
    )
    pc_ref["pc"] = pc

    event_handler = PlayerEventHandler(
        state_manager=state_manager,
        queue_manager=queue_manager,
        engine_controller=engine_controller,
        event_bus=event_bus,
        player_controller_facade=pc,
    )
    pc.set_event_handler(event_handler)

    logger.info("PlayerController e i suoi componenti sono stati creati")
    return pc


def _create_equalizer(self):
    eq = Equalizer(
        db_manager=self.get_service(DatabaseManager),
        localization_manager=self.get_service(LocalizationManager),
        event_bus=self.get_service(AudioEventBus),
        settings_manager=self.get_service(SettingsManager),
    )
    audio_engine = self.get_existing_service(AudioEngine)
    if audio_engine is not None and hasattr(audio_engine, "bind_dsp_processors"):
        audio_engine.bind_dsp_processors(equalizer=eq)
    logger.info("Equalizer created")
    return eq


def _create_equalizer_controller(self):
    ec = EqualizerController(
        equalizer=self.get_service(Equalizer),
        profile_manager=self.get_service(ProfileManager),
        localization_manager=self.get_service(LocalizationManager),
        event_bus=self.get_service(AudioEventBus),
    )
    logger.info("EqualizerController created")
    return ec


def _create_effects_engine(self):
    ee = EffectsEngine(
        event_bus=self.get_service(AudioEventBus),
        localization_manager=self.get_service(LocalizationManager),
        settings_manager=self.get_service(SettingsManager),
    )
    audio_engine = self.get_existing_service(AudioEngine)
    if audio_engine is not None and hasattr(audio_engine, "bind_dsp_processors"):
        audio_engine.bind_dsp_processors(effects_engine=ee)
    logger.info("EffectsEngine created")
    return ee


def _create_effects_controller(self):
    ec = EffectsController(
        effects_engine=self.get_service(EffectsEngine),
        video_player=None,
        profile_manager=self.get_service(ProfileManager),
        localization_manager=self.get_service(LocalizationManager),
        event_bus=self.get_service(AudioEventBus),
    )
    logger.info("EffectsController created")
    return ec


def _create_settings_controller(self):
    sc = SettingsController(
        settings_manager=self.get_service(SettingsManager),
        localization_manager=self.get_service(LocalizationManager),
        theme_manager=self.get_service(ThemeManager),
        event_bus=self.get_service(AudioEventBus),
        audio_engine=self.get_service(AudioEngine),
        video_player=None,
    )
    logger.info("SettingsController created")
    return sc


def _create_video_controller(self):
    vc = VideoController(event_bus=self.get_service(AudioEventBus))
    logger.info("VideoController created")
    return vc


_SERVICE_CONTAINER_FACTORY_METHODS = (
    ("_create_event_bus", _create_event_bus),
    ("_create_localization_manager", _create_localization_manager),
    ("_create_theme_manager", _create_theme_manager),
    ("_create_database_manager", _create_database_manager),
    ("_create_profile_manager", _create_profile_manager),
    ("_create_settings_manager", _create_settings_manager),
    ("_create_audio_engine", _create_audio_engine),
    ("_create_media_loader", _create_media_loader),
    ("_create_ambient_manager", _create_ambient_manager),
    ("_create_library_controller", _create_library_controller),
    ("_create_playlist_controller", _create_playlist_controller),
    ("_create_player_controller", _create_player_controller),
    ("_create_equalizer", _create_equalizer),
    ("_create_equalizer_controller", _create_equalizer_controller),
    ("_create_effects_engine", _create_effects_engine),
    ("_create_effects_controller", _create_effects_controller),
    ("_create_settings_controller", _create_settings_controller),
    ("_create_video_controller", _create_video_controller),
)


def install_service_container_factory_behavior(cls: type[Any]) -> None:
    """Install factory methods while keeping the container module as the public coordinator.

    Edge cases handled:
    1. A non-class target would receive lazy-service factories unexpectedly.
    2. Empty or duplicate binding names would silently shadow an existing factory.
    3. Re-import or reload could rebind every factory without an idempotent guard.
    """
    if not isinstance(cls, type):
        raise TypeError("service container factory behavior can only be installed on classes")
    if getattr(cls, '_service_container_factory_behavior_attached', False):
        return

    seen_names: set[str] = set()
    for method_name, binding in _SERVICE_CONTAINER_FACTORY_METHODS:
        if not method_name:
            raise ValueError("service container factory binding name must not be empty")
        if method_name in seen_names:
            raise ValueError(f"duplicate service container factory binding: {method_name}")
        if not callable(binding):
            raise TypeError(f"service container factory binding must be callable: {method_name}")
        seen_names.add(method_name)
        setattr(cls, method_name, binding)

    setattr(cls, '_service_container_factory_behavior_attached', True)


def _bind_service_container_factory_methods(cls: type[Any]) -> None:
    """Backward-compatible internal helper for historical imports."""
    install_service_container_factory_behavior(cls)


def attach_service_container_factory_behavior(cls: type[Any]) -> None:
    """Backward-compatible shim that delegates to the neutral installer."""
    install_service_container_factory_behavior(cls)
