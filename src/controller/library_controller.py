from __future__ import annotations

import json
import logging
import math
import os
from typing import List, Optional
try:
    import cv2
except ImportError:
    cv2 = None  # type: ignore[assignment]

from src.audio.audio_events import AudioEventBus, AudioEventType
from src.model.database_manager import DatabaseManager
from src.model.media_file import MediaFile, MediaType
from src.utils import ffprobe_service
from src.utils.durable_io import write_bytes_atomic_durable
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
def _canon_path_win(path_value: str) -> str:
    try:
        return os.path.normcase(os.path.abspath(os.path.normpath(path_value)))
    except PATH_EXCEPTIONS:
        return path_value

def _read_library_video_duration(path: str) -> float:
    """Return a finite video duration without blocking library indexing.
    Edge cases: unavailable/corrupt OpenCV; invalid rates/counts; typed ffprobe failures.
    """
    duration = 0.0
    if cv2 is not None:
        capture = None
        try:
            capture = cv2.VideoCapture(path)
            if capture and capture.isOpened():
                fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
                frame_count = float(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
                if math.isfinite(fps) and math.isfinite(frame_count):
                    if fps > 0.0 and frame_count > 0.0:
                        duration = max(0.0, frame_count / fps)
        except VIDEO_METADATA_EXCEPTIONS as error:
            logger.debug("[Library] OpenCV duration failed for '%s': %s", path, error)
        finally:
            if capture is not None:
                try:
                    capture.release()
                except VIDEO_METADATA_EXCEPTIONS as error:
                    logger.debug("[Library] VideoCapture release failed for '%s': %s", path, error)
    if duration > 0.0:
        return duration
    try:
        return ffprobe_service.probe_duration(path)
    except ffprobe_service.FfprobeError as error:
        logger.debug("[Library] ffprobe duration failed for '%s': %s", path, error)
        return 0.0

def _read_library_video_metadata(path: str) -> dict[str, object]:
    """Return JSON-safe video audio metadata through the bounded probe boundary.

    Edge cases: unavailable ffprobe; malformed streams; deterministic candidate ordering.
    """
    try:
        audio_tracks = ffprobe_service.probe_audio_tracks(path)
    except ffprobe_service.FfprobeError as error:
        logger.debug("[Library] ffprobe audio tracks failed for '%s': %s", path, error)
        audio_tracks = []
    return {
        "audio_tracks": audio_tracks,
        "audio_track_candidates": ffprobe_service.build_audio_track_candidates(audio_tracks),
    }
def _summarize_library_payload_for_debug(content: str, payload: object) -> str:
    """Return a privacy-preserving summary for library restore debug logs.

    Edge cases: non-list payloads; non-string entries; large libraries.
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
    """Return normalized audio metadata without hard-failing library imports.

    Edge cases: missing/corrupt tags; blank text tags; invalid durations.
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
            payload = json.dumps(paths, ensure_ascii=False, indent=2).encode("utf-8")
            if not write_bytes_atomic_durable(self._library_path, payload):
                logger.warning("[Library] saved but parent directory sync failed: %s", self._library_path)
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
        metadata: dict[str, object] = {}

        if media_type is MediaType.AUDIO:
            title, duration, audio_metadata = _read_library_audio_metadata(path, title)
            metadata = dict(audio_metadata)
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
