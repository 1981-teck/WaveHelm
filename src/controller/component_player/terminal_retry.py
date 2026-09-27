"""One bounded UI-handoff retry intent; no native operation is retried here."""
from __future__ import annotations
import threading
from src.playback_observation import ProgressSnapshot


class TerminalRetryGate:
    """Coalesce transient pre-teardown cache refusals, at most three retries per sample.

    A queued UI job can reply before its producer finishes dispatch. The independent
    flag survives that ordering. A new source/seek/sample invalidates an old intent.
    Missing native outcomes or teardown failures do not enter this retry route.
    """
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._key: tuple[str, int] | None = None
        self._attempts = 0
        self._pending = False

    def request(self, sample: ProgressSnapshot) -> bool:
        if not sample.terminal:
            return False
        key = (sample.stream, sample.sequence)
        with self._lock:
            if key != self._key:
                self._key, self._attempts, self._pending = key, 0, False
            if self._pending:
                return True
            if self._attempts >= 3:
                return False
            self._pending = True
            self._attempts += 1
            return True

    def take(self, sample: ProgressSnapshot | None) -> bool:
        key = (sample.stream, sample.sequence) if sample is not None else None
        with self._lock:
            pending, self._pending = self._pending, False
            return pending and key == self._key and sample is not None and sample.terminal
