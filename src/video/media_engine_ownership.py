"""Explicit acquired engine references; raw compatibility utilities are not changed.

One native output owns one reference, even at an already-used address. Output storage
exists before the foreign call. Unconfirmed output is retained, not dereferenced.
Release uses ComPtr's exclusive claim; garbage collection never dispatches COM here.
Borrowed views still require the existing COM-thread/lifetime discipline.
"""
from __future__ import annotations

import ctypes
import logging
import sys
import threading
from typing import Literal
from .wic_renderer import WicRenderer
from .wic_pipeline import FramePipeline

from .component_base.com_helpers import ComPtr
from .component_base.definitions import IMFMediaEngine, IUnknown
from .component_base.utils import is_success, safe_release
from .media_engine_core_shared import MediaEngineError

logger = logging.getLogger(__name__)
CLEANUP_EXCEPTIONS = (AttributeError, OSError, ReferenceError, RuntimeError, TypeError, ValueError, ctypes.ArgumentError)

AcquisitionState = Literal['EMPTY', 'RECEIVING', 'ACQUIRED', 'NO_INTERFACE', 'UNCONFIRMED']


class EngineReference(ComPtr):
    """A preallocated output slot, confirmed acquisition and explicit-only release.

    Empty/failed output is not an acquisition. A non-null failed/aborted output is
    uncertain and must not be called. A successful equal-address output is a fresh
    ownership, independent of the legacy recent-address registry.
    """
    __slots__ = ('_output', '_acquisition')

    def __init__(self, interface: type[ctypes.Structure]) -> None:
        super().__init__(iface_type=interface)
        self._output = ctypes.c_void_p()
        self._acquisition: AcquisitionState = 'EMPTY'

    def receive(self) -> ctypes.POINTER(ctypes.c_void_p):
        """Reserve a native output before dispatch; concurrent/reentrant query refuses."""
        result = ctypes.pointer(self._output)
        with self._lock:
            if self._acquisition not in ('EMPTY', 'NO_INTERFACE'):
                raise MediaEngineError('Engine reference acquisition already reserved')
            self._acquisition = 'RECEIVING'
        return result

    def confirm(self, hresult: int) -> None:
        """Bind a returned successful output without post-acquisition allocation.

        Failed calls with an unexpected non-null result remain unconfirmed. A null
        successful base pointer is separately rejected by its acquisition caller.
        A previous confirmed or uncertain reference can never be overwritten.
        """
        succeeded = is_success(hresult)
        with self._lock:
            if self._acquisition != 'RECEIVING':
                raise MediaEngineError('No outstanding engine acquisition')
            if not succeeded or not self._output.value:
                self._acquisition = 'UNCONFIRMED' if self._output.value else 'NO_INTERFACE'
            else:
                self._ptr = self._output
                self._bump_gen_locked()
                self._acquisition = 'ACQUIRED'
        if self._acquisition == 'UNCONFIRMED':
            raise MediaEngineError('Failed acquisition returned unconfirmed engine output')

    @property
    def acquisition_state(self) -> AcquisitionState:
        with self._lock:
            return self._acquisition

    @property
    def unresolved(self) -> bool:
        with self._lock:
            pending = self._acquisition in ('RECEIVING', 'UNCONFIRMED')
            live = bool(self._ptr and self._ptr.value)
            attempt = self._release_attempt
            failed = attempt is not None and attempt.state.value != 'released'
        return pending or live or failed

    def borrow(self) -> object | None:
        """Return a view, not a new ownership or an AddRef lease."""
        with self._lock:
            if self._acquisition in ('RECEIVING', 'UNCONFIRMED'):
                raise MediaEngineError('Engine output has not been confirmed')
        return self.as_interface(self._iface_type)

    def release(self) -> int:
        """Never dispatch Release through an unconfirmed native output."""
        with self._lock:
            if self._acquisition in ('RECEIVING', 'UNCONFIRMED'):
                raise MediaEngineError('Unconfirmed engine acquisition requires review')
        return super().release()

    def __del__(self) -> None:
        """No COM dispatch from arbitrary GC/finalizer threads; explicit cleanup only."""
        # Unresolved native ownership may leak if its last Python owner is lost.
        # It must not be guessed/released on an unqualified apartment instead.


