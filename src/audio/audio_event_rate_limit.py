from __future__ import annotations

import math

MAX_RATE_LIMIT_EVENTS = 4096


class RateLimitWindow:
    """Preallocated sliding-window rate limiter for one event type.

    Time complexity:
        - reserve(): O(1) worst-case.
        - reset(): O(1) worst-case.

    Memory complexity:
        - O(max_events), allocated once during configuration.

    Edge cases:
        1. Non-finite or non-positive intervals are rejected before allocation.
        2. Boolean, non-integer, zero, or excessive capacities are rejected.
        3. A regressing monotonic clock fails closed without mutating the window.
    """

    __slots__ = (
        '_capacity',
        '_count',
        '_head',
        '_interval_seconds',
        '_last_observed',
        '_timestamps',
    )

    def __init__(self, interval_seconds: float, max_events: int) -> None:
        self._interval_seconds = self._validate_interval(interval_seconds)
        self._capacity = self._validate_capacity(max_events)
        self._timestamps = [0.0] * self._capacity
        self._head = 0
        self._count = 0
        self._last_observed = -math.inf

    @staticmethod
    def _validate_interval(value: object) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError('min_interval_seconds must be a finite positive number')
        interval = float(value)
        if not math.isfinite(interval) or interval <= 0.0:
            raise ValueError('min_interval_seconds must be finite and greater than zero')
        return interval

    @staticmethod
    def _validate_capacity(value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError('max_per_interval must be an integer')
        if value < 1 or value > MAX_RATE_LIMIT_EVENTS:
            raise ValueError(
                f'max_per_interval must be between 1 and {MAX_RATE_LIMIT_EVENTS}'
            )
        return value

    @property
    def config(self) -> tuple[float, int]:
        return self._interval_seconds, self._capacity

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def used(self) -> int:
        return self._count

    def reserve(self, now: float) -> bool:
        if not math.isfinite(now) or now < self._last_observed:
            return False
        self._last_observed = now

        if self._count < self._capacity:
            index = (self._head + self._count) % self._capacity
            self._timestamps[index] = now
            self._count += 1
            return True

        oldest = self._timestamps[self._head]
        if now - oldest < self._interval_seconds:
            return False

        self._timestamps[self._head] = now
        self._head = (self._head + 1) % self._capacity
        return True

    def reset(self) -> None:
        self._head = 0
        self._count = 0
        self._last_observed = -math.inf
