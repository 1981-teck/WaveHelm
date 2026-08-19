# -*- coding: utf-8 -*-
"""playback_state_manager.py
Gestisce lo stato di riproduzione del player.

Questo modulo deve rimanere leggero e privo di side-effect su import.
"""

from __future__ import annotations

import logging
from enum import Enum, auto
from typing import Any, Optional, Sequence

from src.audio.audio_events import AudioEventType
from src.model.media_file import MediaFile, MediaType

logger = logging.getLogger(__name__)

PLAYBACK_STATE_CONTEXT_EXCEPTIONS = (TypeError,)
PLAYBACK_STATE_INDEX_EXCEPTIONS = (TypeError, ValueError)
PLAYBACK_STATE_SERIALIZE_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)
PLAYBACK_STATE_PAYLOAD_EXCEPTIONS = (AttributeError, TypeError, ValueError)
PLAYBACK_STATE_PUBLISH_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)


class PlayerState(Enum):
    """Stati del player.

    Nota: questi stati sono usati dai componenti:
    - EngineController
    - ProgressTracker
    - PlayerEventHandler
    """

    IDLE = auto()
    LOADING = auto()

    PLAYING_AUDIO = auto()
    PAUSED_AUDIO = auto()

    PLAYING_VIDEO = auto()
    PAUSED_VIDEO = auto()

    STOPPED = auto()
    ERROR = auto()


