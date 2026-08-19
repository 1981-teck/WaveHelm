from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from src.audio.audio_event_bus import AudioEventBus
    from src.audio.audio_engine import AudioEngine
    from src.model.localization_manager import LocalizationManager
    from src.model.setting_manager import JsonValue, SettingsManager
    from src.model.theme_manager import ThemeManager

from src.audio.audio_events import AudioEventType
from src.controller.settings_controller_cleanup import (
    _clear_directory_contents,
    _clear_logs_dir,
    _truncate_active_log_file,
    clear_processed_audio_cache,
    clear_runtime_artifacts,
    get_app_data_dir,
    get_logs_dir,
    get_third_party_notices_dir,
    get_processed_audio_cleanup_block_message,
    get_processed_audio_dir,
    is_processed_audio_cache_in_use,
)
from src.controller.settings_controller_runtime import (
    SETTINGS_CONTROLLER_RUNTIME_BINDINGS,
)
from src.utils.exceptions import SettingsError

logger = logging.getLogger(__name__)

LOCALIZATION_EXCEPTIONS = (AttributeError, KeyError, TypeError, ValueError)
EVENT_BUS_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)
SETTINGS_MANAGER_EXCEPTIONS = (
    AttributeError,
    KeyError,
    TypeError,
    ValueError,
    RuntimeError,
    OSError,
    SettingsError,
)
FILE_EXCEPTIONS = (IOError, OSError)
JSON_EXCEPTIONS = (json.JSONDecodeError, TypeError, ValueError)
RUNTIME_EXCEPTIONS = (
    AttributeError,
    TypeError,
    ValueError,
    RuntimeError,
    OSError,
    SettingsError,
)
CLEANUP_EXCEPTIONS = (AttributeError, OSError, RuntimeError, TypeError, ValueError)
LOG_HANDLER_EXCEPTIONS = (AttributeError, OSError, RuntimeError, TypeError, ValueError)
MAX_SETTINGS_IMPORT_BYTES = 1_048_576


