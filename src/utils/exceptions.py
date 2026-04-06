from typing import Optional


class WaveHelmError(Exception):
    """
    Eccezione base per tutte le eccezioni specifiche di WaveHelm.
    Supporta messaggi localizzati e codici di errore strutturati.
    """

    DEFAULT_MESSAGE = "Si è verificato un errore nell'applicazione."
    ERROR_CODE = "GENERIC_ERROR"  # Aggiunto un codice di errore di default

    def __init__(
        self,
        message: Optional[str] = None,
        details: Optional[str] = None,
        error_code: Optional[str] = None,
        *args,  # Cattura argomenti posizionali extra
        **kwargs,
    ):  # Cattura argomenti keyword extra
        """
        Inizializza un'eccezione WaveHelm.

        Args:
            message: Messaggio descrittivo dell'errore
            details: Dettagli tecnici aggiuntivi
            error_code: Codice identificativo strutturato per l'errore
            *args, **kwargs: Argomenti extra da passare alla classe base Exception
        """
        self.message = message or self.DEFAULT_MESSAGE
        self.details = details
        self.error_code = (
            error_code or self.ERROR_CODE
        )  # Usa il codice di errore della classe
        super().__init__(
            self.message, *args, **kwargs
        )  # Passa il messaggio e gli argomenti extra alla classe base

    def __str__(self) -> str:
        """Formattazione dell'errore per logging e debug."""
        parts = [f"[{self.error_code}] {self.message}"]
        if self.details:
            parts.append(f"Dettagli: {self.details}")
        return "\n".join(parts)

    def to_dict(self) -> dict:
        """Restituisce una rappresentazione JSON-friendly dell'errore."""
        return {
            "error_code": self.error_code,
            "message": self.message,
            "details": self.details,
        }

# ========================
# Eccezioni di dominio
# ========================
class DomainError(WaveHelmError):
    """Eccezione base per errori logici o di business."""

    DEFAULT_MESSAGE = "Errore di dominio."
    ERROR_CODE = "DOMAIN_ERROR"


class ConfigurationError(DomainError):
    """Eccezione per problemi di configurazione dell'applicazione."""

    DEFAULT_MESSAGE = "Errore di configurazione."
    ERROR_CODE = "CONFIGURATION_ERROR"


class SettingsError(ConfigurationError):
    """Eccezione per errori relativi alle impostazioni."""

    DEFAULT_MESSAGE = "Errore nelle impostazioni."
    ERROR_CODE = "SETTINGS_ERROR"


class ProfileError(DomainError):
    """Eccezione per errori del profilo utente."""

    DEFAULT_MESSAGE = "Errore del profilo utente."
    ERROR_CODE = "PROFILE_ERROR"


class ValidationError(DomainError):
    """Eccezione per errori di validazione dei dati."""

    DEFAULT_MESSAGE = "Errore di validazione."
    ERROR_CODE = "VALIDATION_ERROR"


class NotFoundError(DomainError):
    """Eccezione per risorse non trovate."""

    DEFAULT_MESSAGE = "Risorsa non trovata."
    ERROR_CODE = "NOT_FOUND_ERROR"


# ========================
# Eccezioni di sistema/I/O
# ========================
class SystemError(WaveHelmError):
    """Eccezione base per errori di sistema o I/O."""

    DEFAULT_MESSAGE = "Errore di sistema."
    ERROR_CODE = "SYSTEM_ERROR"


class FileError(SystemError):
    """Eccezione per problemi di accesso o manipolazione file."""

    DEFAULT_MESSAGE = "Errore del file system."
    ERROR_CODE = "FILE_ERROR"


class NetworkError(SystemError):
    """Eccezione per problemi di rete."""

    DEFAULT_MESSAGE = "Errore di rete."
    ERROR_CODE = "NETWORK_ERROR"


class DatabaseError(SystemError):
    """Eccezione per problemi di database."""

    DEFAULT_MESSAGE = "Errore del database."
    ERROR_CODE = "DATABASE_ERROR"


class IntegrityError(DatabaseError):
    """Eccezione per violazioni di integrità del database."""

    DEFAULT_MESSAGE = "Violazione di integrità del database."
    ERROR_CODE = "INTEGRITY_ERROR"


# ========================
# Eccezioni multimediali
# ========================
class MediaError(WaveHelmError):
    """Eccezione base per errori multimediali."""

    DEFAULT_MESSAGE = "Errore multimediale."
    ERROR_CODE = "MEDIA_ERROR"


class PlaybackError(MediaError):
    """Eccezione per errori durante la riproduzione multimediale."""

    DEFAULT_MESSAGE = "Errore di riproduzione."
    ERROR_CODE = "PLAYBACK_ERROR"


class AudioProcessingError(MediaError):
    """Eccezione per errori nell'elaborazione audio."""

    DEFAULT_MESSAGE = "Errore nell'elaborazione audio."
    ERROR_CODE = "AUDIO_PROCESSING_ERROR"


class VideoProcessingError(MediaError):
    """Eccezione per errori nell'elaborazione video."""

    DEFAULT_MESSAGE = "Errore nell'elaborazione video."
    ERROR_CODE = "VIDEO_PROCESSING_ERROR"


class CodecError(MediaError):
    """Eccezione per problemi con i codec multimediali."""

    DEFAULT_MESSAGE = "Problema con il codec multimediale."
    ERROR_CODE = "CODEC_ERROR"


class DownloadError(MediaError):
    """Eccezione per errori durante il download di media."""

    DEFAULT_MESSAGE = "Errore durante il download."
    ERROR_CODE = "DOWNLOAD_ERROR"


# ========================
# Eccezioni di sicurezza
# ========================
class SecurityError(WaveHelmError):
    """Eccezione base per problemi di sicurezza."""

    DEFAULT_MESSAGE = "Problema di sicurezza."
    ERROR_CODE = "SECURITY_ERROR"

    def __init__(self, message=None, details=None):
        super().__init__(message or self.DEFAULT_MESSAGE, details, self.ERROR_CODE)


class AuthenticationError(SecurityError):
    """Eccezione per problemi di autenticazione."""

    DEFAULT_MESSAGE = "Autenticazione fallita."
    ERROR_CODE = "AUTHENTICATION_ERROR"


class AuthorizationError(SecurityError):
    """Eccezione per problemi di autorizzazione."""

    DEFAULT_MESSAGE = "Autorizzazione negata."
    ERROR_CODE = "AUTHORIZATION_ERROR"