class PlaybackStateManager:
    """Gestione dello stato corrente di playback, con pubblicazione eventi."""

    def __init__(self, event_bus: Any = None) -> None:
        self.event_bus = event_bus

        self._state: PlayerState = PlayerState.IDLE

        # Contesto playlist
        self._playlist: list[MediaFile] = []
        self._index: int = 0
        self._current_track: Optional[MediaFile] = None

        # Flags playback
        self._loop: bool = False
        self._shuffle: bool = False

        logger.debug("[PlaybackStateManager] Initialized (state=%s)", self._state.name)

    # ------------------------------------------------------------------
    # Context
    # ------------------------------------------------------------------
    def set_context(self, playlist: Sequence[MediaFile], index: int, current_track: Optional[MediaFile]) -> None:
        """Aggiorna il contesto corrente del player (playlist, indice, traccia)."""
        try:
            self._playlist = list(playlist) if playlist is not None else []
        except PLAYBACK_STATE_CONTEXT_EXCEPTIONS:
            self._playlist = []

        try:
            self._index = int(index)
        except PLAYBACK_STATE_INDEX_EXCEPTIONS:
            self._index = 0

        self._current_track = current_track
        logger.debug(
            "[PlaybackStateManager] Context set: index=%s total=%s track=%s",
            self._index,
            len(self._playlist),
            getattr(current_track, "path", None),
        )

        self._publish_state_changed()

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------
    @property
    def state(self) -> PlayerState:
        return self._state

    def update_state(self, new_state: PlayerState, extra_data: Optional[dict] = None) -> None:
        """Aggiorna lo stato del player e notifica via event bus."""
        if not isinstance(new_state, PlayerState):
            logger.warning("[PlaybackStateManager] Ignored invalid state: %r", new_state)
            return

        old = self._state
        self._state = new_state

        if old != new_state:
            logger.info("[PlaybackStateManager] State changed: %s -> %s", old.name, new_state.name)
        else:
            logger.debug("[PlaybackStateManager] State updated (same): %s", new_state.name)

        self._publish_state_changed(extra_data=extra_data)

    # ------------------------------------------------------------------
    # Flags
    # ------------------------------------------------------------------
    def toggle_loop(self) -> bool:
        """Abilita/disabilita il loop e pubblica lo stato aggiornato."""
        self._loop = not self._loop
        logger.debug("[PlaybackStateManager] Loop toggled -> %s", self._loop)
        self._publish_state_changed()
        return self._loop

    def toggle_shuffle(self) -> bool:
        """Abilita/disabilita lo shuffle e pubblica lo stato aggiornato."""
        self._shuffle = not self._shuffle
        logger.debug("[PlaybackStateManager] Shuffle toggled -> %s", self._shuffle)
        self._publish_state_changed()
        return self._shuffle

    def set_loop(self, enabled: bool) -> None:
        enabled_bool = bool(enabled)
        if self._loop != enabled_bool:
            self._loop = enabled_bool
            logger.debug("[PlaybackStateManager] Loop set -> %s", self._loop)
            self._publish_state_changed()

    def set_shuffle(self, enabled: bool) -> None:
        enabled_bool = bool(enabled)
        if self._shuffle != enabled_bool:
            self._shuffle = enabled_bool
            logger.debug("[PlaybackStateManager] Shuffle set -> %s", self._shuffle)
            self._publish_state_changed()

    # ------------------------------------------------------------------
    # State queries
    # ------------------------------------------------------------------
    def is_playing(self) -> bool:
        return self._state in (PlayerState.PLAYING_AUDIO, PlayerState.PLAYING_VIDEO)

    def is_paused(self) -> bool:
        return self._state in (PlayerState.PAUSED_AUDIO, PlayerState.PAUSED_VIDEO)

    def is_stopped(self) -> bool:
        return self._state in (PlayerState.STOPPED, PlayerState.IDLE)

    def is_video(self) -> bool:
        if self._state in (PlayerState.PLAYING_VIDEO, PlayerState.PAUSED_VIDEO):
            return True
        if self._state in (PlayerState.PLAYING_AUDIO, PlayerState.PAUSED_AUDIO):
            return False
        # Per stati IDLE/LOADING/STOPPED/ERROR, inferisci dal media corrente (se presente)
        mt = getattr(self._current_track, "media_type", None)
        return mt == MediaType.VIDEO

    def is_audio(self) -> bool:
        mt = getattr(self._current_track, "media_type", None)
        if self._state in (PlayerState.PLAYING_AUDIO, PlayerState.PAUSED_AUDIO):
            return True
        if self._state in (PlayerState.PLAYING_VIDEO, PlayerState.PAUSED_VIDEO):
            return False
        return mt == MediaType.AUDIO

    # ------------------------------------------------------------------
    # Properties (utili per UI/diagnostica)
    # ------------------------------------------------------------------
    @property
    def loop_enabled(self) -> bool:
        return self._loop

    @property
    def shuffle_enabled(self) -> bool:
        return self._shuffle

    @property
    def current_track(self) -> Optional[MediaFile]:
        return self._current_track

    @property
    def playlist(self) -> list[MediaFile]:
        return self._playlist

    @property
    def index(self) -> int:
        return self._index

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------
    def _publish_state_changed(self, extra_data: Optional[dict] = None) -> None:
        """Pubblica PLAYER_STATE_CHANGED con un payload coerente."""
        track_dict: Optional[dict] = None
        try:
            if self._current_track and hasattr(self._current_track, "to_dict"):
                track_dict = self._current_track.to_dict()
        except PLAYBACK_STATE_SERIALIZE_EXCEPTIONS:
            logger.debug("[PlaybackStateManager] Failed to serialize current_track", exc_info=True)

        data: dict = {
            "state": self._state.name,
            "playing": self.is_playing(),
            "paused": self.is_paused(),
            "stopped": self.is_stopped(),
            "is_video": self.is_video(),
            "is_audio": self.is_audio(),
            "loop_enabled": self._loop,
            "shuffle_enabled": self._shuffle,
            "current_index": self._index,
            "total_tracks": len(self._playlist),
            "current_track": track_dict,
            "current_track_path": getattr(self._current_track, "path", None),
            "path": getattr(self._current_track, "path", None),
        }

        if extra_data:
            try:
                data.update(extra_data)
            except PLAYBACK_STATE_PAYLOAD_EXCEPTIONS:
                logger.debug("[PlaybackStateManager] Failed to merge extra_data", exc_info=True)

        self._publish_event(AudioEventType.PLAYER_STATE_CHANGED, data)

    def _publish_event(self, event_type: AudioEventType, data: Optional[dict] = None) -> None:
        if not self.event_bus:
            return
        try:
            # Bus usato nel progetto: publish(event_type, payload)
            self.event_bus.publish(event_type, data or {})
        except PLAYBACK_STATE_PUBLISH_EXCEPTIONS as exc:
            logger.warning(
                "[PlaybackStateManager] Failed to publish %s: %s",
                getattr(event_type, "name", event_type),
                exc,
                exc_info=True,
            )
