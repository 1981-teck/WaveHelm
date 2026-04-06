# -*- coding: utf-8 -*-
"""com_thread_manager.py
Gestisce il ciclo di vita e l'esecuzione di task su un thread COM dedicato (STA).

La startup principale dell'app configura comtypes con policy MTA per eventuali
inizializzazioni implicite sul main thread; il backend video continua invece a
inizializzare COM esplicitamente in STA su questo thread dedicato.
"""

from __future__ import annotations

import ctypes
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional, Tuple

from src.video.mf_base import logger
from src.video.component_base.definitions import (
    COINIT_APARTMENTTHREADED,
    COWAIT_DISPATCH_ALL,
    MFSTARTUP_FULL,
    HRESULT,
    _hr_ok,
    _ole32 as _definitions_ole32,
    wintypes,
    byref,
)
from src.video.component_base.definitions_runtime import _bind_function, _load_windll
from src.video.component_base.mf_helpers import MFStartup, MFShutdown
from src.video.component_adapter.com_thread_manager_runtime import (
    _drain_tasks_com_thread as _runtime_drain_tasks_com_thread,
    _is_on_com_thread as _runtime_is_on_com_thread,
    _reject_pending_tasks as _runtime_reject_pending_tasks,
    _wake_com_thread as _runtime_wake_com_thread,
)


class _PatchableDLLProxy:
    """Mutable proxy around DLL bindings that may otherwise be slot-only.

    Test suites monkeypatch COM entrypoints such as ``CoInitializeEx`` directly on
    ``_ole32``. When the historical loader degrades to a slot-based missing-DLL
    proxy outside Windows, ``monkeypatch.setattr(..., raising=False)`` cannot add
    temporary attributes. This wrapper preserves the historical attribute access
    while giving tests a normal ``__dict__``-backed object to patch.
    """

    def __init__(self, dll: Any):
        self._dll = dll

    def __getattr__(self, name: str):
        return getattr(self._dll, name)

    def __bool__(self) -> bool:
        return bool(self._dll)

    def __repr__(self) -> str:
        return f"<PatchableDLLProxy dll={self._dll!r}>"


_ole32 = _definitions_ole32 if hasattr(_definitions_ole32, "__dict__") else _PatchableDLLProxy(_definitions_ole32)

COM_WAIT_EXCEPTIONS = (AttributeError, OSError, RuntimeError, TypeError, ValueError, ctypes.ArgumentError)
QUEUE_RESPONSE_EXCEPTIONS = (queue.Full,)
# Intentional boundary: COM tasks may raise arbitrary application exceptions.
# We log and forward the original object to the waiting caller unchanged.
COM_TASK_EXECUTION_EXCEPTIONS = (Exception,)

# Bind CoWaitForMultipleHandles (se disponibile) - usato per pumping STA + dispatch.
# In alcuni contesti può non essere esportata o può fallire a runtime: in tal caso si effettua fallback.
try:
    _CoWaitForMultipleHandles = _ole32.CoWaitForMultipleHandles
    _CoWaitForMultipleHandles.argtypes = [
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.ULONG,
        ctypes.POINTER(wintypes.HANDLE),
        ctypes.POINTER(wintypes.DWORD),
    ]
    _CoWaitForMultipleHandles.restype = HRESULT
except COM_WAIT_EXCEPTIONS:
    _CoWaitForMultipleHandles = None

# Kernel32 helpers
# Caricamento tollerante in ambienti dove WinDLL non e' disponibile a import-time.
# Su Windows reale il loader rimane fail-fast; fuori Windows restituisce binding
# segnaposto che falliscono solo all'uso, permettendo import/collect dei test.
_kernel32 = _load_windll("kernel32")

_CreateEventW = _bind_function(
    _kernel32,
    "kernel32",
    "CreateEventW",
    argtypes=[ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR],
    restype=wintypes.HANDLE,
)

_SetEvent = _bind_function(
    _kernel32,
    "kernel32",
    "SetEvent",
    argtypes=[wintypes.HANDLE],
    restype=wintypes.BOOL,
)

