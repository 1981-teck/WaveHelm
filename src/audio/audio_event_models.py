from __future__ import annotations

import logging
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Deque, Dict, List, Optional

logger = logging.getLogger(__name__)

# Intentional boundary: filter callbacks are user/application supplied and may
# raise arbitrary exceptions. The event bus must isolate them and treat the
# filter as a non-match instead of letting the error escape.
FILTER_CONDITION_EXCEPTIONS = (Exception,)


@dataclass(frozen=True)
class EventMetadata:
    """Metadata for event tracking and debugging."""

    timestamp: datetime
    source: Optional[str] = None
    priority: int = 0
    correlation_id: Optional[str] = None
    event_id: Optional[str] = None

    def to_dict(self) -> dict:
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


@dataclass
class EventRecord:
    """Record of a published event."""

    event_type: AudioEventType
    data: Any
    metadata: EventMetadata
    subscribers_notified: int = 0
    processing_time_ms: float = 0.0


class EventSubscription:
    """Represents an event subscription with metadata."""

    __slots__ = (
        'callback', 'priority', 'filter_condition', 'is_active',
        'subscription_id', 'created_at', 'call_count'
    )

    def __init__(
        self,
        callback: Callable[[Any], None],
        priority: int = 0,
        filter_condition: Optional[Callable[[Any], bool]] = None,
        subscription_id: Optional[str] = None,
    ):
        self.callback = callback
        self.priority = priority
        self.filter_condition = filter_condition
        self.is_active = True
        self.subscription_id = subscription_id or f"sub_{id(self)}"
        self.created_at = datetime.now()
        self.call_count = 0

    def matches(self, callback: Callable[[Any], None]) -> bool:
        return self.callback == callback

    def matches_id(self, subscription_id: str) -> bool:
        return self.subscription_id == subscription_id

    def should_receive(self, data: Any) -> bool:
        if not self.is_active:
            return False
        if self.filter_condition is None:
            return True
        try:
            return self.filter_condition(data)
        except FILTER_CONDITION_EXCEPTIONS as e:
            logger.error("Error in filter condition for %s: %s", self.subscription_id, e)
            return False

    def increment_call_count(self) -> None:
        self.call_count += 1

    def deactivate(self) -> None:
        self.is_active = False
