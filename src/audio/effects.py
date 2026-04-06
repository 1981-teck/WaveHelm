from __future__ import annotations
import numpy as np
import logging
from typing import Dict, Any, TYPE_CHECKING
from src.audio.audio_events import AudioEventType
from src.audio.effects_dsp import (
    apply_echo,
    apply_reverb,
    apply_vintage_filter,
    normalize_audio,
)
import json  # Per la serializzazione dei preset

# Importazioni per type hinting (per evitare importazioni circolari)
if TYPE_CHECKING:
    from src.audio.audio_events import AudioEventBus
    from src.model.localization_manager import LocalizationManager
    from src.model.setting_manager import SettingsManager

    # Assumiamo che effects_dsp.py esista e contenga le funzioni DSP
    from src.audio.effects_dsp import (
        apply_echo,
        apply_reverb,
        apply_vintage_filter,
        normalize_audio,
    )

# Configurazione del logger per questo modulo
logger = logging.getLogger(__name__)

EFFECTS_TEXT_EXCEPTIONS = (AttributeError, IndexError, KeyError, ValueError)
EFFECTS_APPLY_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError, FloatingPointError, OverflowError)


class EffectsEngine:
    """
    Gestisce lo stato e i parametri degli effetti DSP (Eco, Riverbero, Filtro Vintage).
    Applica gli effetti ai dati audio.
    """

    DEFAULT_SETTINGS = {
        "echo": {
            "enabled": False,
            "delay_ms": 300,  # Millisecondi
            "decay": 0.5,  # Fattore di decadimento (0.0 - 1.0)
        },
        "reverb": {
            "enabled": False,
            "decay_time": 1.5,  # Tempo di decadimento in secondi
            "wet_level": 0.3,  # Livello del segnale wet (0.0 - 1.0)
        },
        "vintage_filter": {
            "enabled": False,
            "cutoff_freq": 1000,  # Frequenza di taglio in Hz
            "resonance": 0.5,  # Risonanza (0.0 - 1.0)
        },
    }

    def __init__(
        self,
        event_bus: AudioEventBus,
        localization_manager: LocalizationManager,
        settings_manager: SettingsManager,
    ):
        """
        Inizializza l'EffectsEngine.

        Args:
            event_bus (AudioEventBus): Il bus eventi per la comunicazione.
            localization_manager (LocalizationManager): L'istanza del LocalizationManager.
            settings_manager (SettingsManager): L'istanza del SettingsManager.
        """
        self.event_bus = event_bus
        self.localization_manager = localization_manager
        self.settings_manager = settings_manager

        self._current_settings: Dict[str, Any] = json.loads(
            json.dumps(self.DEFAULT_SETTINGS)
        )  # Copia profonda
        self.sample_rate: int = (
            44100  # Sample rate predefinito, verrà aggiornato via evento
        )
        self.channels: int = (
            2  # Numero di canali predefinito, verrà aggiornato via evento
        )

        self._subscribe_to_events()
        logger.info(self._get_localized_text("effects_engine_initialized"))

    def _get_localized_text(self, key: str, **kwargs) -> str:
        """Helper per ottenere il testo tradotto dal LocalizationManager."""
        default = kwargs.pop("default", key)
        text = self.localization_manager.get_text(key, default)
        try:
            return str(text).format(**kwargs) if kwargs else str(text)
        except EFFECTS_TEXT_EXCEPTIONS:
            return str(text)

    def _subscribe_to_events(self):
        """Sottoscrive l'EffectsEngine agli eventi pertinenti."""
        self.event_bus.subscribe(
            AudioEventType.SAMPLE_RATE_CHANGED, self._on_sample_rate_changed
        )
        self.event_bus.subscribe(
            AudioEventType.CHANNELS_CHANGED, self._on_channels_changed
        )
        logger.debug("[EffectsEngine] Sottoscritto agli eventi del bus.")

    def _on_sample_rate_changed(self, data: Dict[str, Any]):
        """Gestisce l'evento di cambio sample rate."""
        new_sample_rate = data.get("sample_rate")
        if new_sample_rate and new_sample_rate != self.sample_rate:
            self.sample_rate = new_sample_rate
            logger.info(
                self._get_localized_text("effects_sample_rate_changed").format(
                    sr=self.sample_rate
                )
            )
            self.event_bus.publish(
                AudioEventType.EFFECTS_CHANGED,
                {"settings": self.get_current_settings()},
            )

    def _on_channels_changed(self, data: Dict[str, Any]):
        """Gestisce l'evento di cambio numero di canali."""
        new_channels = data.get("channels")
        if new_channels and new_channels != self.channels:
            self.channels = new_channels
            logger.info(
                self._get_localized_text("effects_channels_changed").format(
                    channels=self.channels
                )
            )
            self.event_bus.publish(
                AudioEventType.EFFECTS_CHANGED,
                {"settings": self.get_current_settings()},
            )

    def apply_effects(self, audio_data: np.ndarray) -> np.ndarray:
        """
        Applica gli effetti DSP abilitati ai dati audio.
        """
        if not any(e["enabled"] for e in self._current_settings.values()):
            return audio_data

        processed_data = audio_data.copy()
        if processed_data.ndim == 1:
            processed_data = np.expand_dims(processed_data, axis=1)

        try:
            if self._current_settings["echo"]["enabled"]:
                delay_samples = int(
                    self._current_settings["echo"]["delay_ms"] * self.sample_rate / 1000
                )
                decay = self._current_settings["echo"]["decay"]
                processed_data = apply_echo(processed_data, delay_samples, decay)

            if self._current_settings["reverb"]["enabled"]:
                decay_time = self._current_settings["reverb"]["decay_time"]
                wet_level = self._current_settings["reverb"]["wet_level"]
                processed_data = apply_reverb(
                    processed_data, self.sample_rate, decay_time, wet_level
                )

            if self._current_settings["vintage_filter"]["enabled"]:
                cutoff_freq = self._current_settings["vintage_filter"]["cutoff_freq"]
                resonance = self._current_settings["vintage_filter"]["resonance"]
                processed_data = apply_vintage_filter(
                    processed_data, self.sample_rate, cutoff_freq, resonance
                )

            if np.max(np.abs(processed_data)) > 1.0:
                processed_data = normalize_audio(processed_data)

        except NameError as ne:
            logger.critical(
                self._get_localized_text("effects_dsp_function_missing").format(
                    error=ne
                ),
                exc_info=True,
            )
            self.event_bus.publish(
                AudioEventType.ERROR,
                {
                    "message": self._get_localized_text(
                        "effects_dsp_function_missing_short"
                    )
                },
            )
            return audio_data
        except EFFECTS_APPLY_EXCEPTIONS as e:
            logger.error(
                self._get_localized_text("effects_error_applying_effects").format(
                    error=e
                ),
                exc_info=True,
            )
            self.event_bus.publish(
                AudioEventType.ERROR,
                {
                    "message": self._get_localized_text(
                        "effects_error_applying_effects_short"
                    )
                },
            )
            return audio_data

        return processed_data

    def set_effect_enabled(self, effect_name: str, enabled: bool):
        """
        Attiva o disattiva un effetto specifico.
        """
        if effect_name not in self._current_settings:
            raise ValueError(
                self._get_localized_text("effects_invalid_effect_name").format(
                    name=effect_name
                )
            )

        if self._current_settings[effect_name]["enabled"] == enabled:
            return

        self._current_settings[effect_name]["enabled"] = enabled
        logger.info(
            self._get_localized_text("effects_toggled").format(
                name=effect_name, status=("enabled" if enabled else "disabled")
            )
        )
        self.event_bus.publish(
            AudioEventType.EFFECTS_CHANGED, {"settings": self.get_current_settings()}
        )

    def _validate_parameter(self, effect_name: str, param_name: str, value: Any) -> Any:
        """Valida e converte un parametro per un effetto."""
        default_value = self.DEFAULT_SETTINGS[effect_name][param_name]
        value_type = type(default_value)

        try:
            new_value = value_type(value)
        except (ValueError, TypeError):
            raise ValueError(
                self._get_localized_text("effects_invalid_type").format(
                    param=param_name,
                    expected=value_type.__name__,
                    got=type(value).__name__,
                )
            )

        if effect_name == "echo":
            if param_name == "delay_ms" and not (0 <= new_value <= 2000):
                raise ValueError(
                    self._get_localized_text("effects_invalid_delay").format(
                        value=new_value
                    )
                )
            if param_name == "decay" and not (0.0 <= new_value <= 1.0):
                raise ValueError(
                    self._get_localized_text("effects_invalid_decay").format(
                        value=new_value
                    )
                )
        elif effect_name == "reverb":
            if param_name == "decay_time" and not (0.1 <= new_value <= 10.0):
                raise ValueError(
                    self._get_localized_text("effects_invalid_decay_time").format(
                        value=new_value
                    )
                )
            if param_name == "wet_level" and not (0.0 <= new_value <= 1.0):
                raise ValueError(
                    self._get_localized_text("effects_invalid_wet_level").format(
                        value=new_value
                    )
                )
        elif effect_name == "vintage_filter":
            if param_name == "cutoff_freq" and not (
                20 <= new_value <= self.sample_rate / 2
            ):
                raise ValueError(
                    self._get_localized_text("effects_invalid_cutoff").format(
                        value=new_value, max_freq=self.sample_rate / 2
                    )
                )
            if param_name == "resonance" and not (0.0 <= new_value <= 1.0):
                raise ValueError(
                    self._get_localized_text("effects_invalid_resonance").format(
                        value=new_value
                    )
                )

        return new_value

    def set_effect_parameter(self, effect_name: str, param_name: str, value: Any):
        """
        Imposta un parametro per un effetto specifico.
        """
        if (
            effect_name not in self._current_settings
            or param_name not in self._current_settings[effect_name]
        ):
            raise ValueError(
                self._get_localized_text("effects_invalid_parameter_name").format(
                    param=param_name, effect=effect_name
                )
            )

        validated_value = self._validate_parameter(effect_name, param_name, value)

        if self._current_settings[effect_name][param_name] == validated_value:
            return

        self._current_settings[effect_name][param_name] = validated_value
        logger.info(
            self._get_localized_text("effects_param_set").format(
                effect=effect_name, param=param_name, value=validated_value
            )
        )
        self.event_bus.publish(
            AudioEventType.EFFECTS_CHANGED, {"settings": self.get_current_settings()}
        )

    def get_effect_settings(self, effect_name: str) -> Dict[str, Any]:
        """
        Restituisce le impostazioni complete per un effetto specifico.
        """
        if effect_name not in self._current_settings:
            raise ValueError(
                self._get_localized_text("effects_invalid_effect_name").format(
                    name=effect_name
                )
            )
        return json.loads(json.dumps(self._current_settings[effect_name]))

    def get_current_settings(self) -> Dict[str, Any]:
        """
        Restituisce un dizionario con tutte le impostazioni correnti degli effetti.
        """
        return json.loads(json.dumps(self._current_settings))

    def is_effect_enabled(self, effect_name: str) -> bool:
        return bool(self.get_effect_settings(effect_name).get("enabled", False))

    @property
    def echo_enabled(self) -> bool:
        return self.is_effect_enabled("echo")

    @property
    def echo_delay_ms(self) -> float:
        return float(self.get_effect_settings("echo").get("delay_ms", 300.0))

    @property
    def echo_decay(self) -> float:
        return float(self.get_effect_settings("echo").get("decay", 0.5))

    @property
    def reverb_enabled(self) -> bool:
        return self.is_effect_enabled("reverb")

    @property
    def reverb_decay_time(self) -> float:
        return float(self.get_effect_settings("reverb").get("decay_time", 1.5))

    @property
    def reverb_wet_level(self) -> float:
        return float(self.get_effect_settings("reverb").get("wet_level", 0.3))

    @property
    def vintage_enabled(self) -> bool:
        return self.is_effect_enabled("vintage_filter")

    @property
    def vintage_cutoff_freq(self) -> float:
        return float(
            self.get_effect_settings("vintage_filter").get("cutoff_freq", 1000.0)
        )

    @property
    def vintage_resonance(self) -> float:
        return float(
            self.get_effect_settings("vintage_filter").get("resonance", 0.5)
        )

    def toggle_echo(self, enabled: bool):
        self.set_effect_enabled("echo", enabled)

    def set_echo_delay(self, value: float):
        self.set_effect_parameter("echo", "delay_ms", value)

    def set_echo_decay(self, value: float):
        self.set_effect_parameter("echo", "decay", value)

    def toggle_reverb(self, enabled: bool):
        self.set_effect_enabled("reverb", enabled)

    def set_reverb_decay_time(self, value: float):
        self.set_effect_parameter("reverb", "decay_time", value)

    def set_reverb_wet_level(self, value: float):
        self.set_effect_parameter("reverb", "wet_level", value)

    def toggle_vintage(self, enabled: bool):
        self.set_effect_enabled("vintage_filter", enabled)

    def set_vintage_cutoff_freq(self, value: float):
        self.set_effect_parameter("vintage_filter", "cutoff_freq", value)

    def set_vintage_resonance(self, value: float):
        self.set_effect_parameter("vintage_filter", "resonance", value)

    def apply_settings(self, settings: Dict[str, Any]):
        """
        Applica un set completo di impostazioni per gli effetti.
        """
        if not isinstance(settings, dict):
            self.reset_all_effects()
            return

        new_settings = json.loads(json.dumps(self.DEFAULT_SETTINGS))
        for effect_name, effect_data in settings.items():
            if effect_name in new_settings and isinstance(effect_data, dict):
                for param_name, value in effect_data.items():
                    if param_name in new_settings[effect_name]:
                        try:
                            validated_value = self._validate_parameter(
                                effect_name, param_name, value
                            )
                            new_settings[effect_name][param_name] = validated_value
                        except ValueError as e:
                            logger.warning(
                                f"Invalid value for {effect_name}.{param_name}: '{value}'. Using default. Error: {e}"
                            )

        self._current_settings = new_settings
        logger.info(self._get_localized_text("effects_settings_applied_from_profile"))
        self.event_bus.publish(
            AudioEventType.EFFECTS_CHANGED, {"settings": self.get_current_settings()}
        )

    def reset_effect(self, effect_name: str):
        """Resetta un singolo effetto ai suoi valori predefiniti."""
        if effect_name not in self._current_settings:
            raise ValueError(
                self._get_localized_text("effects_invalid_effect_name").format(
                    name=effect_name
                )
            )

        self._current_settings[effect_name] = json.loads(
            json.dumps(self.DEFAULT_SETTINGS[effect_name])
        )
        logger.info(
            self._get_localized_text("effects_effect_reset").format(name=effect_name)
        )
        self.event_bus.publish(
            AudioEventType.EFFECTS_CHANGED, {"settings": self.get_current_settings()}
        )

    def reset_all_effects(self):
        """Resetta tutte le impostazioni degli effetti ai valori predefiniti."""
        self._current_settings = json.loads(json.dumps(self.DEFAULT_SETTINGS))
        logger.info(self._get_localized_text("effects_all_reset"))
        self.event_bus.publish(
            AudioEventType.EFFECTS_CHANGED, {"settings": self.get_current_settings()}
        )

    def close(self):
        """
        Pulisce le risorse alla chiusura.
        """
        logger.info("[EffectsEngine] Chiusura EffectsEngine richiesta.")
        self.event_bus.unsubscribe(
            AudioEventType.SAMPLE_RATE_CHANGED, callback=self._on_sample_rate_changed
        )
        self.event_bus.unsubscribe(
            AudioEventType.CHANNELS_CHANGED, callback=self._on_channels_changed
        )
        logger.info("[EffectsEngine] Risorse EffectsEngine pulite.")
