from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import PurePath
from typing import Callable
from uuid import UUID

logger = logging.getLogger(__name__)

EventKey = 'AudioEventType | str'

# Intentional boundary: filter callbacks are user/application supplied and may
# raise arbitrary exceptions. The event bus isolates them as non-matches.
FILTER_CONDITION_EXCEPTIONS = (Exception,)


class EventBusShutdownError(RuntimeError):
    """Raised when an operation cannot complete because the bus is shutting down."""


class EventWaitConditionError(RuntimeError):
    """Raised to the waiting caller when its condition callback fails."""


MAX_EVENT_PAYLOAD_DEPTH = 16
MAX_EVENT_PAYLOAD_NODES = 20_000
MAX_EVENT_CONTAINER_ITEMS = 4_096
MAX_EVENT_TEXT_CHARS = 262_144
MAX_EVENT_BINARY_BYTES = 1_048_576


class EventPayloadSnapshotError(ValueError):
    """Raised when an event payload cannot be snapshotted safely."""


class _SnapshotBudget:
    __slots__ = ('active_containers', 'remaining_nodes')

    def __init__(self) -> None:
        self.active_containers: set[int] = set()
        self.remaining_nodes = MAX_EVENT_PAYLOAD_NODES

    def consume(self, depth: int) -> None:
        if depth > MAX_EVENT_PAYLOAD_DEPTH:
            raise EventPayloadSnapshotError('Event payload nesting exceeds the supported depth')
        self.remaining_nodes -= 1
        if self.remaining_nodes < 0:
            raise EventPayloadSnapshotError('Event payload exceeds the supported node budget')

    def enter_container(self, value: object) -> int:
        identity = id(value)
        if identity in self.active_containers:
            raise EventPayloadSnapshotError('Cyclic event payloads are not supported')
        self.active_containers.add(identity)
        return identity

    def leave_container(self, identity: int) -> None:
        self.active_containers.remove(identity)


_IMMUTABLE_TYPES = (
    type(None),
    bool,
    int,
    float,
    complex,
    datetime,
    date,
    time,
    timedelta,
    Decimal,
    Enum,
    PurePath,
    UUID,
    range,
)


def _validate_container_size(value: object, size: int) -> None:
    if size > MAX_EVENT_CONTAINER_ITEMS:
        raise EventPayloadSnapshotError(
            f'{type(value).__name__} payload exceeds {MAX_EVENT_CONTAINER_ITEMS} items'
        )


def _snapshot_text(value: str) -> str:
    if len(value) > MAX_EVENT_TEXT_CHARS:
        raise EventPayloadSnapshotError('Event payload text exceeds the supported size')
    return value


def _snapshot_binary(value: bytes | bytearray | memoryview) -> bytes | bytearray:
    if len(value) > MAX_EVENT_BINARY_BYTES:
        raise EventPayloadSnapshotError('Event payload binary data exceeds the supported size')
    if isinstance(value, bytearray):
        return bytearray(value)
    if isinstance(value, memoryview):
        return value.tobytes()
    return value


def _snapshot_mapping(value: dict[object, object], budget: _SnapshotBudget, depth: int) -> dict[object, object]:
    _validate_container_size(value, len(value))
    result: dict[object, object] = {}
    for key, item in value.items():
        key_snapshot = _snapshot_value(key, budget, depth + 1)
        item_snapshot = _snapshot_value(item, budget, depth + 1)
        try:
            result[key_snapshot] = item_snapshot
        except TypeError as error:
            raise EventPayloadSnapshotError('Event payload contains an unhashable mapping key') from error
    return result


def _snapshot_sequence(value: list[object] | tuple[object, ...], budget: _SnapshotBudget, depth: int) -> object:
    _validate_container_size(value, len(value))
    items = [_snapshot_value(item, budget, depth + 1) for item in value]
    return tuple(items) if isinstance(value, tuple) else items


def _snapshot_set(value: set[object] | frozenset[object], budget: _SnapshotBudget, depth: int) -> object:
    _validate_container_size(value, len(value))
    items = [_snapshot_value(item, budget, depth + 1) for item in value]
    try:
        return frozenset(items) if isinstance(value, frozenset) else set(items)
    except TypeError as error:
        raise EventPayloadSnapshotError('Event payload set contains an unhashable value') from error


