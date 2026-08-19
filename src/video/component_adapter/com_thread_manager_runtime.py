# -*- coding: utf-8 -*-
"""Runtime helpers for ``ComThreadManager``.

Edge cases and mitigations:
- The main module may be monkeypatched by tests or bootstrap code; helpers resolve
  the historical module dynamically so patched globals are observed at call time.
- Wake-up handles may become invalid during shutdown races; wake operations remain
  best-effort and log failures instead of turning cleanup into a hard crash.
- Response queues may already be full when rejecting or completing tasks; helpers
  preserve the original queue state and only emit diagnostic logging.
"""

from __future__ import annotations

import ctypes
import queue
import sys
from types import ModuleType
from typing import Callable, ContextManager, Protocol


class _ComTaskLike(Protocol):
    """Structural task contract consumed by the extracted runtime helpers."""

    name: str
    fn: Callable[..., object]
    args: tuple[object, ...]
    kwargs: dict[str, object]
    response_q: queue.Queue[object]


class _ComThreadManagerLike(Protocol):
    """Minimal manager state required by this module without importing its owner."""

    thread_name: str
    _lock: ContextManager[object]
    _com_thread_id: int | None
    _com_thread_wakeup: object | None
    _task_queue: queue.Queue[_ComTaskLike]


NONFATAL_COM_TASK_ERROR_MARKERS = (
    'IMFMediaEngineEx::GetNumberOfStreams failed: hr=0x80004005',
    'IMFMediaEngineEx::SetStreamSelection failed: hr=0x80004005',
    'IMFMediaEngineEx::ApplyStreamSelections failed: hr=0x80004005',
)


def _is_nonfatal_com_task_error(exc: BaseException) -> bool:
    """Return whether a COM task error should be downgraded to debug logging.

    Edge cases and mitigations:
    - Media Foundation can return E_FAIL during source warm-up even when playback will continue.
    - Audio-stream priming may be unsupported for some containers and should keep default engine behavior.
    - Unexpected exceptions must remain error-level so real COM regressions stay visible.
    """
    message = str(exc)
    return any(marker in message for marker in NONFATAL_COM_TASK_ERROR_MARKERS)


def _home_module() -> ModuleType:
    """Return the already-loaded public owner module without importing it.

    Runtime helpers are installed by ``com_thread_manager`` itself, so a helper
    invocation without the owner module loaded indicates an invalid internal call.
    Avoiding an import here removes the reverse runtime dependency that previously
    made the extracted helper module capable of importing its owner.
    """
    module_name = "src.video.component_adapter.com_thread_manager"
    module = sys.modules.get(module_name)
    if module is None:
        raise RuntimeError(f"{module_name} must be loaded before COM runtime helpers are used")
    return module


def _is_on_com_thread(self: _ComThreadManagerLike) -> bool:
    """Return ``True`` when the current caller is the managed COM thread."""
    home = _home_module()
    with self._lock:
        tid = self._com_thread_id
    if tid is None:
        return False
    try:
        return int(home._GetCurrentThreadId()) == tid
    except home.COM_WAIT_EXCEPTIONS:
        return False


def _wake_com_thread(self: _ComThreadManagerLike) -> None:
    """Best-effort wake-up for the COM thread.

    The wake-up handle is snapshotted under lock to avoid races with shutdown.
    """
    home = _home_module()
    with self._lock:
        handle = self._com_thread_wakeup

    if not handle:
        return

    try:
        ok = bool(home._SetEvent(handle))
        if not ok:
            err = ctypes.get_last_error()
            home.logger.debug(
                "[%s] _SetEvent(wakeup) returned FALSE (last_error=%s).",
                self.thread_name,
                err,
            )
    except home.COM_WAIT_EXCEPTIONS:
        home.logger.debug("[%s] _SetEvent(wakeup) fallito (best effort).", self.thread_name, exc_info=True)


def _reject_pending_tasks(self: _ComThreadManagerLike, reason: str) -> None:
    """Drain queued tasks and unblock synchronous waiters with a rejection error."""
    home = _home_module()
    rejected = 0
    while True:
        try:
            task = self._task_queue.get_nowait()
        except queue.Empty:
            break

        rejected += 1
        try:
            task.response_q.put_nowait(home.MediaEngineError(f"Task COM '{task.name}' rifiutato: {reason}"))
        except home.QUEUE_RESPONSE_EXCEPTIONS as error:
            home.logger.debug(
                "[%s] Response queue full while rejecting task '%s': %s",
                self.thread_name,
                task.name,
                error,
                exc_info=True,
            )

    if rejected:
        home.logger.debug("[%s] Rifiutati %d task COM pendenti (%s).", self.thread_name, rejected, reason)


def _drain_tasks_com_thread(self: _ComThreadManagerLike) -> None:
    """Execute every task currently queued for the COM worker."""
    home = _home_module()
    while True:
        try:
            task = self._task_queue.get_nowait()
        except queue.Empty:
            break

        try:
            result = task.fn(*task.args, **task.kwargs)
            try:
                task.response_q.put_nowait(result)
            except home.QUEUE_RESPONSE_EXCEPTIONS:
                home.logger.debug(
                    "[%s] Impossibile pubblicare il risultato del task '%s' sulla response_q.",
                    self.thread_name,
                    task.name,
                    exc_info=True,
                )
        except (KeyboardInterrupt, SystemExit):
            raise
        except home.COM_TASK_EXECUTION_EXCEPTIONS as exc:
            if _is_nonfatal_com_task_error(exc):
                home.logger.debug(
                    "[%s] Esecuzione del task '%s' fallita in modo non fatale: %s",
                    self.thread_name,
                    task.name,
                    exc,
                    exc_info=True,
                )
            else:
                home.logger.error(
                    "[%s] Esecuzione del task '%s' fallita: %s",
                    self.thread_name,
                    task.name,
                    exc,
                    exc_info=True,
                )
            try:
                task.response_q.put_nowait(exc)
            except home.QUEUE_RESPONSE_EXCEPTIONS:
                home.logger.debug(
                    "[%s] Impossibile pubblicare l'eccezione del task '%s' sulla response_q.",
                    self.thread_name,
                    task.name,
                    exc_info=True,
                )
