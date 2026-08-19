from __future__ import annotations

import codecs
import logging
import math
import os
import stat
import time
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Optional

from src.model.media_file import MediaFile, MediaType
from src.utils import ffprobe_service
from src.utils.media_metadata import (
    AudioMetadataDependencyUnavailableError,
    AudioMetadataError,
    AudioTagMetadata,
    read_audio_rich_metadata,
)


try:
    import cv2

    OPENCV_AVAILABLE = True
except ImportError:
    cv2 = None  # type: ignore[assignment]
    OPENCV_AVAILABLE = False
    warnings.warn("OpenCV non è installato. Le funzionalità video useranno fallback alternativi.")

from PIL import Image  # noqa: F401


if TYPE_CHECKING:
    from src.model.localization_manager import LocalizationManager

logger = logging.getLogger(__name__)

MEDIA_METADATA_EXCEPTIONS = (AttributeError, OSError, RuntimeError, TypeError, ValueError)
AUDIO_METADATA_EXCEPTIONS = (AudioMetadataDependencyUnavailableError, AudioMetadataError) + MEDIA_METADATA_EXCEPTIONS

SUBTITLE_EXTENSIONS = (".srt", ".vtt", ".ass")
SUBTITLE_MAX_BYTES = 4 * 1024 * 1024
SUBTITLE_READ_CHUNK_BYTES = 64 * 1024


class SubtitleSidecarError(RuntimeError):
    """Base error for rejected sidecars."""


class SubtitleSizeLimitError(SubtitleSidecarError):
    """The byte budget was exceeded."""


class SubtitleEncodingError(SubtitleSidecarError):
    """The encoding policy was violated."""


class SubtitleFileTypeError(SubtitleSidecarError):
    """The path is not a stable regular file."""


