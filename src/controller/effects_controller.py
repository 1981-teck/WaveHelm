from __future__ import annotations

import logging
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict

from src.audio.audio_events import AudioEventType
from src.utils.exceptions import ProfileError, ValidationError

if TYPE_CHECKING:
    from src.audio.audio_events import AudioEventBus
    from src.audio.effects import EffectsEngine
    from src.model.localization_manager import LocalizationManager
    from src.model.profile_manager import ProfileManager


class EffectState(Enum):
    DISABLED = 0
    ENABLED = 1


logger = logging.getLogger(__name__)

ENGINE_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError, ValidationError)
LOCALIZATION_EXCEPTIONS = (AttributeError, KeyError, RuntimeError, TypeError, ValueError)
FORMAT_EXCEPTIONS = (IndexError, KeyError, ValueError)
EVENT_BUS_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)
PROFILE_EXCEPTIONS = (ProfileError, AttributeError, RuntimeError, TypeError, ValueError)
VIDEO_PLAYER_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)


class EffectsController:
    """Controller per la logica degli effetti DSP."""

    def __init__(
        self,
        effects_engine: EffectsEngine,
        video_player: object | None = None,
        profile_manager: ProfileManager = None,
        localization_manager: LocalizationManager = None,
        event_bus: AudioEventBus = None,
    ):
        self.effects_engine = effects_engine
        self.video_player = video_player
        self.profile_manager = profile_manager
        self.localization_manager = localization_manager
        self.event_bus = event_bus

        self._subscribe_to_events()
        self._restore_saved_settings()
        logger.info(self._get_localized_text("effects_controller_initialized"))

    def get_effect_state(self, effect_name: str) -> EffectState:
        """Return EffectState for a named effect."""
        try:
            if hasattr(self.effects_engine, "is_effect_enabled"):
                enabled = bool(self.effects_engine.is_effect_enabled(effect_name))
            else:
                settings = self.effects_engine.get_effect_settings(effect_name) or {}
                enabled = bool(settings.get("enabled", False))
        except ENGINE_EXCEPTIONS:
            enabled = False
        return EffectState.ENABLED if enabled else EffectState.DISABLED

    def get_all_effect_settings(self) -> dict:
        """Back-compat alias for views expecting singular name."""
        if hasattr(self, "get_all_effects_settings"):
            return self.get_all_effects_settings()
        return {}

    def _get_localized_text(self, key: str, **kwargs) -> str:
        """Helper per ottenere il testo tradotto."""
        default = kwargs.pop("default", key)
        if self.localization_manager is None:
            text = default
        else:
            try:
                text = self.localization_manager.get_text(key, default)
            except LOCALIZATION_EXCEPTIONS:
                text = default
        try:
            return str(text).format(**kwargs) if kwargs else str(text)
        except FORMAT_EXCEPTIONS:
            return str(text)

    def _safe_publish(self, event_type: AudioEventType, payload: Dict[str, Any]) -> None:
        if self.event_bus is None:
            return
        try:
            self.event_bus.publish(event_type, payload)
        except EVENT_BUS_EXCEPTIONS as exc:
            logger.error("Failed to publish %s: %s", event_type, exc)

    def _subscribe_to_events(self):
        """Sottoscrive agli eventi pertinenti."""
        if self.event_bus is None:
            return
        try:
            self.event_bus.subscribe(AudioEventType.EFFECTS_CHANGED, self._on_effects_changed)
            logger.debug("Sottoscritto agli eventi EFFECTS_CHANGED")
        except EVENT_BUS_EXCEPTIONS as exc:
            logger.error("Errore nella registrazione eventi effetti: %s", exc)

    def _restore_saved_settings(self) -> None:
        """Ripristina le impostazioni effetti salvate all'avvio.

        Il pulsante "Salva impostazioni" deve produrre uno stato persistente anche
        dopo il riavvio dell'app. In passato il salvataggio finiva nel profilo ma
        nessun componente rileggeva quelle impostazioni in bootstrap, quindi la UI
        sembrava ignorare il comando. Qui ricarichiamo esplicitamente il profilo e
        lo applichiamo al motore effetti se disponibile.
        """
        if self.profile_manager is None:
            return

        getter = getattr(self.profile_manager, 'get_effects_settings', None)
        applier = getattr(self.effects_engine, 'apply_settings', None)
        if not callable(getter) or not callable(applier):
            return

        try:
            settings = getter()
            if not isinstance(settings, dict) or not settings:
                return
            applier(settings)
        except PROFILE_EXCEPTIONS as error:
            self._handle_error(error, 'error_applying_effects_settings')
        except ENGINE_EXCEPTIONS as error:
            self._handle_error(error, 'error_applying_effects_settings')

    def _notify_feedback(self, key: str, color: str = "green", **kwargs):
        """Helper per inviare messaggi di feedback alla UI."""
        message = self._get_localized_text(key, **kwargs)
        self._safe_publish(AudioEventType.FEEDBACK_MESSAGE, {"message": message, "color": color})

    def _handle_error(self, error: Exception, context_key: str, **kwargs):
        """Gestore centralizzato per gli errori."""
        if isinstance(error, (ValueError, ValidationError)):
            error_kwargs = dict(kwargs)
            error_kwargs.setdefault(
                "name",
                kwargs.get("effect") or kwargs.get("name") or kwargs.get("param") or "?",
            )
            error_kwargs.setdefault("error", error)
            message = self._get_localized_text("effects_validation_error", **error_kwargs)
        else:
            context_kwargs = dict(kwargs)
            context_kwargs.setdefault("error", error)
            message = self._get_localized_text(context_key, **context_kwargs)

        logger.error("%s: %s", message, error, exc_info=True)
        self._safe_publish(AudioEventType.ERROR, {"message": f"{message}: {error}"})
        self._notify_feedback("operation_failed", color="red", details=message)

    def set_effect_enabled(self, effect_name: str, enabled: bool):
        """Attiva o disattiva un effetto specifico."""
        try:
            self.effects_engine.set_effect_enabled(effect_name, enabled)
            status_text = self._get_localized_text("enabled") if enabled else self._get_localized_text("disabled")
            self._notify_feedback("effects_toggled_feedback", name=effect_name, status=status_text)
        except ENGINE_EXCEPTIONS as error:
            self._handle_error(error, "effects_set_enabled_error", effect=effect_name)

    def set_effect_parameter(self, effect_name: str, param_name: str, value: Any):
        """Imposta un parametro per un effetto specifico."""
        try:
            self.effects_engine.set_effect_parameter(effect_name, param_name, value)
            self._notify_feedback(
                "effects_param_set_feedback",
                effect=effect_name,
                param=param_name,
                value=value,
            )
        except ENGINE_EXCEPTIONS as error:
            self._handle_error(error, "effects_set_param_error", effect=effect_name, param=param_name)

    def get_effect_settings(self, effect_name: str) -> Dict[str, Any]:
        """Restituisce le impostazioni per un effetto specifico."""
        try:
            return self.effects_engine.get_effect_settings(effect_name)
        except ValueError as error:
            self._handle_error(error, "effects_invalid_effect_name", name=effect_name)
            return {}

    def get_all_effects_settings(self) -> Dict[str, Any]:
        """Restituisce tutte le impostazioni correnti degli effetti."""
        return self.effects_engine.get_current_settings()

    def save_current_settings_to_profile(self) -> bool:
        """Salva esplicitamente le impostazioni correnti nel profilo."""
        try:
            if self.profile_manager is None:
                raise ProfileError("ProfileManager unavailable")
            settings = self.effects_engine.get_current_settings()
            self.profile_manager.set_effects_settings(settings)
            self._notify_feedback("effects_settings_saved")
            return True
        except PROFILE_EXCEPTIONS as error:
            self._handle_error(error, "error_saving_effects_settings_to_profile")
            return False

    def reset_effect(self, effect_name: str):
        """Resetta un singolo effetto ai valori predefiniti."""
        try:
            self.effects_engine.reset_effect(effect_name)
            self._notify_feedback("effect_reset_feedback", name=effect_name)
        except ENGINE_EXCEPTIONS as error:
            self._handle_error(error, "effects_reset_error", effect=effect_name)

    def reset_all_effects(self):
        """Resetta tutte le impostazioni degli effetti ai valori predefiniti."""
        try:
            self.effects_engine.reset_all_effects()
            self._notify_feedback("effects_all_reset_feedback")
        except ENGINE_EXCEPTIONS as error:
            self._handle_error(error, "effects_reset_all_error")

    def _on_effects_changed(self, data: Dict[str, Any]):
        """Gestisce l'evento EFFECTS_CHANGED, salva le impostazioni e le applica al video player."""
        settings = data.get("settings", {}) if isinstance(data, dict) else {}

        try:
            if self.profile_manager is not None:
                self.profile_manager.set_effects_settings(settings)
                logger.debug("Impostazioni effetti salvate nel profilo")
        except PROFILE_EXCEPTIONS as error:
            self._handle_error(error, "error_saving_effects_settings_to_profile")
            return

        if self.video_player and hasattr(self.video_player, "set_effects"):
            try:
                self.video_player.set_effects(settings)
            except VIDEO_PLAYER_EXCEPTIONS as error:
                self._handle_error(error, "unexpected_error")

    def close(self):
        """Pulisce le risorse alla chiusura."""
        logger.info("Chiusura EffectsController in corso...")
        if self.event_bus is None:
            return
        try:
            self.event_bus.unsubscribe(AudioEventType.EFFECTS_CHANGED, callback=self._on_effects_changed)
        except EVENT_BUS_EXCEPTIONS as error:
            logger.error("Errore nella deregistrazione eventi: %s", error)