def _snapshot_container(value: object, budget: _SnapshotBudget, depth: int) -> object:
    identity = budget.enter_container(value)
    try:
        if type(value) is dict:
            return _snapshot_mapping(value, budget, depth)
        if type(value) in (list, tuple):
            return _snapshot_sequence(value, budget, depth)
        if type(value) in (set, frozenset):
            return _snapshot_set(value, budget, depth)
        raise EventPayloadSnapshotError(
            f'Unsupported mutable event payload type: {type(value).__name__}'
        )
    finally:
        budget.leave_container(identity)


def _snapshot_value(value: object, budget: _SnapshotBudget, depth: int) -> object:
    budget.consume(depth)
    if isinstance(value, str):
        return _snapshot_text(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return _snapshot_binary(value)
    if isinstance(value, _IMMUTABLE_TYPES):
        return value
    return _snapshot_container(value, budget, depth)


def snapshot_event_payload(value: object) -> object:
    """Create a bounded payload snapshot without retaining caller-owned aliases.

    Time and memory complexity are O(n), bounded by the configured node,
    depth, container, text, and binary limits.

    Edge cases:
        1. Cyclic or excessively deep containers are rejected before history mutation.
        2. Oversized collections and byte/text payloads fail closed before dispatch.
        3. Unsupported mutable objects cannot retain aliases inside event history.
    """
    return _snapshot_value(value, _SnapshotBudget(), 0)


@dataclass(frozen=True)
class EventMetadata:
    """Immutable metadata for event tracking and diagnostics."""

    timestamp: datetime
    source: str | None = None
    priority: int = 0
    correlation_id: str | None = None
    event_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class AudioEventType(str, Enum):
    """Audio and system event types."""

    EVENT_BUS_STARTED = "event_bus_started"
    EVENT_BUS_SHUTDOWN = "event_bus_shutdown"
    SAMPLE_RATE_CHANGED = "sample_rate_changed"
    CHANNELS_CHANGED = "channels_changed"
    PLAY_REQUESTED = "play_requested"
    PAUSE_REQUESTED = "pause_requested"
    STOP_REQUESTED = "stop_requested"
    PREVIOUS_REQUESTED = "previous_requested"
    NEXT_REQUESTED = "next_requested"
    SEEK_REQUESTED = "seek_requested"
    SEEK = "seek"
    PLAYER_STATE_CHANGED = "player_state_changed"
    PLAYBACK_STARTED = "playback_started"
    PLAYBACK_PAUSED = "playback_paused"
    PLAYBACK_RESUMED = "playback_resumed"
    PLAYBACK_STOPPED = "playback_stopped"
    PLAYBACK_COMPLETED = "playback_completed"
    PLAYBACK_PROGRESS = "playback_progress"
    PLAYBACK_PRESENTATION_FINISHED = 'playback_presentation_finished'
    VOLUME_CHANGE_REQUESTED = "volume_change_requested"
    VOLUME_CHANGED = "volume_changed"
    MUTE_TOGGLED = "mute_toggled"
    MUTE_CHANGED = "mute_changed"
    PLAYLIST_CHANGED = "playlist_changed"
    PLAYLIST_UPDATED = "playlist_updated"
    TRACK_CHANGED = "track_changed"
    SHUFFLE_CHANGED = "shuffle_changed"
    LOOP_CHANGED = "loop_changed"
    CURRENT_PLAYLIST_SET = "current_playlist_set"
    MEDIA_LOADED = "media_loaded"
    MEDIA_ADDED = "media_added"
    MEDIA_DURATION_UPDATE = "media_duration_update"
    VIDEO_DURATION_UPDATE = "video_duration_update"
    NOW_PLAYING_CHANGED = "now_playing_changed"
    VIDEO_PLAYBACK_STARTING = "video_playback_starting"
    VIDEO_PLAYBACK_STARTED = "video_playback_started"
    VIDEO_PLAYBACK_PAUSED = "video_playback_paused"
    VIDEO_PLAYBACK_RESUMED = "video_playback_resumed"
    VIDEO_PLAYBACK_ERROR = "video_playback_error"
    VIDEO_PLAYBACK_ENDED = "video_playback_ended"
    VIDEO_PLAYBACK_STOPPED = "video_playback_stopped"
    VIDEO_WINDOW_CLOSED = "video_window_closed"
    VIDEO_WINDOW_MOVED = "video_window_moved"
    VIDEO_MOUSE_MOVED = "video_mouse_moved"
    PREPARE_VIDEO_PLAYBACK = "prepare_video_playback"
    VIDEO_PLAYBACK_READY = "video_playback_ready"
    CANCEL_VIDEO_PLAYBACK = "cancel_video_playback"
    UI_TOGGLE_FULLSCREEN = "ui_toggle_fullscreen"
    FULLSCREEN_TOGGLE_REQUESTED = "fullscreen_toggle_requested"
    EQ_CHANGED = "eq_changed"
    EFFECTS_CHANGED = "effects_changed"
    SPECTRUM_DATA_UPDATED = "spectrum_data_updated"
    LIBRARY_UPDATED = "library_updated"
    FAVORITES_UPDATED = "favorites_updated"
    FAVORITE_CHANGED = "favorite_changed"
    HISTORY_UPDATED = "history_updated"
    SETTINGS_UPDATED = "settings_updated"
    SETTING_UPDATED = "setting_updated"
    SETTINGS_BATCH_UPDATED = "settings_batch_updated"
    THEME_CHANGED = "theme_changed"
    LANGUAGE_CHANGED = "language_changed"
    FEEDBACK_MESSAGE = "feedback_message"
    PLAYER_ERROR = "player_error"
    WARNING_MESSAGE = "warning_message"
    ERROR = "error"
    AMBIENT_STARTED = "ambient_started"
    AMBIENT_STOPPED = "ambient_stopped"
    AMBIENT_VOLUME = "ambient_volume"
    AMBIENT_MUTED_CHANGED = "ambient_muted_changed"
    AMBIENT_PRESETS_UPDATED = "ambient_presets_updated"
    AMBIENT_PRESET_APPLIED = "ambient_preset_applied"
    PROFILE_LOADED = "profile_loaded"
    PRESET_APPLIED = "preset_applied"
    CUSTOM_PRESETS_UPDATED = "custom_presets_updated"
    UI_READY = "ui_ready"
    APP_STARTED = "app_started"
    APP_SHUTDOWN = "app_shutdown"
    CONFIG_CHANGED = "config_changed"


@dataclass(frozen=True)
class EventRecord:
    """Immutable record of one published event and its payload snapshot."""

    event_type: AudioEventType | str
    data: object
    metadata: EventMetadata
    subscribers_notified: int = 0
    processing_time_ms: float = 0.0


class EventSubscription:
    """Represents an event subscription with delivery metadata."""

    __slots__ = (
        'callback', 'priority', 'filter_condition', 'is_active',
        'subscription_id', 'created_at', 'call_count', 'one_time',
        '_delivery_claimed',
    )

    def __init__(
        self,
        callback: Callable[[object], None],
        priority: int = 0,
        filter_condition: Callable[[object], bool] | None = None,
        subscription_id: str | None = None,
        one_time: bool = False,
    ) -> None:
        self.callback = callback
        self.priority = priority
        self.filter_condition = filter_condition
        self.is_active = True
        self.subscription_id = subscription_id or f"sub_{id(self)}"
        self.created_at = datetime.now()
        self.call_count = 0
        self.one_time = one_time
        self._delivery_claimed = False

    def matches(self, callback: Callable[[object], None]) -> bool:
        return self.callback == callback

    def matches_id(self, subscription_id: str) -> bool:
        return self.subscription_id == subscription_id

    def should_receive(self, data: object) -> bool:
        if not self.is_active:
            return False
        if self.filter_condition is None:
            return True
        try:
            return bool(self.filter_condition(data))
        except FILTER_CONDITION_EXCEPTIONS as error:
            logger.error(
                'Error in filter condition for %s: %s',
                self.subscription_id,
                error,
                exc_info=True,
            )
            return False

    def try_claim_delivery(self) -> bool:
        """Claim one delivery while the owning event-bus lock is held."""
        if not self.is_active:
            return False
        if not self.one_time:
            return True
        if self._delivery_claimed:
            return False
        self._delivery_claimed = True
        return True

    def release_delivery_claim(self) -> None:
        if self.is_active and self.one_time:
            self._delivery_claimed = False

    def increment_call_count(self) -> None:
        self.call_count += 1

    def deactivate(self) -> None:
        self.is_active = False
        self._delivery_claimed = False
