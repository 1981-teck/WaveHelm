from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from src.audio.audio_events import AudioEventType
from src.utils.exceptions import DatabaseError, NotFoundError, ProfileError, ValidationError

if TYPE_CHECKING:
    from src.audio.audio_events import AudioEventBus
    from src.audio.equalizer import Equalizer
    from src.model.localization_manager import LocalizationManager
    from src.model.profile_manager import ProfileManager


logger = logging.getLogger(__name__)

ENGINE_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError, ValidationError, NotFoundError, DatabaseError)
PROFILE_EXCEPTIONS = (ProfileError, DatabaseError, AttributeError, RuntimeError, TypeError, ValueError)
LOCALIZATION_EXCEPTIONS = (AttributeError, KeyError, RuntimeError, TypeError, ValueError)
FORMAT_EXCEPTIONS = (IndexError, KeyError, ValueError)
EVENT_BUS_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)


class EqualizerController:
    """Controller per la logica dell'equalizzatore."""

    def __init__(
        self,
        equalizer: Equalizer,
        profile_manager: ProfileManager,
        localization_manager: LocalizationManager,
        event_bus: AudioEventBus,
    ):
        self.equalizer = equalizer
        self.profile_manager = profile_manager
        self.localization_manager = localization_manager
        self.event_bus = event_bus

        self._subscribe_to_events()
        logger.info(self._get_localized_text("equalizer_controller_initialized"))

    def _get_localized_text(self, key: str, **kwargs) -> str:
        """Helper per ottenere il testo tradotto."""
        default = kwargs.pop("default", key)
        try:
            text = self.localization_manager.get_text(key, default)
        except LOCALIZATION_EXCEPTIONS:
            text = default

        try:
            return str(text).format(**kwargs) if kwargs else str(text)
        except FORMAT_EXCEPTIONS:
            return str(text)

    def _safe_publish(self, event_type: AudioEventType, payload: Dict[str, Any]) -> None:
        try:
            self.event_bus.publish(event_type, payload)
        except EVENT_BUS_EXCEPTIONS as exc:
            logger.error("Failed to publish %s: %s", event_type, exc)

    def _subscribe_to_events(self):
        """Sottoscrive agli eventi pertinenti."""
        try:
            self.event_bus.subscribe(AudioEventType.EQ_CHANGED, self._on_eq_changed)
        except EVENT_BUS_EXCEPTIONS as exc:
            logger.error("Errore nella registrazione eventi EQ: %s", exc)

    def _notify_feedback(self, key: str, color: str = "green", **kwargs):
        """Helper per inviare messaggi di feedback alla UI."""
        message = self._get_localized_text(key, **kwargs)
        self._safe_publish(AudioEventType.FEEDBACK_MESSAGE, {"message": message, "color": color})

    def _handle_error(self, error: Exception, context_key: str, **kwargs):
        """Gestore centralizzato per gli errori."""
        if isinstance(error, (ValueError, ValidationError, NotFoundError, DatabaseError)):
            message = self._get_localized_text(context_key, **kwargs)
        else:
            message = self._get_localized_text("unexpected_error", **kwargs)

        logger.error("%s: %s", message, error, exc_info=True)
        self._safe_publish(AudioEventType.ERROR, {"message": f"{message}: {error}"})
        self._notify_feedback("operation_failed", color="red", details=message)

    def set_band_gain(self, band_name: str, gain_db: float):
        """Set one EQ band gain without emitting per-tick UI feedback.

        Edge cases handled deterministically:
        1. Slider drags can emit dozens of updates per second, so no FEEDBACK_MESSAGE is published for successful per-band writes.
        2. Invalid gains or band names still surface through the existing error path with deterministic ERROR/feedback events.
        3. Programmatic callers still receive the canonical EQ_CHANGED event from the engine, so state synchronization remains intact.
        """
        try:
            self.equalizer.set_band_gain(band_name, gain_db)
        except ENGINE_EXCEPTIONS as error:
            self._handle_error(error, "eq_set_band_error", band=band_name)

    def reset_band_to_default(self, band_name: str):
        """Resetta una singola banda al guadagno predefinito (0 dB)."""
        self.set_band_gain(band_name, 0.0)

    def get_all_bands(self) -> Dict[str, Dict[str, Union[float, int]]]:
        """Restituisce tutte le bande e i loro parametri."""
        return self.equalizer.get_all_bands()

    def toggle_equalizer(self, enable: Optional[bool] = None):
        """Attiva o disattiva l'equalizzatore."""
        try:
            self.equalizer.enable(enable)
            status_text = self._get_localized_text("enabled") if self.equalizer.is_enabled else self._get_localized_text("disabled")
            self._notify_feedback("equalizer_toggled", status=status_text)
        except ENGINE_EXCEPTIONS as error:
            self._handle_error(error, "error_toggling_equalizer")

    def apply_preset(self, preset_name: str):
        """Apply a named EQ preset only after deterministic existence checks.

        Edge cases handled proactively:
        1. Empty or whitespace-only preset names are rejected before touching the engine.
        2. Engines exposing apply_preset() can signal missing presets with False, which is converted into a controller error.
        3. Legacy engines exposing only set_preset() are guarded with an availability check to avoid false-success feedback.
        """
        normalized_name = str(preset_name).strip()
        try:
            if not normalized_name:
                raise ValidationError('preset name cannot be empty')
            applier = getattr(self.equalizer, 'apply_preset', None)
            if callable(applier):
                applied = bool(applier(normalized_name))
                if not applied:
                    raise ValidationError(f'preset not found: {normalized_name}')
            else:
                available_getter = getattr(self.equalizer, 'get_available_presets', None)
                available_presets = available_getter() if callable(available_getter) else []
                if normalized_name not in available_presets:
                    raise ValidationError(f'preset not found: {normalized_name}')
                self.equalizer.set_preset(normalized_name)
            self._notify_feedback("eq_preset_applied_feedback", preset=normalized_name)
        except ENGINE_EXCEPTIONS as error:
            self._handle_error(error, "eq_error_applying_preset", preset=normalized_name)

    def save_current_as_custom_preset(self, preset_name: str) -> bool:
        """Salva le impostazioni EQ correnti come preset personalizzato.

        Restituisce True solo quando il salvataggio è andato a buon fine.
        Questo consente alla UI di evitare stati fantasma quando il backend
        rifiuta il preset o il registry dei preset viene aggiornato in ritardo.
        """
        try:
            self.equalizer.save_custom_preset(preset_name)
        except ENGINE_EXCEPTIONS as error:
            self._handle_error(error, "eq_error_saving_preset", name=preset_name)
            return False

        self._notify_feedback("eq_new_preset_saved", name=preset_name)

        try:
            if self.profile_manager is not None and hasattr(self.profile_manager, "increment_stat"):
                self.profile_manager.increment_stat("eq_presets_saved")
        except PROFILE_EXCEPTIONS as error:
            logger.warning("Impossibile aggiornare le statistiche EQ del profilo: %s", error)

        return True

    def delete_custom_preset(self, preset_name: str):
        """Elimina un preset personalizzato."""
        try:
            deleted = self.equalizer.delete_custom_preset(preset_name)
            if not deleted:
                self._notify_feedback("eq_preset_not_found_delete", color="orange", name=preset_name)
                return
            self._notify_feedback("eq_preset_deleted", name=preset_name)
        except ENGINE_EXCEPTIONS as error:
            self._handle_error(error, "eq_error_deleting_preset", name=preset_name)

    def get_all_preset_names(self) -> List[str]:
        """Restituisce tutti i nomi dei preset."""
        return self.equalizer.get_available_presets()

    def get_current_state(self) -> Dict[str, Any]:
        """Restituisce lo stato corrente dell'equalizzatore."""
        return self.equalizer.get_current_state()

    def _on_eq_changed(self, data: Dict[str, Any]):
        """Gestisce l'evento EQ_CHANGED: salva le impostazioni nel profilo."""
        try:
            self.profile_manager.set_eq_settings(data)
            logger.debug("Impostazioni EQ salvate nel profilo")
        except PROFILE_EXCEPTIONS as error:
            self._handle_error(error, "error_saving_eq_settings_to_profile")

    def close(self):
        """Pulisce le risorse alla chiusura."""
        logger.info("Chiusura EqualizerController in corso...")
        try:
            self.event_bus.unsubscribe(AudioEventType.EQ_CHANGED, callback=self._on_eq_changed)
        except EVENT_BUS_EXCEPTIONS as error:
            logger.error("Errore nella deregistrazione eventi: %s", error)
