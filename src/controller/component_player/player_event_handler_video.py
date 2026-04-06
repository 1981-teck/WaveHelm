from __future__ import annotations

import copy
import logging
import os
from typing import Any, Optional

from src.audio.audio_events import AudioEventType

from .playback_state_manager import PlayerState

logger = logging.getLogger(__name__)

MATCH_EXCEPTIONS = (AttributeError, KeyError, RuntimeError, TypeError, ValueError)
EVENT_BUS_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)
ENGINE_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError, OSError)
VIDEO_CONTROLLER_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError, OSError)
COERCE_EXCEPTIONS = (TypeError, ValueError)


def _matches_current_track_path(current_track: Any, candidate_path: str) -> bool:
    if not current_track or not candidate_path:
        return False

    candidate_norm = str(candidate_path).strip().lower()
    if not candidate_norm:
        return False

    direct_path = str(getattr(current_track, "path", "") or "").strip().lower()
    if direct_path and direct_path == candidate_norm:
        return True

    try:
        metadata = getattr(current_track, "metadata", {}) or {}
        resolved_stream_url = str(metadata.get("_resolved_stream_url") or "").strip().lower()
    except MATCH_EXCEPTIONS:
        resolved_stream_url = ""

    if resolved_stream_url and resolved_stream_url == candidate_norm:
        return True

    return False


def _reset_video_start_signature(self, reason: str) -> None:
    """Azzera il dedup dello start video quando la sessione corrente termina."""
    if self._last_video_start_signature is None:
        return
    logger.debug(
        "[PlayerEventHandler] Reset video start signature (%s): %s",
        reason,
        self._last_video_start_signature,
    )
    self._last_video_start_signature = None


def _safe_publish_ui_event(self, event_type: AudioEventType, payload: dict[str, Any]) -> None:
    try:
        self.event_bus.publish(event_type, payload, require_ui_thread=True)
    except EVENT_BUS_EXCEPTIONS:
        logger.debug("[PlayerEventHandler] Failed publishing %s", event_type, exc_info=True)


def _current_track_path(self) -> str:
    """Return the current queue path with a deterministic empty-string fallback.

    Edge cases:
        1. queue_manager is partially initialized and has no current_track attribute.
        2. current_track.path is missing, None, or not safely coercible to string.
        3. Stale close/start events race while the queue context is being replaced.
    """
    try:
        current_track = getattr(self.queue_manager, "current_track", None)
        return str(getattr(current_track, "path", "") or "")
    except MATCH_EXCEPTIONS:
        return ""


def _extract_video_event_path(data: Any) -> str:
    """Return a normalized video-path payload from runtime events.

    Edge cases:
        1. Runtime events can arrive as None or non-dict payloads during teardown races.
        2. payload['path'] can be missing, empty, or a non-string object requiring coercion.
        3. Legacy publishers can still emit blank payloads and must keep stop handling backward compatible.
    """
    if not isinstance(data, dict):
        return ""

    try:
        return str(data.get("path") or "").strip()
    except MATCH_EXCEPTIONS:
        return ""



def _should_ignore_video_playback_stopped(self, data: Any) -> bool:
    """Ignore stale VIDEO_PLAYBACK_STOPPED events from a previous video session.

    Edge cases:
        1. A previous video session can publish STOPPED after the queue already moved to a different media item.
        2. The current track can be represented by a resolved stream URL, so path matching must reuse the normal track matcher.
        3. Legacy stop events can still omit the path and must keep the old reset behavior instead of being dropped blindly.
    """
    event_path = _extract_video_event_path(data)
    if not event_path:
        return False

    current_track = getattr(self.queue_manager, "current_track", None)
    if current_track and self._matches_current_track_path(current_track, event_path):
        return False

    signature = getattr(self, "_last_video_start_signature", None)
    signature_path = ""
    if isinstance(signature, tuple) and signature:
        try:
            signature_path = str(signature[0] or "").strip()
        except MATCH_EXCEPTIONS:
            signature_path = ""
    if signature_path and signature_path == event_path:
        return False

    if self._current_track_path() or signature_path:
        logger.debug(
            "[PlayerEventHandler] Ignored stale VIDEO_PLAYBACK_STOPPED current=%s signature=%s event=%s",
            self._current_track_path(),
            signature_path,
            event_path,
        )
        return True
    return False



