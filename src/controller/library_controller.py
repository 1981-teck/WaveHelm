from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from typing import Any, List, Optional


try:
    import cv2
except ImportError:
    cv2 = None  # type: ignore[assignment]

from src.audio.audio_events import AudioEventBus, AudioEventType
from src.model.database_manager import DatabaseManager
from src.model.media_file import MediaFile, MediaType
from src.utils.helpers import get_app_data_path, is_audio_file, is_video_file
from src.utils.media_metadata import (
    AudioMetadataDependencyUnavailableError,
    AudioMetadataError,
    read_audio_basic_metadata,
)

logger = logging.getLogger(__name__)


PATH_EXCEPTIONS = (AttributeError, OSError, TypeError, ValueError)
FILE_IO_EXCEPTIONS = (OSError, TypeError, ValueError)
JSON_LOAD_EXCEPTIONS = (OSError, TypeError, ValueError, json.JSONDecodeError)
DATABASE_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)
EVENT_BUS_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)
AUDIO_METADATA_EXCEPTIONS = (
    AudioMetadataDependencyUnavailableError,
    AudioMetadataError,
    FileNotFoundError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)
VIDEO_METADATA_EXCEPTIONS = (AttributeError, OSError, RuntimeError, TypeError, ValueError)
FFPROBE_EXCEPTIONS = (
    AttributeError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
    subprocess.SubprocessError,
    json.JSONDecodeError,
)
FFPROBE_AVAILABLE = shutil.which("ffprobe") is not None


_LIBRARY_AUDIO_CODEC_COMPATIBILITY_ORDER = {
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


def _canon_path_win(path_value: str) -> str:
    try:
        return os.path.normcase(os.path.abspath(os.path.normpath(path_value)))
    except PATH_EXCEPTIONS:
        return path_value




def _probe_video_duration_ffprobe(path: str) -> float:
    """Probe a video duration with ffprobe without leaking subprocess failures.

    Edge cases handled:
    - missing ffprobe must return 0.0 deterministically
    - malformed or blank ffprobe payloads must not break library indexing
    - negative or non-numeric durations are clamped back to 0.0
    """
    if not FFPROBE_AVAILABLE:
        return 0.0

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                path,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            timeout=8.0,
            check=False,
        )
        if result.returncode != 0 or not result.stdout:
            return 0.0

        payload = json.loads(result.stdout)
        duration_raw = (payload.get("format") or {}).get("duration")
        if duration_raw in (None, ""):
            return 0.0
        return max(0.0, float(duration_raw))
    except FFPROBE_EXCEPTIONS as error:
        logger.debug("[Library] ffprobe video duration failed for '%s': %s", path, error, exc_info=True)
        return 0.0


def _read_library_video_duration(path: str) -> float:
    """Return a best-effort video duration for library rows.

    Edge cases handled:
    - OpenCV may be unavailable and must fall back to ffprobe without hard-failing
    - corrupted containers or zero-fps streams must collapse to 0.0
    - ffprobe failures must not block library restore/import flows
    """
    duration = 0.0

    if cv2 is not None:
        capture = None
        try:
            capture = cv2.VideoCapture(path)
            if capture and capture.isOpened():
                fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
                frame_count = float(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
                if fps > 0.0 and frame_count > 0.0:
                    duration = max(0.0, frame_count / fps)
        except VIDEO_METADATA_EXCEPTIONS as error:
            logger.debug("[Library] OpenCV video duration failed for '%s': %s", path, error, exc_info=True)
        finally:
            try:
                if capture is not None:
                    capture.release()
            except VIDEO_METADATA_EXCEPTIONS:
                logger.debug("[Library] VideoCapture release failed for '%s'", path, exc_info=True)

    if duration <= 0.0:
        duration = _probe_video_duration_ffprobe(path)
    return duration

def _probe_library_video_audio_tracks(path: str) -> list[dict[str, Any]]:
    """Return normalized ffprobe audio tracks for one video library row.

    Edge cases handled:
    - ffprobe can be unavailable and must degrade to an empty list without blocking indexing
    - malformed or blank ffprobe payloads must not leak exceptions into library restore/import flows
    - mixed-container probes can include unsupported/non-audio streams that must be ignored deterministically
    """
    if not FFPROBE_AVAILABLE:
        return []

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "stream=index,codec_type,codec_name,codec_long_name,channels,channel_layout:stream_tags=language,title:stream_disposition=default,forced",
                "-of",
                "json",
                path,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            timeout=8.0,
            check=False,
        )
        if result.returncode != 0 or not result.stdout:
            return []
        payload = json.loads(result.stdout)
    except FFPROBE_EXCEPTIONS as error:
        logger.debug("[Library] ffprobe audio tracks failed for '%s': %s", path, error, exc_info=True)
        return []

    return _parse_library_video_audio_tracks(payload)