class SettingsController:
    """Controller per la gestione delle impostazioni dell'applicazione."""

    CLEANUP_EXCEPTIONS = CLEANUP_EXCEPTIONS
    LOG_HANDLER_EXCEPTIONS = LOG_HANDLER_EXCEPTIONS
    RUNTIME_EXCEPTIONS = RUNTIME_EXCEPTIONS
    SETTINGS_MANAGER_EXCEPTIONS = SETTINGS_MANAGER_EXCEPTIONS

    def __init__(
        self,
        settings_manager: "SettingsManager",
        localization_manager: "LocalizationManager",
        theme_manager: "ThemeManager",
        event_bus: "AudioEventBus",
        audio_engine: "AudioEngine",
        video_player: object | None = None,
    ) -> None:
        self.settings_manager = settings_manager
        self.localization_manager = localization_manager
        self.theme_manager = theme_manager
        self.event_bus = event_bus
        self.audio_engine = audio_engine
        self.video_player = video_player

        self._subscribe_to_events()
        self._load_initial_settings()
        logger.info(self._get_localized_text("settings_controller_initialized"))

    def _get_localized_text(self, translation_key: str, **kwargs: Any) -> str:
        try:
            text = self.localization_manager.get_text(translation_key, default=translation_key)
        except LOCALIZATION_EXCEPTIONS:
            text = translation_key
        if kwargs:
            try:
                return str(text).format(**kwargs)
            except (KeyError, TypeError, ValueError):
                return str(text)
        return str(text)

    def _publish_event(self, event_type: AudioEventType, payload: Dict[str, Any]) -> bool:
        try:
            self.event_bus.publish(event_type, payload)
        except EVENT_BUS_EXCEPTIONS:
            return False
        return True

    def _subscribe_event(self, event_type: AudioEventType, callback: Any) -> bool:
        try:
            self.event_bus.subscribe(event_type, callback)
        except EVENT_BUS_EXCEPTIONS:
            return False
        return True

    def _unsubscribe_event(self, event_type: AudioEventType, callback: Any) -> bool:
        try:
            self.event_bus.unsubscribe(event_type, callback)
        except EVENT_BUS_EXCEPTIONS:
            return False
        return True

    def _notify_feedback(self, key: str, color: str = "green", **kwargs: Any) -> None:
        message = self._get_localized_text(key, **kwargs)
        self._publish_event(
            AudioEventType.FEEDBACK_MESSAGE,
            {"message": message, "color": color},
        )

    def _handle_error(self, error: Exception, context_key: str, **kwargs: Any) -> None:
        message = self._get_localized_text(context_key, **kwargs)
        logger.error("%s: %s", message, error, exc_info=True)
        self._publish_event(
            AudioEventType.ERROR,
            {"message": f"{message}: {error}"},
        )

    def _subscribe_to_events(self) -> None:
        self._subscribe_event(AudioEventType.SETTINGS_UPDATED, self._on_settings_updated)

    def _publish_language_changed(self) -> None:
        try:
            language = str(self.localization_manager.get_current_language())
        except LOCALIZATION_EXCEPTIONS:
            return
        self._publish_event(
            AudioEventType.LANGUAGE_CHANGED,
            {"language": language},
        )

    def _load_initial_settings(self) -> None:
        """Carica le impostazioni e le applica a audio/video/tema/lingua."""
        try:
            settings = self.settings_manager.get_all_settings()
            self._apply_volume_from_settings(settings)
            self._apply_language_from_settings(settings)
            self._apply_theme_from_settings(settings)
            self._apply_video_runtime_from_settings(settings)
        except SETTINGS_MANAGER_EXCEPTIONS as error:
            logger.error(
                "Errore applicazione impostazioni iniziali: %s",
                error,
                exc_info=True,
            )

    def _apply_volume_from_settings(self, settings: Dict[str, Any]) -> None:
        try:
            volume = float(settings.get("volume", 70)) / 100.0
        except (AttributeError, TypeError, ValueError):
            volume = 0.7
        self._set_engine_volume_safe(volume)
        self._set_video_volume_safe(volume)

    def _apply_language_from_settings(self, settings: Dict[str, Any]) -> None:
        try:
            language = str(settings.get("language", "en"))
            self.localization_manager.set_language(language)
            self._publish_language_changed()
        except RUNTIME_EXCEPTIONS as error:
            logger.debug("Language runtime update skipped: %s", error, exc_info=True)

    def _apply_theme_from_settings(self, settings: Dict[str, Any]) -> None:
        try:
            theme = settings.get("theme", "System")
            primary = settings.get("primary_color", "blue")
            self.theme_manager.set_theme(theme, primary)
        except RUNTIME_EXCEPTIONS as error:
            logger.debug("Theme runtime update skipped: %s", error, exc_info=True)

    def _apply_video_runtime_from_settings(self, settings: Dict[str, Any]) -> None:
        if not self.video_player:
            return

        try:
            hw_enabled = bool(settings.get("video_hw_accel_enabled", True))
            if hasattr(self.video_player, "set_hw_accel"):
                self.video_player.set_hw_accel(hw_enabled)

            if hasattr(self.video_player, "configure_video_runtime"):
                self.video_player.configure_video_runtime(
                    drop_late_frames=bool(settings.get("video_drop_late_frames", True)),
                    frame_queue_size=int(settings.get("video_frame_queue_size", 10)),
                    decode_threads=int(settings.get("video_decode_threads", 0)),
                    fast_seek=bool(settings.get("video_fast_seek", True)),
                    resize_quality_high=bool(
                        settings.get("video_resize_quality_high", False)
                    ),
                )
        except RUNTIME_EXCEPTIONS as error:
            logger.debug("Video runtime update skipped: %s", error, exc_info=True)

    def set_setting(self, key: str, value: "JsonValue") -> None:
        try:
            persisted_value = self.settings_manager.set_setting(key, value)
            self._publish_event(
                AudioEventType.SETTINGS_UPDATED,
                {"key": key, "value": persisted_value},
            )
            self._notify_feedback("setting_updated", name=key)
        except SETTINGS_MANAGER_EXCEPTIONS as error:
            self._handle_error(error, "error_setting_value", key=key)

    def get_all_settings(self) -> Dict[str, Any]:
        return self.settings_manager.get_all_settings()

    def export_settings(self, file_path: str) -> None:
        try:
            path = Path(file_path)
            exportable = self.settings_manager.get_exportable_settings()
            path.write_text(
                json.dumps(exportable, indent=4, ensure_ascii=False, allow_nan=False),
                encoding="utf-8",
            )
            self._notify_feedback("settings_exported", path=file_path)
        except FILE_EXCEPTIONS as error:
            self._handle_error(error, "error_exporting_settings")
        except JSON_EXCEPTIONS as error:
            self._handle_error(error, "error_exporting_settings")
        except SETTINGS_MANAGER_EXCEPTIONS as error:
            self._handle_error(error, "error_exporting_settings")

    def import_settings(self, file_path: str) -> None:
        if not self._unsubscribe_event(AudioEventType.SETTINGS_UPDATED, self._on_settings_updated):
            logger.debug("Unable to unsubscribe SETTINGS_UPDATED during import")

        try:
            path = Path(file_path)
            if path.stat().st_size > MAX_SETTINGS_IMPORT_BYTES:
                raise ValueError("settings import exceeds the 1 MiB limit")
            with path.open("r", encoding="utf-8") as handle:
                settings = json.load(handle)
            if not isinstance(settings, dict):
                raise ValueError("settings import must be a JSON object")

            result = self.settings_manager.apply_imported_settings(settings)
            self._load_initial_settings()
            self._publish_event(
                AudioEventType.SETTINGS_BATCH_UPDATED,
                {
                    "updated_keys": list(result.updated_keys),
                    "ignored_keys": list(result.ignored_keys),
                    "source": "import",
                },
            )
            if result.ignored_keys:
                logger.info(
                    "Ignored protected settings during import: %s",
                    ", ".join(result.ignored_keys),
                )
            self._notify_feedback("settings_imported", path=file_path)
        except FILE_EXCEPTIONS as error:
            self._handle_error(error, "error_importing_settings")
        except JSON_EXCEPTIONS as error:
            self._handle_error(error, "error_importing_settings")
        except SETTINGS_MANAGER_EXCEPTIONS as error:
            self._handle_error(error, "error_importing_settings")
        finally:
            self._subscribe_event(AudioEventType.SETTINGS_UPDATED, self._on_settings_updated)

    def reset_to_defaults(self) -> None:
        try:
            self.settings_manager.reset_to_defaults()
            self._load_initial_settings()
            self._publish_event(
                AudioEventType.SETTINGS_BATCH_UPDATED,
                {
                    "updated_keys": list(self.settings_manager.get_all_settings().keys()),
                    "source": "reset",
                },
            )
            self._notify_feedback("settings_reset_to_defaults")
        except SETTINGS_MANAGER_EXCEPTIONS as error:
            self._handle_error(error, "error_resetting_settings")