_CloseHandle = _bind_function(
    _kernel32,
    "kernel32",
    "CloseHandle",
    argtypes=[wintypes.HANDLE],
    restype=wintypes.BOOL,
)

_WaitForSingleObject = _bind_function(
    _kernel32,
    "kernel32",
    "WaitForSingleObject",
    argtypes=[wintypes.HANDLE, wintypes.DWORD],
    restype=wintypes.DWORD,
)

_GetCurrentThreadId = _bind_function(
    _kernel32,
    "kernel32",
    "GetCurrentThreadId",
    argtypes=[],
    restype=wintypes.DWORD,
)

RPC_E_CALL_REJECTED = 0x80010001
RPC_S_CALLPENDING = 0x80010115


class MediaEngineError(RuntimeError):
    """Errore generico di esecuzione sul thread COM."""


class MediaEngineTimeoutError(TimeoutError):
    """Timeout durante l'attesa del risultato di un task sul thread COM."""


COM_THREAD_INIT_EXCEPTIONS = (
    MediaEngineError,
    AttributeError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
    ctypes.ArgumentError,
)


@dataclass(frozen=True)
class _ComTask:
    name: str
    fn: Callable[..., Any]
    args: Tuple[Any, ...]
    kwargs: dict
    response_q: "queue.Queue[Any]"


