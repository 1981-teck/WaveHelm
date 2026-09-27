"""COM worker lifecycle and dispatch APIs.

Extracted from the public owner. Native symbols/errors remain resolved through
that loaded owner so documented test patch points retain their meaning. Existing
synchronous/fire-and-forget contracts remain; tracked submission is explicit.
"""
from __future__ import annotations
import queue
import time
from typing import Any, Callable
from .com_thread_manager_runtime import _home_module, ResponsePort


class ComThreadApi:
    def start(self) -> None:
        """Avvia il thread COM e attende la sua inizializzazione (idempotente)."""
        home = _home_module()
        with self._lock:
            if self._shutdown_requested:
                home.logger.warning('[%s] start() ignorato: shutdown già richiesto; non riavvio il thread.', self.thread_name)
                return
            if self._com_thread and self._com_thread.is_alive():
                return
            self._stop_event.clear()
            self._com_thread_id_event.clear()
            self._com_ready_event.clear()
            self._com_init_error = None
            self._com_thread_id = None
            self._com_thread = home.threading.Thread(target=self._com_thread_main, name=self.thread_name, daemon=True)
            self._com_thread.start()
        if not self._com_thread_id_event.wait(timeout=5.0):
            self._stop_event.set()
            if self._com_thread and self._com_thread.is_alive():
                self._com_thread.join(timeout=2.0)
            raise home.MediaEngineError('Il thread COM non è partito entro il timeout (thread id non disponibile).')
        if not self._com_ready_event.wait(timeout=5.0):
            self._stop_event.set()
            if self._com_thread and self._com_thread.is_alive():
                self._wake_com_thread()
                self._com_thread.join(timeout=2.0)
            raise home.MediaEngineError('Il thread COM non è pronto entro il timeout (inizializzazione incompleta).')
        if self._com_init_error is not None:
            raise home.MediaEngineError(f'Inizializzazione thread COM fallita: {self._com_init_error}') from self._com_init_error
        if self._com_thread_id is None:
            raise home.MediaEngineError("L'ID del thread COM non è disponibile dopo l'avvio.")
        home.logger.info('[%s] Thread avviato con successo (id=%s)', self.thread_name, self._com_thread_id)

    def shutdown(self) -> None:
        """Arresta il thread COM in modo pulito (idempotente)."""
        home = _home_module()
        with self._lock:
            if self._shutdown_requested:
                return
            self._shutdown_requested = True
        self._stop_event.set()
        self._wake_com_thread()
        self._reject_pending_tasks('shutdown requested')
        t = self._com_thread
        if t and t.is_alive():
            home.logger.info('[%s] In attesa della terminazione del thread...', self.thread_name)
            t.join(timeout=5.0)
            if t.is_alive():
                home.logger.warning('[%s] Il thread non è terminato correttamente.', self.thread_name)
            else:
                home.logger.info('[%s] Thread terminato.', self.thread_name)
        with self._lock:
            self._com_thread = None
            self._com_thread_id = None
            self._com_thread_id_event.clear()
            self._com_ready_event.clear()
            self._com_thread_wakeup = None

    def post_to_com_thread(self, name: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        """Invia una funzione al thread COM per esecuzione asincrona (fire-and-forget)."""
        home = _home_module()
        if self._stop_event.is_set() or self._shutdown_requested:
            home.logger.debug("[%s] Ignoro task COM '%s': thread in shutdown.", self.thread_name, name)
            return
        t = self._com_thread
        if not t or not t.is_alive():
            home.logger.warning("[%s] Thread COM non attivo, impossibile inviare il task '%s'", self.thread_name, name)
            return
        dummy_response_q: 'queue.Queue[Any]' = queue.Queue(maxsize=1)
        self._task_queue.put(home._ComTask(name=name, fn=fn, args=args, kwargs=kwargs, response_q=dummy_response_q))
        self._wake_com_thread()

    def submit_to_com_thread(self, name: str, fn: Callable[[], object], response: ResponsePort) -> bool:
        """Admit a tracked task without waiting or implicitly starting a worker.

        Cold O(1) admission. Dead/not-ready workers, full queues and shutdown
        refuse insertion. Shutdown after insertion is a failed worker reply, not
        false completion. This API does not bound the legacy shared queue.
        """
        home = _home_module()
        if type(name) is not str or not name or len(name) > 64 or not callable(fn):
            return False
        if not callable(getattr(response, 'put_nowait', None)):
            return False
        thread = self._com_thread
        if (self._shutdown_requested or self._stop_event.is_set() or thread is None
                or not thread.is_alive() or not self._com_ready_event.is_set()):
            return False

        def guarded() -> object:
            if (self._shutdown_requested or self._stop_event.is_set()
                    or self._com_thread is not thread or not self._is_on_com_thread()):
                raise home.MediaEngineError('Tracked COM command lost its worker ownership')
            return fn()

        task = home._ComTask(name, guarded, (), {}, response)
        try:
            self._task_queue.put_nowait(task)
        except queue.Full:
            return False
        try:
            self._wake_com_thread()
        except home.COM_WAIT_EXCEPTIONS:
            home.logger.warning('Tracked COM task admitted but wake failed', exc_info=True)
        return True

    def submit_cleanup_to_com_thread(self, name: str, fn: Callable[[], object],
                                     response: ResponsePort) -> bool:
        """Admit teardown with shutdown-atomic insertion and same-worker execution.

        No implicit start or wrong-thread fallback. Full/dead/closing workers
        explicitly reject. Inline execution on the managed thread avoids a
        self-wait; wake/reply failures after admission never authorize a replay.
        """
        home = _home_module()
        if type(name) is not str or not name or len(name) > 64 or not callable(fn):
            return False
        if not callable(getattr(response, 'put_nowait', None)):
            return False
        thread = self._com_thread
        inline = self._is_on_com_thread()

        def guarded() -> object:
            with self._lock:
                available = (self._com_thread is thread and not self._shutdown_requested
                             and not self._stop_event.is_set())
            if not available or not self._is_on_com_thread():
                raise home.MediaEngineError("Cleanup lost its COM worker ownership")
            return fn()

        task = home._ComTask(name, guarded, (), {}, response)
        with self._lock:
            if (thread is None or self._com_thread is not thread or not thread.is_alive()
                    or self._shutdown_requested or self._stop_event.is_set()
                    or not self._com_ready_event.is_set()):
                return False
            if not inline:
                try:
                    self._task_queue.put_nowait(task)
                except queue.Full:
                    return False
        if inline:
            response.put_nowait(guarded())
        else:
            try:
                self._wake_com_thread()
            except home.COM_WAIT_EXCEPTIONS:
                home.logger.warning("Cleanup admitted but wake failed", exc_info=True)
        return True

    def call_on_com_thread(self, name: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """
            Esegue una funzione sul thread COM, attendendo il risultato.
            Se chiamato dallo stesso thread COM, esegue la funzione direttamente.
            """
        home = _home_module()
        if self._is_on_com_thread():
            return fn(*args, **kwargs)
        if self._stop_event.is_set() or self._shutdown_requested:
            raise home.MediaEngineError('Il thread COM sta chiudendo; impossibile eseguire task sincroni.')
        t = self._com_thread
        if not t or not t.is_alive():
            raise home.MediaEngineError('Il thread COM non è attivo.')
        response_q: 'queue.Queue[Any]' = queue.Queue(maxsize=1)
        self._task_queue.put(home._ComTask(name=name, fn=fn, args=args, kwargs=kwargs, response_q=response_q))
        self._wake_com_thread()
        t0 = time.perf_counter()
        try:
            result = response_q.get(timeout=self.com_task_timeout)
        except queue.Empty as exc:
            raise home.MediaEngineTimeoutError(f"Task COM '{name}' ha superato il timeout di {self.com_task_timeout:.3f}s") from exc
        dt = time.perf_counter() - t0
        if dt >= 0.25:
            home.logger.info("[%s] COM task '%s' completed in %.3fs", self.thread_name, name, dt)
        if isinstance(result, Exception):
            raise result from result
        return result