_SETTINGS_CONTROLLER_CLEANUP_METHODS: tuple[tuple[str, Any], ...] = (
    ("get_app_data_dir", get_app_data_dir),
    ("get_processed_audio_dir", get_processed_audio_dir),
    ("get_logs_dir", get_logs_dir),
    ("get_third_party_notices_dir", get_third_party_notices_dir),
    ("_clear_directory_contents", _clear_directory_contents),
    ("_truncate_active_log_file", _truncate_active_log_file),
    ("_clear_logs_dir", _clear_logs_dir),
    ("is_processed_audio_cache_in_use", is_processed_audio_cache_in_use),
    ("get_processed_audio_cleanup_block_message", get_processed_audio_cleanup_block_message),
    ("clear_processed_audio_cache", clear_processed_audio_cache),
    ("clear_runtime_artifacts", clear_runtime_artifacts),
)



def _attach_settings_controller_binding_group(
    controller_cls: type[SettingsController],
    binding_group: tuple[tuple[str, Any], ...],
) -> None:
    seen_names: set[str] = set()
    for attribute_name, method in binding_group:
        if not attribute_name:
            raise TypeError("Settings controller binding name cannot be empty")
        if attribute_name in seen_names:
            raise TypeError(f"Duplicate settings controller binding: {attribute_name}")
        if not callable(method):
            raise TypeError(f"Invalid settings controller binding: {attribute_name}")
        setattr(controller_cls, attribute_name, method)
        seen_names.add(attribute_name)


