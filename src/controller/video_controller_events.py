from __future__ import annotations

import logging
from typing import Any, Callable

from src.audio.audio_events import AudioEventType
from .video_controller_state import VideoState

logger = logging.getLogger(__name__)

VIDEO_WINDOW_MOVE_COERCE_EXCEPTIONS = (TypeError, ValueError, OverflowError)


def _coerce_surface_signature(data: Any) -> tuple[int, int, int] | None:
    """Return a normalized surface signature when an event payload is usable.

    Edge cases:
        1. Event payloads can contain non-numeric values and must fail closed instead of raising during resize storms.
        2. Zero or negative dimensions must be rejected so adapter refresh calls stay deterministic and bounded.
        3. Host migration can emit stale HWND values, so the signature is only valid when all three numeric fields coerce cleanly.
    """
    if not isinstance(data, dict):
        return None

    try:
        hwnd = int(data.get('hwnd') or 0)
        width = int(data.get('width') or 0)
        height = int(data.get('height') or 0)
    except VIDEO_WINDOW_MOVE_COERCE_EXCEPTIONS:
        return None

    if hwnd <= 0 or width <= 0 or height <= 0:
        return None
    return (hwnd, width, height)


def _cache_surface_signature(self, signature: tuple[int, int, int]) -> None:
    """Persist the latest valid surface geometry observed for the active host.

    Edge cases:
        1. A resize event can arrive before playback reaches READY/PLAYING, so geometry must survive the transition instead of being discarded.
        2. Repeated events with the same signature must remain idempotent and not grow controller state.
        3. Host migration can switch HWND ownership quickly, so cached geometry must always reflect the most recent valid active-host signature.
    """
    self._last_seen_surface_signature = signature
    self._pending_surface_signature = signature


def get_pending_surface_signature(self) -> tuple[int, int, int] | None:
    """Return the latest valid surface geometry waiting to be applied.

    Edge cases:
        1. Older controller instances may not have initialized pending-geometry attributes yet.
        2. Non-tuple or malformed cached values must not escape to playback code as valid geometry.
        3. Geometry cached for a previous HWND must not be returned for the current session.
    """
    signature = getattr(self, '_pending_surface_signature', None)
    if not isinstance(signature, tuple) or len(signature) != 3:
        return None
    try:
        hwnd, width, height = (int(signature[0]), int(signature[1]), int(signature[2]))
    except VIDEO_WINDOW_MOVE_COERCE_EXCEPTIONS:
        return None
    current_hwnd = getattr(self, '_current_hwnd', None)
    if current_hwnd is None or hwnd != int(current_hwnd):
        return None
    if width <= 0 or height <= 0:
        return None
    return (hwnd, width, height)


def clear_pending_surface_signature(self) -> None:
    """Drop cached pending surface geometry after a successful apply or host reset.

    Edge cases:
        1. Legacy controller instances may not define the cache attribute and must still clear cleanly.
        2. Repeated clears must remain idempotent during fast stop/start transitions.
        3. Host toggles can invalidate pending geometry while playback continues elsewhere, so the clear path cannot rely on prior state.
    """
    self._pending_surface_signature = None


def _on_fullscreen_toggle_requested(self, data: Any = None) -> None:
    """Handle fullscreen toggle requests from the control strip."""
    logger.debug(
        "[VideoController] Fullscreen toggle requested, routing to UI",
    )
    # Re-publish on the UI-specific channel to avoid recursive event loops.
    self._publish_event(AudioEventType.UI_TOGGLE_FULLSCREEN, data or {})


