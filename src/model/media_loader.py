from __future__ import annotations
import os
import logging
import warnings
from pathlib import Path
from typing import Optional, Dict, Any, TYPE_CHECKING, List, Tuple
import time
import subprocess
import json
import shutil

from src.model.media_file import MediaFile, MediaType
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

FFPROBE_AVAILABLE = shutil.which("ffprobe") is not None

if TYPE_CHECKING:
    from src.model.localization_manager import LocalizationManager

logger = logging.getLogger(__name__)

MEDIA_METADATA_EXCEPTIONS = (AttributeError, OSError, RuntimeError, TypeError, ValueError)
AUDIO_METADATA_EXCEPTIONS = (AudioMetadataDependencyUnavailableError, AudioMetadataError) + MEDIA_METADATA_EXCEPTIONS

SUBTITLE_IO_EXCEPTIONS = (OSError, UnicodeError)
FFPROBE_EXCEPTIONS = (AttributeError, OSError, RuntimeError, TypeError, ValueError, subprocess.SubprocessError, json.JSONDecodeError)

_AUDIO_CODEC_COMPATIBILITY_ORDER = {
    "aac": 0,
    "mp3": 1,
    "ac3": 2,
    "eac3": 3,
    "pcm_s16le": 4,
    "pcm_s24le": 5,
    "flac": 6,
    "vorbis": 7,
    "opus": 8,
    "truehd": 20,
    "dts": 21,
    "dtshd": 22,
}


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
        """Carica i metadati di un file video usando OpenCV e ffprobe come fallback.

        Edge cases handled:
        - OpenCV may be unavailable and the loader must still try ffprobe deterministically
        - container metadata may expose zero or invalid FPS values and must not divide by zero
        - sidecar subtitles may exist even when duration probing fails
        """
        start_time = time.time()
        title = Path(file_path).stem
        duration = 0.0

        if OPENCV_AVAILABLE:
            cap = None
            try:
                cap = cv2.VideoCapture(file_path)
                if cap.isOpened():
                    frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
                    fps = cap.get(cv2.CAP_PROP_FPS)
                    if fps > 0:
                        duration = frame_count / fps
                        logger.debug(f"Durata ottenuta con OpenCV: {duration:.2f}s")
            except MEDIA_METADATA_EXCEPTIONS as e:
                logger.error(
                    self._get_localized_text("error_reading_video_metadata").format(
                        file_path=file_path, error=e
                    )
                )
            finally:
                if cap:
                    cap.release()

        if duration == 0.0 and FFPROBE_AVAILABLE:
            try:
                probed = self._probe_duration_ffprobe(file_path)
                if probed > 0.0:
                    duration = probed
                    logger.debug(
                        f"Durata ottenuta con ffprobe (video): {duration:.2f}s"
                    )
            except FFPROBE_EXCEPTIONS as e:
                logger.debug(f"ffprobe ha fallito per {file_path} ({e})")

        audio_tracks = self._probe_audio_tracks_ffprobe(file_path)
        metadata = {
            "artist": "",
            "album": "",
            "genre": "",
            "cover_image": None,
            "lyrics": None,
            "subtitles": self._get_video_subtitles(file_path),
            "audio_tracks": audio_tracks,
            "audio_track_candidates": self._build_audio_track_candidates(audio_tracks),
        }

        logger.debug(
            self._get_localized_text("video_metadata_loaded").format(
                time_taken=time.time() - start_time, file_path=file_path
            )
        )
        return MediaFile(
            path=file_path,
            title=title,
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
        """Resolve a stable audio duration using ffprobe only when necessary.

        Edge cases handled:
        - existing positive durations must be preserved without extra probing work
        - ffprobe may be unavailable or fail and must return 0.0 deterministically
        - malformed ffprobe output must not leak exceptions into the runtime loader
        """
        if duration > 0.0 or not FFPROBE_AVAILABLE:
            return duration
        try:
            probed = self._probe_duration_ffprobe(file_path)
        except FFPROBE_EXCEPTIONS as e:
            logger.debug(f"ffprobe ha fallito per {file_path} ({e})")
            return 0.0
        if probed > 0.0:
            logger.debug(f"Durata ottenuta con ffprobe (audio): {probed:.2f}s")
            return probed
        return 0.0

    def _build_audio_metadata(self, tag_metadata: AudioTagMetadata) -> Dict[str, Any]:
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

    def _get_video_subtitles(self, video_path: str) -> Optional[List[Dict[str, Any]]]:
        """Cerca file di sottotitoli associati a un video."""
        base_name = os.path.splitext(video_path)[0]
        for ext in [".srt", ".vtt", ".ass"]:
            subtitle_path = base_name + ext
            if os.path.exists(subtitle_path):
                try:
                    with open(subtitle_path, "r", encoding="utf-8", errors="ignore") as f:
                        # Per semplicità, restituiamo il contenuto come una singola stringa.
                        # In un'applicazione reale, si parsa il file per ottenere timestamp e testo.
                        return [
                            {"lang": "und", "text": f.read()}
                        ]  # 'und' per lingua non definita
                except SUBTITLE_IO_EXCEPTIONS as exc:
                    logger.warning("Impossibile leggere i sottotitoli %s: %s", subtitle_path, exc)
        return None

    def _probe_duration_ffprobe(self, file_path: str) -> float:
        """Tenta di stimare la durata con ffprobe (se disponibile).

        Usa il binario ffprobe se presente nel PATH di sistema.
        Ritorna 0.0 in caso di errore o se ffprobe non è disponibile.
        """
        if not FFPROBE_AVAILABLE:
            return 0.0
        try:
            # ffprobe -v error -show_entries format=duration -of json "file"
            cmd = [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                file_path,
            ]
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                return 0.0
            data = json.loads(result.stdout or "{}")
            dur_str = None
            fmt = data.get("format") if isinstance(data, dict) else None
            if isinstance(fmt, dict):
                dur_str = fmt.get("duration")
            if not dur_str:
                return 0.0
            try:
                dur = float(dur_str)
            except (TypeError, ValueError):
                return 0.0
            if dur <= 0.0:
                return 0.0
            return dur
        except FFPROBE_EXCEPTIONS as e:
            logging.getLogger(__name__).debug(
                "ffprobe duration probe failed for %s: %s", file_path, e, exc_info=True
            )
            return 0.0

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


    def _probe_audio_tracks_ffprobe(self, file_path: str) -> List[Dict[str, Any]]:
        """Probe audio streams with ffprobe for deterministic track fallback planning.

        Edge cases handled:
        - ffprobe may be unavailable or fail and must degrade to an empty list
        - malformed stream payloads must be ignored without aborting the whole probe
        - duplicate or unsorted stream indexes must return a stable sorted candidate list
        """
        if not FFPROBE_AVAILABLE:
            return []
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=index,codec_type,codec_name,codec_long_name,channels,channel_layout:stream_tags=language,title:stream_disposition=default,forced",
            "-of",
            "json",
            file_path,
        ]
        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                return []
            data = json.loads(result.stdout or "{}")
            return self._parse_ffprobe_audio_tracks(data)
        except FFPROBE_EXCEPTIONS as exc:
            logger.debug("ffprobe audio track probe failed for %s: %s", file_path, exc)
            return []

    def _parse_ffprobe_audio_tracks(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Normalize ffprobe audio stream payloads into UI/backend-ready track records.

        Edge cases handled:
        - missing streams arrays must return an empty list without exceptions
        - non-audio streams in mixed containers must be skipped deterministically
        - invalid tags or dispositions must degrade to stable default values
        """
        streams = data.get("streams") if isinstance(data, dict) else None
        if not isinstance(streams, list):
            return []
        audio_tracks: List[Dict[str, Any]] = []
        for stream in streams:
            if not isinstance(stream, dict) or stream.get("codec_type") != "audio":
                continue
            entry = self._build_audio_track_entry(stream, len(audio_tracks))
            if entry is not None:
                audio_tracks.append(entry)
        audio_tracks.sort(key=lambda item: (item["stream_index"], item["track_index"]))
        return audio_tracks

    def _build_audio_track_entry(
        self,
        stream: Dict[str, Any],
        ordinal: int,
    ) -> Optional[Dict[str, Any]]:
        """Build one stable audio track entry from a raw ffprobe stream record.

        Edge cases handled:
        - stream indexes may be missing or invalid and must be rejected cleanly
        - language or title tags may be absent and must degrade to empty strings
        - channel metadata may be malformed and must normalize to bounded primitives
        """
        stream_index = self._safe_int(stream.get("index"), default=-1)
        if stream_index < 0:
            return None
        tags = stream.get("tags") if isinstance(stream.get("tags"), dict) else {}
        disposition = stream.get("disposition") if isinstance(stream.get("disposition"), dict) else {}
        codec_name = str(stream.get("codec_name") or "").strip().lower()
        channel_layout = str(stream.get("channel_layout") or "").strip()
        channels = self._safe_int(stream.get("channels"), default=0)
        language = str(tags.get("language") or "").strip().lower()
        title = str(tags.get("title") or "").strip()
        return {
            "stream_index": stream_index,
            "track_index": ordinal,
            "language": language,
            "title": title,
            "codec_name": codec_name,
            "codec_long_name": str(stream.get("codec_long_name") or "").strip(),
            "channels": max(0, channels),
            "channel_layout": channel_layout,
            "is_default": bool(self._safe_int(disposition.get("default"), default=0)),
            "is_forced": bool(self._safe_int(disposition.get("forced"), default=0)),
            "label": self._format_audio_track_label(
                ordinal=ordinal,
                language=language,
                title=title,
                codec_name=codec_name,
                channel_layout=channel_layout,
                channels=channels,
            ),
        }

    def _build_audio_track_candidates(self, audio_tracks: List[Dict[str, Any]]) -> List[int]:
        """Return a deterministic fallback order for audio stream selection.

        Edge cases handled:
        - empty track inventories must return an empty candidate list
        - duplicate stream indexes must be de-duplicated without reordering priority
        - unknown codec names must still participate with a stable low-priority score
        """
        ordered: List[Tuple[Tuple[int, int, int, int], int]] = []
        seen: set[int] = set()
        for track in audio_tracks:
            stream_index = self._safe_int(track.get("stream_index"), default=-1)
            if stream_index < 0 or stream_index in seen:
                continue
            seen.add(stream_index)
            ordered.append((self._audio_track_sort_key(track), stream_index))
        ordered.sort(key=lambda item: item[0])
        return [stream_index for _, stream_index in ordered]

    def _audio_track_sort_key(self, track: Dict[str, Any]) -> Tuple[int, int, int, int]:
        """Compute a stable priority key for audio fallback ordering.

        Edge cases handled:
        - default tracks must outrank non-default tracks deterministically
        - unknown codec families must sort after known compatibility-friendly codecs
        - stream indexes remain the final tie-breaker to avoid unstable ordering
        """
        codec_name = str(track.get("codec_name") or "").strip().lower()
        compatibility_rank = _AUDIO_CODEC_COMPATIBILITY_ORDER.get(codec_name, 10)
        default_rank = 0 if bool(track.get("is_default")) else 1
        forced_rank = 0 if bool(track.get("is_forced")) else 1
        stream_index = self._safe_int(track.get("stream_index"), default=1_000_000)
        return (default_rank, forced_rank, compatibility_rank, stream_index)

    def _format_audio_track_label(
        self,
        *,
        ordinal: int,
        language: str,
        title: str,
        codec_name: str,
        channel_layout: str,
        channels: int,
    ) -> str:
        """Format one stable human-readable label for the audio-track selector.

        Edge cases handled:
        - missing language and title tags must still produce a non-empty label
        - absent channel layout must degrade to a bounded channels fallback
        - unknown codec names must remain visible for diagnostics and manual override
        """
        pieces = [f"Track {ordinal + 1}"]
        if language:
            pieces.append(language)
        if title:
            pieces.append(title)
        if codec_name:
            pieces.append(codec_name.upper())
        if channel_layout:
            pieces.append(channel_layout)
        elif channels > 0:
            pieces.append(f"{channels} ch")
        return " — ".join(pieces)

    def _safe_int(self, value: Any, *, default: int) -> int:
        """Convert scalar ffprobe values to int without leaking parsing failures.

        Edge cases handled:
        - None or empty-string payloads must return the provided default
        - boolean-like values from JSON must normalize predictably to integers
        - malformed numeric strings must not raise into loader call-sites
        """
        try:
            if value in (None, ""):
                return default
            return int(value)
        except (TypeError, ValueError):
            return default