def _should_ignore_video_start_after_window_close(self, path_str: str) -> bool:
    """Drop stale video-start callbacks emitted after a manual window close.

    Edge cases:
        1. A queued HWND-ready callback arrives after the user closes the external window.
        2. The user intentionally restarts the same video, which must still be allowed.
        3. The queue switches to a different media item while close cleanup is still running.
    """
    blocked_path = str(getattr(self, "_blocked_video_start_path", "") or "")
    if not blocked_path:
        return False

    if blocked_path != path_str:
        self._blocked_video_start_path = ""
        return False

    if self.state_manager.state == PlayerState.LOADING:
        logger.debug(
            "[PlayerEventHandler] Allowing explicit restart for previously closed video: %s",
            path_str,
        )
        self._blocked_video_start_path = ""
        return False

    logger.info(
        "[PlayerEventHandler] Ignored stale video-start callback after manual window close: %s",
        path_str,
    )
    return True


def _on_video_playback_stopped(self, data: Any = None):
    """Release video-start dedup only for the active video session.

    Edge cases:
        1. The previous adapter can emit STOPPED after a new video has already been queued or started.
        2. Legacy STOPPED events can omit the path payload and must preserve backward-compatible reset behavior.
        3. Audio handoffs can leave a non-video current track, so stale video stops must not clear the active signature.
    """
    if self._should_ignore_video_playback_stopped(data):
        return
    self._reset_video_start_signature("video_playback_stopped")


def _on_video_playback_error(self, data: Any = None):
    """Gestisce errori video runtime senza lasciare il player in stato non recuperabile."""
    if self._is_shutting_down or not isinstance(data, dict):
        return

    current_track = self.queue_manager.current_track
    current_path = str(getattr(current_track, "path", "") or "")
    event_path = str(data.get("path") or "")
    if event_path and current_track and not self._matches_current_track_path(current_track, event_path):
        logger.debug(
            "[PlayerEventHandler] Ignored stale VIDEO_PLAYBACK_ERROR current=%s event=%s",
            current_path,
            event_path,
        )
        return

    self._reset_video_start_signature("video_playback_error")

    filename = str(data.get("filename") or "")
    if not filename and event_path:
        filename = os.path.basename(event_path)

    message = str(data.get("user_message") or data.get("error") or "Errore video sconosciuto.")
    if filename and filename not in message:
        message = f"{filename}: {message}"

    logger.error(
        "[PlayerEventHandler] VIDEO_PLAYBACK_ERROR path=%s message=%s",
        event_path or current_path or "<unknown>",
        message,
    )

    try:
        self.engine_controller.stop()
    except ENGINE_EXCEPTIONS:
        logger.debug("[PlayerEventHandler] engine_controller.stop() failed after video error", exc_info=True)

    _safe_publish_ui_event(self, AudioEventType.CANCEL_VIDEO_PLAYBACK, {})
    _safe_publish_ui_event(self, AudioEventType.FEEDBACK_MESSAGE, {"message": message, "color": "red"})
    _safe_publish_ui_event(
        self,
        AudioEventType.PLAYER_ERROR,
        {"message": message, "path": event_path or current_path, "details": dict(data)},
    )

    recovery_payload = dict(data)
    recovery_payload.setdefault("message", message)
    recovery_payload["recoverable_error"] = True
    self.state_manager.update_state(PlayerState.STOPPED, recovery_payload)


def _on_video_playback_ready(self, data: Any = None):
    """Gestore per l'evento VIDEO_PLAYBACK_READY dal bus eventi."""
    if self._is_shutting_down:
        return

    if not data or not isinstance(data, dict):
        logger.debug(
            "[PlayerEventHandler] VIDEO_PLAYBACK_READY ignorato: payload invalido (%r)",
            data,
        )
        return

    try:
        hwnd = int(data.get("hwnd") or 0)
    except COERCE_EXCEPTIONS:
        hwnd = 0
    path = data.get("path")
    loop = self._coerce_bool(data.get("loop", False))

    self._start_video_playback(
        hwnd=hwnd,
        path=path,
        loop=loop,
        source="event_bus",
    )


