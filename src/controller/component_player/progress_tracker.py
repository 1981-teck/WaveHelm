# -*- coding: utf-8 -*-
"""progress_tracker.py
Gestisce il polling in background per il progresso della riproduzione e
il rilevamento della fine della traccia.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from src.audio.audio_events import AudioEventType
from .playback_state_manager import PlaybackStateManager, PlayerState
from .queue_manager import QueueManager
from .engine_controller import EngineController

logger = logging.getLogger(__name__)

ENGINE_QUERY_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)
CALLBACK_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)
EVENT_PUBLISH_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)
WORKER_EXCEPTIONS = ENGINE_QUERY_EXCEPTIONS


class ProgressTracker:
    """
    Esegue un thread in background per monitorare il progresso della riproduzione.
    """

    def __init__(
        self,
        state_manager: PlaybackStateManager,
        queue_manager: QueueManager,
        engine_controller: EngineController,
        event_publisher: Callable,
        on_track_end_callback: Callable,
    ):
        self.state_manager = state_manager
        self.queue_manager = queue_manager
        self.engine_controller = engine_controller
        self._publish_event = event_publisher
        self._on_track_end = on_track_end_callback

        self._lock = threading.RLock()
        self._stop_event = threading.Event()

        self._polling_active = False
        self._poll_thread: Optional[threading.Thread] = None
        logger.debug("[ProgressTracker] Initialized")

    def start(self):
        """Avvia il thread di polling."""
        with self._lock:
            if self._polling_active:
                return

            self._stop_event.clear()
            self._polling_active = True
            self._poll_thread = threading.Thread(target=self._poll_worker, daemon=True)
            self._poll_thread.start()
            logger.info("[ProgressTracker] Polling thread started.")

    def stop(self):
        """Ferma il thread di polling (idempotente)."""
        with self._lock:
            if not self._polling_active:
                self._poll_thread = None
                return

            self._polling_active = False
            self._stop_event.set()
            thread = self._poll_thread

        # Evita join se siamo già nel thread di polling
        if thread and thread.is_alive():
            if threading.current_thread() is thread:
                logger.debug("[ProgressTracker] stop() called from polling thread; skip join.")
            else:
                thread.join(timeout=1.5)
                if thread.is_alive():
                    logger.warning("[ProgressTracker] Polling thread did not stop within timeout (best effort).")
                else:
                    logger.info("[ProgressTracker] Polling thread stopped.")

        with self._lock:
            self._poll_thread = None

    def shutdown(self):
        """Alias per teardown uniforme (PlayerController / AppController)."""
        self.stop()

    def _poll_worker(self):
        """
        Loop del thread di monitoraggio.
        - Controlla se la traccia è terminata.
        - Aggiorna il progresso per la UI.
        """
        logger.debug(
            "[ProgressTracker] Poll worker started (tid=%s).",
            threading.get_ident(),
        )

        try:
            while self._polling_active and not self._stop_event.is_set():
                # wait() permette stop immediato (meglio di sleep)
                if self._stop_event.wait(0.25):
                    break

                if not self._polling_active or self._stop_event.is_set():
                    break

                if not self.state_manager.is_playing():
                    continue

                state = self.state_manager.state

                # Per i VIDEO, preferisci la verifica robusta tramite VideoController.poll_end()
                if state == PlayerState.PLAYING_VIDEO:
                    video_controller = getattr(self.engine_controller, "video_controller", None)
                    if video_controller and hasattr(video_controller, "poll_end"):
                        try:
                            ended = bool(video_controller.poll_end())
                        except ENGINE_QUERY_EXCEPTIONS as e:
                            logger.debug(
                                "[ProgressTracker] video_controller.poll_end() failed: %s",
                                e,
                                exc_info=True,
                            )
                            ended = False

                        if ended and not self._stop_event.is_set():
                            logger.info("[ProgressTracker] Rilevata fine video (poll_end).")
                            try:
                                self._on_track_end()
                            except CALLBACK_EXCEPTIONS:
                                logger.debug("[ProgressTracker] _on_track_end callback failed.", exc_info=True)
                            # Attendi un po' prima del prossimo ciclo per permettere al nuovo brano di iniziare
                            self._stop_event.wait(1.0)
                            continue

                duration = self.get_duration()
                position = self.get_position()

                # Rilevamento fine traccia (fallback su position/duration)
                if duration > 0 and position >= duration - 0.3:  # Tolleranza di 300ms
                    loop_enabled = bool(getattr(self.state_manager, "_loop", False))

                    if state == PlayerState.PLAYING_AUDIO:
                        audio_engine = getattr(self.engine_controller, "audio_engine", None)
                        native_loop_active = bool(
                            audio_engine
                            and getattr(audio_engine, "_current_play_uses_native_loop", False)
                        )
                        # Se il loop e' gia gestito dal playback nativo corrente, evita di forzare il restart.
                        if native_loop_active:
                            logger.debug(
                                "[ProgressTracker] Fine traccia rilevata ma loop audio attivo (gestito dal playback corrente)."
                            )
                        else:
                            logger.info(
                                "Rilevata fine traccia (pos: %.2f, dur: %.2f)",
                                position,
                                duration,
                            )
                            if not self._stop_event.is_set():
                                try:
                                    self._on_track_end()
                                except CALLBACK_EXCEPTIONS:
                                    logger.debug("[ProgressTracker] _on_track_end callback failed.", exc_info=True)
                            self._stop_event.wait(1.0)
                            continue

                    elif state == PlayerState.PLAYING_VIDEO:
                        # Se non c'è poll_end(), evita avanzamento in loop e lascia che il video controller gestisca.
                        if loop_enabled:
                            logger.debug(
                                "[ProgressTracker] Fine video rilevata ma loop attivo (fallback position/duration)."
                            )
                        else:
                            logger.info(
                                "Rilevata fine traccia (pos: %.2f, dur: %.2f)",
                                position,
                                duration,
                            )
                            if not self._stop_event.is_set():
                                try:
                                    self._on_track_end()
                                except CALLBACK_EXCEPTIONS:
                                    logger.debug("[ProgressTracker] _on_track_end callback failed.", exc_info=True)
                            self._stop_event.wait(1.0)
                            continue

                    else:
                        logger.info(
                            "Rilevata fine traccia (pos: %.2f, dur: %.2f)",
                            position,
                            duration,
                        )
                        if not self._stop_event.is_set():
                            try:
                                self._on_track_end()
                            except CALLBACK_EXCEPTIONS:
                                logger.debug("[ProgressTracker] _on_track_end callback failed.", exc_info=True)
                        self._stop_event.wait(1.0)
                        continue

                # Pubblica evento di progresso
                self._publish_progress(position, duration)

        except WORKER_EXCEPTIONS as e:
            logger.debug("[ProgressTracker] Errore nel poll worker: %s", e, exc_info=True)
            time.sleep(0.2)
        finally:
            logger.debug("[ProgressTracker] Poll worker exiting (tid=%s).", threading.get_ident())

    def get_duration(self) -> float:
        """Ottiene la durata (in secondi) dall'engine attivo."""
        state = self.state_manager.state
        try:
            if state in (PlayerState.PLAYING_AUDIO, PlayerState.PAUSED_AUDIO):
                audio_engine = getattr(self.engine_controller, "audio_engine", None)
                if audio_engine and hasattr(audio_engine, "get_duration"):
                    return float(audio_engine.get_duration())
            elif state in (PlayerState.PLAYING_VIDEO, PlayerState.PAUSED_VIDEO):
                video_controller = getattr(self.engine_controller, "video_controller", None)
                if video_controller:
                    return float(video_controller.get_duration())
        except ENGINE_QUERY_EXCEPTIONS:
            return 0.0
        return 0.0

    def get_position(self) -> float:
        """Ottiene la posizione corrente (in secondi) dall'engine attivo."""
        state = self.state_manager.state
        try:
            if state in (PlayerState.PLAYING_AUDIO, PlayerState.PAUSED_AUDIO):
                audio_engine = getattr(self.engine_controller, "audio_engine", None)
                if audio_engine and hasattr(audio_engine, "get_position"):
                    return float(audio_engine.get_position())
            elif state in (PlayerState.PLAYING_VIDEO, PlayerState.PAUSED_VIDEO):
                video_controller = getattr(self.engine_controller, "video_controller", None)
                if video_controller:
                    return float(video_controller.get_position())
        except ENGINE_QUERY_EXCEPTIONS:
            return 0.0
        return 0.0

    def _publish_progress(self, position: float, duration: float):
        """Pubblica l'evento PLAYBACK_PROGRESS."""
        if duration <= 0:
            return

        current_track = self.queue_manager.current_track
        percent = (position / duration * 100.0)

        # Clamp best-effort per UI
        if percent < 0.0:
            percent = 0.0
        elif percent > 100.0:
            percent = 100.0

        try:
            self._publish_event(
                AudioEventType.PLAYBACK_PROGRESS,
                {
                    "current_time": position,
                    "total_duration": duration,
                    "progress_percent": percent,
                    "path": getattr(current_track, "path", None),
                },
            )
        except EVENT_PUBLISH_EXCEPTIONS:
            logger.debug("[ProgressTracker] publish_event failed (best effort).", exc_info=True)