def _parse_library_video_audio_tracks(payload: object) -> list[dict[str, Any]]:
    """Normalize ffprobe audio stream payloads for library video metadata.

    Edge cases handled:
    - payloads can miss the streams array entirely and must collapse to an empty list
    - duplicate or invalid stream indices must be dropped before reaching the playback layer
    - missing language/title/disposition fields must still produce stable bounded dictionaries
    """
    streams = payload.get("streams") if isinstance(payload, dict) else None
    if not isinstance(streams, list):
        return []

    tracks: list[dict[str, Any]] = []
    for stream in streams:
        if not isinstance(stream, dict) or stream.get("codec_type") != "audio":
            continue
        entry = _build_library_video_audio_track(stream, len(tracks))
        if entry is not None:
            tracks.append(entry)

    tracks.sort(key=lambda item: (item["stream_index"], item["track_index"]))
    return tracks



def _build_library_video_audio_track(stream: dict[str, Any], ordinal: int) -> dict[str, Any] | None:
    """Build one stable audio-track record for persisted video metadata.

    Edge cases handled:
    - stream indexes can be missing or malformed and must be rejected cleanly
    - absent channel metadata must degrade to bounded primitives instead of raw ffprobe values
    - empty language/title tags must remain visible as empty strings so UI fallback labeling stays deterministic
    """
    try:
        stream_index = int(stream.get("index"))
    except (TypeError, ValueError):
        return None
    if stream_index < 0:
        return None

    tags = stream.get("tags") if isinstance(stream.get("tags"), dict) else {}
    disposition = stream.get("disposition") if isinstance(stream.get("disposition"), dict) else {}
    codec_name = str(stream.get("codec_name") or "").strip().lower()
    language = str(tags.get("language") or "").strip().lower()
    title = str(tags.get("title") or "").strip()
    channel_layout = str(stream.get("channel_layout") or "").strip()
    try:
        channels = max(0, int(stream.get("channels") or 0))
    except (TypeError, ValueError):
        channels = 0

    return {
        "stream_index": stream_index,
        "track_index": ordinal,
        "language": language,
        "title": title,
        "codec_name": codec_name,
        "codec_long_name": str(stream.get("codec_long_name") or "").strip(),
        "channels": channels,
        "channel_layout": channel_layout,
        "is_default": bool(int(disposition.get("default") or 0)),
        "is_forced": bool(int(disposition.get("forced") or 0)),
        "label": _format_library_video_audio_label(ordinal, language, title, codec_name, channel_layout, channels),
    }



def _format_library_video_audio_label(
    ordinal: int,
    language: str,
    title: str,
    codec_name: str,
    channel_layout: str,
    channels: int,
) -> str:
    """Return a stable audio label for video metadata cached in the library.

    Edge cases handled:
    - missing language/title tags must still yield a non-empty label for selector UIs
    - absent channel layouts must degrade to a bounded channel-count suffix when available
    - unknown codecs must remain visible for diagnostics and manual track selection
    """
    parts = [f"Track {ordinal + 1}"]
    if language:
        parts.append(language)
    if title:
        parts.append(title)
    if codec_name:
        parts.append(codec_name.upper())
    if channel_layout:
        parts.append(channel_layout)
    elif channels > 0:
        parts.append("1 ch" if channels == 1 else f"{channels} ch")
    return " — ".join(parts)



