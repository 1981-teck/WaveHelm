"""Single-flight UI/COM handoff: no UI wait, bounded buffers, explicit failures.

The wx owner consumes before requesting the next frame. There is at most one
worker request plus one completion, so a producer cannot overwrite the UI front
buffer. Locks protect only bounded metadata; native I/O/copy/release is outside.
Python task envelopes are bounded but not a certified heap-free real-time path.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import threading
import time
from typing import Callable, Protocol

from .wic_native import WicError, checked_size
from .wic_renderer import Frame


def wic_selected() -> bool:
    """Use the Windows-qualified WIC backend by default; explicit legacy override only."""
    default = 'wic' if os.name == 'nt' else 'legacy_hwnd'
    value = os.environ.get('WAVEHELM_VIDEO_BACKEND', default)
    if value not in ('wic', 'legacy_hwnd'):
        raise ValueError('WAVEHELM_VIDEO_BACKEND must be wic or legacy_hwnd')
    return value == 'wic'


class Renderer(Protocol):
    def pump_seek(self) -> None: ...
    def render(self, width: int, height: int, revision: int) -> Frame | None: ...
    def close(self) -> None: ...


class Submitter(Protocol):
    def __call__(self, name: str, callback: Callable[[], object], response: 'Ticket') -> bool: ...


@dataclass(slots=True)
class Ticket:
    """One worker receipt. Error objects preserve the actual manager boundary error."""
    pipeline: 'FramePipeline'
    revision: int
    width: int
    height: int
    started: float
    returned: bool = False

    def run(self) -> object:
        if not self.pipeline._eligible(self):
            return None
        if not self.pipeline.ready():
            self.pipeline.renderer.pump_seek()
            with self.pipeline._lock:
                self.pipeline.seek_pumps += 1
            return None  # Even a SEEKED callback during this tick publishes no pixels.
        return self.pipeline.renderer.render(self.width, self.height, self.revision)

    def put_nowait(self, value: object, /) -> None:
        self.pipeline._complete(self, value)


class FramePipeline:
    """An explicit preview backend, not a background creator of adapters/threads.

    Edges: shutdown while queued; resize/source/seek invalidates an in-flight
    result; refused/failed/timed-out worker; duplicate receipt; paused repaint.
    Pending seeks keep tick-only work alive, but consume/current stay fail-closed.
    The one-flight slot and revision guards also cover these tick-only requests.
    """

    def __init__(self, renderer: Renderer, submit: Submitter,
                 ready: Callable[[], bool], hwnd: int,
                 pump_ready: Callable[[], bool] | None = None) -> None:
        self.renderer, self.submit, self.ready = renderer, submit, ready
        self.hwnd = hwnd
        self._pump_ready = ready if pump_ready is None else pump_ready
        self._lock = threading.Lock()
        self._revision = 0
        self._size = (0, 0)
        self._ticket: Ticket | None = None
        self._completed: Frame | None = None
        self._front: Frame | None = None
        self._error: BaseException | None = None
        self._stopped = False
        self.requests = self.delivered = self.discarded = 0
        self.presented = 0
        self.seek_pumps = 0
        self._ui_owner: int | None = None

    def _ui(self) -> None:
        caller = threading.get_ident()
        if self._ui_owner is None:
            self._ui_owner = caller
        if self._ui_owner != caller:
            raise WicError('A second UI consumer attempted to own the WIC buffer')

    def invalidate(self) -> None:
        """Invalidate ownership before source/seek/rebind without cancelling native work."""
        with self._lock:
            self._revision += 1
            old = (self._completed, self._front)
            self._completed = self._front = None
        del old

    def rebind(self, hwnd: int) -> None:
        """No new engine: the new window must acquire its own GUI consumer identity."""
        if type(hwnd) is not int or hwnd <= 0:
            raise ValueError('Invalid WIC presentation HWND')
        self.invalidate()
        with self._lock:
            self.hwnd = hwnd
            self._ui_owner = None

    def stop(self) -> None:
        """Prevent further work immediately; native release stays with COM cleanup."""
        self.invalidate()
        with self._lock:
            self._stopped = True

    def fail(self, error: BaseException) -> None:
        """Latch the first failure without converting it to a legacy-backend request."""
        self.stop()
        with self._lock:
            if self._error is None:
                self._error = error

    def error(self) -> BaseException | None:
        with self._lock:
            return self._error

    def _eligible(self, ticket: Ticket) -> bool:
        with self._lock:
            valid = (self._ticket is ticket and ticket.revision == self._revision
                     and not self._stopped and self._error is None)
        return valid and self._pump_ready()

    def request(self, width: int, height: int) -> bool:
        """Consume completions first; at most one admission, no synchronous COM call."""
        self._ui()
        if width <= 0 or height <= 0:
            self.invalidate()
            return False
        checked_size(width, height)
        size = (width, height)
        if size != self._size:
            self.invalidate()
            self._size = size
        if not self._pump_ready():
            return False
        with self._lock:
            if self._stopped or self._ticket is not None or self._completed is not None:
                return False
            ticket = Ticket(self, self._revision, width, height, time.monotonic())
            self._ticket = ticket
            self.requests += 1
        try:
            admitted = self.submit('wic_frame', ticket.run, ticket)
        except (AttributeError, OSError, RuntimeError, ValueError, TypeError) as error:
            self.fail(error)  # Unknown admission: retain ticket and never retry.
            return False
        if admitted is not True:
            self.fail(WicError('COM worker refused WIC frame admission'))
            return False
        return True

    def _complete(self, ticket: Ticket, value: object) -> None:
        """Executed at the worker reply boundary; never runs GUI/native cleanup."""
        with self._lock:
            if ticket is not self._ticket or ticket.returned:
                return
            ticket.returned = True
            self._ticket = None
            valid = not self._stopped and ticket.revision == self._revision
            if isinstance(value, BaseException):
                self._error = value
                self._stopped = True
            elif value is not None and type(value) is not Frame:
                self._error = WicError('Worker returned an invalid WIC frame receipt')
                self._stopped = True
            elif isinstance(value, Frame) and valid and value.revision == self._revision:
                self._completed = value
                self.delivered += 1
            elif value is not None:
                self.discarded += 1

    def consume(self) -> Frame | None:
        """Advance front-buffer ownership on the GUI thread; check worker timeout."""
        self._ui()
        with self._lock:
            ticket = self._ticket
        if ticket is not None and time.monotonic() - ticket.started > 3.0:
            self.fail(TimeoutError('WIC frame response exceeded 3 seconds; no resubmission'))
        with self._lock:
            old = self._front
            if not self._stopped and self._completed is not None:
                self._front, self._completed = self._completed, None
            value = self._front if not self._stopped else None
        del old
        return value if self.ready() else None

    def current(self) -> Frame | None:
        """Borrow only the currently valid front; producer uses the other buffer."""
        self._ui()
        with self._lock:
            value = self._front
            valid = (not self._stopped and value is not None
                     and value.revision == self._revision)
        return value if valid and self.ready() else None

    def close_native(self) -> None:
        """Ordered behind admitted work on the existing COM thread."""
        self.stop()
        self.renderer.close()

    def snapshot(self) -> dict[str, int | bool | str]:
        """Cold, bounded diagnostics only; no per-frame logging or image hashes."""
        with self._lock:
            return {'requests': self.requests, 'delivered': self.delivered,
                    'presented': self.presented, 'discarded': self.discarded,
                    'seek_pumps': self.seek_pumps,
                    'in_flight': self._ticket is not None, 'stopped': self._stopped,
                    'error': '' if self._error is None else type(self._error).__name__}
