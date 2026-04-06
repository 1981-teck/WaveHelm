from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

from src.audio.audio_events import AudioEventType

from .engine_controller import EngineController
from .playback_state_manager import PlaybackStateManager, PlayerState
from .player_event_handler_video import _bind_player_event_handler_video_methods as _attach_player_event_handler_video_behavior
from .queue_manager import QueueManager

logger = logging.getLogger(__name__)

EVENT_BUS_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)
SIGNAL_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)
ENGINE_COMMAND_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)
COERCE_EXCEPTIONS = (TypeError, ValueError)
TRANSPORT_DEDUP_WINDOW_SEC = 0.18


_PLAYER_EVENT_HANDLER_ATTACHERS = (
    _attach_player_event_handler_video_behavior,
)


def attach_player_event_handler_behavior(handler_cls: type) -> None:
    """Attach player event handler behavior from one central coordinator.

    Edge cases handled:
        - handler_cls is not a class and cannot receive methods deterministically.
        - repeated imports or reloads can silently rebind handler methods.
        - a partial split import can leave the handler only partially patched.
    """
    if not isinstance(handler_cls, type):
        raise TypeError('handler_cls must be a class')
    if getattr(handler_cls, '_player_event_handler_behavior_attached', False):
        return

    for installer in _PLAYER_EVENT_HANDLER_ATTACHERS:
        installer(handler_cls)

    setattr(handler_cls, '_player_event_handler_behavior_attached', True)


