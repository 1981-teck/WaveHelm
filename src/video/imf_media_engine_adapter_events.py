from __future__ import annotations

import os
import queue
from src.audio.audio_events import AudioEventType

from .component_base.com_helpers import _hr_to_hex, describe_hresult
from .mf_base import (
    MF_MEDIA_ENGINE_EVENT_ENDED,
    MF_MEDIA_ENGINE_EVENT_ERROR,
    MF_MEDIA_ENGINE_EVENT_CANPLAY,
    MF_MEDIA_ENGINE_EVENT_LOADEDDATA,
    MF_MEDIA_ENGINE_EVENT_LOADEDMETADATA,
    MF_MEDIA_ENGINE_EVENT_LOADSTART,
    MF_MEDIA_ENGINE_EVENT_PAUSE,
    MF_MEDIA_ENGINE_EVENT_PLAYING,
    MF_MEDIA_ENGINE_EVENT_PURGEQUEUEDEVENTS,
    logger,
)

_EVENT_VIDEO_ENDED = "VIDEO_ENDED"
_EVENT_VIDEO_ERROR = "VIDEO_ERROR"
_EVENT_VIDEO_READY = "VIDEO_READY"
_EVENT_VIDEO_PLAYING = "VIDEO_PLAYING"

IMF_ADAPTER_EVENT_QUEUE_EXCEPTIONS = (queue.Full,)
IMF_ADAPTER_EVENT_PUBLISH_EXCEPTIONS = (RuntimeError, TypeError, ValueError)
IMF_ADAPTER_DEFERRED_PLAY_EXCEPTIONS = (AttributeError, OSError, ReferenceError, RuntimeError, TypeError, ValueError)

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
    """Queue one native event with the source epoch observed at callback time.

    Edge cases:
        - Events queued before a source replacement retain the old source epoch.
        - A callback racing shutdown cannot reclassify an event as a newer source.
        - Queue saturation drops the complete event record rather than partial state.
    """
    with self._lock:
        source_epoch = self._source_epoch
    try:
        self._event_queue.put_nowait((int(me_event), int(param1), int(param2), source_epoch))
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
    """Process queued Media Engine events in callback order."""
    while True:
        try:
            me_event, p1, p2, source_epoch = self._event_queue.get_nowait()
        except queue.Empty:
            break

        self._dispatch_event(me_event, p1, p2, source_epoch)


def _begin_source_detach(self, source_epoch: int) -> bool:
    """Detach the active source once before a pending replacement is committed.

    Edge cases:
        - Duplicate PAUSE notifications cannot start a second detach.
        - A stale PAUSE cannot detach the newly committed source.
        - Native detach failure rolls back the pending replacement and propagates.
    """
    with self._lock:
        pending_epoch = self._pending_source_epoch
        valid = source_epoch == self._source_epoch and pending_epoch is not None
        if not valid or self._shutdown_requested or self._closed:
            return False
        if self._detaching_source_epoch is not None:
            return self._detaching_source_epoch == source_epoch
        self._detaching_source_epoch = source_epoch
        core = self._core

    logger.info('[IMFAdapter] Detaching native source epoch=%d before replacement', source_epoch)
    try:
        core.load_source(None)
    except IMF_ADAPTER_DEFERRED_PLAY_EXCEPTIONS:
        with self._lock:
            if self._detaching_source_epoch == source_epoch:
                self._detaching_source_epoch = None
                self._pending_source_epoch = None
                self._pending_source_path = None
                if self._pending_play_epoch == pending_epoch:
                    self._pending_play_epoch = None
        raise
    return True


def _release_deferred_source_switch(self, source_epoch: int) -> None:
    """Commit a pending source only after native event-queue purge confirms detach.

    Edge cases:
        - A stale/duplicate PURGE cannot release another source generation.
        - Rapid selections commit only the latest uncommitted path once.
        - SetSource failure is published and leaves playback blocked fail-closed.
    """
    with self._lock:
        pending_epoch = self._pending_source_epoch
        valid = (
            source_epoch == self._source_epoch
            and self._detaching_source_epoch == source_epoch
            and pending_epoch is not None
        )
        if not valid or self._shutdown_requested or self._closed:
            return
        path = self._pending_source_path
        self._pending_source_epoch = None
        self._pending_source_path = None
        self._detaching_source_epoch = None
        self._source_epoch = pending_epoch
        self._source = path
        self._loadstart_epoch = None
        self._ready_epoch = None
        self._playing_epoch = None
        core = self._core

    if not path:
        return
    logger.info('[IMFAdapter] PURGE released deferred SetSource epoch=%d', pending_epoch)
    try:
        core.load_source(path)
    except IMF_ADAPTER_DEFERRED_PLAY_EXCEPTIONS as error:
        with self._lock:
            if pending_epoch == self._source_epoch:
                self._pending_play_epoch = None
                self._ready_epoch = None
        payload: dict[str, object] = {
            'event': MF_MEDIA_ENGINE_EVENT_PURGEQUEUEDEVENTS,
            'path': path,
            'error_type': type(error).__name__,
            'error': str(error),
            'user_message': 'Unable to replace the native video source after detach purge.',
        }
        logger.error('[IMFAdapter] Deferred SetSource failed after PURGE', exc_info=True)
        self._publish(_EVENT_VIDEO_ERROR, payload)
        self._publish(AudioEventType.VIDEO_PLAYBACK_ERROR, payload, require_ui_thread=True)