def borrow_engine(value: object | None) -> object | None:
    """Unwrap only provenance-bearing owners; legacy raw arguments stay raw."""
    return value.borrow() if isinstance(value, EngineReference) else value


def release_engine(value: object | None, name: str) -> None:
    """Check an owned disposition; retain the legacy guard for raw callers.

    A zero result alone is not success. Pre-dispatch failure, uncertain dispatch
    and incomplete output are errors, so the enclosing cleanup job retains them.
    """
    if not isinstance(value, EngineReference):
        safe_release(value, name)
        return
    value.release()
    if value.unresolved:
        receipt = value.release_snapshot
        error = None if receipt is None else receipt.error
        raise MediaEngineError('Owned engine release did not complete: ' + name) from error


class EngineResources:
    """One creation/rebind lifetime, retained on failed cleanup, fixed cardinality.

    Factory/attributes/notify retain their previous ownership rules. This scope
    changes only the separately acquired base and extension references. Cleanup
    claims before external effects, does not retry and preserves exact exceptions.
    """
    def __init__(self) -> None:
        self.engine = EngineReference(IMFMediaEngine)
        self.extension = EngineReference(IUnknown)
        self.wic_renderer: WicRenderer | None = None
        self.frame_pipeline: FramePipeline | None = None
        self.attributes: ComPtr | None = None
        self.factory: ComPtr | None = None
        self.notify_handler: object | None = None
        self.notify_iunknown: object | None = None
        self.creation_error: BaseException | None = None
        self.cleanup_error: BaseException | None = None
        self._cleanup_lock = threading.RLock()
        self._cleanup_started = False
        self._cleanup_returned = False

    @property
    def reclaimed(self) -> bool:
        return self._cleanup_returned and not self.engine.unresolved and not self.extension.unresolved

    def rollback_creation(self, core) -> None:
        """Keep failed cleanup and its primary cause reachable; never overwrite a newer owner."""
        try:
            self.cleanup_once(core, stop=True)
        except CLEANUP_EXCEPTIONS:
            logger.exception('Creation cleanup unresolved; no automatic retry.')
        finally:
            if self.reclaimed:
                with core._state_lock:
                    if core._creation_resources is self:
                        core._creation_resources = None

    def cleanup_once(self, core, *, stop: bool) -> None:
        with self._cleanup_lock:
            if self._cleanup_started:
                raise MediaEngineError('Engine lifetime cleanup was already claimed')
            self._cleanup_started = True
        returned = False
        try:
            if self.frame_pipeline is not None:
                self.frame_pipeline.close_native()
            elif self.wic_renderer is not None:
                self.wic_renderer.close()
            if stop:
                core._stop_engine_ptr_playback(self.engine.borrow())
                core._shutdown_engine_ptr(self.engine.borrow())
            release_engine(self.extension, 'media engine ex')
            release_engine(self.engine, 'media engine')
            safe_release(self.factory, 'factory')
            safe_release(self.attributes, 'attributes')
            returned = True
        finally:
            failure = None if returned else sys.exception()
            notified = False
            try:
                core._release_notify_iunknown(self.notify_iunknown)
                notified = True
            finally:
                self._cleanup_returned = returned and notified
                self.cleanup_error = failure if notified else sys.exception()


def owned_release_pair(core) -> tuple[object | None, object | None]:
    """Select actual acquired owners, rejecting mismatched borrowed fields.

    Caller holds the core lock. No foreign method executes. Legacy shells without
    an acquisition record retain their raw guarded contract; no ownership invented.
    """
    resources = getattr(core, '_engine_references', None)
    if resources is None:
        return core._media_engine, core._media_engine_ex
    for raw, owner in ((core._media_engine, resources.engine),
                       (core._media_engine_ex, resources.extension)):
        expected = owner.ptr
        actual = ctypes.cast(raw, ctypes.c_void_p).value if raw is not None else None
        if actual != (expected.value if expected is not None else None):
            raise MediaEngineError('Borrowed engine view no longer matches its owner')
    return resources.engine, resources.extension