def _on_video_window_moved(self, data: Any = None) -> None:
    """Cache and refresh viewport geometry for the active video host.

    Edge cases:
        1. The UI can publish geometry before playback reaches READY/PLAYING, so valid surface data must be cached instead of dropped.
        2. Duplicate resize events must not spam adapter refresh calls once the same signature has already been applied.
        3. Late events for a previous HWND must be ignored so host migration never rebinds stale geometry onto the current session.
    """
    if not self._adapter or not self._current_hwnd:
        return

    signature = _coerce_surface_signature(data)
    if signature is None:
        return

    current_hwnd = int(self._current_hwnd)
    if signature[0] != current_hwnd:
        return

    _cache_surface_signature(self, signature)

    if self._state not in (VideoState.READY, VideoState.PLAYING, VideoState.PAUSED):
        return

    if getattr(self, '_last_surface_signature', None) == signature:
        return

    ok = self._safe_adapter_call(self._adapter.refresh_video_window, signature[0], signature[1], signature[2])
    if ok:
        self._last_surface_signature = signature
        clear_pending_surface_signature(self)


def _on_video_playback_error(self, data: Any = None) -> None:
    """Align controller state when the backend reports a runtime playback error."""
    if not isinstance(data, dict):
        return

    if self._shutting_down or self._closing:
        logger.debug("[VideoController] Ignoring VIDEO_PLAYBACK_ERROR during shutdown/close.")
        return

    if self._adapter is None:
        logger.debug("[VideoController] Ignoring stale VIDEO_PLAYBACK_ERROR after adapter teardown.")
        return

    event_path = str(data.get("path") or "")
    current_path = str(self._current_path or "")
    if event_path and current_path and event_path.lower() != current_path.lower():
        logger.debug(
            "[VideoController] Ignoring stale VIDEO_PLAYBACK_ERROR current=%s event=%s",
            current_path,
            event_path,
        )
        return

    if self._state not in (VideoState.READY, VideoState.PLAYING, VideoState.PAUSED):
        logger.debug(
            "[VideoController] Ignoring VIDEO_PLAYBACK_ERROR in inactive state=%s path=%s",
            self._state.name,
            event_path or current_path or "<unknown>",
        )
        return

    self._last_error_info = dict(data)
    logger.error(
        "[VideoController] Runtime video error path=%s media_error=%s hr=%s",
        event_path or current_path or "<unknown>",
        data.get("media_error_name"),
        data.get("hresult"),
    )
    self._update_state(VideoState.ERROR)


_VIDEO_CONTROLLER_EVENT_METHODS: tuple[tuple[str, Callable[..., Any]], ...] = (
    ("_on_fullscreen_toggle_requested", _on_fullscreen_toggle_requested),
    ("_on_video_window_moved", _on_video_window_moved),
    ("_on_video_playback_error", _on_video_playback_error),
    ("get_pending_surface_signature", get_pending_surface_signature),
    ("clear_pending_surface_signature", clear_pending_surface_signature),
)


def install_video_controller_event_behavior(controller_cls: type) -> None:
    """Install video controller event behavior on the central coordinator.

    Edge cases:
        1. A binding name is empty and would overwrite an unintended attribute.
        2. Duplicate event names silently shadow an earlier controller method.
        3. A non-callable binding reaches class wiring and breaks runtime behavior.
    """
    if getattr(controller_cls, '_video_controller_event_behavior_attached', False):
        return

    seen_names: set[str] = set()
    for attribute_name, method in _VIDEO_CONTROLLER_EVENT_METHODS:
        if not attribute_name:
            raise TypeError('Video controller event binding name cannot be empty')
        if attribute_name in seen_names:
            raise TypeError(f'Duplicate video controller event binding: {attribute_name}')
        if not callable(method):
            raise TypeError(f'Invalid video controller event binding: {attribute_name}')
        setattr(controller_cls, attribute_name, method)
        seen_names.add(attribute_name)

    setattr(controller_cls, '_video_controller_event_behavior_attached', True)


def attach_video_controller_event_behavior(controller_cls: type) -> None:
    """Compatibility shim for historical attach_* imports.

    Prefer install_video_controller_event_behavior() from the central coordinator path.
    """
    install_video_controller_event_behavior(controller_cls)
