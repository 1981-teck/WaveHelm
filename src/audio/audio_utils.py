from __future__ import annotations
import soundfile as sf
import numpy as np
import os
import logging
from typing import Tuple, Optional, TYPE_CHECKING

# Importazioni per type hinting (per evitare importazioni circolari)
if TYPE_CHECKING:
    from src.model.localization_manager import LocalizationManager

# Configurazione del logger per questo modulo
logger = logging.getLogger(__name__)

AUDIO_READ_ERROR_EXCEPTIONS = (OSError, TypeError, ValueError)


class AudioUtils:
    """
    Contiene funzioni di utilità per il caricamento e la manipolazione dei dati PCM audio.
    """

    _localization_manager: Optional[LocalizationManager] = None

    @classmethod
    def set_localization_manager(cls, manager: LocalizationManager):
        """Imposta il gestore della localizzazione per la classe."""
        cls._localization_manager = manager
        logger.info("[AudioUtils] LocalizationManager impostato.")

    @staticmethod
    def _get_localized_text(key: str, **kwargs) -> str:
        """Helper per ottenere il testo tradotto, con fallback se il manager non è impostato."""
        if AudioUtils._localization_manager:
            return AudioUtils._localization_manager.get_text(key, **kwargs)
        logger.warning(
            f"[AudioUtils] LocalizationManager non impostato. Restituisco chiave '{key}'."
        )
        return key  # Fallback alla chiave se il manager non è disponibile

    @staticmethod
    def get_raw_audio_data(file_path: str) -> Tuple[np.ndarray, int, int]:
        """
        Carica i dati audio grezzi da un file e restituisce l'array NumPy,
        il sample rate e il numero di canali.
        Args:
            file_path (str): Il percorso del file audio.
        Returns:
            Tuple[np.ndarray, int, int]: Un tuple contenente:
                - np.ndarray: I dati audio come array NumPy (float32).
                - int: Il sample rate (Hz).
                - int: Il numero di canali.
        Raises:
            FileNotFoundError: Se il file non viene trovato.
            ValueError: Se si verifica un errore durante il caricamento del file.
        """
        if not os.path.exists(file_path):
            error_msg = AudioUtils._get_localized_text("error_file_not_found").format(
                path=file_path
            )
            logger.error(f"[AudioUtils] {error_msg}")
            raise FileNotFoundError(error_msg)

        try:
            data, samplerate = sf.read(file_path, dtype="float32")

            if data.ndim == 1:
                channels = 1
            else:
                channels = data.shape[1]

            logger.info(
                AudioUtils._get_localized_text("raw_audio_loaded").format(
                    path=file_path,
                    sr=samplerate,
                    channels=channels,
                    samples=data.shape[0],
                )
            )
            return data, samplerate, channels
        except (sf.LibsndfileError, RuntimeError) as e:
            error_msg = AudioUtils._get_localized_text(
                "error_loading_audio_file_soundfile"
            ).format(path=file_path, error=e)
            logger.error(f"[AudioUtils] {error_msg}", exc_info=True)
            raise ValueError(error_msg) from e
        except AUDIO_READ_ERROR_EXCEPTIONS as e:
            error_msg = AudioUtils._get_localized_text(
                "error_loading_audio_file_unexpected"
            ).format(path=file_path, error=e)
            logger.critical(f"[AudioUtils] {error_msg}", exc_info=True)
            raise ValueError(error_msg) from e

    @staticmethod
    def normalize_audio(audio_data: np.ndarray) -> np.ndarray:
        """
        Normalizza i dati audio per prevenire il clipping, scalando i valori
        all'intervallo [-1.0, 1.0].
        Args:
            audio_data (np.ndarray): Array NumPy dei dati audio.
        Returns:
            np.ndarray: Dati audio normalizzati.
        """
        if audio_data.size == 0:
            logger.warning("[AudioUtils] Tentativo di normalizzare dati audio vuoti.")
            return audio_data

        max_abs_val = np.max(np.abs(audio_data))
        if max_abs_val > 1.0:
            normalized_data = audio_data / max_abs_val
            logger.debug(
                AudioUtils._get_localized_text("audio_normalized").format(
                    max_val=max_abs_val
                )
            )
            return normalized_data

        # Se max_abs_val è <= 1.0, non è necessaria alcuna normalizzazione.
        # Questo gestisce anche il caso max_abs_val == 0.0.
        logger.debug(
            "[AudioUtils] Dati audio già nell'intervallo [-1.0, 1.0], nessuna normalizzazione necessaria."
        )
        return audio_data