class MediaLoader:
    """Carica i metadati di file multimediali (audio e video) in modo efficiente."""

    _AUDIO_EXTENSIONS = {
        ".mp3",
        ".wav",
        ".flac",
        ".ogg",
        ".aac",
        ".m4a",
        ".wma",
        ".opus",
    }
    _VIDEO_EXTENSIONS = {
        ".mp4",
        ".avi",
        ".mov",
        ".mkv",
        ".webm",
        ".flv",
        ".wmv",
        ".mpeg",
    }

    def __init__(self, localization_manager: LocalizationManager):
        """
        Inizializza il MediaLoader con un gestore di localizzazione.
        Args:
            localization_manager (LocalizationManager): L'istanza del LocalizationManager.
        """
        self.localization_manager = localization_manager
        logger.info("MediaLoader inizializzato con LocalizationManager.")

    def _get_localized_text(self, key: str, **kwargs) -> str:
        """Helper per ottenere il testo tradotto."""
        return self.localization_manager.get_text(key, **kwargs)

    def load(self, file_path: str) -> Optional[MediaFile]:
        """
        Carica i metadati di un file multimediale rilevando automaticamente il tipo.
        Supporta audio e video locali.
        """
        path = Path(file_path)
        if not path.exists():
            logger.error(
                self._get_localized_text("file_not_found").format(file_path=file_path)
            )
            return None

        # Rileva il tipo di media
        media_type = self._detect_media_type(
            file_path
        )  # Usa self per accedere ai metodi

        if media_type in (MediaType.AUDIO, MediaType.CD_TRACK):
            return self.load_audio(file_path)  # Usa self
        elif media_type == MediaType.VIDEO:
            return self.load_video(file_path)  # Usa self
        else:
            logger.warning(
                self._get_localized_text("unsupported_media_type_loader").format(
                    file_path=file_path
                )
            )
            return self._create_basic_media_file(
                file_path, MediaType.UNKNOWN
            )  # Usa self

    def _detect_media_type(self, file_path: str) -> MediaType:
        """Rileva il tipo di media in base al percorso o all'estensione.

        Edge cases handled:
        - legacy video extensions such as .wmv/.flv/.mpeg stay classified as video
        - unknown or extension-less local files fall back deterministically to UNKNOWN
        """
        path = Path(file_path)

        # Rileva CD audio
        if file_path.startswith("cdda://"):
            return MediaType.CD_TRACK

        # Rileva per estensione
        suffix = path.suffix.lower()
        if suffix in self._AUDIO_EXTENSIONS:
            return MediaType.AUDIO
        if suffix in self._VIDEO_EXTENSIONS:
            return MediaType.VIDEO

        return MediaType.UNKNOWN

    def load_audio(self, file_path: str) -> Optional[MediaFile]:
        """Load audio metadata with deterministic fallback behavior.

        Edge cases handled:
        - the audio metadata reader may be unavailable and must degrade to a basic media file
        - malformed audio files must not crash the loader and must return a basic media file
        - missing or zero durations must fall back to ffprobe when available
        """
        try:
            start_time = time.time()
            tag_metadata = self._read_audio_runtime_metadata(file_path)
            duration = self._resolve_audio_duration(file_path, tag_metadata.duration)
            metadata = self._build_audio_metadata(tag_metadata)
            logger.debug(
                "Audio metadata loaded in %.3fs for %s",
                time.time() - start_time,
                file_path,
            )
            return MediaFile(
                path=file_path,
                title=Path(file_path).stem,
                media_type=MediaType.AUDIO,
                duration=duration,
                metadata=metadata,
            )
        except AUDIO_METADATA_EXCEPTIONS as e:
            logger.error(
                self._get_localized_text("error_reading_audio_metadata").format(
                    file_path=file_path, error=e
                )
            )
            return self._create_basic_media_file(file_path, MediaType.AUDIO)

    def load_video(self, file_path: str) -> Optional[MediaFile]:
        """Load video metadata through bounded OpenCV and ffprobe fallbacks.

        Edge cases handled:
        - unavailable or failing OpenCV falls back to the typed ffprobe boundary
        - invalid frame rates never trigger division by zero
        - ffprobe failures preserve a usable video record with empty track metadata
        """
        start_time = time.time()
        duration = self._read_opencv_video_duration(file_path)
        if duration <= 0.0:
            duration = self._probe_duration_or_zero(file_path, media_kind="video")
        audio_tracks = self._probe_audio_tracks_or_empty(file_path)
        metadata: dict[str, object] = {
            "artist": "",
            "album": "",
            "genre": "",
            "cover_image": None,
            "lyrics": None,
            "subtitles": self._get_video_subtitles(file_path),
            "audio_tracks": audio_tracks,
            "audio_track_candidates": ffprobe_service.build_audio_track_candidates(audio_tracks),
        }
        logger.debug(
            self._get_localized_text("video_metadata_loaded").format(
                time_taken=time.time() - start_time, file_path=file_path
            )
        )
        return MediaFile(
            path=file_path,
            title=Path(file_path).stem,
            media_type=MediaType.VIDEO,
            duration=duration,
            metadata=metadata,
        )

    def _get_audio_metadata_unavailable_text(self) -> str:
        """Return the stable unavailable message for the shared audio metadata reader.

        Edge cases handled:
        - locale files may miss the preferred translation key and must degrade safely
        - localization backends may return no value and must not break runtime warnings
        - the fallback text must remain deterministic across audio metadata failures
        """
        return self.localization_manager.get_text(
            "audio_metadata_reader_not_available",
            default="Audio metadata reader not available.",
        )

    def _read_audio_runtime_metadata(self, file_path: str) -> AudioTagMetadata:
        """Read runtime audio metadata from the current shared adapter.

        Edge cases handled:
        - the adapter dependency may be unavailable and must fail deterministically
        - malformed files must surface stable metadata errors for caller fallback
        - embedded artwork or lyrics may be absent and must degrade to None values
        """
        try:
            return read_audio_rich_metadata(file_path)
        except AudioMetadataDependencyUnavailableError:
            logger.warning(self._get_audio_metadata_unavailable_text())
            raise

    def _resolve_audio_duration(self, file_path: str, duration: float) -> float:
        """Preserve a valid tag duration or use the bounded ffprobe fallback."""
        if math.isfinite(duration) and duration > 0.0:
            return duration
        return self._probe_duration_or_zero(file_path, media_kind="audio")

    def _build_audio_metadata(self, tag_metadata: AudioTagMetadata) -> dict[str, object]:
        """Map adapter metadata to the runtime media payload.

        Edge cases handled:
        - N/A for this bounded pass-through mapping helper
        """
        return {
            "artist": "",
            "album": "",
            "genre": "",
            "cover_image": tag_metadata.cover_image,
            "lyrics": tag_metadata.lyrics,
            "subtitles": None,
        }

    def _get_video_subtitles(self, video_path: str) -> Optional[list[dict[str, str]]]:
        """Load the first acceptable local subtitle sidecar.

        Edge cases: oversized/growing files; invalid or ambiguous encodings;
        links, reparse points, non-regular files, and path-swap races.
        """
        video = Path(video_path)
        for extension in SUBTITLE_EXTENSIONS:
            subtitle_path = video.with_suffix(extension)
            try:
                text = self._read_subtitle_sidecar(subtitle_path)
            except FileNotFoundError:
                continue
            except (OSError, SubtitleSidecarError) as error:
                logger.warning(
                    "Subtitle sidecar rejected for %s (%s): %s",
                    subtitle_path, type(error).__name__, error,
                )
                continue
            return [{"lang": "und", "text": text}]
        return None

    @staticmethod
    def _read_subtitle_sidecar(subtitle_path: Path) -> str:
        """Read a regular sidecar with bounded memory and strict decoding.

        Edge cases: path replacement; growth after the size check; malformed
        UTF-8/UTF-16 data or unsupported NUL-bearing payloads.
        """
        initial_stat = subtitle_path.lstat()
        MediaLoader._validate_subtitle_path(subtitle_path, initial_stat)
        descriptor = os.open(subtitle_path, MediaLoader._subtitle_open_flags())
        try:
            opened_stat = os.fstat(descriptor)
            current_stat = subtitle_path.lstat()
            MediaLoader._validate_subtitle_handle(subtitle_path, current_stat, opened_stat)
            with os.fdopen(descriptor, "rb", closefd=True) as stream:
                descriptor = -1
                payload = MediaLoader._read_bounded_subtitle_bytes(stream)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        return MediaLoader._decode_subtitle_bytes(payload)

    @staticmethod
    def _subtitle_open_flags() -> int:
        """Return local-file flags that avoid blocking and link traversal where supported."""
        flags = os.O_RDONLY
        for name in ("O_BINARY", "O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK"):
            flags |= int(getattr(os, name, 0))
        return flags

    @staticmethod
    def _validate_subtitle_path(subtitle_path: Path, metadata: os.stat_result) -> None:
        """Reject links, reparse points, and non-regular subtitle candidates."""
        reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
        file_attributes = int(getattr(metadata, "st_file_attributes", 0))
        if stat.S_ISLNK(metadata.st_mode) or file_attributes & reparse_flag:
            raise SubtitleFileTypeError(f"subtitle path is a link or reparse point: {subtitle_path}")
        if not stat.S_ISREG(metadata.st_mode):
            raise SubtitleFileTypeError(f"subtitle path is not a regular file: {subtitle_path}")

    @staticmethod
    def _validate_subtitle_handle(
        subtitle_path: Path, path_stat: os.stat_result, handle_stat: os.stat_result
    ) -> None:
        """Verify stable file identity and the initial size budget after opening."""
        MediaLoader._validate_subtitle_path(subtitle_path, path_stat)
        if not stat.S_ISREG(handle_stat.st_mode):
            raise SubtitleFileTypeError(f"opened subtitle is not a regular file: {subtitle_path}")
        if not os.path.samestat(path_stat, handle_stat):
            raise SubtitleFileTypeError(f"subtitle path changed while opening: {subtitle_path}")
        if handle_stat.st_size > SUBTITLE_MAX_BYTES:
            raise SubtitleSizeLimitError(f"subtitle exceeds byte budget: {subtitle_path}")

    @staticmethod
    def _read_bounded_subtitle_bytes(stream: BinaryIO) -> bytes:
        """Read at most the configured byte budget plus one detection byte."""
        payload = bytearray()
        while True:
            remaining = SUBTITLE_MAX_BYTES + 1 - len(payload)
            if remaining <= 0:
                raise SubtitleSizeLimitError("subtitle exceeds byte budget")
            chunk = stream.read(min(SUBTITLE_READ_CHUNK_BYTES, remaining))
            if not chunk:
                return bytes(payload)
            payload.extend(chunk)
            if len(payload) > SUBTITLE_MAX_BYTES:
                raise SubtitleSizeLimitError("subtitle exceeds byte budget")

    @staticmethod
    def _decode_subtitle_bytes(payload: bytes) -> str:
        """Decode UTF-8 or BOM-marked UTF-16 without lossy error handling."""
        if payload.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
            raise SubtitleEncodingError("UTF-32 subtitle sidecars are not supported")
        if payload.startswith(codecs.BOM_UTF8):
            encoding = "utf-8-sig"
        elif payload.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
            encoding = "utf-16"
        else:
            encoding = "utf-8"
        try:
            text = payload.decode(encoding, errors="strict")
        except UnicodeDecodeError as error:
            raise SubtitleEncodingError(
                f"subtitle is not valid {encoding} at byte {error.start}"
            ) from error
        if "\x00" in text:
            raise SubtitleEncodingError("subtitle contains NUL characters")
        return text.replace("\r\n", "\n").replace("\r", "\n")

    def _read_opencv_video_duration(self, file_path: str) -> float:
        """Read a finite positive OpenCV duration or return the fallback sentinel.

        Edge cases handled:
        - OpenCV can be absent or fail while opening a corrupt container
        - zero/negative/non-finite FPS or frame counts are rejected
        - capture release errors are logged without hiding the original probe result
        """
        if not OPENCV_AVAILABLE or cv2 is None:
            return 0.0
        capture = None
        try:
            capture = cv2.VideoCapture(file_path)
            if not capture.isOpened():
                return 0.0
            frame_count = float(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
            fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
            if not math.isfinite(frame_count) or not math.isfinite(fps):
                return 0.0
            if frame_count <= 0.0 or fps <= 0.0:
                return 0.0
            duration = frame_count / fps
            return duration if math.isfinite(duration) and duration > 0.0 else 0.0
        except MEDIA_METADATA_EXCEPTIONS as error:
            logger.debug("OpenCV video duration failed for %s: %s", file_path, error)
            return 0.0
        finally:
            if capture is not None:
                try:
                    capture.release()
                except MEDIA_METADATA_EXCEPTIONS as error:
                    logger.debug("OpenCV capture release failed for %s: %s", file_path, error)

    def _probe_duration_or_zero(self, file_path: str, *, media_kind: str) -> float:
        """Convert typed ffprobe failures into the loader's documented zero fallback."""
        try:
            duration = ffprobe_service.probe_duration(file_path)
        except ffprobe_service.FfprobeError as error:
            logger.debug("ffprobe %s duration failed for %s: %s", media_kind, file_path, error)
            return 0.0
        logger.debug("Duration obtained with ffprobe (%s): %.2fs", media_kind, duration)
        return duration

    def _probe_audio_tracks_or_empty(
        self,
        file_path: str,
    ) -> list[ffprobe_service.AudioTrackMetadata]:
        """Convert typed ffprobe failures into a stable empty track inventory."""
        try:
            return ffprobe_service.probe_audio_tracks(file_path)
        except ffprobe_service.FfprobeError as error:
            logger.debug("ffprobe audio track probe failed for %s: %s", file_path, error)
            return []

    def _create_basic_media_file(
        self, file_path: str, media_type: MediaType
    ) -> MediaFile:
        """Crea un oggetto MediaFile di base."""
        path = Path(file_path)
        title = path.stem if file_path else self._get_localized_text("unknown")
        return MediaFile(
            path=file_path,
            title=title,
            duration=0.0,
            media_type=media_type,
            metadata={},
        )