def _on_video_playback_ready_signal(self, hwnd: int, path: str, loop: bool):
    """Gestore per il segnale UI video_playback_ready dalla MainView."""
    if self._is_shutting_down:
        return
    self._start_video_playback(
        hwnd=int(hwnd or 0),
        path=path,
        loop=self._coerce_bool(loop),
        source="ui_signal",
    )


def _extract_video_track_metadata(current_track: Any) -> dict[str, Any]:
    """Return a defensive metadata snapshot for the current video track.

    Edge cases:
        1. current_track can be None or expose a non-dict metadata attribute during teardown/startup races.
        2. Nested metadata can be mutated by other components, so a defensive copy avoids leaking shared state into the video controller.
        3. Copy failures on malformed custom metadata objects must degrade to an empty metadata payload instead of aborting playback.
    """
    if not current_track:
        return {}

    try:
        metadata = getattr(current_track, "metadata", None)
    except MATCH_EXCEPTIONS:
        return {}
    if not isinstance(metadata, dict):
        return {}

    try:
        return copy.deepcopy(metadata)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        try:
            return dict(metadata)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return {}


def _extract_audio_track_candidates_from_track(current_track: Any) -> tuple[int, ...]:
    """Return deterministic audio-track candidates from the current media metadata.

    Edge cases:
        1. current_track can be None or expose non-dict metadata while video startup is already in progress.
        2. Metadata can contain malformed, duplicate, or negative stream indices that must not leak to the controller.
        3. Files without audio-track probe data must clear stale controller candidates instead of reusing the previous video state.
    """
    metadata = _extract_video_track_metadata(current_track)
    if not metadata:
        return ()

    raw_candidates = metadata.get("audio_track_candidates")
    try:
        candidate_items = tuple(raw_candidates or ())
    except COERCE_EXCEPTIONS:
        return ()

    normalized: list[int] = []
    seen: set[int] = set()
    for raw_index in candidate_items:
        try:
            stream_index = int(raw_index)
        except COERCE_EXCEPTIONS:
            continue
        if stream_index < 0 or stream_index in seen:
            continue
        seen.add(stream_index)
        normalized.append(stream_index)
    return tuple(normalized)


def _extract_audio_track_descriptors_from_track(current_track: Any) -> tuple[dict[str, Any], ...]:
    """Return normalized audio-track descriptors for the current media metadata.

    Edge cases:
        1. audio_tracks can be absent or malformed and must clear stale UI/controller state deterministically.
        2. Duplicate, negative, or non-numeric stream indices must be dropped before reaching the controller.
        3. Descriptor dictionaries can miss optional fields like language/title/label and still need a stable shape.
    """
    metadata = _extract_video_track_metadata(current_track)
    raw_tracks = metadata.get("audio_tracks")
    try:
        track_items = tuple(raw_tracks or ())
    except COERCE_EXCEPTIONS:
        return ()

    descriptors: list[dict[str, Any]] = []
    seen_stream_indices: set[int] = set()
    for raw_track in track_items:
        if not isinstance(raw_track, dict):
            continue
        try:
            stream_index = int(raw_track.get("stream_index"))
        except COERCE_EXCEPTIONS:
            continue
        if stream_index < 0 or stream_index in seen_stream_indices:
            continue
        seen_stream_indices.add(stream_index)
        descriptor = {
            "stream_index": stream_index,
            "track_index": len(descriptors),
            "language": str(raw_track.get("language") or "").strip(),
            "title": str(raw_track.get("title") or "").strip(),
            "codec_name": str(raw_track.get("codec_name") or "").strip(),
            "codec_long_name": str(raw_track.get("codec_long_name") or "").strip(),
            "channels": raw_track.get("channels"),
            "channel_layout": str(raw_track.get("channel_layout") or "").strip(),
            "is_default": bool(raw_track.get("is_default")),
            "is_forced": bool(raw_track.get("is_forced")),
            "label": str(raw_track.get("label") or "").strip(),
        }
        try:
            descriptor["track_index"] = int(raw_track.get("track_index", descriptor["track_index"]))
        except COERCE_EXCEPTIONS:
            pass
        descriptors.append(descriptor)
    return tuple(descriptors)


