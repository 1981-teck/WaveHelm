"""COM-owner frame production into two reusable CPU buffers, never the window.

The UI retains the last buffer; only one request can be outstanding. A request
alternates buffers only after successful CopyPixels. Resize is a cold allocation
path. Steady-state pixel work is O(width*height), bounded by the output budget;
no hashing, image logging, growing queue or per-frame image allocation.
"""
from __future__ import annotations

import ctypes as C
from dataclasses import dataclass
import threading
import sys
from typing import Callable

from .wic_native import (BitmapInfo, I32, NormalRect, P, Rect, U8, U32,
                         WicApi, WicError, check_hr, checked_size, method, release, source_identity)


@dataclass(frozen=True, slots=True)
class Frame:
    """Borrowed immutable-until-next-consumption payload; no native COM pointer."""
    revision: int
    pts: int
    pixels: C.Array
    info: BitmapInfo


class WicRenderer:
    """Owned native target, used and destroyed only on the original COM thread.

    Edges: acquisition/readback failure; S_FALSE without a new frame; failed
    transfer/copy; resize during source change; duplicate or wrong-thread close.
    """

    def __init__(self, api: WicApi | None = None,
                 identity: Callable[[], str | None] | None = None) -> None:
        self.owner = threading.get_ident()
        self.identity = identity
        self.close_error: BaseException | None = None
        self.api = WicApi() if api is None else api
        self.factory, self.bitmap, self.engine = P(), P(), P()
        self.buffers: tuple[C.Array, C.Array] | None = None
        self.size = (0, 0)
        self.index = 0
        self.revision = -1
        self.last_pts = -1
        self.closed = False
        self.frames = 0
        self.no_frame = 0
        self.resizes = 0
        self._pts = C.c_int64(-1)
        self._source = NormalRect(0, 0, 1, 1)
        self._dest = Rect()
        self._border = (U8 * 4)(0, 0, 0, 255)
        self._info = BitmapInfo()

    def _owner(self) -> None:
        if threading.get_ident() != self.owner:
            raise WicError('WIC operation attempted outside the COM owner thread')

    def open(self, engine: P) -> None:
        """Bind borrowed engine functions, then acquire the first owned reference."""
        self._owner()
        if self.closed or self.engine.value or not engine.value:
            raise WicError('Invalid/duplicate WIC renderer initialization')
        self.engine = P(engine.value)
        self._tick = method(engine, 44, I32, (C.POINTER(C.c_int64),))
        self._transfer = method(engine, 43, I32, (P, C.POINTER(NormalRect), C.POINTER(Rect), P))
        self._current_source = method(engine, 7, I32, (C.POINTER(P),)) if self.identity else None
        self.factory = self.api.factory()

    def _resize(self, width: int, height: int) -> None:
        """Dispose obsolete native target before allocating the next bounded pair."""
        count = checked_size(width, height)
        release(self.bitmap)
        self.buffers = None
        self.size = (0, 0)
        self.bitmap = self.api.bitmap(self.factory, width, height)
        self.buffers = ((U8 * count)(), (U8 * count)())
        self._copy = method(self.bitmap, 7, I32, (P, U32, U32, P))
        self._info = BitmapInfo(40, width, -height, 1, 32, 0, count, 0, 0, 0, 0)
        self.size, self.index = (width, height), 0
        self.resizes += 1

    def render(self, width: int, height: int, revision: int) -> Frame | None:
        """Return a complete frame or the documented no-new-frame state, never fake success."""
        self._owner()
        if self.closed or not self.engine.value or not self.factory.value:
            raise WicError('Render requested without an open WIC owner')
        changed = self.size != (width, height)
        if changed:
            self._resize(width, height)
        refresh = changed or self.revision != revision
        if refresh:
            self._verify_identity()
            # MediaEngine scales/letterboxes once into the ENTIRE bitmap.
            self._dest = Rect(0, 0, width, height)
            self.revision, self.last_pts = revision, -1
        self._pts.value = -1
        hr = int(self._tick(self.engine, C.byref(self._pts))) & 0xFFFFFFFF
        if hr == 1:
            self.no_frame += 1
            # Resize/restore/rebind or completed seek while paused needs a repaint.
            if not refresh:
                return None
        else:
            check_hr(hr, 'OnVideoStreamTick')
        if hr == 0 and self._pts.value < 0:
            raise WicError('OnVideoStreamTick returned an invalid timestamp')
        return self._copy_frame(revision, self._pts.value if hr == 0 else self.last_pts)

    def pump_seek(self) -> None:
        """Poll frame-server progress without copying or publishing any pixels.

        The COM owner serializes this behind an accepted SetCurrentTime. S_FALSE
        is normal; bad HRESULT/timestamp, wrong-thread and closed owners fail.
        Revision, last frame and both pixel buffers are intentionally unchanged,
        so completed paused seeks still force the normal fresh-revision repaint.
        """
        self._owner()
        if self.closed or not self.engine.value or not self.factory.value:
            raise WicError('Seek tick requested without an open WIC owner')
        self._pts.value = -1
        hr = int(self._tick(self.engine, C.byref(self._pts))) & 0xFFFFFFFF
        if hr == 1:
            self.no_frame += 1
            return
        check_hr(hr, 'OnVideoStreamTick(seek)')
        if hr != 0 or self._pts.value < 0:
            raise WicError('Seek tick returned an invalid result or timestamp')

    def _verify_identity(self) -> None:
        """Product always supplies an identity; native-only unit fixtures may omit it."""
        if self.identity is None:
            return
        expected = self.identity()
        if expected is None or self._current_source is None:
            raise WicError('No committed source for WIC frame delivery')
        actual = self.api.current_source(self.engine, self._current_source)
        if source_identity(expected) != source_identity(actual):
            raise WicError('Native source identity differs from the committed source')

    def _copy_frame(self, revision: int, pts: int) -> Frame:
        if self.buffers is None:
            raise WicError('WIC pixel storage unavailable')
        output = self.buffers[self.index]
        check_hr(self._transfer(self.engine, self.bitmap, C.byref(self._source),
                                C.byref(self._dest), self._border), 'TransferVideoFrame(WIC)')
        check_hr(self._copy(self.bitmap, None, self.size[0] * 4, C.sizeof(output), output), 'WIC.CopyPixels')
        self.index = 1 - self.index
        self.last_pts = pts
        self.frames += 1
        return Frame(revision, pts, output, self._info)

    def close(self) -> None:
        """Release only on the owner, once. No GC cleanup and no cross-thread fallback."""
        self._owner()
        if self.closed:
            if self.close_error is not None:
                raise WicError('WIC cleanup remains uncertain; no second release') from self.close_error
            return
        self.closed = True
        self.engine.value = None
        try:
            try:
                release(self.bitmap)
            finally:
                release(self.factory)
                self.buffers = None
                self.identity = None
            self.api.verify_resolved()
        finally:
            self.close_error = sys.exception()