class PlayerEventHandler:
    """Sottoscrive e gestisce gli eventi del bus per il player."""

    def __init__(
        self,
        state_manager: PlaybackStateManager,
        queue_manager: QueueManager,
        engine_controller: EngineController,
        event_bus: Any,
        player_controller_facade: "PlayerController",
    ):
        self.state_manager = state_manager
        self.queue_manager = queue_manager
        self.engine_controller = engine_controller
        self.event_bus = event_bus
        self._player = player_controller_facade

        self._last_video_start_signature: Optional[tuple] = None
        self._video_ready_connected = False

        self._shutdown_lock = threading.RLock()
        self._is_shutting_down: bool = False

        self._subscriptions_active: bool = False
        self._subscriptions: Dict[AudioEventType, Any] = {}
        self._main_view_signal_ref: Optional[Tuple[Any, Callable[..., Any]]] = None
        self._last_transport_request_ts: Dict[str, float] = {}
        self._transport_dedup_window_sec = float(TRANSPORT_DEDUP_WINDOW_SEC)

        logger.debug("[PlayerEventHandler] Initialized")

    @staticmethod
    def _event_name(event: AudioEventType) -> str:
        return getattr(event, 'name', str(event))

    def _disconnect_main_view_signal(self) -> None:
        if not self._main_view_signal_ref:
            self._video_ready_connected = False
            return

        sig, handler = self._main_view_signal_ref
        try:
            if sig and hasattr(sig, 'disconnect'):
                sig.disconnect(handler)  # type: ignore[misc]
                logger.debug('[PlayerEventHandler] Disconnesso segnale MainView.video_playback_ready')
        except SIGNAL_EXCEPTIONS:
            logger.debug('[PlayerEventHandler] Disconnect segnale video failed (best effort).', exc_info=True)
        finally:
            self._main_view_signal_ref = None
            self._video_ready_connected = False

    def shutdown(self) -> None:
        """Idempotent teardown: disiscrive dagli eventi e disconnette segnali (best effort)."""
        with self._shutdown_lock:
            if self._is_shutting_down:
                return
            self._is_shutting_down = True

        logger.debug('[PlayerEventHandler] shutdown()')
        self._unsubscribe_event_subscriptions()
        self._disconnect_main_view_signal()
        self.event_bus = None

    def _unsubscribe_event_subscriptions(self) -> None:
        """Best effort unsubscribe: dipende dall'API dell'event bus."""
        if not self.event_bus or not self._subscriptions_active:
            self._subscriptions_active = False
            self._subscriptions.clear()
            return

        unsubscribe = getattr(self.event_bus, 'unsubscribe', None)
        if not callable(unsubscribe):
            self._subscriptions_active = False
            self._subscriptions.clear()
            return

        for event, subscription_ref in list(self._subscriptions.items()):
            try:
                if hasattr(subscription_ref, 'deactivate') or hasattr(subscription_ref, 'subscription_id'):
                    unsubscribe(event, subscription=subscription_ref)
                else:
                    unsubscribe(event, callback=subscription_ref)
            except EVENT_BUS_EXCEPTIONS:
                logger.debug(
                    '[PlayerEventHandler] Unsubscribe failed for %s (best effort).',
                    self._event_name(event),
                    exc_info=True,
                )

        self._subscriptions_active = False
        self._subscriptions.clear()

    def setup_event_subscriptions(self):
        """Configura tutte le sottoscrizioni agli eventi."""
        if self._is_shutting_down:
            return

        if not self.event_bus:
            logger.warning('[PlayerEventHandler] Event bus non disponibile.')
            return

        if self._subscriptions_active:
            return

        subscribe = getattr(self.event_bus, 'subscribe', None)
        if not callable(subscribe):
            logger.warning('[PlayerEventHandler] Event bus subscribe non disponibile.')
            return

        subscriptions = {
            AudioEventType.PLAY_REQUESTED: '_on_play_requested',
            AudioEventType.PAUSE_REQUESTED: '_on_pause_requested',
            AudioEventType.STOP_REQUESTED: '_on_stop_requested',
            AudioEventType.PREVIOUS_REQUESTED: '_on_previous_requested',
            AudioEventType.NEXT_REQUESTED: '_on_next_requested',
            AudioEventType.VOLUME_CHANGE_REQUESTED: '_on_volume_change_requested',
            AudioEventType.SEEK_REQUESTED: '_on_seek_requested',
            AudioEventType.VIDEO_WINDOW_CLOSED: '_on_video_window_closed',
            AudioEventType.VIDEO_PLAYBACK_STOPPED: '_on_video_playback_stopped',
            AudioEventType.VIDEO_PLAYBACK_READY: '_on_video_playback_ready',
            AudioEventType.VIDEO_PLAYBACK_ERROR: '_on_video_playback_error',
        }

        for event, handler_name in subscriptions.items():
            handler = getattr(self, handler_name, None)
            if not callable(handler):
                logger.warning(
                    '[PlayerEventHandler] Handler mancante per %s: %s (subscription skipped)',
                    self._event_name(event),
                    handler_name,
                )
                continue
            try:
                subscription = subscribe(event, handler)
                self._subscriptions[event] = subscription if subscription is not None else handler
                logger.debug('[PlayerEventHandler] Sottoscritto a %s', self._event_name(event))
            except EVENT_BUS_EXCEPTIONS as error:
                logger.error(
                    '[PlayerEventHandler] Errore sottoscrizione a %s: %s',
                    self._event_name(event),
                    error,
                    exc_info=True,
                )

        self._subscriptions_active = True

    def connect_main_view_signals(self, main_view: Any):
        """Collega i segnali della MainView, se presenti."""
        if self._is_shutting_down or self._video_ready_connected or not main_view:
            return

        sig = getattr(main_view, 'video_playback_ready', None)
        if not sig or not hasattr(sig, 'connect'):
            return

        try:
            sig.connect(self._on_video_playback_ready_signal)
            self._main_view_signal_ref = (sig, self._on_video_playback_ready_signal)
            self._video_ready_connected = True
            logger.debug('[PlayerEventHandler] Connesso al segnale MainView.video_playback_ready')
        except SIGNAL_EXCEPTIONS as error:
            logger.warning('[PlayerEventHandler] Errore connessione segnale video: %s', error, exc_info=True)

    @staticmethod
    def _coerce_bool(value: Any) -> bool:
        """Converte in modo robusto un valore in bool (utile per payload eventi)."""
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        if isinstance(value, (int, float)):
            return bool(int(value))
        if isinstance(value, str):
            v = value.strip().lower()
            if v in ('1', 'true', 'yes', 'y', 'on'):
                return True
            if v in ('0', 'false', 'no', 'n', 'off', ''):
                return False
            return True
        return bool(value)


    def _should_drop_transport_request(self, event_type: AudioEventType) -> bool:
        """Best-effort duplicate guard for transport events.

        Edge cases handled:
            - duplicated UI dispatch can emit the same transport event twice within a few milliseconds.
            - system clock adjustments must not affect deduplication, so monotonic time is required.
            - distinct transport actions must stay independent even when they are triggered close together.
        """
        event_name = self._event_name(event_type)
        now = time.monotonic()
        last_seen = self._last_transport_request_ts.get(event_name)
        self._last_transport_request_ts[event_name] = now
        if last_seen is None:
            return False

        elapsed = now - last_seen
        if elapsed >= self._transport_dedup_window_sec:
            return False

        logger.debug(
            '[PlayerEventHandler] Duplicate transport ignored: %s (delta=%.3fs)',
            event_name,
            elapsed,
        )
        return True

    def _on_play_requested(self, data: Any = None):
        if self._is_shutting_down:
            return
        if self.state_manager.is_playing():
            logger.debug('[PlayerEventHandler] PLAY_REQUESTED ignored: already playing.')
            return
        if self.state_manager.is_paused():
            self._player.resume()
            return
        self._player.play_action()

    def _on_pause_requested(self, data: Any = None):
        if self._is_shutting_down:
            return
        if not self.state_manager.is_playing():
            logger.debug('[PlayerEventHandler] PAUSE_REQUESTED ignored: player is not playing.')
            return
        self._player.pause()

    def _on_stop_requested(self, data: Any = None):
        if self._is_shutting_down:
            return
        if self._should_drop_transport_request(AudioEventType.STOP_REQUESTED):
            return
        self._reset_video_start_signature('stop_requested')
        self._player.stop()

    def _on_previous_requested(self, data: Any = None):
        if self._is_shutting_down:
            return
        if self._should_drop_transport_request(AudioEventType.PREVIOUS_REQUESTED):
            return
        self._reset_video_start_signature('previous_requested')
        self._player.previous()

    def _on_next_requested(self, data: Any = None):
        if self._is_shutting_down:
            return
        if self._should_drop_transport_request(AudioEventType.NEXT_REQUESTED):
            return
        self._reset_video_start_signature('next_requested')
        self._player.next()

    def _on_volume_change_requested(self, data: Any):
        """Gestisce richiesta cambio volume."""
        if self._is_shutting_down or not isinstance(data, dict):
            return
        if 'volume' in data:
            try:
                self.engine_controller.set_volume(float(data['volume']))
            except (ENGINE_COMMAND_EXCEPTIONS + COERCE_EXCEPTIONS) as exc:
                logger.warning(
                    '[PlayerEventHandler] Invalid volume payload: %r (%s)',
                    data,
                    exc,
                    exc_info=True,
                )

    def _on_seek_requested(self, data: Any):
        """Gestisce richiesta seek."""
        if self._is_shutting_down:
            return
        if self.state_manager.state == PlayerState.LOADING:
            logger.debug('[PlayerEventHandler] seek ignored: state=LOADING.')
            return
        if not isinstance(data, dict):
            return
        if 'position' in data:
            try:
                self.engine_controller.seek(float(data['position']))
            except (ENGINE_COMMAND_EXCEPTIONS + COERCE_EXCEPTIONS) as exc:
                logger.warning(
                    '[PlayerEventHandler] Invalid seek payload: %r (%s)',
                    data,
                    exc,
                    exc_info=True,
                )


attach_player_event_handler_behavior(PlayerEventHandler)