def _release_deferred_play(self, source_epoch: int) -> None:
    """Release one deferred Play only for the matching loaded source.

    Edge cases:
        - CANPLAY without a matching LOADSTART is ignored as stale or incomplete.
        - CANPLAY from an older source cannot release the current source's Play.
        - Shutdown clears pending playback and prevents a native Play dispatch.
    """
    with self._lock:
        current_epoch = self._source_epoch
        valid = source_epoch == current_epoch and self._loadstart_epoch == source_epoch
        if not valid or self._shutdown_requested or self._closed:
            return
        self._ready_epoch = source_epoch
        if self._pending_play_epoch != source_epoch:
            return
        self._pending_play_epoch = None
        core = self._core

    logger.info('[IMFAdapter] CANPLAY released deferred Play epoch=%d', source_epoch)
    try:
        core.play()
    except IMF_ADAPTER_DEFERRED_PLAY_EXCEPTIONS as error:
        payload: dict[str, object] = {
            "event": MF_MEDIA_ENGINE_EVENT_CANPLAY,
            "path": self._source,
            "error_type": type(error).__name__,
            "error": str(error),
            "user_message": "Unable to start native video playback after CANPLAY.",
        }
        logger.error('[IMFAdapter] Deferred Play failed after CANPLAY', exc_info=True)
        self._publish(_EVENT_VIDEO_ERROR, payload)
        self._publish(AudioEventType.VIDEO_PLAYBACK_ERROR, payload, require_ui_thread=True)


def _handle_pause_event(self, source_epoch: int) -> None:
    """Begin native detach for a matching PAUSE and surface failures once."""
    try:
        self._begin_source_detach(source_epoch)
    except IMF_ADAPTER_DEFERRED_PLAY_EXCEPTIONS as error:
        payload: dict[str, object] = {
            'event': MF_MEDIA_ENGINE_EVENT_PAUSE,
            'path': self._source,
            'error_type': type(error).__name__,
            'error': str(error),
            'user_message': 'Unable to detach the native video source after PAUSE.',
        }
        logger.error('[IMFAdapter] Native source detach failed after PAUSE', exc_info=True)
        self._publish(_EVENT_VIDEO_ERROR, payload)
        self._publish(AudioEventType.VIDEO_PLAYBACK_ERROR, payload, require_ui_thread=True)
    with self._lock:
        if source_epoch == self._source_epoch:
            self._playing_epoch = None


def _dispatch_event(self, me_event: int, p1: int, p2: int, source_epoch: int) -> None:
    """Translate one Media Engine event and advance source readiness."""
    with self._lock:
        if source_epoch != self._source_epoch or self._closed or self._shutdown_requested:
            return
    if me_event == MF_MEDIA_ENGINE_EVENT_PAUSE:
        _handle_pause_event(self, source_epoch)
    elif me_event == MF_MEDIA_ENGINE_EVENT_PURGEQUEUEDEVENTS:
        self._release_deferred_source_switch(source_epoch)
    elif me_event == MF_MEDIA_ENGINE_EVENT_LOADSTART:
        with self._lock:
            if source_epoch == self._source_epoch and self._pending_source_epoch is None:
                self._loadstart_epoch = source_epoch
    elif me_event == MF_MEDIA_ENGINE_EVENT_CANPLAY:
        with self._lock:
            source_switch_pending = self._pending_source_epoch == self._source_epoch
        if not source_switch_pending:
            self._release_deferred_play(source_epoch)
    elif me_event in (MF_MEDIA_ENGINE_EVENT_LOADEDMETADATA, MF_MEDIA_ENGINE_EVENT_LOADEDDATA):
        self._publish(_EVENT_VIDEO_READY, {"event": me_event, "p1": p1, "p2": p2})
    elif me_event == MF_MEDIA_ENGINE_EVENT_PLAYING:
        with self._lock:
            if source_epoch == self._source_epoch and self._pending_source_epoch is None:
                self._playing_epoch = source_epoch
        self._publish(_EVENT_VIDEO_PLAYING, {"event": me_event, "p1": p1, "p2": p2})
    elif me_event == MF_MEDIA_ENGINE_EVENT_ENDED:
        with self._lock:
            if source_epoch == self._source_epoch:
                self._playing_epoch = None
        self._publish(_EVENT_VIDEO_ENDED, {"event": me_event, "p1": p1, "p2": p2})
    elif me_event == MF_MEDIA_ENGINE_EVENT_ERROR:
        with self._lock:
            if source_epoch == self._source_epoch:
                self._pending_play_epoch = None
                self._ready_epoch = None
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


def _build_video_error_payload(self, me_event: int, p1: int, p2: int) -> dict[str, object]:
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


def _publish(self, event_name: object, payload: dict[str, object], *, require_ui_thread: bool = False) -> None:
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
    ("_begin_source_detach", _begin_source_detach),
    ("_release_deferred_source_switch", _release_deferred_source_switch),
    ("_release_deferred_play", _release_deferred_play),
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
