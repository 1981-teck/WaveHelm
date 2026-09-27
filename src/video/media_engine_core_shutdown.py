from __future__ import annotations

from .media_engine_seek import invalidate_seek
from .wic_pipeline import FramePipeline
from .media_engine_ownership import EngineReference, borrow_engine, release_engine, owned_release_pair

import ctypes
import logging
import math
import sys
import threading
from typing import Callable, Literal, Optional, NamedTuple

from src.video.component_base.com_helpers import ComPtr
from src.video.component_base.definitions import IMFMediaEngine

logger = logging.getLogger(__name__)

MEDIA_ENGINE_CORE_SHUTDOWN_SYNC_EXCEPTIONS = (AttributeError, OSError, RuntimeError, TimeoutError, TypeError, ValueError)
MEDIA_ENGINE_CORE_SHUTDOWN_ASYNC_EXCEPTIONS = (AttributeError, OSError, RuntimeError, TimeoutError, TypeError, ValueError)


class _ShutdownResources(NamedTuple):
    """Separate acquired references; equal addresses do not merge ownership."""

    engine: EngineReference | ctypes.POINTER(IMFMediaEngine) | None
    engine_ex: object | None
    factory: ComPtr | None
    attributes: ComPtr | None
    notify_iunknown: object | None
    notify_handler: object | None
    pipeline: FramePipeline | None = None


AdmissionState = Literal["NOT_SUBMITTED", "SUBMITTING", "UNCONFIRMED", "ACCEPTED", "REJECTED", "INCONSISTENT"]
ExecutionState = Literal["PREPARED", "RUNNING", "RETURNED", "FAILED_UNCERTAIN", "REJECTED"]


class ShutdownSnapshot(NamedTuple):
    """Observation, not a native release certificate; errors retain their identity."""

    admission: AdmissionState
    execution: ExecutionState
    wait_expired: bool
    error: BaseException | None
    transport_error: BaseException | None
    reply_seen: bool


class _ShutdownJob:
    """One detached bundle, one execution claim, no automatic destructive retry.

    Timeout leaves the original callback eligible. Reentry/overlap cannot claim
    twice. A failed callback is terminal and retains its uncertain resources.
    No foreign call, wait, or diagnostic logging occurs under the claim lock.
    """

    def __init__(self, callback: Callable[..., None]) -> None:
        self._lock = threading.RLock()
        self._done = threading.Event()
        self._callback = callback
        self._resources: _ShutdownResources | None = None
        self._admission: AdmissionState = "NOT_SUBMITTED"
        self._execution: ExecutionState = "PREPARED"
        self._wait_expired = False
        self._error: BaseException | None = None
        self._transport_error: BaseException | None = None
        self._reply_seen = False

    def snapshot(self) -> ShutdownSnapshot:
        with self._lock:
            return ShutdownSnapshot(self._admission, self._execution,
                                    self._wait_expired, self._error,
                                    self._transport_error, self._reply_seen)

    def begin_submission(self) -> bool:
        """Reserve the submission slot before invoking any transport."""
        with self._lock:
            if self._admission != "NOT_SUBMITTED":
                return False
            self._admission = "SUBMITTING"
            return True

    def set_admission(self, accepted: bool) -> None:
        """False revokes an unstarted callback, never rewrites observed execution."""
        if type(accepted) is not bool:
            raise TypeError("Shutdown admission must be an explicit bool")
        rejected = False
        with self._lock:
            contradictory = not accepted and self._execution in ("RUNNING", "RETURNED", "FAILED_UNCERTAIN")
            self._admission = "INCONSISTENT" if contradictory else ("ACCEPTED" if accepted else "REJECTED")
            if not accepted and self._execution == "PREPARED":
                self._execution = "REJECTED"
                rejected = True
        if rejected:
            self._done.set()
        if contradictory:
            raise RuntimeError("Cleanup executed despite negative admission")

    def finish_submission(self, error: BaseException | None) -> None:
        """An enqueue/wake exception is not evidence of cancellation."""
        with self._lock:
            if error is not None:
                self._transport_error = error
            if self._admission == "SUBMITTING":
                self._admission = "UNCONFIRMED"

    def put_nowait(self, result: object, /) -> None:
        """Record worker rejection separately from the cleanup callback's outcome.

        A bare successful reply cannot certify execution. Late/duplicate replies
        cannot restore a claimed bundle or overwrite its original failure.
        """
        rejected = False
        with self._lock:
            if self._reply_seen:
                return
            self._reply_seen = True
            if isinstance(result, BaseException):
                self._transport_error = result
                if self._execution == "PREPARED":
                    self._execution = "REJECTED"
                    self._error = result
                    rejected = True
        if rejected:
            self._done.set()

    def wait(self, timeout: float) -> bool:
        """A finite expired wait observes incompletion, not native cancellation."""
        if not math.isfinite(timeout) or not 0 <= timeout <= threading.TIMEOUT_MAX:
            raise ValueError("Shutdown wait must be finite and nonnegative")
        completed = self._done.wait(timeout)
        if not completed:
            with self._lock:
                self._wait_expired = True
        return completed

    def run(self) -> None:
        with self._lock:
            if self._execution != "PREPARED":
                return
            resources = self._resources
            if resources is None:
                raise RuntimeError("Shutdown job has no detached resources")
            self._execution = "RUNNING"
        returned = False
        try:
            self._callback(*resources)
            returned = True
        finally:
            error = None if returned else sys.exception()
            with self._lock:
                self._execution = "RETURNED" if returned else "FAILED_UNCERTAIN"
                self._error = error
                if returned:
                    self._resources = None
            self._done.set()