def _build_library_video_audio_candidates(audio_tracks: list[dict[str, Any]]) -> list[int]:
    """Return a deterministic compatibility-first audio candidate order for videos.

    Edge cases handled:
    - empty inventories must return an empty candidate list without leaking stale state
    - duplicate or negative stream indices must be discarded before ranking
    - default flags must remain tie-breakers instead of outranking clearly unsupported codecs
    """
    ordered: list[tuple[tuple[int, int, int, int], int]] = []
    seen: set[int] = set()
    for track in audio_tracks:
        try:
            stream_index = int(track.get("stream_index"))
        except (AttributeError, TypeError, ValueError):
            continue
        if stream_index < 0 or stream_index in seen:
            continue
        seen.add(stream_index)
        ordered.append((_library_video_audio_sort_key(track), stream_index))
    ordered.sort(key=lambda item: item[0])
    return [stream_index for _, stream_index in ordered]



def _library_video_audio_sort_key(track: dict[str, Any]) -> tuple[int, int, int, int]:
    """Rank audio tracks so broadly compatible codecs win before default-only preferences.

    Edge cases handled:
    - unknown codec families must remain sortable with a stable low-priority bucket
    - default/forced flags still break ties among equally compatible codecs
    - stream index remains the deterministic final tie-breaker across rebuilds/restores
    """
    codec_name = str(track.get("codec_name") or "").strip().lower()
    compatibility_rank = _LIBRARY_AUDIO_CODEC_COMPATIBILITY_ORDER.get(codec_name, 10)
    default_rank = 0 if bool(track.get("is_default")) else 1
    forced_rank = 0 if bool(track.get("is_forced")) else 1
    try:
        stream_index = int(track.get("stream_index"))
    except (AttributeError, TypeError, ValueError):
        stream_index = 1_000_000
    return (compatibility_rank, default_rank, forced_rank, stream_index)



def _read_library_video_metadata(path: str) -> dict[str, Any]:
    """Return persisted video metadata needed for audio-track fallback and selector UIs.

    Edge cases handled:
    - ffprobe can be unavailable or fail and must leave video rows indexable with empty metadata
    - files without multiple audio tracks must still serialize a stable empty/one-track structure
    - metadata snapshots must stay JSON-serializable because they are persisted in the library database
    """
    audio_tracks = _probe_library_video_audio_tracks(path)
    return {
        "audio_tracks": audio_tracks,
        "audio_track_candidates": _build_library_video_audio_candidates(audio_tracks),
    }



def _summarize_library_payload_for_debug(content: str, payload: object) -> str:
    """Return a privacy-preserving summary for library restore debug logs.

    Edge cases handled:
    - malformed or non-list payloads must not be logged as raw content
    - non-string list entries must be summarized without assuming path semantics
    - very large libraries must produce bounded debug output
    """
    char_count = len(content)
    if not isinstance(payload, list):
        return f"chars={char_count} payload_type={type(payload).__name__}"

    preview: list[str] = []
    for entry in payload[:5]:
        if isinstance(entry, str) and entry:
            preview.append(os.path.basename(entry))
        else:
            preview.append(f"<{type(entry).__name__}>")

    suffix = " ..." if len(payload) > 5 else ""
    return (
        f"chars={char_count} entries={len(payload)} "
        f"preview={preview}{suffix}"
    )


