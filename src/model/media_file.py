from dataclasses import dataclass, field
from enum import Enum
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

MEDIA_FILE_INIT_EXCEPTIONS = (AttributeError, OSError, TypeError, ValueError)
MEDIA_FILE_TITLE_EXCEPTIONS = (AttributeError, TypeError, ValueError)
MEDIA_FILE_TYPE_EXCEPTIONS = (AttributeError, OSError, TypeError, ValueError)
MEDIA_FILE_DURATION_EXCEPTIONS = (OverflowError, TypeError, ValueError)
MEDIA_FILE_FROM_DICT_EXCEPTIONS = (AttributeError, TypeError, ValueError)


class MediaType(Enum):
    AUDIO = "audio"
    VIDEO = "video"
    CD_TRACK = "cd_track"
    PODCAST = "podcast"
    STREAM = "stream"
    UNKNOWN = "unknown"
    ALL = "all"


@dataclass
class MediaFile:
    path: str
    title: str = field(default="")
    media_type: MediaType = MediaType.UNKNOWN
    duration: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        try:
            self.path = os.path.normpath(self.path) if self.is_local_file() else self.path

            if not self.title:
                self.title = self._derive_title()

            if self.media_type == MediaType.UNKNOWN:
                self.media_type = self._detect_media_type()

            if self.duration < 0:
                logger.warning(f"Durata negativa impostata a 0 per: {self.path}")
                self.duration = 0.0
        except MEDIA_FILE_INIT_EXCEPTIONS as error:
            logger.error(f"Errore in __post_init__: {str(error)}", exc_info=True)

        if not isinstance(self.metadata, dict):
            logger.warning(
                f"Metadata non valido per {self.path}, reimpostato a dict vuoto"
            )
            self.metadata = {}

    def _derive_title(self) -> str:
        try:
            if self.metadata.get("title"):
                return self.metadata["title"]

            if self.path:
                return Path(self.path).stem

            return "Sconosciuto"
        except MEDIA_FILE_TITLE_EXCEPTIONS as error:
            logger.error(f"Errore derivazione titolo: {str(error)}", exc_info=True)
            return "Sconosciuto"

    def _detect_media_type(self) -> MediaType:
        try:
            if self.path.startswith(("http://", "https://", "rtsp://", "rtmp://")):
                return MediaType.STREAM

            if self.path and os.path.exists(self.path):
                ext = Path(self.path).suffix.lower()
                audio_exts = {
                    ".mp3",
                    ".wav",
                    ".flac",
                    ".ogg",
                    ".aac",
                    ".m4a",
                    ".wma",
                    ".opus",
                }
                video_exts = {
                    ".mp4",
                    ".avi",
                    ".mov",
                    ".mkv",
                    ".webm",
                    ".flv",
                    ".wmv",
                    ".mpeg",
                }

                if ext in audio_exts:
                    return MediaType.AUDIO
                if ext in video_exts:
                    return MediaType.VIDEO
                if ext in {".cda"}:
                    return MediaType.CD_TRACK

            if "media_type" in self.metadata:
                try:
                    return MediaType(self.metadata["media_type"].lower())
                except (ValueError, AttributeError) as error:
                    logger.debug(
                        "Invalid media_type metadata for %s: %s",
                        self.path,
                        error,
                        exc_info=True,
                    )

            return MediaType.UNKNOWN
        except MEDIA_FILE_TYPE_EXCEPTIONS as error:
            logger.error(f"Errore rilevamento tipo media: {str(error)}", exc_info=True)
            return MediaType.UNKNOWN

    def get_display_title(self) -> str:
        artist = self.metadata.get("artist", "")
        album = self.metadata.get("album", "")

        if artist and album:
            return f"{self.title} - {artist} ({album})"
        elif artist:
            return f"{artist} - {self.title}"
        elif album:
            return f"{self.title} ({album})"
        else:
            return self.title

    def get_formatted_duration(self) -> str:
        try:
            total_seconds = int(self.duration)
            hours, remainder = divmod(total_seconds, 3600)
            minutes, seconds = divmod(remainder, 60)

            if hours > 0:
                return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            return f"{minutes:02d}:{seconds:02d}"
        except MEDIA_FILE_DURATION_EXCEPTIONS as error:
            logger.error(f"Errore formattazione durata: {str(error)}", exc_info=True)
            return "00:00"

    def is_local_file(self) -> bool:
        return not self.path.startswith(("http://", "https://", "rtsp://", "rtmp://"))

    @property
    def file_extension(self) -> str:
        return Path(self.path).suffix.lower()

    @property
    def file_size(self) -> int:
        if self.is_local_file() and self.exists():
            return os.path.getsize(self.path)
        return 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "title": self.title,
            "media_type": self.media_type.value,
            "duration": self.duration,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Optional["MediaFile"]:
        if not data or not isinstance(data, dict):
            logger.warning("Dati non validi per creare MediaFile")
            return None

        try:
            path = data.get("path", "")
            title = data.get("title", "")
            duration = data.get("duration", 0.0)
            metadata = data.get("metadata", {})

            media_type_str = data.get("media_type")
            media_type = MediaType.UNKNOWN

            if media_type_str:
                try:
                    media_type = MediaType(media_type_str.lower())
                except ValueError:
                    logger.warning(f"Tipo media non riconosciuto: {media_type_str}")

            return cls(
                path=path,
                title=title,
                media_type=media_type,
                duration=duration,
                metadata=metadata,
            )
        except MEDIA_FILE_FROM_DICT_EXCEPTIONS as error:
            logger.error(f"Errore creazione MediaFile da dict: {error}")
            return None


Media = MediaFile