def _reset_video_controller_text_track_state(self, video_controller: Any) -> None:
    """Clear stale subtitle/text-track state before a new video session starts.

    Edge cases:
        1. The previous video can leave cached text-track descriptors that would otherwise leak into the next file.
        2. Legacy video controllers can miss subtitle setters and must keep startup behavior unchanged.
        3. Setter failures must not abort video startup before the backend has a chance to expose fresh timed-text tracks.
    """
    descriptor_setter = getattr(video_controller, "set_text_track_descriptors", None)
    if not callable(descriptor_setter):
        return

    try:
        descriptor_setter(())
    except VIDEO_CONTROLLER_EXCEPTIONS:
        logger.debug("[PlayerEventHandler] Failed clearing text-track descriptors", exc_info=True)


def _refresh_video_controller_text_track_state(self, video_controller: Any) -> tuple[dict[str, Any], ...]:
    """Warm subtitle/text-track descriptors from the runtime backend once playback starts.

    Edge cases:
        1. Timed-text tracks can be unavailable for a given file and must keep the controller cache empty.
        2. Legacy video controllers can expose only getters or only setters, so refresh must degrade cleanly.
        3. Runtime adapter probes can fail transiently during startup and must not abort video playback.
    """
    descriptor_getter = getattr(video_controller, "get_text_track_descriptors", None)
    descriptor_setter = getattr(video_controller, "set_text_track_descriptors", None)
    active_getter = getattr(video_controller, "get_active_text_track_ids", None)

    if not callable(descriptor_getter):
        return ()

    try:
        descriptors = tuple(descriptor_getter() or ())
    except VIDEO_CONTROLLER_EXCEPTIONS:
        logger.debug("[PlayerEventHandler] Failed probing text-track descriptors", exc_info=True)
        return ()

    if callable(descriptor_setter):
        try:
            descriptor_setter(descriptors)
        except VIDEO_CONTROLLER_EXCEPTIONS:
            logger.debug("[PlayerEventHandler] Failed caching text-track descriptors", exc_info=True)

    if callable(active_getter):
        try:
            active_getter()
        except VIDEO_CONTROLLER_EXCEPTIONS:
            logger.debug("[PlayerEventHandler] Failed probing active text-track ids", exc_info=True)

    normalized_descriptors: list[dict[str, Any]] = []
    for raw_descriptor in descriptors:
        if isinstance(raw_descriptor, dict):
            normalized_descriptors.append(dict(raw_descriptor))
    return tuple(normalized_descriptors)


def _configure_video_controller_audio_track_state(self, video_controller: Any, current_track: Any) -> tuple[int, ...]:
    """Push metadata, descriptors, and candidates into the video controller before playback.

    Edge cases:
        1. Legacy video controllers can expose only a subset of setters and must keep startup behavior unchanged.
        2. Metadata/descriptors can be missing on the current track and must clear stale controller state from the previous video.
        3. Setter failures must not abort playback before the backend attempts the default stream.
    """
    metadata = _extract_video_track_metadata(current_track)
    descriptors = _extract_audio_track_descriptors_from_track(current_track)

    metadata_setter = getattr(video_controller, "set_current_media_metadata", None)
    if callable(metadata_setter):
        try:
            metadata_setter(metadata)
        except VIDEO_CONTROLLER_EXCEPTIONS:
            logger.debug("[PlayerEventHandler] Failed configuring current media metadata", exc_info=True)

    descriptor_setter = getattr(video_controller, "set_audio_track_descriptors", None)
    if callable(descriptor_setter):
        try:
            descriptor_setter(descriptors)
        except VIDEO_CONTROLLER_EXCEPTIONS:
            logger.debug("[PlayerEventHandler] Failed configuring audio-track descriptors", exc_info=True)

    return _configure_video_controller_audio_track_candidates(self, video_controller, current_track)