class ComThreadManager:
    """
    Gestisce un thread COM (STA) dedicato.

    Note:
    - Usa un evento di wake-up *auto-reset* per evitare loop busy nel caso di SetEvent ripetuti.
    - I task sincroni usano una response_q con maxsize=1 e timeout configurabile.
    """

    def __init__(self, thread_name: str = "COMThread", com_task_timeout: float = 10.0):
        self.thread_name = thread_name
        self.com_task_timeout = float(com_task_timeout)

        self._lock = threading.RLock()
        self._shutdown_requested = False

        self._com_thread: Optional[threading.Thread] = None
        self._com_thread_id: Optional[int] = None

        self._com_thread_id_event = threading.Event()
        self._com_ready_event = threading.Event()
        self._com_init_error: Optional[Exception] = None

        self._stop_event = threading.Event()
        self._task_queue: "queue.Queue[_ComTask]" = queue.Queue()

        self._com_thread_wakeup: Optional[wintypes.HANDLE] = None

    # -------------------------
    # Public API
    # -------------------------

    def start(self) -> None:
        """Avvia il thread COM e attende la sua inizializzazione (idempotente)."""
        with self._lock:
            if self._shutdown_requested:
                logger.warning(
                    "[%s] start() ignorato: shutdown già richiesto; non riavvio il thread.",
                    self.thread_name,
                )
                return

            if self._com_thread and self._com_thread.is_alive():
                return

            # reset stato start
            self._stop_event.clear()
            self._com_thread_id_event.clear()
            self._com_ready_event.clear()
            self._com_init_error = None
            self._com_thread_id = None

            self._com_thread = threading.Thread(
                target=self._com_thread_main,
                name=self.thread_name,
                daemon=True,
            )
            self._com_thread.start()

        # Attende almeno l'assegnazione del thread id
        if not self._com_thread_id_event.wait(timeout=5.0):
            self._stop_event.set()
            if self._com_thread and self._com_thread.is_alive():
                self._com_thread.join(timeout=2.0)
            raise MediaEngineError("Il thread COM non è partito entro il timeout (thread id non disponibile).")

        # Attende readiness completa (COM init + MFStartup + wake event)
        if not self._com_ready_event.wait(timeout=5.0):
            self._stop_event.set()
            if self._com_thread and self._com_thread.is_alive():
                self._wake_com_thread()
                self._com_thread.join(timeout=2.0)
            raise MediaEngineError("Il thread COM non è pronto entro il timeout (inizializzazione incompleta).")

        if self._com_init_error is not None:
            raise MediaEngineError(f"Inizializzazione thread COM fallita: {self._com_init_error}") from self._com_init_error

        if self._com_thread_id is None:
            raise MediaEngineError("L'ID del thread COM non è disponibile dopo l'avvio.")

        logger.info("[%s] Thread avviato con successo (id=%s)", self.thread_name, self._com_thread_id)

    def shutdown(self) -> None:
        """Arresta il thread COM in modo pulito (idempotente)."""
        with self._lock:
            if self._shutdown_requested:
                return
            self._shutdown_requested = True

        self._stop_event.set()
        self._wake_com_thread()

        # Rifiuta eventuali task pendenti per sbloccare chi attende risultati
        self._reject_pending_tasks("shutdown requested")

        t = self._com_thread
        if t and t.is_alive():
            logger.info("[%s] In attesa della terminazione del thread...", self.thread_name)
            t.join(timeout=5.0)
            if t.is_alive():
                logger.warning("[%s] Il thread non è terminato correttamente.", self.thread_name)
            else:
                logger.info("[%s] Thread terminato.", self.thread_name)

        with self._lock:
            self._com_thread = None
            self._com_thread_id = None
            self._com_thread_id_event.clear()
            self._com_ready_event.clear()
            self._com_thread_wakeup = None

    def post_to_com_thread(self, name: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        """Invia una funzione al thread COM per esecuzione asincrona (fire-and-forget)."""
        if self._stop_event.is_set() or self._shutdown_requested:
            logger.debug("[%s] Ignoro task COM '%s': thread in shutdown.", self.thread_name, name)
            return

        t = self._com_thread
        if not t or not t.is_alive():
            logger.warning("[%s] Thread COM non attivo, impossibile inviare il task '%s'", self.thread_name, name)
            return

        dummy_response_q: "queue.Queue[Any]" = queue.Queue(maxsize=1)
        self._task_queue.put(_ComTask(name=name, fn=fn, args=args, kwargs=kwargs, response_q=dummy_response_q))
        self._wake_com_thread()

    def call_on_com_thread(self, name: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """
        Esegue una funzione sul thread COM, attendendo il risultato.
        Se chiamato dallo stesso thread COM, esegue la funzione direttamente.
        """
        if self._is_on_com_thread():
            return fn(*args, **kwargs)

        if self._stop_event.is_set() or self._shutdown_requested:
            raise MediaEngineError("Il thread COM sta chiudendo; impossibile eseguire task sincroni.")

        t = self._com_thread
        if not t or not t.is_alive():
            raise MediaEngineError("Il thread COM non è attivo.")

        response_q: "queue.Queue[Any]" = queue.Queue(maxsize=1)
        self._task_queue.put(_ComTask(name=name, fn=fn, args=args, kwargs=kwargs, response_q=response_q))
        self._wake_com_thread()

        t0 = time.perf_counter()
        try:
            result = response_q.get(timeout=self.com_task_timeout)
        except queue.Empty as exc:
            raise MediaEngineTimeoutError(
                f"Task COM '{name}' ha superato il timeout di {self.com_task_timeout:.3f}s"
            ) from exc

        dt = time.perf_counter() - t0
        if dt >= 0.250:
            logger.info("[%s] COM task '%s' completed in %.3fs", self.thread_name, name, dt)

        if isinstance(result, Exception):
            raise result from result
        return result

    # -------------------------
    # Internals
    # -------------------------

    def _com_thread_main(self) -> None:
        """Loop principale del thread. Inizializza e pulisce le risorse COM."""
        cleanup_callbacks = []
        try:
            # Pubblica thread id ASAP (per evitare deadlock su start())
            with self._lock:
                self._com_thread_id = int(_GetCurrentThreadId())
            self._com_thread_id_event.set()

            hr = int(_ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED))
            if not _hr_ok(hr):
                raise MediaEngineError(f"CoInitializeEx fallito hr=0x{hr:08X}")
            cleanup_callbacks.append(_ole32.CoUninitialize)

            MFStartup(MFSTARTUP_FULL)
            logger.info("[MF] MFStartup ok (flags=0)")
            cleanup_callbacks.append(MFShutdown)

            # Evento wakeup:
            # - auto-reset (bManualReset=False) per evitare loop busy quando viene segnalato ripetutamente.
            h_wakeup = _CreateEventW(None, False, False, None)
            if not h_wakeup:
                raise MediaEngineError("CreateEventW fallito (wakeup)")

            with self._lock:
                self._com_thread_wakeup = h_wakeup

            def _close_wakeup_handle(handle=h_wakeup) -> None:
                # Evita race: azzera prima il riferimento globale, poi chiude l'handle.
                with self._lock:
                    if self._com_thread_wakeup == handle:
                        self._com_thread_wakeup = None
                try:
                    _CloseHandle(handle)
                except COM_WAIT_EXCEPTIONS:
                    logger.debug("[%s] CloseHandle(wakeup) fallito (best effort).", self.thread_name, exc_info=True)

            cleanup_callbacks.append(_close_wakeup_handle)

            self._com_ready_event.set()
            self._com_thread_loop()

        except (KeyboardInterrupt, SystemExit) as exc:
            self._com_init_error = RuntimeError(f'Inizializzazione thread COM interrotta: {exc}')
            logger.error("[%s] Inizializzazione thread COM interrotta: %s", self.thread_name, exc, exc_info=True)
            self._com_ready_event.set()  # sblocca start()
            raise
        except COM_THREAD_INIT_EXCEPTIONS as exc:
            self._com_init_error = exc
            logger.error("[%s] Inizializzazione thread COM fallita: %s", self.thread_name, exc, exc_info=True)
            self._com_ready_event.set()  # sblocca start()
        finally:
            # Best effort: pulizia in ordine inverso
            for cb in reversed(cleanup_callbacks):
                try:
                    cb()
                except COM_WAIT_EXCEPTIONS:
                    logger.debug("[%s] Cleanup callback fallita (best effort).", self.thread_name, exc_info=True)

    def _com_thread_loop(self) -> None:
        """Esegue pumping STA + drenaggio task finché non viene richiesto lo stop."""
        use_cowait = _CoWaitForMultipleHandles is not None
        cowait_failures = 0

        if use_cowait:
            logger.info("[%s] Loop STA: CoWaitForMultipleHandles abilitato (pumping COM).", self.thread_name)
        else:
            logger.info("[%s] Loop STA: CoWaitForMultipleHandles non disponibile; uso WaitForSingleObject.", self.thread_name)

        while not self._stop_event.is_set():
            # Drena task pendenti
            self._drain_tasks_com_thread()

            with self._lock:
                h = self._com_thread_wakeup
            if not h:
                time.sleep(0.01)
                continue

            # Attende wakeup o timeout breve
            if use_cowait and _CoWaitForMultipleHandles:
                fallback = False
                hr = 0
                hr_u32 = 0

                try:
                    handles = (wintypes.HANDLE * 1)(h)
                    index = wintypes.DWORD(0)
                    hr = int(_CoWaitForMultipleHandles(COWAIT_DISPATCH_ALL, 50, 1, handles, byref(index)))
                except COM_WAIT_EXCEPTIONS as exc:
                    fallback = True
                    hr = 0x80004005  # E_FAIL (marker)
                    logger.warning(
                        "[%s] CoWaitForMultipleHandles ha generato eccezione (%s); fallback a WaitForSingleObject",
                        self.thread_name,
                        exc,
                        exc_info=True,
                    )

                hr_u32 = hr & 0xFFFFFFFF

                if hr_u32 == RPC_S_CALLPENDING:
                    cowait_failures = 0
                    continue
                if hr_u32 == RPC_E_CALL_REJECTED:
                    fallback = True
                    logger.warning(
                        "[%s] CoWaitForMultipleHandles hr=0x%08X; fallback a WaitForSingleObject",
                        self.thread_name,
                        hr_u32,
                    )
                elif (hr_u32 & 0x80000000) != 0:
                    # Errore HRESULT (non timeout): fallback best-effort.
                    fallback = True
                    logger.debug(
                        "[%s] CoWaitForMultipleHandles hr=0x%08X; uso WaitForSingleObject (best effort).",
                        self.thread_name,
                        hr_u32,
                    )

                if fallback:
                    cowait_failures += 1
                    if cowait_failures >= 3:
                        logger.warning(
                            "[%s] Disabilito CoWaitForMultipleHandles dopo errori ripetuti; continuo con WaitForSingleObject.",
                            self.thread_name,
                        )
                        use_cowait = False
                    _WaitForSingleObject(h, 50)
                else:
                    cowait_failures = 0
            else:
                _WaitForSingleObject(h, 50)

        # Drenaggio finale
        self._drain_tasks_com_thread()



_COM_THREAD_MANAGER_RUNTIME_METHODS: tuple[tuple[str, Callable[..., Any]], ...] = (
    ("_is_on_com_thread", _runtime_is_on_com_thread),
    ("_wake_com_thread", _runtime_wake_com_thread),
    ("_reject_pending_tasks", _runtime_reject_pending_tasks),
    ("_drain_tasks_com_thread", _runtime_drain_tasks_com_thread),
)


def install_com_thread_manager_runtime_behavior(
    manager_cls: type[ComThreadManager],
) -> type[ComThreadManager]:
    """Install runtime helpers while keeping the historical module as the public entrypoint.

    Edge cases considered:
    - Runtime monkeypatches still target the historical module-level helper names.
    - Empty or duplicate binding names would silently shadow worker methods.
    - Re-import or reload should not repeat runtime wiring without an idempotent guard.
    """
    if not isinstance(manager_cls, type):
        raise TypeError("COM thread manager runtime target must be a class")
    if getattr(manager_cls, '_com_thread_manager_runtime_behavior_attached', False):
        return manager_cls

    seen_names: set[str] = set()
    for attribute_name, method in _COM_THREAD_MANAGER_RUNTIME_METHODS:
        if not attribute_name:
            raise TypeError("COM thread manager binding name cannot be empty")
        if attribute_name in seen_names:
            raise TypeError(f"Duplicate COM thread manager binding: {attribute_name}")
        if not callable(method):
            raise TypeError(f"Invalid COM thread manager binding: {attribute_name}")
        setattr(manager_cls, attribute_name, method)
        seen_names.add(attribute_name)

    setattr(manager_cls, '_com_thread_manager_runtime_behavior_attached', True)
    return manager_cls


_COM_THREAD_MANAGER_ATTACHERS: tuple[tuple[str, Callable[..., Any]], ...] = (
    ("runtime", install_com_thread_manager_runtime_behavior),
)


def install_com_thread_manager_behavior(
    manager_cls: type[ComThreadManager],
) -> type[ComThreadManager]:
    """Install extracted behavior blocks for ``ComThreadManager``."""
    if not isinstance(manager_cls, type):
        raise TypeError("COM thread manager target must be a class")
    if getattr(manager_cls, '_com_thread_manager_behavior_attached', False):
        return manager_cls

    for group_name, installer in _COM_THREAD_MANAGER_ATTACHERS:
        if not callable(installer):
            raise TypeError(f"Invalid COM thread manager installer: {group_name}")
        installer(manager_cls)

    setattr(manager_cls, '_com_thread_manager_behavior_attached', True)
    return manager_cls


def attach_com_thread_manager_runtime_behavior() -> None:
    """Backward-compatible shim that delegates to the neutral runtime installer."""
    install_com_thread_manager_runtime_behavior(ComThreadManager)


def attach_com_thread_manager_behavior() -> None:
    """Backward-compatible shim that delegates to the neutral installer."""
    install_com_thread_manager_behavior(ComThreadManager)


install_com_thread_manager_behavior(ComThreadManager)


com_thread_manager = ComThreadManager()
