from __future__ import annotations
import numpy as np
from scipy import signal
import logging

# Configurazione del logger per questo modulo
logger = logging.getLogger(__name__)

VINTAGE_FILTER_EXCEPTIONS = (FloatingPointError, RuntimeError, TypeError, ValueError)


def normalize_audio(audio_data: np.ndarray) -> np.ndarray:
    """
    Normalizza i dati audio per prevenire il clipping, scalando i valori
    all'intervallo [-1.0, 1.0].
    """
    if audio_data.size == 0:
        return audio_data

    max_abs_val = np.max(np.abs(audio_data))
    if max_abs_val > 1.0:
        return audio_data / max_abs_val
    return audio_data


def apply_echo(audio_data: np.ndarray, delay_samples: int, decay: float) -> np.ndarray:
    """
    Applica un effetto eco ai dati audio in modo efficiente.
    """
    if delay_samples <= 0 or not (0 <= decay <= 1):
        return audio_data

    output_data = np.copy(audio_data)
    if audio_data.ndim == 1:
        output_data[delay_samples:] += audio_data[:-delay_samples] * decay
    else:
        output_data[delay_samples:, :] += audio_data[:-delay_samples, :] * decay
    return output_data


def _comb_filter(data, delay, gain):
    """Filtro comb per il riverbero."""
    output = np.zeros_like(data)
    buffer = np.zeros(delay)
    for i, sample in enumerate(data):
        delayed_sample = buffer[i % delay]
        buffer[i % delay] = sample + delayed_sample * gain
        output[i] = delayed_sample
    return output


def _allpass_filter(data, delay, gain):
    """Filtro all-pass per il riverbero."""
    output = np.zeros_like(data)
    buffer = np.zeros(delay)
    for i, sample in enumerate(data):
        delayed_sample = buffer[i % delay]
        buffer[i % delay] = sample + delayed_sample * gain
        output[i] = delayed_sample * gain + sample
    return output


def _add_delayed_mix(
    target: np.ndarray, source: np.ndarray, delay_samples: int, gain: float
) -> None:
    """Accumulate a delayed tap into target without allocating full extra buffers."""
    if delay_samples <= 0 or delay_samples >= source.shape[0] or abs(gain) <= 1e-5:
        return
    target[delay_samples:] += source[:-delay_samples] * np.float32(gain)


def apply_reverb(
    audio_data: np.ndarray, sample_rate: int, decay_time: float, wet_level: float
) -> np.ndarray:
    """
    Applica un riverbero leggero multi-tap, adatto al rendering offline
    di file lunghi senza creare grandi picchi di memoria.
    """
    if sample_rate <= 0 or decay_time <= 0 or not (0 <= wet_level <= 1):
        return audio_data

    if audio_data.ndim == 1:
        audio_data = np.expand_dims(audio_data, axis=1)

    dry = np.asarray(audio_data, dtype=np.float32)
    num_samples, num_channels = dry.shape
    wet = np.zeros_like(dry, dtype=np.float32)

    # Scale room size with decay time, but keep the tap train bounded.
    room_scale = float(np.clip(decay_time / 1.5, 0.6, 2.4))
    tap_times = np.array(
        [0.009, 0.013, 0.017, 0.021, 0.029, 0.037, 0.043, 0.051],
        dtype=np.float32,
    ) * room_scale
    tap_gains = np.array(
        [0.52, 0.46, 0.40, 0.34, 0.28, 0.22, 0.17, 0.13],
        dtype=np.float32,
    ) * np.float32(np.clip(0.75 + decay_time * 0.08, 0.75, 1.10))

    for channel_index in range(num_channels):
        channel_source = dry[:, channel_index]
        channel_wet = wet[:, channel_index]

        for tap_time, tap_gain in zip(tap_times, tap_gains):
            delay_samples = max(1, int(float(tap_time) * sample_rate))
            _add_delayed_mix(channel_wet, channel_source, delay_samples, float(tap_gain))

        # Add a very small crossfeed to widen stereo tails without a second pass buffer.
        if num_channels > 1:
            other_source = dry[:, 1 - channel_index]
            stereo_spread = max(1, int(sample_rate * 0.006 * room_scale))
            _add_delayed_mix(channel_wet, other_source, stereo_spread, 0.08)

    if num_samples:
        peak = float(np.max(np.abs(wet)))
        if peak > 1.0:
            wet = wet / peak

    mixed_data = (1.0 - wet_level) * dry + wet_level * wet
    return mixed_data.astype(np.float32, copy=False)


def apply_vintage_filter(
    audio_data: np.ndarray, sample_rate: int, cutoff_freq: float, resonance: float
) -> np.ndarray:
    """
    Applica un filtro passa-basso risonante stabile, con una lieve saturazione
    per un carattere piu' "vintage".
    """
    nyquist = 0.5 * sample_rate
    if not (20 <= cutoff_freq < nyquist and 0 <= resonance <= 1):
        return audio_data

    if audio_data.ndim == 1:
        audio_data = np.expand_dims(audio_data, axis=1)

    dry = np.asarray(audio_data, dtype=np.float32)

    # Mappa la risonanza a un fattore Q utile ma stabile.
    q_factor = 0.707 + float(resonance) * (4.5 - 0.707)
    omega = 2.0 * np.pi * float(cutoff_freq) / float(sample_rate)
    sin_omega = np.sin(omega)
    cos_omega = np.cos(omega)
    alpha = sin_omega / (2.0 * q_factor)

    try:
        a0 = 1.0 + alpha
        b = np.array(
            [
                (1.0 - cos_omega) / 2.0,
                1.0 - cos_omega,
                (1.0 - cos_omega) / 2.0,
            ],
            dtype=np.float64,
        ) / a0
        a = np.array(
            [
                1.0,
                (-2.0 * cos_omega) / a0,
                (1.0 - alpha) / a0,
            ],
            dtype=np.float64,
        )

        output_data = np.zeros_like(dry, dtype=np.float32)
        drive = 1.0 + float(resonance) * 0.45
        drive_norm = np.tanh(drive)

        for c in range(dry.shape[1]):
            filtered = signal.lfilter(b, a, dry[:, c]).astype(np.float32, copy=False)
            # Lieve saturazione per aggiungere calore senza snaturare il file.
            warmed = np.tanh(filtered * drive) / drive_norm
            output_data[:, c] = warmed.astype(np.float32, copy=False)

        return output_data
    except VINTAGE_FILTER_EXCEPTIONS as e:
        logger.error(
            f"[effects_dsp] Errore durante l'applicazione del filtro vintage: {e}",
            exc_info=True,
        )
        return audio_data
