from __future__ import annotations
import numpy as np
from scipy import signal  # Per il calcolo dei filtri biquad
import logging
from typing import Dict, Any, List, Optional, Tuple, TYPE_CHECKING

from src.audio.audio_events import AudioEventBus
from src.audio.audio_events import AudioEventType
from src.utils.exceptions import WaveHelmError

# Importazioni per type hinting (per evitare importazioni circolari)
if TYPE_CHECKING:
    from src.model.localization_manager import LocalizationManager
    from src.model.setting_manager import SettingsManager  # Importa SettingsManager
    from src.model.database_manager import (
        DatabaseManager,
    )  # Per i preset personalizzati

# Configurazione del logger per questo modulo
logger = logging.getLogger(__name__)

EQUALIZER_PRESET_EXCEPTIONS = (WaveHelmError, AttributeError, RuntimeError, TypeError, ValueError)


class Equalizer:
    """
    Implementa un equalizzatore a bande di frequenza.
    Applica filtri biquad ai dati audio.
    Gestisce preset predefiniti e personalizzati.
    """

    DEFAULT_BANDS = {
        "31Hz": {"freq": 31, "gain": 0.0, "q": 1.0},
        "62Hz": {"freq": 62, "gain": 0.0, "q": 1.0},
        "125Hz": {"freq": 125, "gain": 0.0, "q": 1.0},
        "250Hz": {"freq": 250, "gain": 0.0, "q": 1.0},
        "500Hz": {"freq": 500, "gain": 0.0, "q": 1.0},
        "1kHz": {"freq": 1000, "gain": 0.0, "q": 1.0},
        "2kHz": {"freq": 2000, "gain": 0.0, "q": 1.0},
        "4kHz": {"freq": 4000, "gain": 0.0, "q": 1.0},
        "8kHz": {"freq": 8000, "gain": 0.0, "q": 1.0},
        "16kHz": {"freq": 16000, "gain": 0.0, "q": 1.0},
    }
    BAND_FREQUENCIES = {
        band_name: float(band_data["freq"])
        for band_name, band_data in DEFAULT_BANDS.items()
    }

    PREDEFINED_PRESETS = {
        "Flat": {band: 0.0 for band in DEFAULT_BANDS.keys()},
        "Bass Boost": {
            "31Hz": 6.0,
            "62Hz": 5.0,
            "125Hz": 3.0,
            "250Hz": 1.0,
            "500Hz": -1.0,
            "1kHz": -2.0,
            "2kHz": -1.0,
            "4kHz": 0.0,
            "8kHz": 1.0,
            "16kHz": 1.0,
        },
        "Vocal Boost": {
            "31Hz": -4.0,
            "62Hz": -3.0,
            "125Hz": -2.0,
            "250Hz": 0.0,
            "500Hz": 2.0,
            "1kHz": 4.0,
            "2kHz": 5.0,
            "4kHz": 3.0,
            "8kHz": 1.0,
            "16kHz": -1.0,
        },
        "Treble Boost": {
            "31Hz": -4.0,
            "62Hz": -3.0,
            "125Hz": -2.0,
            "250Hz": -1.0,
            "500Hz": 0.0,
            "1kHz": 1.0,
            "2kHz": 3.0,
            "4kHz": 5.0,
            "8kHz": 6.0,
            "16kHz": 5.0,
        },
        "Rock": {
            "31Hz": 4.0,
            "62Hz": 3.0,
            "125Hz": 2.0,
            "250Hz": 0.0,
            "500Hz": -2.0,
            "1kHz": -1.0,
            "2kHz": 1.0,
            "4kHz": 3.0,
            "8kHz": 4.0,
            "16kHz": 3.0,
        },
        "Pop": {
            "31Hz": 3.0,
            "62Hz": 2.0,
            "125Hz": 1.0,
            "250Hz": 0.0,
            "500Hz": -1.0,
            "1kHz": 0.0,
            "2kHz": 2.0,
            "4kHz": 3.0,
            "8kHz": 2.0,
            "16kHz": 1.0,
        },
        "Jazz": {
            "31Hz": 1.0,
            "62Hz": 2.0,
            "125Hz": 2.0,
            "250Hz": 1.0,
            "500Hz": 0.0,
            "1kHz": 1.0,
            "2kHz": 2.0,
            "4kHz": 1.0,
            "8kHz": 1.0,
            "16kHz": 0.0,
        },
        "Classical": {
            "31Hz": 0.0,
            "62Hz": 1.0,
            "125Hz": 1.0,
            "250Hz": 0.0,
            "500Hz": 0.0,
            "1kHz": 1.0,
            "2kHz": 2.0,
            "4kHz": 2.0,
            "8kHz": 1.0,
            "16kHz": 0.0,
        },
    }

    def __init__(
        self,
        db_manager: DatabaseManager,
        localization_manager: LocalizationManager,
        event_bus: AudioEventBus,
        settings_manager: SettingsManager,
    ):
        self.db_manager = db_manager
        self.localization_manager = localization_manager
        self.event_bus = event_bus
        self.settings_manager = settings_manager

        self._sample_rate: int = 44100
        self._channels: int = 2
        self._is_enabled: bool = self.settings_manager.get_setting(
            "equalizer_enabled", True
        )
        self._current_preset_name: str = "Flat"
        self._custom_presets: Dict[str, Dict[str, Any]] = {}

        self._band_gains: Dict[str, float] = {
            band: 0.0 for band in self.DEFAULT_BANDS.keys()
        }
        self._coefficients: Dict[str, Tuple[np.ndarray, np.ndarray]] = {}
        self._zi: Dict[str, np.ndarray] = {}

        self._subscribe_to_events()
        self._load_custom_presets()
        self._load_initial_eq_settings()
        self._recalculate_all_filters()

        logger.info(self._get_localized_text("equalizer_initialized"))

    def _get_localized_text(self, key: str, **kwargs) -> str:
        return self.localization_manager.get_text(key, **kwargs)

    def _subscribe_to_events(self):
        self.event_bus.subscribe(
            AudioEventType.SAMPLE_RATE_CHANGED, self._on_sample_rate_changed
        )
        self.event_bus.subscribe(
            AudioEventType.CHANNELS_CHANGED, self._on_channels_changed
        )
        self.event_bus.subscribe(AudioEventType.PROFILE_LOADED, self._on_profile_loaded)
        self.event_bus.subscribe(
            AudioEventType.SETTINGS_UPDATED, self._on_settings_updated
        )

    def _load_initial_eq_settings(self):
        last_preset = self.settings_manager.get_setting("eq_last_preset", "Flat")
        last_gains = self.settings_manager.get_setting(
            "eq_last_gains", self.PREDEFINED_PRESETS["Flat"]
        )
        self.set_preset(last_preset, last_gains)

    def _load_custom_presets(self):
        try:
            presets_data = self.db_manager.get_custom_eq_presets()
            self._custom_presets = {p["name"]: p["settings"] for p in presets_data}
            self.event_bus.publish(
                AudioEventType.CUSTOM_PRESETS_UPDATED,
                {"type": "eq", "presets": list(self._custom_presets.keys())},
            )
        except EQUALIZER_PRESET_EXCEPTIONS as e:
            logger.error(
                self._get_localized_text("error_loading_custom_eq_presets").format(
                    error=e
                ),
                exc_info=True,
            )
            self._custom_presets = {}

    def _recalculate_filter_for_band(self, band_name: str):
        band_info = self.DEFAULT_BANDS[band_name]
        gain = self._band_gains.get(band_name, 0.0)
        q = band_info["q"]

        A = 10 ** (gain / 40.0)
        w0 = 2 * np.pi * band_info["freq"] / self._sample_rate
        alpha = np.sin(w0) / (2 * q)

        b0 = 1 + alpha * A
        b1 = -2 * np.cos(w0)
        b2 = 1 - alpha * A
        a0 = 1 + alpha / A
        a1 = -2 * np.cos(w0)
        a2 = 1 - alpha / A

        b = np.array([b0, b1, b2]) / a0
        a = np.array([1, a1 / a0, a2 / a0])

        self._coefficients[band_name] = (b, a)
        self._zi[band_name] = np.zeros((max(len(a), len(b)) - 1, self._channels))

    def _recalculate_all_filters(self):
        for band_name in self.DEFAULT_BANDS:
            self._recalculate_filter_for_band(band_name)
        logger.debug(self._get_localized_text("eq_filters_recalculated"))

    def apply_equalization(self, audio_data: np.ndarray) -> np.ndarray:
        if not self._is_enabled or not self._coefficients or audio_data.size == 0:
            return audio_data

        processed_data = audio_data.copy()
        for band_name, (b, a) in self._coefficients.items():
            processed_data, self._zi[band_name] = signal.lfilter(
                b, a, processed_data, axis=0, zi=self._zi[band_name]
            )

        return processed_data

    @property
    def is_enabled(self) -> bool:
        return bool(self._is_enabled)

    @property
    def custom_presets(self) -> Dict[str, Dict[str, Any]]:
        return {name: settings.copy() for name, settings in self._custom_presets.items()}

    def enable(self, enable: Optional[bool] = None):
        if enable is None:
            enable = not self._is_enabled
        enable = bool(enable)
        if self._is_enabled != enable:
            self._is_enabled = enable
            self.settings_manager.set_setting("equalizer_enabled", enable)
            self.event_bus.publish(AudioEventType.EQ_CHANGED, self.get_current_state())

    def set_band_gain(self, band_name: str, gain: float):
        if band_name in self._band_gains:
            clamped_gain = max(-12.0, min(12.0, gain))
            if self._band_gains[band_name] != clamped_gain:
                self._band_gains[band_name] = clamped_gain
                self._recalculate_filter_for_band(band_name)
                self._current_preset_name = "Custom"
                self.event_bus.publish(
                    AudioEventType.EQ_CHANGED, self.get_current_state()
                )

    def get_current_state(self) -> Dict[str, Any]:
        return {
            "enabled": self._is_enabled,
            "preset_name": self._current_preset_name,
            "band_gains": self._band_gains.copy(),
        }

    def get_current_eq_settings(self) -> Dict[str, Any]:
        return self.get_current_state()

    def get_current_preset_name(self) -> str:
        return self._current_preset_name

    def get_band_gain(self, band_name: str) -> float:
        return float(self._band_gains.get(band_name, 0.0))

    def get_band_names(self) -> List[str]:
        return list(self.DEFAULT_BANDS.keys())

    def get_all_bands(self) -> Dict[str, Dict[str, Union[float, int]]]:
        return {
            band_name: {
                "freq": int(band_info["freq"]),
                "gain": float(self._band_gains.get(band_name, 0.0)),
                "q": float(band_info["q"]),
            }
            for band_name, band_info in self.DEFAULT_BANDS.items()
        }

    def get_available_presets(self) -> List[str]:
        return list(self.PREDEFINED_PRESETS.keys()) + list(self._custom_presets.keys())

    def _resolve_known_preset_name(self, preset_name: str) -> Optional[str]:
        normalized = str(preset_name).strip().lower()
        if not normalized:
            return None
        for candidate in self.get_available_presets():
            if str(candidate).strip().lower() == normalized:
                return candidate
        return None

    def _sanitize_band_gains(self, gains: Dict[str, float]) -> Dict[str, float]:
        return {
            band_name: max(-12.0, min(12.0, float(gains.get(band_name, 0.0))))
            for band_name in self.DEFAULT_BANDS
        }

    def is_builtin_preset(self, preset_name: str) -> bool:
        normalized = str(preset_name).strip().lower()
        return any(str(name).strip().lower() == normalized for name in self.PREDEFINED_PRESETS)

    def set_preset(self, preset_name: str, gains: Optional[Dict[str, float]] = None):
        """Apply a preset or an ad-hoc gain snapshot without persisting dangling names.

        Edge cases handled proactively:
        1. Unknown or whitespace-only preset names are ignored unless explicit gains are supplied.
        2. Explicit gains for a missing preset fall back to the runtime-only Custom state.
        3. Restored gains are sanitized and completed for every band to avoid malformed session state.
        """
        canonical_name = self._resolve_known_preset_name(preset_name)
        if gains is not None:
            new_gains = self._sanitize_band_gains(gains)
            applied_name = canonical_name or "Custom"
        elif canonical_name in self.PREDEFINED_PRESETS:
            new_gains = self.PREDEFINED_PRESETS[canonical_name]
            applied_name = canonical_name
        elif canonical_name in self._custom_presets:
            new_gains = self._custom_presets[canonical_name]
            applied_name = canonical_name
        else:
            logger.warning(
                self._get_localized_text("eq_preset_not_found").format(
                    preset=preset_name
                )
            )
            return

        self._band_gains = new_gains.copy()
        self._current_preset_name = applied_name
        self._recalculate_all_filters()
        self.event_bus.publish(AudioEventType.EQ_CHANGED, self.get_current_state())

    def apply_preset(self, preset_name: str) -> bool:
        canonical_name = self._resolve_known_preset_name(preset_name)
        if canonical_name is None:
            return False
        self.set_preset(canonical_name)
        return True

    def set_eq_settings(self, settings: Dict[str, Any]) -> bool:
        if not isinstance(settings, dict):
            raise ValueError("EQ settings must be a dictionary.")

        enabled = settings.get("enabled")
        preset_name = settings.get("preset_name")
        band_gains = settings.get("band_gains")
        if not isinstance(band_gains, dict):
            if any(band_name in settings for band_name in self.DEFAULT_BANDS):
                band_gains = {
                    band_name: settings.get(band_name, self._band_gains.get(band_name, 0.0))
                    for band_name in self.DEFAULT_BANDS
                }
            else:
                band_gains = None

        if preset_name and band_gains is None:
            if not self.apply_preset(str(preset_name)):
                raise ValueError(
                    self._get_localized_text("eq_preset_not_found").format(
                        preset=preset_name
                    )
                )
        elif band_gains is not None:
            new_gains: Dict[str, float] = {}
            for band_name in self.DEFAULT_BANDS:
                raw_gain = band_gains.get(band_name, self._band_gains.get(band_name, 0.0))
                try:
                    parsed_gain = float(raw_gain)
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        self._get_localized_text("eq_invalid_gain").format(
                            gain=raw_gain,
                            min_gain=-12,
                            max_gain=12,
                        )
                    ) from exc
                new_gains[band_name] = max(-12.0, min(12.0, parsed_gain))

            self._band_gains = new_gains
            self._current_preset_name = (
                str(preset_name).strip() if preset_name else "Custom"
            )
            self._recalculate_all_filters()
            self.event_bus.publish(AudioEventType.EQ_CHANGED, self.get_current_state())

        if enabled is not None:
            self.enable(bool(enabled))

        return True

    def save_custom_preset(self, name: str) -> bool:
        """Persist the current EQ gains as a named custom preset.

        Edge cases handled proactively:
        1. Empty or whitespace-only names are rejected before touching storage.
        2. Built-in preset names and case-insensitive duplicates are blocked.
        3. Current in-memory gains are clamped before persistence to avoid invalid restores.
        """
        preset_name = str(name).strip()
        available_names = {str(existing).strip().lower() for existing in self.get_available_presets()}
        if not preset_name or self.is_builtin_preset(preset_name) or preset_name.lower() in available_names:
            raise ValueError(
                self._get_localized_text("eq_preset_name_exists").format(name=preset_name)
            )

        validated_gains = {
            band: max(-12.0, min(12.0, gain)) for band, gain in self._band_gains.items()
        }

        try:
            self.db_manager.add_custom_eq_preset(preset_name, validated_gains)
            self._custom_presets[preset_name] = validated_gains.copy()
            self.set_preset(preset_name, validated_gains)
            self.event_bus.publish(
                AudioEventType.CUSTOM_PRESETS_UPDATED,
                {"type": "eq", "presets": self.get_available_presets()},
            )
            return True
        except EQUALIZER_PRESET_EXCEPTIONS as e:
            logger.error(
                self._get_localized_text("error_saving_custom_eq_preset").format(
                    name=preset_name, error=e
                ),
                exc_info=True,
            )
            raise

    def delete_custom_preset(self, name: str) -> bool:
        """Delete a persisted custom preset without leaving dangling runtime state.

        Edge cases handled proactively:
        1. Built-in preset names are rejected even if a caller bypasses the UI guard.
        2. Missing custom presets return False without mutating runtime state.
        3. Deleting the currently active custom preset falls back to Flat to avoid persisting an invalid preset name.
        """
        preset_name = str(name).strip()
        if self.is_builtin_preset(preset_name):
            raise ValueError(
                self._get_localized_text("eq_preset_not_custom").format(name=preset_name)
            )
        if preset_name not in self._custom_presets:
            return False

        normalized_current = self.get_current_preset_name().strip().lower()
        should_reset_current = normalized_current == preset_name.lower()

        try:
            self.db_manager.delete_custom_eq_preset(preset_name)
            del self._custom_presets[preset_name]
            if should_reset_current:
                self.set_preset("Flat")
            self.event_bus.publish(
                AudioEventType.CUSTOM_PRESETS_UPDATED,
                {"type": "eq", "presets": self.get_available_presets()},
            )
            return True
        except EQUALIZER_PRESET_EXCEPTIONS as e:
            logger.error(
                self._get_localized_text("error_deleting_custom_eq_preset").format(
                    name=preset_name, error=e
                ),
                exc_info=True,
            )
            raise

    def apply_eq_to_chunk(self, audio_data: np.ndarray) -> np.ndarray:
        return self.apply_equalization(audio_data)

    def _on_sample_rate_changed(self, data: Dict[str, Any]):
        new_sample_rate = data.get("sample_rate")
        if new_sample_rate and new_sample_rate != self._sample_rate:
            self._sample_rate = new_sample_rate
            self._recalculate_all_filters()

    def _on_channels_changed(self, data: Dict[str, Any]):
        new_channels = data.get("channels")
        if new_channels and new_channels != self._channels:
            self._channels = new_channels
            self._recalculate_all_filters()  # Ricalcola anche le condizioni iniziali

    def _on_profile_loaded(self, data: Dict[str, Any]):
        self._load_initial_eq_settings()
        self._recalculate_all_filters()

    def _on_settings_updated(self, data: Dict[str, Any]):
        setting_name = data.get("setting_name", data.get("key"))
        if setting_name == "equalizer_enabled":
            self.enable(data.get("value"))

    def close(self):
        """Persist a deterministic Flat startup state instead of the last runtime preset.

        Edge cases handled proactively:
        1. Custom or built-in runtime presets must not survive a clean app restart.
        2. Partial runtime band states are replaced with the canonical Flat gains snapshot.
        3. The enabled flag remains managed separately, so only preset/gain startup state is reset.
        """
        flat_gains = self.PREDEFINED_PRESETS["Flat"].copy()
        self.settings_manager.set_setting("eq_last_preset", "Flat")
        self.settings_manager.set_setting("eq_last_gains", flat_gains)
        logger.info(self._get_localized_text("equalizer_closing"))
