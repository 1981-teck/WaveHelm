from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Tuple
import logging
from threading import Lock

# Configure logger for this module
logger = logging.getLogger(__name__)


@dataclass
class AudioEngineConfig:
    """Centralized configuration for the audio engine"""

    # Pygame mixer settings
    frequency: int = 44100
    size: int = -16
    channels: int = 2
    buffer: int = 512
    num_channels: int = 8

    # File limits
    max_file_size_mb: int = 500
    supported_formats: Tuple[str, ...] = (".mp3", ".wav", ".flac", ".ogg", ".m4a")

    # Threading
    progress_update_interval: float = 0.1
    thread_join_timeout: float = 2.0

    # DSP
    default_chunk_size: int = 2048
    max_samples_for_memory_processing: int = 10_000_000

    # EQ
    # Standardized 10-band EQ definition
    # This is the central reference for all EQ bands in the application
    eq_bands: Dict[str, int] = field(
        default_factory=lambda: {
            "31 Hz": 31,
            "62 Hz": 62,
            "125 Hz": 125,
            "250 Hz": 250,
            "500 Hz": 500,
            "1 kHz": 1000,
            "2 kHz": 2000,
            "4 kHz": 4000,
            "8 kHz": 8000,
            "16 kHz": 16000,
        }
    )

    # Default EQ gains (flat)
    default_eq_gains: Dict[str, float] = field(
        default_factory=lambda: {
            "31 Hz": 0.0,
            "62 Hz": 0.0,
            "125 Hz": 0.0,
            "250 Hz": 0.0,
            "500 Hz": 0.0,
            "1 kHz": 0.0,
            "2 kHz": 0.0,
            "4 kHz": 0.0,
            "8 kHz": 0.0,
            "16 kHz": 0.0,
        }
    )

    # Gain limits for EQ bands
    min_gain_db: float = -20.0
    max_gain_db: float = 20.0

    # DSP effects
    default_echo_delay_ms: float = 300.0
    default_echo_decay: float = 0.5
    default_reverb_decay_time: float = 1.5
    default_vintage_filter_type: str = "lowpass"
    default_vintage_cutoff_freq: float = 1000.0
    default_vintage_filter_order: int = 4

    def __post_init__(self):
        """
        Method called after initialization to set default values
        if not already provided.
        """
        # Validate value ranges
        if not (8000 <= self.frequency <= 192000):
            raise ValueError(
                f"Invalid frequency: {self.frequency}. Must be between 8000 and 192000 Hz"
            )

        if self.buffer not in [128, 256, 512, 1024, 2048]:
            logger.warning(
                f"Non-standard buffer size: {self.buffer}. Recommended values: 128, 256, 512, 1024, 2048"
            )

        self.validate_eq_bands()

        # Ensure default_eq_gains is aligned with eq_bands
        self._align_eq_gains()

        logger.info("Audio engine configuration initialized successfully")

    def validate_eq_bands(self):
        """Validate that EQ bands are in ascending order and have proper values"""
        frequencies = list(self.eq_bands.values())

        # Check if frequencies are in ascending order
        if frequencies != sorted(frequencies):
            raise ValueError("EQ bands must be in ascending frequency order")

        # Check if all frequencies are positive
        if any(freq <= 0 for freq in frequencies):
            raise ValueError("All EQ band frequencies must be positive")

        # Check if band names are unique
        if len(self.eq_bands.keys()) != len(set(self.eq_bands.keys())):
            raise ValueError("EQ band names must be unique")

    def _align_eq_gains(self):
        """Ensure default_eq_gains matches the eq_bands structure"""
        aligned_gains = {}
        for band_name in self.eq_bands.keys():
            aligned_gains[band_name] = self.default_eq_gains.get(band_name, 0.0)

        self.default_eq_gains = aligned_gains
        logger.debug("EQ gains aligned with EQ bands")

    def validate_gain(self, gain_db: float) -> bool:
        """Check if a gain value is within valid range"""
        return self.min_gain_db <= gain_db <= self.max_gain_db

    def get_eq_band_frequencies(self) -> List[int]:
        """Return EQ band frequencies as a sorted list"""
        return sorted(self.eq_bands.values())

    def get_eq_band_names(self) -> List[str]:
        """Return EQ band names in frequency order"""
        sorted_bands = sorted(self.eq_bands.items(), key=lambda x: x[1])
        return [band[0] for band in sorted_bands]

    def to_dict(self) -> Dict:
        """Convert configuration to dictionary for serialization"""
        return {
            "frequency": self.frequency,
            "size": self.size,
            "channels": self.channels,
            "buffer": self.buffer,
            "num_channels": self.num_channels,
            "max_file_size_mb": self.max_file_size_mb,
            "supported_formats": self.supported_formats,
            "progress_update_interval": self.progress_update_interval,
            "thread_join_timeout": self.thread_join_timeout,
            "default_chunk_size": self.default_chunk_size,
            "max_samples_for_memory_processing": self.max_samples_for_memory_processing,
            "eq_bands": self.eq_bands,
            "default_eq_gains": self.default_eq_gains,
            "min_gain_db": self.min_gain_db,
            "max_gain_db": self.max_gain_db,
            "default_echo_delay_ms": self.default_echo_delay_ms,
            "default_echo_decay": self.default_echo_decay,
            "default_reverb_decay_time": self.default_reverb_decay_time,
            "default_vintage_filter_type": self.default_vintage_filter_type,
            "default_vintage_cutoff_freq": self.default_vintage_cutoff_freq,
            "default_vintage_filter_order": self.default_vintage_filter_order,
        }

    @classmethod
    def from_dict(cls, config_dict: Dict) -> AudioEngineConfig:
        """Create configuration from dictionary"""
        return cls(**config_dict)


# Thread-safe singleton implementation
_audio_config_instance: Optional[AudioEngineConfig] = None
_audio_config_lock = Lock()


def get_audio_config() -> AudioEngineConfig:
    """
    Returns the singleton instance of AudioEngineConfig.
    Thread-safe implementation.
    """
    global _audio_config_instance

    if _audio_config_instance is None:
        with _audio_config_lock:
            # Double-checked locking pattern
            if _audio_config_instance is None:
                _audio_config_instance = AudioEngineConfig()
                logger.info("AudioEngineConfig singleton instance created")

    return _audio_config_instance


def reset_audio_config() -> None:
    """
    Reset the singleton instance (mainly for testing purposes).
    """
    global _audio_config_instance
    with _audio_config_lock:
        _audio_config_instance = None
    logger.info("AudioEngineConfig singleton instance reset")
