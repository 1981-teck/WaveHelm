"""Opt-in bounded drawing diagnostics; no file I/O or text formatting per frame.

Only numeric identities and enum labels are retained, not media paths/titles.
At most 2048 records per surface; flush once at close into a user-owned external
folder, using exclusive creation and folder/file budgets. Not crash-proof logging.
"""
from __future__ import annotations

from itertools import islice
import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING
import uuid

from src.playback_observation import finite_seconds

if TYPE_CHECKING:
    from src.controller.playback_view import PlaybackView
    from src.playback_observation import ProgressSnapshot
    from src.ui_wx.playback_presentation import DisplayReading

logger = logging.getLogger(__name__)
CAPACITY = 2048
MAX_FILES = 32
MAX_BYTES = 1024 * 1024
FIELDS = ('time', 'revision', 'continuity', 'index', 'state', 'sequence', 'query_start',
          'query_end', 'generation', 'seek_request', 'seek_phase', 'clock_position',
          'clock_duration', 'measured_position', 'measured_duration', 'drawn_ratio',
          'validity', 'motion', 'terminal', 'input_current', 'painted', 'reason')


class ProgressTrace:
    """GUI-owned fixed-size ring; disabled unless explicitly requested at startup."""
    def __init__(self, surface: str) -> None:
        self.directory: Path | None = None
        self.surface = surface if surface in ('mini', 'overlay') else 'unspecified'
        self._rows: list[tuple[object, ...] | None] = []
        self._count = 0
        self._closed = False
        raw = os.environ.get('WAVEHELM_PROGRESS_TRACE_DIR')
        if raw is None:
            return
        try:
            path = Path(raw)
            root = Path(__file__).resolve().parents[2]
            if not path.is_absolute() or path.is_symlink() or not path.is_dir():
                raise ValueError('Trace directory must exist and be absolute/non-symlink')
            path = path.resolve(strict=True)
            if path == root or root in path.parents:
                raise ValueError('Trace directory must be outside project source')
            self.directory = path
            self._rows = [None] * CAPACITY
        except (OSError, ValueError, RuntimeError):
            logger.warning('Progress trace disabled: invalid external directory.', exc_info=True)

    def record(self, view: PlaybackView | None, sample: ProgressSnapshot | None,
               reading: DisplayReading, now: float, *, current: bool, painted: bool,
               reason: str, preview_ratio: float | None = None) -> None:
        if self.directory is None or self._closed:
            return
        clock = sample.clock if sample is not None else None
        receipt = view.seek.receipt if view is not None else None
        row = (
            finite_seconds(now), view.revision if view else None,
            view.continuity if view else None, view.index if view else None,
            view.state.name if view else None, sample.sequence if sample else None,
            clock.started_at if clock else None, clock.finished_at if clock else None,
            clock.generation if clock else None, receipt.request_id if receipt is not None else None,
            reading.seek_phase.name if reading.seek_phase else None,
            clock.position.seconds if clock else None, clock.duration.seconds if clock else None,
            reading.position, reading.duration,
            reading.visual_ratio if preview_ratio is None else preview_ratio,
            reading.status.value, reading.motion_mode.value, reading.terminal,
            current, painted, reason,
        )
        self._rows[self._count % CAPACITY] = row
        self._count += 1

    def close(self) -> None:
        """Flush once; a failed diagnostic write never conceals a playback failure."""
        if self._closed:
            return
        self._closed = True
        if self.directory is None:
            return
        try:
            self._flush()
        except (OSError, RuntimeError, TypeError, ValueError):
            logger.warning('Progress trace could not be saved.', exc_info=True)
        finally:
            self._rows.clear()

    def _flush(self) -> None:
        directory = self.directory
        if directory is None:
            return
        files = list(islice(directory.glob('progress-*.json'), MAX_FILES))
        if len(files) >= MAX_FILES:
            raise ValueError('Progress trace file-count budget reached')
        count = min(self._count, CAPACITY)
        start = self._count - count
        rows = [self._rows[n % CAPACITY] for n in range(start, self._count)]
        payload = {'schema': 'wavehelm-progress-trace-v1', 'surface': self.surface,
                   'fields': FIELDS, 'total': self._count, 'omitted': self._count - count,
                   'records': rows, 'scope': 'application observations and widget writes, not pixel timing'}
        encoded = json.dumps(payload, ensure_ascii=True, allow_nan=False).encode('utf-8')
        if len(encoded) > MAX_BYTES:
            raise ValueError('Progress trace byte budget exceeded')
        target = directory / ('progress-' + self.surface + '-' + uuid.uuid4().hex + '.json')
        with target.open('xb') as output:
            output.write(encoded)