def _configure_video_controller_audio_track_candidates(self, video_controller: Any, current_track: Any) -> tuple[int, ...]:
    """Push current-track audio candidates into the video controller before playback.

    Edge cases:
        1. Legacy video controllers can lack the setter and must keep startup behavior unchanged.
        2. Track metadata can be absent on video files and must clear old candidates deterministically.
        3. Controller-side setter failures must not abort playback before the backend gets a chance to try the default stream.
    """
    candidates = _extract_audio_track_candidates_from_track(current_track)
    setter = getattr(video_controller, "set_audio_track_candidates", None)
    if not callable(setter):
        return candidates

    try:
        result = setter(candidates)
    except VIDEO_CONTROLLER_EXCEPTIONS:
        logger.debug("[PlayerEventHandler] Failed configuring audio-track candidates", exc_info=True)
        return candidates

    try:
        normalized_result = tuple(int(index) for index in tuple(result or ()))
    except COERCE_EXCEPTIONS:
        return candidates
    return tuple(index for index in normalized_result if index >= 0)


def _start_video_playback(self, hwnd: int, path: Optional[str], loop: bool, source: str):
    """Logica centralizzata per avviare la riproduzione video."""
    if self._is_shutting_down:
        return

    if not path or hwnd <= 0:
        logger.warning("Richiesta di avvio video da %s invalida (path o hwnd mancanti).", source)
        return

    path_str = str(path)
    if self._should_ignore_video_start_after_window_close(path_str):
        return

    current_track = self.queue_manager.current_track
    try:
        duration_hint = max(0.0, float(getattr(current_track, "duration", 0.0) or 0.0))
    except COERCE_EXCEPTIONS:
        duration_hint = 0.0

    current_path = getattr(current_track, "path", None)
    if current_track and not self._matches_current_track_path(current_track, path_str):
        logger.debug(
            "Ignorata richiesta di avvio video da %s: percorso obsoleto. Attuale=%s Richiesto=%s",
            source,
            current_path,
            path_str,
        )
        return

    signature = (path_str, int(hwnd), bool(loop))
    if self._last_video_start_signature == signature and self.state_manager.is_video():
        logger.debug("Ignorata richiesta duplicata di avvio video da %s.", source)
        return

    logger.info("Avvio riproduzione video da %s: hwnd=%s, path=%s", source, hwnd, path_str)

    video_controller = getattr(self.engine_controller, "video_controller", None)
    if not video_controller:
        factory = getattr(self.engine_controller, "_video_controller_factory", None)
        if callable(factory):
            try:
                created = factory()
                self.engine_controller.video_controller = created
                video_controller = created
                logger.debug("[PlayerEventHandler] VideoController creato JIT tramite factory (%s)", source)
            except VIDEO_CONTROLLER_EXCEPTIONS as exc:
                logger.error(
                    "Errore creando VideoController JIT da %s: %s",
                    source,
                    exc,
                    exc_info=True,
                )

    if not video_controller:
        logger.error("VideoController non disponibile.")
        self.state_manager.update_state(PlayerState.ERROR, {"message": "VideoController not available"})
        return

    try:
        _reset_video_controller_text_track_state(self, video_controller)
        _configure_video_controller_audio_track_state(self, video_controller, current_track)
        ok = video_controller.ensure_video_adapter(hwnd=int(hwnd), loop_enabled=bool(loop))
        if not ok:
            logger.warning(
                "Video adapter not ready yet for hwnd=%s path=%s; keeping playback request pending.",
                hwnd,
                path_str,
            )
            self.state_manager.update_state(PlayerState.LOADING)
            return

        try:
            audio_engine = getattr(self.engine_controller, "audio_engine", None)
            if audio_engine and hasattr(audio_engine, "get_volume"):
                volume = float(audio_engine.get_volume())
                if hasattr(video_controller, "set_volume"):
                    video_controller.set_volume(volume)
        except VIDEO_CONTROLLER_EXCEPTIONS:
            logger.debug("[PlayerEventHandler] Sync volume to video failed (ignored)", exc_info=True)

        video_controller.play_media(path_str, duration_hint=duration_hint)
        _refresh_video_controller_text_track_state(self, video_controller)

        self._last_video_start_signature = signature
        self.state_manager.update_state(PlayerState.PLAYING_VIDEO)

    except VIDEO_CONTROLLER_EXCEPTIONS as error:
        logger.error("Errore avvio riproduzione video: %s", error, exc_info=True)
        self.state_manager.update_state(PlayerState.ERROR, {"message": f"Video playback failed: {error}"})


