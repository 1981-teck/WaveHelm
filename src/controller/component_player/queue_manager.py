# -*- coding: utf-8 -*-
"""queue_manager.py
Gestisce la coda di riproduzione (playlist), l'indice corrente e la logica
di navigazione tra le tracce.

Nota: questo modulo deve rimanere privo di side-effect su import.
"""

from __future__ import annotations

import logging
import random
from typing import List, Optional, Sequence

from src.model.media_file import MediaFile

logger = logging.getLogger(__name__)

QUEUE_CONTEXT_EXCEPTIONS = (TypeError,)
QUEUE_INDEX_EXCEPTIONS = (TypeError, ValueError)
QUEUE_SERIALIZE_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)


class QueueManager:
    """Gestisce la logica della playlist corrente."""

    def __init__(self) -> None:
        self._playlist: List[MediaFile] = []
        self._index: int = 0
        self._shuffle_enabled: bool = False
        self._shuffle_history: List[int] = []
        self._shuffle_history_pos: int = -1
        self._shuffle_unplayed: List[int] = []
        self._current_cycle_order: List[int] = []
        self._last_cycle_order: List[int] = []
        self._rng = random.Random()

    # ------------------------------------------------------------------
    # Context
    # ------------------------------------------------------------------
    def set_context(self, items: Sequence[MediaFile], start_index: int) -> Optional[MediaFile]:
        """Imposta una nuova playlist e posiziona l'indice.

        Args:
            items: lista (o sequenza) di MediaFile.
            start_index: indice iniziale (verrà clampato nel range valido).

        Returns:
            La traccia corrente se disponibile, altrimenti None.
        """
        try:
            self._playlist = list(items) if items is not None else []
        except QUEUE_CONTEXT_EXCEPTIONS:
            self._playlist = []

        if not self._playlist:
            self._index = 0
            logger.debug("[QueueManager] set_context: playlist vuota")
            return None

        try:
            idx = int(start_index)
        except QUEUE_INDEX_EXCEPTIONS:
            idx = 0

        if idx < 0:
            idx = 0
        if idx >= len(self._playlist):
            idx = len(self._playlist) - 1

        self._index = idx
        self._reset_shuffle_state(current_index=self._index)
        track = self.current_track
        logger.info("[QueueManager] Context set: index=%d total=%d track=%s",
                    self._index, len(self._playlist), getattr(track, "path", "N/A"))
        return track

    def set_shuffle(self, enabled: bool) -> None:
        """Abilita/disabilita la navigazione casuale preservando la traccia corrente."""
        enabled_bool = bool(enabled)
        if self._shuffle_enabled == enabled_bool:
            return

        self._shuffle_enabled = enabled_bool
        self._reset_shuffle_state(current_index=self._index if self._playlist else None)
        logger.info("[QueueManager] Shuffle set -> %s", self._shuffle_enabled)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------
    @property
    def is_empty(self) -> bool:
        return len(self._playlist) == 0

    @property
    def playlist(self) -> List[MediaFile]:
        return self._playlist

    @property
    def index(self) -> int:
        return self._index

    @property
    def current_track(self) -> Optional[MediaFile]:
        if not self._playlist:
            return None
        if self._index < 0 or self._index >= len(self._playlist):
            return None
        return self._playlist[self._index]

    def next(self) -> Optional[MediaFile]:
        """Avanza alla traccia successiva (wrap-around)."""
        if not self._playlist:
            return None

        if self._shuffle_enabled:
            return self._next_shuffle()

        self._index = (self._index + 1) % len(self._playlist)
        track = self.current_track
        logger.info("[QueueManager] NEXT -> index=%d (%s)", self._index, getattr(track, "path", "N/A"))
        return track

    def previous(self) -> Optional[MediaFile]:
        """Torna alla traccia precedente (wrap-around)."""
        if not self._playlist:
            return None

        if self._shuffle_enabled:
            return self._previous_shuffle()

        self._index = (self._index - 1 + len(self._playlist)) % len(self._playlist)
        track = self.current_track
        logger.info("[QueueManager] PREVIOUS -> index=%d (%s)", self._index, getattr(track, "path", "N/A"))
        return track

    def _reset_shuffle_state(self, current_index: Optional[int] = None) -> None:
        self._shuffle_history = []
        self._shuffle_history_pos = -1
        self._shuffle_unplayed = []
        self._current_cycle_order = []
        self._last_cycle_order = []

        if not self._playlist or not self._shuffle_enabled:
            return

        if current_index is None or current_index < 0 or current_index >= len(self._playlist):
            current_index = 0

        self._shuffle_history = [current_index]
        self._shuffle_history_pos = 0
        self._current_cycle_order = [current_index]

        remaining = [idx for idx in range(len(self._playlist)) if idx != current_index]
        self._rng.shuffle(remaining)
        self._shuffle_unplayed = remaining

    def _next_shuffle(self) -> Optional[MediaFile]:
        if not self._playlist:
            return None

        if self._shuffle_history_pos + 1 < len(self._shuffle_history):
            self._shuffle_history_pos += 1
            self._index = self._shuffle_history[self._shuffle_history_pos]
            track = self.current_track
            logger.info(
                "[QueueManager] NEXT (history) -> index=%d (%s)",
                self._index,
                getattr(track, "path", "N/A"),
            )
            return track

        if not self._shuffle_unplayed:
            if self._current_cycle_order:
                self._last_cycle_order = list(self._current_cycle_order)
            self._current_cycle_order = []
            self._shuffle_unplayed = self._build_shuffle_cycle(exclude_first=self._index)

        if not self._shuffle_unplayed:
            return self.current_track

        next_index = self._shuffle_unplayed.pop(0)
        self._index = next_index
        self._shuffle_history.append(next_index)
        self._shuffle_history_pos = len(self._shuffle_history) - 1
        self._current_cycle_order.append(next_index)

        track = self.current_track
        logger.info("[QueueManager] NEXT (shuffle) -> index=%d (%s)", self._index, getattr(track, "path", "N/A"))
        return track

    def _previous_shuffle(self) -> Optional[MediaFile]:
        if not self._playlist:
            return None

        if self._shuffle_history_pos > 0:
            self._shuffle_history_pos -= 1
            self._index = self._shuffle_history[self._shuffle_history_pos]
            track = self.current_track
            logger.info(
                "[QueueManager] PREVIOUS (history) -> index=%d (%s)",
                self._index,
                getattr(track, "path", "N/A"),
            )
            return track

        track = self.current_track
        logger.info(
            "[QueueManager] PREVIOUS (shuffle start) -> index=%d (%s)",
            self._index,
            getattr(track, "path", "N/A"),
        )
        return track

    def _build_shuffle_cycle(self, exclude_first: Optional[int] = None) -> List[int]:
        indices = list(range(len(self._playlist)))
        if not indices:
            return []
        if len(indices) == 1:
            return indices

        attempts = 0
        previous_cycle = list(self._last_cycle_order)
        candidate = indices[:]

        while attempts < 12:
            candidate = indices[:]
            self._rng.shuffle(candidate)
            candidate = self._normalize_cycle(candidate, exclude_first=exclude_first)
            if candidate != previous_cycle:
                break
            attempts += 1

        if candidate == previous_cycle:
            candidate = self._rotate_cycle(candidate, exclude_first=exclude_first)

        return candidate

    @staticmethod
    def _rotate_cycle(candidate: List[int], exclude_first: Optional[int] = None) -> List[int]:
        if len(candidate) <= 1:
            return candidate

        rotated = candidate[1:] + candidate[:1]
        if exclude_first is not None and rotated[0] == exclude_first and len(rotated) > 1:
            rotated = rotated[1:] + rotated[:1]
        return rotated

    @staticmethod
    def _normalize_cycle(candidate: List[int], exclude_first: Optional[int] = None) -> List[int]:
        if exclude_first is None or len(candidate) <= 1:
            return candidate

        if candidate[0] != exclude_first:
            return candidate

        for idx in range(1, len(candidate)):
            if candidate[idx] != exclude_first:
                candidate[0], candidate[idx] = candidate[idx], candidate[0]
                return candidate

        return candidate

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------
    def get_playlist_info(self) -> dict:
        """Restituisce informazioni sulla playlist corrente (per UI/event bus)."""
        track = self.current_track
        track_dict = None
        try:
            if track and hasattr(track, "to_dict"):
                track_dict = track.to_dict()
        except QUEUE_SERIALIZE_EXCEPTIONS:
            logger.debug("[QueueManager] Failed to serialize current_track", exc_info=True)

        return {
            "current_index": self._index,
            "total_tracks": len(self._playlist),
            "current_track": track_dict,
        }
