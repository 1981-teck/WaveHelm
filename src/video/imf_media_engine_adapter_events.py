from __future__ import annotations

import os
import queue
from typing import Any

from src.audio.audio_events import AudioEventType

from .component_base.com_helpers import _hr_to_hex, describe_hresult
from .mf_base import (
    MF_MEDIA_ENGINE_EVENT_ENDED,
    MF_MEDIA_ENGINE_EVENT_ERROR,
    MF_MEDIA_ENGINE_EVENT_LOADEDDATA,
    MF_MEDIA_ENGINE_EVENT_LOADEDMETADATA,
    MF_MEDIA_ENGINE_EVENT_PLAYING,
    logger,
)

_EVENT_VIDEO_ENDED = "VIDEO_ENDED"
_EVENT_VIDEO_ERROR = "VIDEO_ERROR"
_EVENT_VIDEO_READY = "VIDEO_READY"
_EVENT_VIDEO_PLAYING = "VIDEO_PLAYING"

IMF_ADAPTER_EVENT_QUEUE_EXCEPTIONS = (queue.Full,)
IMF_ADAPTER_EVENT_PUBLISH_EXCEPTIONS = (RuntimeError, TypeError, ValueError)

_MEDIA_ENGINE_ERROR_NAMES: dict[int, str] = {
    1: "MEDIA_ERR_ABORTED",
    2: "MEDIA_ERR_NETWORK",
    3: "MEDIA_ERR_DECODE",
    4: "MEDIA_ERR_SRC_NOT_SUPPORTED",
    5: "MEDIA_ERR_ENCRYPTED",
}

_MEDIA_ENGINE_ERROR_MESSAGES: dict[int, str] = {
    1: "La riproduzione video e' stata interrotta.",
    2: "Errore di rete durante il caricamento del video.",
    3: "Windows Media Foundation non riesce a decodificare uno stream del file.",
    4: "La sorgente video non e' supportata dal backend Windows Media Foundation.",
    5: "Il video e' cifrato o richiede una pipeline DRM non disponibile.",
}


def on_media_engine_event(self, me_event: int, param1: int, param2: int) -> None:
    """Riceve eventi dal callback COM; viene chiamato sul thread COM."""
    try:
        self._event_queue.put_nowait((int(me_event), int(param1), int(param2)))
    except IMF_ADAPTER_EVENT_QUEUE_EXCEPTIONS as error:
        logger.debug(
            "[IMFAdapter] Event queue full, dropping event %s (%s, %s): %s",
            me_event,
            param1,
            param2,
            error,
            exc_info=True,
        )


def pump_events(self) -> None:
    """Processa gli eventi accumulati dal callback COM."""
    while True:
        try:
            me_event, p1, p2 = self._event_queue.get_nowait()
        except queue.Empty:
            break

        self._dispatch_event(me_event, p1, p2)


def _dispatch_event(self, me_event: int, p1: int, p2: int) -> None:
    """Traduce l'evento MediaEngine in eventi applicativi e li pubblica."""
    if me_event in (MF_MEDIA_ENGINE_EVENT_LOADEDMETADATA, MF_MEDIA_ENGINE_EVENT_LOADEDDATA):
        self._publish(_EVENT_VIDEO_READY, {"event": me_event, "p1": p1, "p2": p2})
    elif me_event == MF_MEDIA_ENGINE_EVENT_PLAYING:
        self._publish(_EVENT_VIDEO_PLAYING, {"event": me_event, "p1": p1, "p2": p2})
    elif me_event == MF_MEDIA_ENGINE_EVENT_ENDED:
        self._publish(_EVENT_VIDEO_ENDED, {"event": me_event, "p1": p1, "p2": p2})
    elif me_event == MF_MEDIA_ENGINE_EVENT_ERROR:
        payload = self._build_video_error_payload(me_event, p1, p2)
        logger.error(
            "[IMFAdapter] MediaEngine error path=%s media_error=%s hr=%s",
            payload.get("path"),
            payload.get("media_error_name"),
            payload.get("hresult"),
        )
        self._publish(_EVENT_VIDEO_ERROR, payload)
        self._publish(
            AudioEventType.VIDEO_PLAYBACK_ERROR,
            payload,
            require_ui_thread=True,
        )


def _build_video_error_payload(self, me_event: int, p1: int, p2: int) -> dict[str, Any]:
    """Costruisce un payload diagnostico ricco per gli errori runtime del video."""
    media_error_code = int(p1 or 0)
    media_error_name = _MEDIA_ENGINE_ERROR_NAMES.get(media_error_code, f"MEDIA_ERR_{media_error_code}")
    media_error_message = _MEDIA_ENGINE_ERROR_MESSAGES.get(
        media_error_code,
        "Errore sconosciuto del motore video.",
    )
    path = self._source
    filename = os.path.basename(path) if path else ""
    hresult = _hr_to_hex(p2)
    hresult_description = describe_hresult(p2)

    user_message = media_error_message
    if media_error_code == 4 and filename:
        user_message = f"Il file video '{filename}' non e' supportato dal backend Windows Media Foundation."
    if hresult_description and hresult_description != hresult:
        user_message = f"{user_message}\nDettagli tecnici: {hresult_description}"
    elif hresult:
        user_message = f"{user_message}\nDettagli tecnici: {hresult}"

    return {
        "event": int(me_event),
        "p1": int(p1 or 0),
        "p2": int(p2 or 0),
        "path": path,
        "filename": filename,
        "media_error_code": media_error_code,
        "media_error_name": media_error_name,
        "media_error_message": media_error_message,
        "hresult": hresult,
        "hresult_description": hresult_description,
        "user_message": user_message,
    }


def _publish(self, event_name: Any, payload: dict, *, require_ui_thread: bool = False) -> None:
    """Pubblica su event bus se disponibile (best effort)."""
    event_bus = self._event_bus
    if not event_bus:
        return

    try:
        if hasattr(event_bus, "publish"):
            event_bus.publish(event_name, payload, require_ui_thread=require_ui_thread)
        elif hasattr(event_bus, "emit"):
            event_bus.emit(event_name, payload)
        else:
            logger.debug("[IMFAdapter] event_bus has no publish/emit")
    except IMF_ADAPTER_EVENT_PUBLISH_EXCEPTIONS:
        logger.warning("[IMFAdapter] Failed to publish %s", event_name, exc_info=True)


_IMF_MEDIA_ENGINE_ADAPTER_EVENTS_METHODS: tuple[tuple[str, object], ...] = (
    ("on_media_engine_event", on_media_engine_event),
    ("pump_events", pump_events),
    ("_dispatch_event", _dispatch_event),
    ("_build_video_error_payload", _build_video_error_payload),
    ("_publish", _publish),
)


def install_imf_media_engine_adapter_events_behavior(cls) -> None:
    """Install event behavior on the adapter leaf module.

    Edge cases:
        - Duplicate installer calls can silently rebind event handlers.
        - Partial imports can leave queue pumping attached without publish helpers.
        - Legacy callers can still import the old attach_* entrypoint directly.
    """
    if getattr(cls, "_imf_media_engine_adapter_events_behavior_attached", False):
        return

    for name, method in _IMF_MEDIA_ENGINE_ADAPTER_EVENTS_METHODS:
        setattr(cls, name, method)

    setattr(cls, "_imf_media_engine_adapter_events_behavior_attached", True)



def attach_imf_media_engine_adapter_events_behavior(cls) -> None:
    """Backward-compatible shim for legacy attach_* imports."""
    install_imf_media_engine_adapter_events_behavior(cls)