def _detach_shutdown_resources(self) -> _ShutdownJob | None:
    """Publish the owner before detaching; allocation failure preserves the core.

    Duplicate callers receive no new job. Cache disposal remains outside the
    core lock. A rejected or failed job stays attached for explicit observation.
    """
    with self._state_lock:
        if self._shutdown_requested:
            return None
    job = _ShutdownJob(self._shutdown_and_release_on_com_thread)
    new_cache = {}
    with self._state_lock:
        if self._shutdown_requested:
            return None
        engine, extension = owned_release_pair(self)
        resources = _ShutdownResources(engine, extension,
                                       self._factory, self._attributes,
                                       self._notify_iunknown, self._notify_handler,
                                       getattr(self, '_wic_pipeline', None))
        job._resources = resources
        self._shutdown_job = job
        self._engine_references = None
        self._wic_pipeline = None
        self._shutdown_requested = True
        self._media_engine = self._factory = self._media_engine_ex = None
        self._attributes = self._notify_iunknown = self._notify_handler = None
        old_cache = self._vtable_call_cache
        self._vtable_call_cache = new_cache
    old_cache.clear()
    return job


def get_shutdown_snapshot(self) -> ShutdownSnapshot | None:
    """Read the retained bundle disposition without scheduling or releasing."""
    with self._state_lock:
        job = getattr(self, "_shutdown_job", None)
    return None if job is None else job.snapshot()


def _dispatch_shutdown(adapter: object, job: _ShutdownJob) -> None:
    """Use tracked cleanup when available; legacy sync is one unconfirmed attempt.

    Legacy adapters cannot provide an admission receipt. Their synchronous API
    must honor its COM-thread contract; it is never followed by an async/local
    fallback. An unknown outcome remains visible instead of becoming completion.
    """
    if not job.begin_submission():
        return
    returned = False
    try:
        tracked = getattr(adapter, "call_shutdown_on_com_thread", None)
        if callable(tracked):
            tracked("shutdown", job.run, job)
        else:
            legacy = getattr(adapter, "call_on_com_thread")
            legacy("shutdown", job.run)
        returned = True
    finally:
        job.finish_submission(None if returned else sys.exception())