def _on_video_window_closed(self, data: Any = None):
    """Gestisce la chiusura della finestra video da parte dell'utente."""
    if self._is_shutting_down:
        return

    logger.info("La finestra video è stata chiusa, fermo la riproduzione.")
    self._blocked_video_start_path = self._current_track_path()
    self._reset_video_start_signature("video_window_closed")

    _safe_publish_ui_event(
        self,
        AudioEventType.CANCEL_VIDEO_PLAYBACK,
        {"reason": "video_window_closed", "path": self._blocked_video_start_path},
    )

    if self.state_manager.is_video() or self._blocked_video_start_path:
        self.engine_controller.stop()
        self.state_manager.update_state(PlayerState.STOPPED)


_PLAYER_EVENT_HANDLER_VIDEO_METHODS: tuple[tuple[str, Any], ...] = (
    ("_matches_current_track_path", staticmethod(_matches_current_track_path)),
    ("_reset_video_start_signature", _reset_video_start_signature),
    ("_safe_publish_ui_event", _safe_publish_ui_event),
    ("_current_track_path", _current_track_path),
    ("_extract_video_event_path", staticmethod(_extract_video_event_path)),
    ("_should_ignore_video_playback_stopped", _should_ignore_video_playback_stopped),
    ("_should_ignore_video_start_after_window_close", _should_ignore_video_start_after_window_close),
    ("_on_video_playback_stopped", _on_video_playback_stopped),
    ("_on_video_playback_error", _on_video_playback_error),
    ("_extract_video_track_metadata", staticmethod(_extract_video_track_metadata)),
    ("_extract_audio_track_candidates_from_track", staticmethod(_extract_audio_track_candidates_from_track)),
    ("_extract_audio_track_descriptors_from_track", staticmethod(_extract_audio_track_descriptors_from_track)),
    ("_reset_video_controller_text_track_state", _reset_video_controller_text_track_state),
    ("_refresh_video_controller_text_track_state", _refresh_video_controller_text_track_state),
    ("_configure_video_controller_audio_track_state", _configure_video_controller_audio_track_state),
    ("_configure_video_controller_audio_track_candidates", _configure_video_controller_audio_track_candidates),
    ("_on_video_playback_ready", _on_video_playback_ready),
    ("_on_video_playback_ready_signal", _on_video_playback_ready_signal),
    ("_start_video_playback", _start_video_playback),
    ("_on_video_window_closed", _on_video_window_closed),
)


def install_player_event_handler_video_behavior(handler_cls: type) -> None:
    """Install video handler behavior on the player event handler class.

    Edge cases:
        1. handler_cls is not a class and cannot accept deterministic method rebinding.
        2. Duplicate binding names would silently shadow an earlier video handler method.
        3. A split module exports an invalid binding object and breaks handler wiring.
    """
    seen_names: set[str] = set()
    if not isinstance(handler_cls, type):
        raise TypeError('handler_cls must be a class')
    if getattr(handler_cls, '_player_event_handler_video_behavior_attached', False):
        return

    for attribute_name, method in _PLAYER_EVENT_HANDLER_VIDEO_METHODS:
        if not attribute_name:
            raise TypeError('Player event handler video binding name cannot be empty')
        if attribute_name in seen_names:
            raise TypeError(f'Duplicate player event handler video binding: {attribute_name}')
        if not callable(method) and not isinstance(method, staticmethod):
            raise TypeError(f'Invalid player event handler video binding: {attribute_name}')
        setattr(handler_cls, attribute_name, method)
        seen_names.add(attribute_name)

    setattr(handler_cls, '_player_event_handler_video_behavior_attached', True)


def _bind_player_event_handler_video_methods(handler_cls: type) -> None:
    """Compatibility helper kept for central coordinator imports."""
    install_player_event_handler_video_behavior(handler_cls)


def attach_player_event_handler_video_behavior(handler_cls: type) -> None:
    """Compatibility shim for legacy leaf-level attach imports."""
    install_player_event_handler_video_behavior(handler_cls)
