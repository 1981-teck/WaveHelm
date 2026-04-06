# -*- coding: utf-8 -*-
"""
player_controller.py
Coordinatore del playback (facciata).
Orchestra i componenti per la gestione della coda, dello stato e degli engine.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, List, Optional, Tuple

from src.audio.audio_events import AudioEventType
from src.model.media_file import MediaFile, MediaType

# Compatibilita: alcuni branch hanno rinominato la cartella dei componenti.
try:
    from .component_player.playback_state_manager import PlaybackStateManager, PlayerState
    from .component_player.queue_manager import QueueManager
    from .component_player.engine_controller import EngineController
    from .component_player.player_event_handler import PlayerEventHandler
    from .component_player.progress_tracker import ProgressTracker
except ImportError:  # pragma: no cover
    from .component_player.playback_state_manager import PlaybackStateManager, PlayerState  # type: ignore
    from .component_player.queue_manager import QueueManager  # type: ignore
    from .component_player.engine_controller import EngineController  # type: ignore
    from .component_player.player_event_handler import PlayerEventHandler  # type: ignore
    from .component_player.progress_tracker import ProgressTracker  # type: ignore

logger = logging.getLogger(__name__)

START_INDEX_EXCEPTIONS = (TypeError, ValueError)
DISPATCH_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)
SHUTDOWN_EXCEPTIONS = (AttributeError, OSError, RuntimeError, TypeError, ValueError)
FACTORY_FORWARD_EXCEPTIONS = (AttributeError, OSError, RuntimeError, TypeError, ValueError)


class PlayerController:
    """
    Facciata per il sistema di riproduzione.
    Delega la logica a componenti specializzati.
    """

    def __init__(
        self,
        state_manager: PlaybackStateManager,
        queue_manager: QueueManager,
        engine_controller: EngineController,
        progress_tracker: ProgressTracker,
        event_bus: Any,
    ):
        self.state_manager = state_manager
        self.queue_manager = queue_manager
        self.engine_controller = engine_controller
        self.progress_tracker = progress_tracker
        self.event_bus = event_bus
        self._event_handler: Optional[PlayerEventHandler] = None

        # Shutdown idempotente + protezione race con thread di polling
        self._shutdown_lock = threading.RLock()
        self._is_shutdown = False
        self._track_end_dispatch_pending = False

        logger.debug('[PlayerController Facade] Initialized')

    @staticmethod
    def _coerce_start_index(start_index: Any, items_count: int) -> Optional[int]:
        try:
            index = int(start_index)
        except START_INDEX_EXCEPTIONS:
            return None
        if 0 <= index < items_count:
            return index
        return None

    @staticmethod
    def _best_effort_call(action: Callable[[], None], log_message: str) -> None:
        try:
            action()
        except SHUTDOWN_EXCEPTIONS:
            logger.debug(log_message, exc_info=True)

    def set_event_handler(self, handler: PlayerEventHandler):
        """Imposta l'event handler e completa l'inizializzazione."""
        self._event_handler = handler
        self._event_handler.setup_event_subscriptions()
        self.progress_tracker.start()
        logger.debug('[PlayerController Facade] Event handler set and initialized.')

    def set_main_view(self, main_view: Any):
        """Passa la main_view all'event handler per connettere i segnali."""
        if self._event_handler:
            self._event_handler.connect_main_view_signals(main_view)

    def _stop_current_playback_before_switch(self, target_track: Optional[MediaFile]) -> None:
        """Ferma il playback corrente prima di cambiare contesto traccia."""
        if self.state_manager.is_stopped():
            return

        was_video = self.state_manager.is_video()
        target_is_video = getattr(target_track, 'media_type', None) == MediaType.VIDEO

        self.engine_controller.stop()

        if was_video and not target_is_video:
            self._publish_event(AudioEventType.CANCEL_VIDEO_PLAYBACK)

    # --- API Pubblica per Controlli di Riproduzione ---

    def set_playback_context(self, items: List[MediaFile], start_index: int, autoplay: bool = True):
        """Imposta la playlist e avvia la riproduzione."""
        target_track: Optional[MediaFile] = None
        resolved_index = self._coerce_start_index(start_index, len(items)) if items else None
        if resolved_index is not None:
            target_track = items[resolved_index]

        if target_track is not None:
            self._stop_current_playback_before_switch(target_track)

        track = self.queue_manager.set_context(items, start_index)
        if track:
            self.state_manager.set_context(self.queue_manager.playlist, self.queue_manager.index, track)
            self._publish_event(
                AudioEventType.PLAYLIST_CHANGED,
                self.queue_manager.get_playlist_info(),
            )
            if autoplay:
                self.play_action(track)

    def play_action(self, track: Optional[MediaFile] = None, selection: Optional[MediaFile] = None):
        """Avvia la riproduzione (compat: accetta selection=...)."""
        if selection is not None:
            track = selection

        if track is None:
            if self.state_manager.is_paused():
                self.resume()
                return
            track = self.queue_manager.current_track
            if track is None:
                logger.debug('[PlayerController] play_action called with no track and no current_track')
                return

        started = self.engine_controller.play(track, self.state_manager._loop)

        # Se e un video, l'avvio e asincrono: chiedi alla UI di preparare HWND
        if started and getattr(track, 'media_type', None) == MediaType.VIDEO:
            self._publish_event(
                AudioEventType.PREPARE_VIDEO_PLAYBACK,
                {
                    'path': track.path,
                    'loop': self.state_manager._loop,
                    'track': track.to_dict() if hasattr(track, 'to_dict') else None,
                },
            )

    def play_pause(self):
        """Alterna tra play e pausa."""
        if self.state_manager.is_playing():
            self.engine_controller.pause()
        elif self.state_manager.is_paused():
            self.engine_controller.resume()
        else:
            self.play_action()

    def pause(self):
        """Pausa esplicita (compat UI)."""
        self.engine_controller.pause()

    def resume(self):
        """Ripresa esplicita (compat UI)."""
        self.engine_controller.resume()

    def stop(self):
        """Ferma la riproduzione."""
        was_video = self.state_manager.is_video()
        self.engine_controller.stop()
        if was_video:
            self._publish_event(AudioEventType.CANCEL_VIDEO_PLAYBACK)

    def next(self):
        """Passa alla traccia successiva."""
        track = self.queue_manager.next()
        if track:
            self._stop_current_playback_before_switch(track)
            self.state_manager.set_context(
                self.queue_manager.playlist,
                self.queue_manager.index,
                track,
            )
            self.play_action(track)

    def previous(self):
        """Torna alla traccia precedente."""
        track = self.queue_manager.previous()
        if track:
            self._stop_current_playback_before_switch(track)
            self.state_manager.set_context(
                self.queue_manager.playlist,
                self.queue_manager.index,
                track,
            )
            self.play_action(track)

    def seek(self, position_sec: float):
        """Seek della traccia corrente."""
        self.engine_controller.seek(position_sec)

    def set_volume(self, value: float):
        """Imposta volume (0..1)."""
        self.engine_controller.set_volume(value)

    def toggle_mute(self):
        """Toggle mute."""
        self.engine_controller.toggle_mute()

    def toggle_loop(self):
        """Attiva/disattiva il loop."""
        enabled = self.state_manager.toggle_loop()
        self.engine_controller.set_loop(enabled)
        self._publish_event(
            AudioEventType.LOOP_CHANGED,
            {'loop_enabled': enabled},
        )

    def toggle_shuffle(self):
        """Attiva/disattiva shuffle."""
        enabled = self.state_manager.toggle_shuffle()
        self.queue_manager.set_shuffle(enabled)
        self._publish_event(
            AudioEventType.SHUFFLE_CHANGED,
            {'shuffled': enabled, 'shuffle_enabled': enabled},
        )

    def _capture_track_end_signature(self) -> Optional[Tuple[Optional[str], int, str]]:
        """Capture a deterministic snapshot for a pending end-of-track transition.

        Edge cases handled:
        - shutdown or missing current track must fail closed and skip the transition
        - manual next/previous/stop before UI dispatch must invalidate stale callbacks
        - duplicate paths remain distinguishable through the current playlist index
        """
        if getattr(self, '_is_shutdown', False):
            return None

        current_track = self.queue_manager.current_track
        state_name = getattr(self.state_manager.state, 'name', 'UNKNOWN')
        if current_track is None:
            return None

        current_path = getattr(current_track, 'path', None)
        current_index = int(getattr(self.queue_manager, 'index', -1))
        return (current_path, current_index, state_name)

    def _is_track_end_signature_current(self, expected: Tuple[Optional[str], int, str]) -> bool:
        """Validate that a deferred track-end callback still matches the active track."""
        current_signature = self._capture_track_end_signature()
        if current_signature is None:
            return False
        return current_signature == expected

    def _handle_track_end(self):
        """Gestisce la fine di una traccia (chiamato dal ProgressTracker)."""
        if getattr(self, '_is_shutdown', False):
            logger.debug('PlayerController: _handle_track_end ignored during shutdown')
            return

        expected_signature = self._capture_track_end_signature()
        if expected_signature is None:
            logger.debug('PlayerController: _handle_track_end ignored without current track context')
            return

        if threading.current_thread() is threading.main_thread():
            self._run_track_end_transition(expected_signature)
            return

        if self._track_end_dispatch_pending:
            logger.debug('PlayerController: track-end transition gia accodata sul thread UI.')
            return

        self._track_end_dispatch_pending = True
        dispatch = lambda: self._run_track_end_transition(expected_signature)
        if not self._dispatch_to_ui_thread(dispatch):
            logger.debug('PlayerController: fallback track-end transition sul thread corrente.')
            self._run_track_end_transition(expected_signature)

    def shutdown(self):
        """Ferma tutti i processi in background (idempotente)."""
        with self._shutdown_lock:
            if self._is_shutdown:
                logger.debug('[PlayerController Facade] shutdown() gia eseguito; skip')
                return
            self._is_shutdown = True

        logger.info('[PlayerController Facade] Inizio shutdown.')

        if self._event_handler is not None:
            self._best_effort_call(
                self._event_handler.shutdown,
                '[PlayerController Facade] event_handler.shutdown() failed',
            )

        # Best-effort: evita crash in doppia chiusura
        self._best_effort_call(
            self.progress_tracker.stop,
            '[PlayerController Facade] progress_tracker.stop() failed',
        )
        self._best_effort_call(
            self.engine_controller.stop,
            '[PlayerController Facade] engine_controller.stop() failed',
        )
        self._best_effort_call(
            lambda: self.state_manager.update_state(PlayerState.IDLE),
            '[PlayerController Facade] state_manager.update_state(IDLE) failed',
        )

        logger.info('[PlayerController Facade] Shutdown completato.')

    # --- Metodi ausiliari e di stato ---

    def _publish_event(self, event_type: AudioEventType, data: Optional[dict] = None):
        """Pubblica un evento tramite il bus."""
        if self.event_bus:
            self.event_bus.publish(event_type, data or {})

    def _dispatch_to_ui_thread(self, callback: Callable[[], None]) -> bool:
        """Esegue una callback sul thread UI se il dispatcher e disponibile."""
        dispatcher = getattr(self.event_bus, '_ui_dispatcher', None)
        if not callable(dispatcher):
            return False

        try:
            dispatcher(callback)
            return True
        except DISPATCH_EXCEPTIONS:
            logger.debug('[PlayerController Facade] UI dispatch failed.', exc_info=True)
            return False

    def _run_track_end_transition(self, expected_signature: Optional[Tuple[Optional[str], int, str]] = None) -> None:
        """Esegue la logica di fine traccia sul thread UI."""
        try:
            if getattr(self, '_is_shutdown', False):
                logger.debug('PlayerController: _run_track_end_transition ignored during shutdown')
                return

            if expected_signature is not None and not self._is_track_end_signature_current(expected_signature):
                logger.debug('PlayerController: stale track-end transition ignored for %r', expected_signature)
                return

            logger.debug('PlayerController: Gestione fine traccia.')
            should_repeat_current = bool(self.state_manager._loop)
            repeat_getter = getattr(self.engine_controller, 'should_repeat_current_track', None)
            if callable(repeat_getter):
                try:
                    should_repeat_current = bool(repeat_getter())
                except DISPATCH_EXCEPTIONS:
                    logger.debug('PlayerController: should_repeat_current_track fallback failed.', exc_info=True)

            if should_repeat_current:
                logger.info('Looping traccia corrente.')
                if self.queue_manager.current_track:
                    self.play_action(self.queue_manager.current_track)
            else:
                logger.info('Passaggio alla traccia successiva.')
                self.next()
        finally:
            self._track_end_dispatch_pending = False

    @property
    def current_track(self) -> Optional[MediaFile]:
        return self.queue_manager.current_track

    def get_duration(self) -> float:
        """Espone la durata corrente tramite il tracker di progresso."""
        return float(self.progress_tracker.get_duration())

    def get_position(self) -> float:
        """Espone la posizione corrente tramite il tracker di progresso."""
        return float(self.progress_tracker.get_position())

    def is_video(self) -> bool:
        """Indica se il media corrente appartiene al ramo video."""
        return bool(self.state_manager.is_video())

    def get_current_player_state_payload(self) -> dict:
        """Restituisce un payload coerente per il dispatcher UI mantenuto."""
        current_track = self.state_manager.current_track
        current_path = getattr(current_track, 'path', None)
        return {
            'state': self.state_manager.state.name,
            'playing': self.state_manager.is_playing(),
            'paused': self.state_manager.is_paused(),
            'stopped': self.state_manager.is_stopped(),
            'is_video': self.state_manager.is_video(),
            'is_audio': self.state_manager.is_audio(),
            'loop_enabled': self.state_manager.loop_enabled,
            'shuffle_enabled': self.state_manager.shuffle_enabled,
            'current_index': self.state_manager.index,
            'total_tracks': len(self.state_manager.playlist),
            'current_track': current_track,
            'current_media': current_track,
            'current_track_path': current_path,
            'path': current_path,
        }

    # --- Metodi deprecati o da non usare esternamente ---

    def set_video_controller_factory(self, factory: Callable[[], Optional[Any]]):
        """Compat: inoltra factory al motore (JIT VideoController)."""
        try:
            setter = getattr(self.engine_controller, 'set_video_controller_factory', None)
            if callable(setter):
                setter(factory)
            else:
                setattr(self.engine_controller, '_video_controller_factory', factory)
            logger.debug('[PlayerController Facade] VideoController factory forwarded to EngineController')
        except FACTORY_FORWARD_EXCEPTIONS as exc:
            logger.error(
                '[PlayerController Facade] Failed to forward VideoController factory: %s',
                exc,
                exc_info=True,
            )