def shutdown(self) -> None:
    """Detach once and submit once; timeout never creates a replacement cleanup."""
    pipeline = getattr(self, '_wic_pipeline', None)
    if pipeline is not None:
        pipeline.stop()
    invalidate_seek(self, 'Shutdown invalidated prior seek intent')
    job = _detach_shutdown_resources(self)
    if job is None:
        return
    logger.info("[MediaEngineCore] Inizio shutdown.")
    adapter = self._adapter_ref()
    if adapter is None:
        job.set_admission(False)
        logger.error("[MediaEngineCore] Shutdown not admitted: no adapter; resources retained.")
        return
    try:
        _dispatch_shutdown(adapter, job)
    except MEDIA_ENGINE_CORE_SHUTDOWN_SYNC_EXCEPTIONS:
        logger.exception("[MediaEngineCore] Shutdown completion unconfirmed; no destructive retry.")
    snapshot = job.snapshot()
    if snapshot.execution == "RETURNED":
        logger.info("[MediaEngineCore] Shutdown callback returned on COM thread.")
    else:
        logger.warning("[MediaEngineCore] Shutdown incomplete: admission=%s execution=%s wait_expired=%s",
                       snapshot.admission, snapshot.execution, snapshot.wait_expired)


def _shutdown_and_release_on_com_thread(
    self,
    media_engine: EngineReference | ctypes.POINTER(IMFMediaEngine) | None,
    media_engine_ex: object | None,
    factory: Optional[ComPtr],
    attributes: Optional[ComPtr],
    notify_iunknown: object | None,
    notify_handler: object | None,
    pipeline: FramePipeline | None = None,
) -> None:
    try:
        from src.video.component_base.utils import safe_release
        if pipeline is not None:
            pipeline.close_native()
            logger.info("[WIC] CLOSED %s", pipeline.snapshot())
        engine_view = borrow_engine(media_engine)
        self._stop_engine_ptr_playback(engine_view)
        self._shutdown_engine_ptr(engine_view)
        release_engine(media_engine_ex, "media engine ex")
        release_engine(media_engine, "media engine")
        safe_release(factory, "factory")
        safe_release(attributes, "attributes")
    finally:
        _ = notify_handler
        self._release_notify_iunknown(notify_iunknown)


def _shutdown_and_release_local(
    self,
    media_engine: EngineReference | ctypes.POINTER(IMFMediaEngine) | None,
    media_engine_ex: object | None,
    factory: Optional[ComPtr],
    attributes: Optional[ComPtr],
    notify_iunknown: object | None,
    notify_handler: object | None,
    pipeline: FramePipeline | None = None,
) -> None:
    try:
        from src.video.component_base.utils import safe_release
        if pipeline is not None:
            pipeline.close_native()
            logger.info("[WIC] CLOSED %s", pipeline.snapshot())
        engine_view = borrow_engine(media_engine)
        self._stop_engine_ptr_playback(engine_view)
        self._shutdown_engine_ptr(engine_view)
        release_engine(media_engine_ex, "media engine ex")
        release_engine(media_engine, "media engine")
        safe_release(factory, "factory")
        safe_release(attributes, "attributes")
    finally:
        _ = notify_handler
        self._release_notify_iunknown(notify_iunknown)


_MEDIA_ENGINE_CORE_SHUTDOWN_METHODS = (
    ("shutdown", shutdown),
    ("get_shutdown_snapshot", get_shutdown_snapshot),
    ("_shutdown_and_release_on_com_thread", _shutdown_and_release_on_com_thread),
    ("_shutdown_and_release_local", _shutdown_and_release_local),
)


def install_media_engine_core_shutdown_behavior(cls) -> None:
    """Install MediaEngineCore shutdown behavior on the central class.

    Edge cases:
        - Re-running the installer during reloads can silently override shutdown hooks.
        - Partial split imports can leave the coordinator with incomplete shutdown wiring.
        - Legacy leaf entrypoints can diverge from the central shutdown contract over time.
    """
    if getattr(cls, "_media_engine_core_shutdown_behavior_attached", False):
        return

    for name, method in _MEDIA_ENGINE_CORE_SHUTDOWN_METHODS:
        setattr(cls, name, method)

    setattr(cls, "_media_engine_core_shutdown_behavior_attached", True)


def attach_media_engine_core_shutdown_behavior(cls) -> None:
    """Compatibility shim for historical attach_* imports.

    Prefer install_media_engine_core_shutdown_behavior() from the central coordinator path.
    """
    install_media_engine_core_shutdown_behavior(cls)
