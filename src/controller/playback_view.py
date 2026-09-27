"""Current, cache-only playback state for GUI readers.

No clock/COM/mixer call is permitted here. Context mutation during capture, absent
samples and stopped/shutdown state are explicit; observation is not seek completion.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from src.playback_observation import ProgressSnapshot, valid_source
from .component_player.playback_state_manager import PlaybackStateManager, PlayerState
from .component_player.queue_manager import QueueManager
from .seek_observation import SeekObservation, read_seek_observation


@dataclass(frozen=True, slots=True)
class PlaybackView:
    """One bounded presentation context plus its optional matching cached sample."""

    revision: int
    state: PlayerState
    path: str | None
    index: int | None
    title: str
    is_video: bool
    loop: bool
    shuffle: bool
    sample: ProgressSnapshot | None = None
    seek: SeekObservation = SeekObservation()
    continuity: int | None = None

    def __post_init__(self) -> None:
        if self.continuity is not None and (type(self.continuity) is not int or self.continuity < 0):
            raise ValueError('Invalid visual continuity identity')
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError('Invalid view revision')
        if type(self.state) is not PlayerState or not valid_source(self.path):
            raise ValueError('Invalid view state/path')
        if self.index is not None and (type(self.index) is not int or self.index < 0):
            raise ValueError('Invalid view index')
        if type(self.title) is not str or len(self.title) > 32768:
            raise ValueError('Invalid view title')
        if any(type(flag) is not bool for flag in (self.is_video, self.loop, self.shuffle)):
            raise TypeError('Invalid view flags')
        if type(self.seek) is not SeekObservation:
            raise TypeError('Invalid view seek observation')
        if self.seek.receipt is not None and (not self.is_video or self.seek.receipt.source != self.path):
            raise ValueError('Seek receipt does not belong to current view')
        if self.sample is not None:
            if type(self.sample) is not ProgressSnapshot:
                raise TypeError('Invalid view sample')
            if (self.sample.revision, self.sample.state, self.sample.path, self.sample.index) != (
                self.revision, self.state.name, self.path, self.index,
            ):
                raise ValueError('Sample does not belong to current view')
            if self.sample.clock.source not in (None, self.path):
                raise ValueError('Clock source does not belong to view')


class ViewSource(Protocol):
    """Production facade contract; the sample getter is cache-only."""

    state_manager: PlaybackStateManager
    queue_manager: QueueManager
    _is_shutdown: bool

    def get_progress_snapshot(self) -> ProgressSnapshot | None: ...


def _context(source: ViewSource) -> tuple[PlaybackView, object] | None:
    """Freeze context twice around the cache read; never call scalar clock getters."""
    if source._is_shutdown:
        return None
    manager, queue = source.state_manager, source.queue_manager
    revision, state = manager.playback_revision, manager.state
    track, index = queue.current_track, queue.index
    if manager.current_track is not track or (track is not None and manager.index != index):
        return None
    path = getattr(track, 'path', None)
    title = getattr(track, 'title', '')
    if title is None or title == '':
        title = Path(path).stem if type(path) is str and valid_source(path) else ''
    if type(title) is not str:
        raise TypeError('Track title is not text')
    view = PlaybackView(revision, state, path, index, title[:32768],
                        manager.is_video(), manager.loop_enabled, manager.shuffle_enabled,
                        continuity=getattr(manager, 'playback_epoch', None))
    return view, track


def read_playback_view(source: ViewSource) -> PlaybackView | None:
    """Read Python state and a single cached record with bounded before/after guards.

    Replacement, same-file seek/replay, malformed samples or lifecycle drift cause
    refusal, not legacy getter fallback. This is not an all-thread atomic snapshot.
    """
    try:
        before = _context(source)
        if before is None:
            return None
        if before[0].state in (PlayerState.IDLE, PlayerState.LOADING, PlayerState.STOPPED, PlayerState.ERROR):
            after = _context(source)
            return before[0] if after == before else None
        backend = _video_backend(source) if before[0].is_video else None
        seek_before = read_seek_observation(backend, before[0].path)
        sample = source.get_progress_snapshot()
        seek_after = read_seek_observation(backend, before[0].path)
        if seek_before != seek_after or (before[0].is_video and _video_backend(source) is not backend):
            return None
        after = _context(source)
        if after is None or before[0] != after[0] or before[1] is not after[1]:
            return None
        view = before[0]
        return PlaybackView(view.revision, view.state, view.path, view.index, view.title,
                            view.is_video, view.loop, view.shuffle, sample, seek_after, view.continuity)
    except (AttributeError, RuntimeError, TypeError, ValueError, OverflowError):
        # Optional display boundary. No numeric result or backend fallback on failure.
        return None


def _video_backend(source: ViewSource) -> object | None:
    """Optional facade boundary for non-video/test consumers; no native work."""
    engine = getattr(source, 'engine_controller', None)
    return getattr(engine, 'video_controller', None)