def install_settings_controller_cleanup_behavior(
    controller_cls: type[SettingsController],
) -> type[SettingsController]:
    """Install cleanup helpers while keeping this module as the controller entrypoint.

    Edge cases considered:
    - Tests and integration code may resolve cleanup methods directly from ``SettingsController``.
    - Cleanup bindings depend on controller-level exception policies staying aligned.
    - Re-import or reload should not repeat cleanup wiring without an idempotent guard.
    """
    if not isinstance(controller_cls, type):
        raise TypeError("Settings controller cleanup target must be a class")
    if getattr(controller_cls, '_settings_controller_cleanup_behavior_attached', False):
        return controller_cls

    controller_cls.CLEANUP_EXCEPTIONS = CLEANUP_EXCEPTIONS
    controller_cls.LOG_HANDLER_EXCEPTIONS = LOG_HANDLER_EXCEPTIONS
    _attach_settings_controller_binding_group(controller_cls, _SETTINGS_CONTROLLER_CLEANUP_METHODS)
    setattr(controller_cls, '_settings_controller_cleanup_behavior_attached', True)
    return controller_cls


def install_settings_controller_runtime_behavior(
    controller_cls: type[SettingsController],
) -> type[SettingsController]:
    """Install runtime helpers from the split runtime module."""
    if not isinstance(controller_cls, type):
        raise TypeError("Settings controller runtime target must be a class")
    if getattr(controller_cls, '_settings_controller_runtime_behavior_attached', False):
        return controller_cls

    _attach_settings_controller_binding_group(controller_cls, SETTINGS_CONTROLLER_RUNTIME_BINDINGS)
    setattr(controller_cls, '_settings_controller_runtime_behavior_attached', True)
    return controller_cls


_SETTINGS_CONTROLLER_ATTACHERS: tuple[tuple[str, Any], ...] = (
    ("runtime", install_settings_controller_runtime_behavior),
    ("cleanup", install_settings_controller_cleanup_behavior),
)


def install_settings_controller_behavior(
    controller_cls: type[SettingsController],
) -> type[SettingsController]:
    """Install runtime and cleanup helpers from the central controller module.

    Edge cases considered:
    - Runtime helper bindings may become invalid during future splits.
    - The controller must remain the stable public integration point.
    - Cleanup and runtime satellites must not silently shadow each other.
    """
    if not isinstance(controller_cls, type):
        raise TypeError("Settings controller target must be a class")
    if getattr(controller_cls, '_settings_controller_behavior_attached', False):
        return controller_cls

    for group_name, installer in _SETTINGS_CONTROLLER_ATTACHERS:
        if not callable(installer):
            raise TypeError(f"Invalid settings controller installer: {group_name}")
        installer(controller_cls)

    setattr(controller_cls, '_settings_controller_behavior_attached', True)
    return controller_cls


def attach_settings_controller_cleanup_behavior(
    controller_cls: type[SettingsController],
) -> type[SettingsController]:
    """Backward-compatible shim that delegates to the neutral cleanup installer."""
    return install_settings_controller_cleanup_behavior(controller_cls)


def attach_settings_controller_behavior(
    controller_cls: type[SettingsController],
) -> type[SettingsController]:
    """Backward-compatible shim that delegates to the neutral installer."""
    return install_settings_controller_behavior(controller_cls)


install_settings_controller_behavior(SettingsController)
