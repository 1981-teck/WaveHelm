"""wx presentation boundary for the explicit WIC preview backend.

Only the GUI timer/paint handlers call wx/GDI. Native MediaEngine/WIC work uses
the existing COM owner and a single-flight mailbox. Display-derived timer pacing
is not a claim of vertical-blank synchronization or real-time certification.
"""
from __future__ import annotations

import logging
from ctypes import ArgumentError
import time
from typing import Callable, Protocol

from src.video.wic_native import GdiPainter, WicError
from src.video.wic_pipeline import FramePipeline

logger = logging.getLogger(__name__)
_ERRORS = (AttributeError, OSError, RuntimeError, TypeError, ValueError, ArgumentError)


class TimerPort(Protocol):
    def Start(self, milliseconds: int) -> bool: ...
    def Stop(self) -> None: ...


class PanelPort(Protocol):
    def Bind(self, event: object, callback: Callable, source: object = ...) -> None: ...
    def Unbind(self, event: object, **kwargs: object) -> bool: ...
    def SetBackgroundStyle(self, style: int) -> None: ...
    def GetHandle(self) -> int: ...
    def GetClientSize(self) -> tuple[int, int]: ...
    def IsShownOnScreen(self) -> bool: ...
    def Refresh(self, eraseBackground: bool = ...) -> None: ...


class PaintPort(Protocol):
    def GetHandle(self) -> int: ...
    def SetBackground(self, brush: object) -> None: ...
    def Clear(self) -> None: ...


class WxPort(Protocol):
    EVT_PAINT: object
    EVT_TIMER: object
    BG_STYLE_PAINT: int
    def Timer(self, panel: PanelPort) -> TimerPort: ...
    def AutoBufferedPaintDC(self, panel: PanelPort) -> PaintPort: ...
    def Brush(self, colour: str) -> object: ...
    def CallAfter(self, callback: Callable, *args: object) -> None: ...
    def GetTopLevelParent(self, panel: PanelPort) -> object: ...


def display_interval(wx: object, panel: PanelPort) -> int:
    """Resolve monitor rate at startup/move. Unknown rate uses an explicit 60 Hz budget."""
    display = getattr(wx, 'Display', None)
    if display is not None:
        index = display.GetFromWindow(panel)
        if isinstance(index, int) and index >= 0:
            refresh = int(display(index).GetCurrentMode().refresh)
            if 24 <= refresh <= 360:
                return max(8, round(1000 / min(120, refresh)))
    logger.warning('[WIC] Monitor refresh unavailable; timer budget=60Hz (not vsync)')
    return 17


