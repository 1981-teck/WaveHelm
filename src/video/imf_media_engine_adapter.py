from __future__ import annotations

import queue
import threading
import time
from typing import Callable
from .wic_pipeline import FramePipeline
from .seek_receipt import SeekPhase

from .component_adapter.media_engine_core import MediaEngineCore
from .imf_media_engine_adapter_events import attach_imf_media_engine_adapter_events_behavior as _attach_imf_media_engine_adapter_events_behavior
from .imf_media_engine_adapter_playback import attach_imf_media_engine_adapter_playback_behavior as _attach_imf_media_engine_adapter_playback_behavior
from .imf_media_engine_adapter_threading import attach_imf_media_engine_adapter_threading_behavior as _attach_imf_media_engine_adapter_threading_behavior
from .mf_base import logger
from .media_engine_core_shared import QUERY_EXCEPTIONS
from src.playback_observation import ClockObservation, ClockOrigin, empty_clock


class IMFMediaEngineAdapter:
    """Adapter di alto livello per IMFMediaEngine (HWND-based)."""

    def __init__(self, event_bus: object | None = None) -> None:
        self._event_bus = event_bus
        self._lock = threading.RLock()

        self._hwnd: int = 0
        self._source: str | None = None
        self._loop_enabled: bool = False
        self._volume: float = 1.0
        self._muted: bool = False
        self._event_queue: "queue.Queue[tuple[int, int, int, int]]" = queue.Queue()
        self._source_epoch: int = 0
        self._loadstart_epoch: int | None = None
        self._ready_epoch: int | None = None
        self._playing_epoch: int | None = None
        self._pending_play_epoch: int | None = None
        self._pending_source_epoch: int | None = None
        self._pending_source_path: str | None = None
        self._detaching_source_epoch: int | None = None

        self._shutdown_requested: bool = False
        self._closed: bool = False

        self._com_thread_manager = None
        self._core = MediaEngineCore(self)

        logger.info("[IMFAdapter] Adapter inizializzato.")


    def get_frame_pipeline(self, hwnd: int) -> FramePipeline | None:
        """Borrow the exact current window's service; no implicit startup or wait."""
        with self._lock:
            if self._closed or self._shutdown_requested or hwnd != self._hwnd:
                return None
            pipeline = self._core._wic_pipeline
        return pipeline if pipeline is not None and pipeline.hwnd == hwnd else None

    def submit_frame_to_com_thread(self, name: str, callback: Callable[[], object],
                                   response: object) -> bool:
        """Single-flight frame jobs carry their own generation, unlike seek jobs.

        Edges: close before admission; queued work outlives a core; new source
        invalidates an admitted frame. Never start a manager or wait on the UI.
        """
        with self._lock:
            manager, core = self._com_thread_manager, self._core
            if self._closed or self._shutdown_requested or manager is None:
                return False

        def guarded() -> object:
            with self._lock:
                stale = (self._closed or self._shutdown_requested or self._core is not core
                         or self._com_thread_manager is not manager)
            return None if stale else callback()

        return manager.submit_to_com_thread(name, guarded, response) is True

    def frame_ready(self) -> bool:
        """Reject pre-load, replacement, incomplete seek and closed state."""
        with self._lock:
            valid = (not self._closed and not self._shutdown_requested
                     and self._pending_source_epoch is None and self._source is not None
                     and self._ready_epoch == self._source_epoch)
        if not valid:
            return False
        receipt = self._core.get_seek_receipt()
        return receipt is None or receipt.phase in (
            SeekPhase.NATIVE_COMPLETED, SeekPhase.REJECTED, SeekPhase.CANCELLED)

    def frame_pump_ready(self) -> bool:
        """Permit tick-only progress after native seek admission, never presentation.

        Pending source, uncertain/expired seek and shutdown forbid all new work.
        An accepted seek may need frame-server ticks before its SEEKED callback;
        frame_ready remains the separate, stricter pixel-publication gate.
        """
        with self._lock:
            valid = (not self._closed and not self._shutdown_requested
                     and self._pending_source_epoch is None and self._source is not None
                     and self._ready_epoch == self._source_epoch)
        if not valid:
            return False
        receipt = self._core.get_seek_receipt()
        return receipt is None or receipt.phase in (
            SeekPhase.NATIVE_ACCEPTED_UNCONFIRMED, SeekPhase.NATIVE_COMPLETED,
            SeekPhase.REJECTED, SeekPhase.CANCELLED)

    def report_frame_error(self, error: BaseException) -> None:
        """Publish one native/presentation failure; never switch backends implicitly."""
        from src.audio.audio_events import AudioEventType
        payload = {'path': self._source, 'error_type': type(error).__name__,
                   'error': str(error)[:1024], 'user_message': 'WIC video rendering failed.'}
        logger.error('[WIC] FRAME_ERROR %s: %s', type(error).__name__, str(error)[:1024])
        self._publish(AudioEventType.VIDEO_PLAYBACK_ERROR, payload, require_ui_thread=True)

    def observe_progress(self) -> ClockObservation:
        """Guard the core pair across adapter replacement, shutdown and source change."""
        started = time.monotonic()
        try:
            with self._lock:
                core, source = self._core, self._source
                unavailable = self._closed or self._shutdown_requested
            if unavailable or core is None:
                return empty_clock(ClockOrigin.VIDEO_NATIVE, started, time.monotonic())
            result = core.observe_progress()
            if type(result) is not ClockObservation:
                raise TypeError("Invalid core progress observation")
            with self._lock:
                changed = (self._core is not core or self._source != source
                           or self._closed or self._shutdown_requested)
            return result.invalidate() if changed else result
        except QUERY_EXCEPTIONS as error:
            return empty_clock(ClockOrigin.VIDEO_NATIVE, started, time.monotonic(), error=error)


def create_media_engine_adapter(event_bus=None):
    """Factory function per creare un'istanza dell'adapter."""
    return IMFMediaEngineAdapter(event_bus)


_IMF_MEDIA_ENGINE_ADAPTER_ATTACHERS: tuple[Callable[[type["IMFMediaEngineAdapter"]], None], ...] = (
    _attach_imf_media_engine_adapter_threading_behavior,
    _attach_imf_media_engine_adapter_playback_behavior,
    _attach_imf_media_engine_adapter_events_behavior,
)


def attach_imf_media_engine_adapter_behavior(cls: type["IMFMediaEngineAdapter"]) -> None:
    """Attach IMFMediaEngineAdapter behavior in one central place.

    Edge cases:
        - Duplicate attachment during reloads can silently rebind methods.
        - Partial split imports can leave the adapter only partially patched.
        - Reordered installers can break threading, playback, or event wiring.
    """
    if getattr(cls, "_imf_media_engine_adapter_behavior_attached", False):
        return

    for installer in _IMF_MEDIA_ENGINE_ADAPTER_ATTACHERS:
        installer(cls)

    setattr(cls, "_imf_media_engine_adapter_behavior_attached", True)


attach_imf_media_engine_adapter_behavior(IMFMediaEngineAdapter)