def _read_library_audio_metadata(path: str, fallback_title: str) -> tuple[str, float, dict[str, str]]:
    """Return normalized audio metadata for library indexing without hard-failing imports.

    Edge cases handled:
    - missing, corrupted, or unsupported tags fall back to filename and empty metadata
    - blank text tags are ignored so search/index state stays deterministic
    - invalid or absent duration values stay clamped to 0.0 through the shared adapter
    """
    title = fallback_title
    duration = 0.0
    metadata: dict[str, str] = {}

    try:
        tag_data = read_audio_basic_metadata(path)
    except AUDIO_METADATA_EXCEPTIONS as error:
        logger.warning("[Library] Audio metadata error for '%s': %s", path, error)
        return title, duration, metadata

    if tag_data.title:
        title = tag_data.title
    if tag_data.artist:
        metadata["artist"] = tag_data.artist
    if tag_data.album:
        metadata["album"] = tag_data.album
    if tag_data.duration > 0.0:
        duration = float(tag_data.duration)
    return title, duration, metadata


class LibraryController:
    """Controller della Libreria."""

    def __init__(self, event_bus: AudioEventBus, database_manager: DatabaseManager):
        self.event_bus = event_bus
        self.database_manager = database_manager
        self._media: List[MediaFile] = []
        self._paths_index: set[str] = set()

        self._app_dir = get_app_data_path()
        self._library_path = self._app_dir / "library.json"

        self.load_persistent_library()

    def _safe_publish(self, event_type: AudioEventType, payload: dict) -> None:
        try:
            self.event_bus.publish(event_type, payload)
        except EVENT_BUS_EXCEPTIONS as error:
            logger.error("[Library] Failed to publish %s: %s", event_type, error)

    def _save(self):
        logger.info("[Library] Attempting to save library.")
        try:
            paths = [media.path for media in self._media if media.path]
            logger.info("[Library] Saving %s paths to %s", len(paths), self._library_path)
            with open(self._library_path, "w", encoding="utf-8") as file_obj:
                json.dump(paths, file_obj, ensure_ascii=False, indent=2)
            logger.info("[Library] successfully saved library to %s", self._library_path)
        except FILE_IO_EXCEPTIONS as error:
            logger.warning("[Library] save failed: %s", error)

    def _sync_media_to_database(self, media: MediaFile) -> None:
        if not media or not media.path:
            return

        try:
            self.database_manager.add_library_item(
                path=media.path,
                title=media.title or os.path.basename(media.path),
                media_type=getattr(media.media_type, "name", str(media.media_type)),
                duration=float(media.duration or 0.0),
                metadata=dict(media.metadata or {}),
            )
        except DATABASE_EXCEPTIONS:
            logger.debug("[Library] Database sync skipped/failed for '%s'", media.path, exc_info=True)

    def _remove_media_from_database(self, path: str) -> None:
        if not path:
            return

        try:
            self.database_manager.remove_library_item(path)
        except DATABASE_EXCEPTIONS:
            logger.debug("[Library] Database delete skipped/failed for '%s'", path, exc_info=True)

    def load_persistent_library(self):
        logger.info("[Library] Loading persistent library from: %s", self._library_path)
        if not self._library_path.is_file():
            logger.info("[Library] library.json does not exist. Starting with an empty library.")
            return

        try:
            with open(self._library_path, "r", encoding="utf-8") as file_obj:
                content = file_obj.read()
                if not content.strip():
                    logger.warning("[Library] library.json is empty. Starting with an empty library.")
                    return
                paths = json.loads(content)
                logger.debug(
                    "[Library] library.json summary: %s",
                    _summarize_library_payload_for_debug(content, paths),
                )
        except json.JSONDecodeError as error:
            logger.error("[Library] Failed to decode library.json: %s. The file might be corrupted.", error, exc_info=True)
            return
        except JSON_LOAD_EXCEPTIONS as error:
            logger.error("[Library] Error while loading the library: %s", error, exc_info=True)
            return

        if isinstance(paths, list):
            logger.info("[Library] Found %s paths in library.json. Processing...", len(paths))
            self._add_paths_internal(paths, emit_feedback=False)
            logger.info("[Library] Restored %s items from %s", len(self._media), self._library_path)
        else:
            logger.error("[Library] Expected a list in library.json, but found %s. Library not loaded.", type(paths).__name__)

    def add_media_files_from_objects(
        self,
        media_files: List[MediaFile],
        emit_event: bool = True,
        emit_feedback: bool = True,
    ):
        if not media_files:
            return

        added: List[MediaFile] = []
        for media in media_files:
            if _canon_path_win(media.path) in self._paths_index:
                continue
            self._media.append(media)
            self._paths_index.add(_canon_path_win(media.path))
            self._sync_media_to_database(media)
            added.append(media)

        if added:
            self._save()
            if emit_event:
                self._safe_publish(AudioEventType.LIBRARY_UPDATED, {"count": len(added)})

        if emit_feedback:
            if added:
                self._safe_publish(
                    AudioEventType.FEEDBACK_MESSAGE,
                    {"message": f"{len(added)} file aggiunti alla libreria.", "color": "green"},
                )
            else:
                self._safe_publish(
                    AudioEventType.FEEDBACK_MESSAGE,
                    {"message": "Nessun file riproducibile trovato.", "color": "orange"},
                )

    def add_media(self, path: str, emit_event: bool = True, emit_feedback: bool = True):
        if not path:
            return
        media_files = self._get_media_files_from_paths([path])
        self.add_media_files_from_objects(media_files, emit_event, emit_feedback)

    def add_media_files(self, paths: List[str], emit_event: bool = True, emit_feedback: bool = True):
        if not paths:
            return
        media_files = self._get_media_files_from_paths(paths)
        self.add_media_files_from_objects(media_files, emit_event, emit_feedback)

    def _get_media_files_from_paths(self, paths: List[str]) -> List[MediaFile]:
        to_add: List[str] = []
        for candidate in paths:
            if not candidate:
                continue
            if os.path.isdir(candidate):
                to_add.extend(self._expand_folder(candidate))
            elif os.path.isfile(candidate) and (is_audio_file(candidate) or is_video_file(candidate)):
                to_add.append(candidate)

        media_files: List[MediaFile] = []
        for file_path in to_add:
            media = self._create_media_from_path(file_path)
            if media:
                media_files.append(media)
        return media_files

    def remove_media(self, path: str, emit_event: bool = True, emit_feedback: bool = True) -> bool:
        if not path:
            return False

        before = len(self._media)
        self._media = [media for media in self._media if media.path != path]
        self._paths_index.discard(_canon_path_win(path))
        after = len(self._media)
        if after == before:
            return False

        self._save()
        self._remove_media_from_database(path)
        if emit_event:
            self._safe_publish(AudioEventType.LIBRARY_UPDATED, {"count": after})
        if emit_feedback:
            self._safe_publish(
                AudioEventType.FEEDBACK_MESSAGE,
                {"message": f"Rimosso dalla libreria: {os.path.basename(path)}", "color": "green"},
            )
        return True

    def remove_media_by_path(self, path: str, emit_event: bool = True, emit_feedback: bool = True) -> bool:
        return self.remove_media(path=path, emit_event=emit_event, emit_feedback=emit_feedback)

    def add_to_favorites(
        self,
        media_file: MediaFile,
        *,
        emit_feedback: bool = True,
        emit_event: bool = True,
    ) -> bool:
        if not media_file or not media_file.path:
            return False

        try:
            if self.database_manager.is_favorite(media_file.path):
                if emit_feedback:
                    self._safe_publish(
                        AudioEventType.FEEDBACK_MESSAGE,
                        {"message": f"Gia' nei preferiti: {media_file.title}", "color": "orange"},
                    )
                return False
        except DATABASE_EXCEPTIONS:
            logger.debug("Favorite existence check failed", exc_info=True)

        try:
            self.database_manager.add_favorite(
                path=media_file.path,
                title=media_file.title,
                media_type=media_file.media_type.name,
                duration=media_file.duration,
                metadata=media_file.metadata,
            )
            if emit_event:
                self._safe_publish(AudioEventType.FAVORITE_CHANGED, {"path": media_file.path, "is_favorite": True})
            if emit_feedback:
                self._safe_publish(
                    AudioEventType.FEEDBACK_MESSAGE,
                    {"message": f"Aggiunto ai preferiti: {media_file.title}", "color": "green"},
                )
            return True
        except DATABASE_EXCEPTIONS as error:
            logger.error("Error adding favorite: %s", error)
            if emit_feedback:
                self._safe_publish(
                    AudioEventType.FEEDBACK_MESSAGE,
                    {"message": f"Errore nell'aggiungere il preferito: {media_file.title}", "color": "red"},
                )
            return False

    def get_all_media(self) -> List[MediaFile]:
        return list(self._media)

    def save_library(self):
        self._save()

    def get_media_by_path(self, path: str) -> Optional[MediaFile]:
        wanted = _canon_path_win(path)
        for media in self._media:
            if media.path == path or _canon_path_win(media.path) == wanted:
                return media
        return None

    def search_media(self, query: str) -> List[MediaFile]:
        normalized_query = (query or "").strip().lower()
        if not normalized_query:
            return self.get_all_media()

        result: List[MediaFile] = []
        for media in self._media:
            title = (media.title or "").lower()
            artist = ((media.metadata.get("artist", "") or "").lower() if hasattr(media, "metadata") else "")
            album = ((media.metadata.get("album", "") or "").lower() if hasattr(media, "metadata") else "")
            path_value = media.path.lower() if media.path else ""
            if normalized_query in title or normalized_query in artist or normalized_query in album or normalized_query in path_value:
                result.append(media)
        return result

    def _expand_folder(self, folder: str) -> List[str]:
        media_paths: List[str] = []
        for root, _, files in os.walk(folder):
            for filename in files:
                file_path = os.path.join(root, filename)
                if is_audio_file(file_path) or is_video_file(file_path):
                    media_paths.append(file_path)
        return media_paths

    def _create_media_from_path(self, path: str) -> Optional[MediaFile]:
        if is_audio_file(path):
            media_type = MediaType.AUDIO
        elif is_video_file(path):
            media_type = MediaType.VIDEO
        else:
            return None

        title = os.path.basename(path)
        duration = 0.0
        metadata: dict[str, str] = {}

        if media_type is MediaType.AUDIO:
            title, duration, metadata = _read_library_audio_metadata(path, title)
        else:
            duration = _read_library_video_duration(path)
            metadata = _read_library_video_metadata(path)

        return MediaFile(path=path, title=title, media_type=media_type, duration=duration, metadata=metadata)

    def _add_paths_internal(self, paths: List[str], emit_feedback: bool) -> List[MediaFile]:
        to_add: List[str] = []
        for candidate in paths:
            if not candidate:
                continue
            if os.path.isdir(candidate):
                to_add.extend(self._expand_folder(candidate))
            elif os.path.isfile(candidate) and (is_audio_file(candidate) or is_video_file(candidate)):
                to_add.append(candidate)

        added: List[MediaFile] = []
        for file_path in to_add:
            if _canon_path_win(file_path) in self._paths_index:
                continue
            media = self._create_media_from_path(file_path)
            if media:
                self._media.append(media)
                self._paths_index.add(_canon_path_win(file_path))
                self._sync_media_to_database(media)
                added.append(media)

        if emit_feedback:
            if added:
                self._safe_publish(
                    AudioEventType.FEEDBACK_MESSAGE,
                    {"message": f"{len(added)} file aggiunti alla libreria.", "color": "green"},
                )
            else:
                self._safe_publish(
                    AudioEventType.FEEDBACK_MESSAGE,
                    {"message": "Nessun file riproducibile trovato.", "color": "orange"},
                )
        return added