class WicSurfaceDriver:
    """One timer and one double-buffered paint consumer, detached with its panel.

    Edges: no adapter yet; minimized/zero-sized viewport; destroyed window;
    native completion after source switch; queued callback after panel teardown.
    """

    def __init__(
        self,
        wx: WxPort,
        panel: PanelPort,
        *,
        pipeline_resolver: Callable[[int], FramePipeline | None],
        error_reporter: Callable[[BaseException], None],
    ) -> None:
        self.wx, self.panel = wx, panel
        self.pipeline_resolver = pipeline_resolver
        self.error_reporter = error_reporter
        self.painter = GdiPainter()
        self.background = wx.Brush('#000000')
        self.pipeline: FramePipeline | None = None
        self.closed = False
        self.reported: BaseException | None = None
        self.first_revision = -1
        self.logged_revision = -1
        self.last_frame: object | None = None
        self.next_monitor_query = 0.0
        self.suspended = False
        self.interval = display_interval(wx, panel)
        self.next_tick = 0.0
        self.timer = wx.Timer(panel)
        bound_paint = bound_timer = committed = False
        try:
            panel.SetBackgroundStyle(wx.BG_STYLE_PAINT)
            panel.Bind(wx.EVT_PAINT, self.on_paint)
            bound_paint = True
            panel.Bind(wx.EVT_TIMER, self.on_timer, self.timer)
            bound_timer = True
            if self.timer.Start(self.interval) is not True:
                raise WicError('wx refused the WIC presentation timer')
            committed = True
        finally:
            if not committed:
                self.timer.Stop()
                if bound_timer:
                    panel.Unbind(wx.EVT_TIMER, source=self.timer, handler=self.on_timer)
                if bound_paint:
                    panel.Unbind(wx.EVT_PAINT, handler=self.on_paint)
                self.closed = True

    def _resolve(self) -> FramePipeline | None:
        """A dynamic wx/factory boundary; internal messages are FramePipeline objects."""
        pipeline = self.pipeline_resolver(int(self.panel.GetHandle()))
        if pipeline is not None and not isinstance(pipeline, FramePipeline):
            raise WicError('Adapter advertised an invalid WIC pipeline')
        if pipeline is not self.pipeline:
            self.pipeline = pipeline
            self.last_frame = None
            self.first_revision = -1
            if pipeline is not None:
                logger.info('[WIC] PIPELINE_BOUND hwnd=%d', int(self.panel.GetHandle()))
            self.panel.Refresh(False)
        return pipeline

    def _visible(self) -> bool:
        if not self.panel.IsShownOnScreen():
            return False
        top = self.wx.GetTopLevelParent(self.panel)
        iconized = getattr(top, 'IsIconized', None)
        return not (callable(iconized) and iconized())

    def _report(self, error: BaseException) -> None:
        if self.reported is not None or self.closed:
            return
        self.reported = error
        self.timer.Stop()
        if self.pipeline is not None:
            self.pipeline.fail(error)
        self.panel.Refresh(False)
        logger.error('[WIC] UI_FRAME_ERROR %s: %s', type(error).__name__, str(error)[:1024])
        # Avoid destroying the wx window while its PaintDC is still alive.
        self.wx.CallAfter(self.error_reporter, error)

    def on_timer(self, event: object = None) -> None:
        if self.closed or self.reported is not None:
            return
        try:
            now = time.monotonic()
            if now < self.next_tick:
                return
            self.next_tick = now + self.interval / 1000  # No catch-up burst.
            self._tick(now)
        except _ERRORS as error:
            self._report(error)

    def _tick(self, now: float) -> None:
        pipeline = self._resolve()
        if pipeline is None:
            return
        error = pipeline.error()
        if error is not None:
            self._report(error)
            return
        width, height = self.panel.GetClientSize()
        hidden = not self._visible() or width <= 0 or height <= 0
        if hidden:
            if not self.suspended:
                pipeline.invalidate()
            self.suspended = True
            return
        if self.suspended:
            pipeline.invalidate()
            self.panel.Refresh(False)
            self.suspended = False
        if now >= self.next_monitor_query:
            self.next_monitor_query = now + 2.0
            self._report_presentation(pipeline)
            interval = display_interval(self.wx, self.panel)
            if interval != self.interval:
                self.interval = interval
                if self.timer.Start(interval) is not True:
                    raise WicError('wx timer refused monitor-rate change')
        frame = pipeline.consume()
        if frame is not self.last_frame:
            self.last_frame = frame
            self.panel.Refresh(False)
        pipeline.request(int(width), int(height))

    def _report_presentation(self, pipeline: FramePipeline) -> None:
        """Cold (at most once per 2 seconds) receipt, never log in the paint callback."""
        frame = pipeline.current()
        if frame is not None and self.first_revision == frame.revision and self.logged_revision != frame.revision:
            self.logged_revision = frame.revision
            logger.info('[WIC] PRESENTED revision=%d viewport=%dx%d pts=%d',
                        frame.revision, frame.info.width, -frame.info.height, frame.pts)

    def on_paint(self, event: object = None) -> None:
        if self.closed:
            return
        try:
            # BG_STYLE_PAINT + AutoBufferedPaintDC prevents exposing an intermediate
            # black clear before the full-frame StretchDIBits copy reaches the panel.
            dc = self.wx.AutoBufferedPaintDC(self.panel)
            frame = None if self.pipeline is None else self.pipeline.current()
            width, height = self.panel.GetClientSize()
            geometry_ok = (frame is not None and
                           (int(frame.info.width), -int(frame.info.height)) == (width, height))
            if self.closed or self.reported is not None or not geometry_ok:
                dc.SetBackground(self.background)
                dc.Clear()
                return
            self.painter.paint(int(dc.GetHandle()), frame.pixels, frame.info)
            self.pipeline.presented += 1
            if frame.revision != self.first_revision:
                self.first_revision = frame.revision
        except _ERRORS as error:
            self._report(error)

    def close(self) -> None:
        """Stop GUI requests; the core's detached cleanup still owns native release."""
        if self.closed:
            return
        self.closed = True
        self.timer.Stop()
        if self.pipeline is not None:
            self.pipeline.invalidate()
        self.panel.Unbind(self.wx.EVT_TIMER, source=self.timer, handler=self.on_timer)
        self.panel.Unbind(self.wx.EVT_PAINT, handler=self.on_paint)
        self.pipeline = None
        self.last_frame = None
