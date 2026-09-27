"""Shared wx input boundary for both progress bars.

Pointer input uses one owned capture and commits only on button release. Native
scroll input commits only on SCROLL_CHANGED, not its duplicate EVT_SLIDER event.
Lost capture/focus, invalid context, Escape and teardown cancel without dispatch.
Preview modifies the thumb only, never the measured clock or end-of-stream state.
"""
from __future__ import annotations

import logging
import time
from typing import Protocol, cast

from src.controller.playback_status import seek_to
from src.controller.playback_view import PlaybackView
from src.ui_wx.playback_presentation import DisplayReading, PlaybackPresentation
from src.ui_wx.seek_gesture import GestureAnchor, SeekGesture, SeekHandoff, finite_number, ratio_value

logger = logging.getLogger(__name__)
_ERRORS = (AttributeError, RuntimeError, ReferenceError, TypeError, ValueError, OverflowError)


class SliderPort(Protocol):
    """Only wx control operations; dynamic event extraction stays at this boundary."""
    def Bind(self, event_type: object, handler: object) -> None: ...
    def GetValue(self) -> int: ...
    def GetClientSize(self) -> object: ...
    def CaptureMouse(self) -> None: ...
    def ReleaseMouse(self) -> None: ...
    def HasCapture(self) -> bool: ...
    def SetFocus(self) -> None: ...


class ProgressSurface(Protocol):
    _wx: object
    _closed: bool
    _dragging: bool
    _updating_progress_slider: bool
    _presentation: PlaybackPresentation
    progress_slider: SliderPort
    def _set_progress_slider_value(self, ratio: float) -> None: ...
    def close(self) -> None: ...


def _skip(event: object | None) -> None:
    skip = getattr(event, 'Skip', None)
    if callable(skip):
        skip()


class ProgressGestureMixin:
    """GUI-thread-only input adapter; fixed-size state, no native clock queries."""

    def _bind_progress_gesture(self) -> None:
        host = cast(ProgressSurface, self)
        self._seek_gesture = SeekGesture()
        self._capture_owned = False
        self._ignore_native_finish = False
        self._keyboard_gesture = False
        self._key_finish_scheduled = False
        self._key_finish_anchor: GestureAnchor | None = None
        bindings = (
            ('EVT_LEFT_DOWN', self._on_progress_slider_pointer_down),
            ('EVT_LEFT_UP', self._on_progress_slider_pointer_up),
            ('EVT_MOTION', self._on_progress_slider_motion),
            ('EVT_SCROLL_THUMBTRACK', self._on_progress_thumb_track),
            ('EVT_SCROLL_CHANGED', self._on_progress_slider_changed),
            ('EVT_MOUSE_CAPTURE_LOST', self._on_progress_capture_lost),
            ('EVT_KILL_FOCUS', self._on_progress_focus_lost),
            ('EVT_KEY_DOWN', self._on_progress_key_down),
            ('EVT_KEY_UP', self._on_progress_key_up),
            ('EVT_WINDOW_DESTROY', self._on_progress_destroy),
        )
        for name, callback in bindings:
            event_type = getattr(host._wx, name, None)
            if event_type is None:
                raise RuntimeError(f'Required Windows progress event unavailable: {name}')
            host.progress_slider.Bind(event_type, callback)

    def _gesture_ready(self) -> bool:
        host = cast(ProgressSurface, self)
        return not host._closed and not host._updating_progress_slider

    def _begin_progress(self) -> bool:
        host = cast(ProgressSurface, self)
        owner, view, reading = host._presentation.input_state()
        self._ignore_native_finish = False
        accepted = self._seek_gesture.begin(owner, view, reading, time.monotonic())
        host._dragging = accepted
        if accepted:
            host._presentation.cancel_seek_handoff()
        # A preview changes the native thumb, not the presentation's measured pair.
        host._presentation.invalidate_paint()
        return accepted

    def _progress_allows_render(self, view: PlaybackView | None,
                                reading: DisplayReading) -> bool:
        """Every timer/event still checks ownership while a gesture freezes painting."""
        host = cast(ProgressSurface, self)
        if self._seek_gesture.active:
            if (host._presentation.input_is_current
                    and self._seek_gesture.matches(host._presentation.source_owner, view, reading,
                                                   time.monotonic())):
                return False
            self._cancel_progress_gesture(repaint=False)
        return not host._dragging

    def _show_progress_preview(self, ratio: object) -> bool:
        host = cast(ProgressSurface, self)
        owner, view, reading = host._presentation.input_state()
        if (not self._seek_gesture.matches(owner, view, reading, time.monotonic())
                or not self._seek_gesture.preview(ratio)):
            self._cancel_progress_gesture()
            return False
        preview = self._seek_gesture.ratio
        if preview is None:
            self._cancel_progress_gesture()
            return False
        try:
            host._set_progress_slider_value(preview)
        except _ERRORS:
            host.close()
            return False
        host._presentation.note_preview(preview)
        return not host._closed

    def _release_progress_capture(self, *, lost: bool = False) -> bool:
        """Drop our ownership before ReleaseMouse; never release someone else's capture."""
        host = cast(ProgressSurface, self)
        owned, self._capture_owned = self._capture_owned, False
        if not owned or lost:
            return True
        try:
            if host.progress_slider.HasCapture():
                host.progress_slider.ReleaseMouse()
            return True
        except _ERRORS:
            logger.warning('Progress mouse release failed after intent invalidation.', exc_info=True)
            return False

    def _cancel_progress_gesture(self, *, repaint: bool = True, lost: bool = False) -> None:
        host = cast(ProgressSurface, self)
        self._seek_gesture.cancel()
        self._keyboard_gesture = False
        self._key_finish_anchor = None
        self._ignore_native_finish = True
        host._dragging = False
        self._release_progress_capture(lost=lost)
        if repaint and not host._closed:
            host._presentation.repaint()

    def _finish_progress(self) -> bool:
        """Consume once; preserve a submitted preview without publishing a fake clock.

        Reentrant refresh sees the handoff before dispatch. Rejection restores the
        observed display; cache/native completion and a later sample end the hold.
        """
        host = cast(ProgressSurface, self)
        self._ignore_native_finish = True  # ReleaseMouse may synchronously reenter wx.
        if not self._release_progress_capture():
            self._cancel_progress_gesture()
            return False
        accepted = False
        handoff: SeekHandoff | None = None
        try:
            owner, view, reading = host._presentation.input_state()
            target = self._seek_gesture.take_target(owner, view, reading, time.monotonic())
            host._dragging = False
            if target is not None and owner is not None and view is not None and not host._closed:
                handoff = host._presentation.begin_seek_handoff(
                    owner, view, target, reading.duration, time.monotonic())
                accepted = seek_to(owner, target)
                self._seek_gesture.mark_forwarded(accepted)
            return accepted
        finally:
            host._presentation.finish_seek_handoff(handoff, accepted, time.monotonic())
            host._dragging = False
            self._keyboard_gesture = False
            self._key_finish_anchor = None
            self._ignore_native_finish = True
            if not host._closed:
                host._presentation.repaint()

    def _pointer_ratio_from_progress_event(self, event: object | None) -> float | None:
        host = cast(ProgressSurface, self)
        getter = getattr(event, 'GetX', None)
        if not callable(getter):
            return None
        x = finite_number(getter())
        size = host.progress_slider.GetClientSize()
        raw_width = size[0] if isinstance(size, tuple) and size else getattr(size, 'width', None)
        width = finite_number(raw_width)
        if x is None or width is None or width <= 1:
            return None
        return ratio_value(x / (width - 1))

    def _on_progress_slider_pointer_down(self, event: object | None = None) -> None:
        if not self._gesture_ready():
            return
        host = cast(ProgressSurface, self)
        try:
            self._cancel_progress_gesture(repaint=False)
            ratio = self._pointer_ratio_from_progress_event(event)
            if ratio is None or not self._begin_progress():
                self._cancel_progress_gesture()
                return
            host.progress_slider.SetFocus()
            if host.progress_slider.HasCapture():
                self._cancel_progress_gesture()
                return
            host.progress_slider.CaptureMouse()
            self._capture_owned = True
            if not host.progress_slider.HasCapture():
                self._cancel_progress_gesture()
                return
            self._show_progress_preview(ratio)
        except _ERRORS:
            logger.debug('Progress pointer begin refused.', exc_info=True)
            self._cancel_progress_gesture()
        # Do not Skip: this absolute-pointer path owns native capture and preview.

    def _on_progress_slider_motion(self, event: object | None = None) -> None:
        if not self._gesture_ready() or not self._seek_gesture.active:
            _skip(event)
            return
        try:
            if not self._capture_owned:
                return
            host = cast(ProgressSurface, self)
            left_down = getattr(event, 'LeftIsDown', None)
            if not host.progress_slider.HasCapture() or (callable(left_down) and not left_down()):
                self._cancel_progress_gesture(lost=not host.progress_slider.HasCapture())
                return
            self._show_progress_preview(self._pointer_ratio_from_progress_event(event))
        except _ERRORS:
            logger.debug('Progress motion cancelled.', exc_info=True)
            self._cancel_progress_gesture()

    def _on_progress_slider_pointer_up(self, event: object | None = None) -> None:
        if not self._gesture_ready() or not self._capture_owned:
            return
        try:
            if not cast(ProgressSurface, self).progress_slider.HasCapture():
                self._cancel_progress_gesture(lost=True)
                return
            if self._show_progress_preview(self._pointer_ratio_from_progress_event(event)):
                self._finish_progress()
        except _ERRORS:
            logger.debug('Progress pointer release cancelled.', exc_info=True)
            self._cancel_progress_gesture()

    def _on_progress_thumb_track(self, event: object | None = None) -> None:
        """Native thumb preview; cancellation requires a fresh pointer/key interaction."""
        if (not self._gesture_ready() or self._capture_owned
                or self._ignore_native_finish):
            return
        try:
            if not self._seek_gesture.active and not self._begin_progress():
                return
            self._show_progress_preview(self._scroll_ratio(event))
        except _ERRORS:
            logger.debug('Native thumb preview cancelled.', exc_info=True)
            self._cancel_progress_gesture()

    def _scroll_ratio(self, event: object | None) -> float | None:
        getter = getattr(event, 'GetPosition', None)
        raw = getter() if callable(getter) else cast(ProgressSurface, self).progress_slider.GetValue()
        value = finite_number(raw)
        if value is None:
            return None
        return ratio_value(value / 1000.0)

    def _on_progress_slider_changed(self, event: object | None = None) -> None:
        """Single native scroll-end/keyboard commit, not the repeated EVT_SLIDER signal."""
        if not self._gesture_ready() or self._capture_owned or self._ignore_native_finish:
            return
        try:
            ratio = self._scroll_ratio(event)
            if ratio is None:
                self._cancel_progress_gesture()
                return
            if not self._seek_gesture.active and not self._begin_progress():
                return
            if self._show_progress_preview(ratio):
                self._finish_progress()
        except _ERRORS:
            logger.debug('Native scroll commit refused.', exc_info=True)
            self._cancel_progress_gesture()

    def _on_progress_key_down(self, event: object | None = None) -> None:
        host = cast(ProgressSurface, self)
        getter = getattr(event, 'GetKeyCode', None)
        try:
            key = getter() if callable(getter) else None
            if key == getattr(host._wx, 'WXK_ESCAPE', 27):
                self._cancel_progress_gesture()
                return
            names = ('LEFT', 'RIGHT', 'UP', 'DOWN', 'HOME', 'END', 'PAGEUP', 'PAGEDOWN')
            keys = tuple(getattr(host._wx, 'WXK_' + name, None) for name in names)
            if self._gesture_ready() and key is not None and key in keys:
                self._cancel_progress_gesture(repaint=False)
                if not self._begin_progress():
                    return
                self._keyboard_gesture = True
            _skip(event)
        except _ERRORS:
            logger.debug('Progress keyboard request cancelled.', exc_info=True)
            self._cancel_progress_gesture()

    def _on_progress_key_up(self, event: object | None = None) -> None:
        """Allow native end-of-scroll first; cancel a no-change key gesture afterwards.

        At an endpoint a key may produce no change notification. One deferred slot
        avoids a frozen drag. A completed/replaced/closed gesture cannot be cancelled
        by this cleanup. It does not infer or submit a missing native change.
        """
        _skip(event)
        if not self._gesture_ready() or not self._keyboard_gesture:
            return
        self._key_finish_anchor = self._seek_gesture.anchor
        if self._key_finish_scheduled:
            return
        self._key_finish_scheduled = True
        try:
            dispatch = getattr(cast(ProgressSurface, self)._wx, 'CallAfter')
            dispatch(self._drain_key_finish)
        except _ERRORS:
            self._key_finish_scheduled = False
            logger.debug('Keyboard gesture cleanup could not be scheduled.', exc_info=True)
            self._cancel_progress_gesture()

    def _drain_key_finish(self) -> None:
        anchor = self._key_finish_anchor
        self._key_finish_anchor = None
        self._key_finish_scheduled = False
        if (self._gesture_ready() and self._keyboard_gesture and anchor is not None
                and self._seek_gesture.anchor is anchor):
            self._cancel_progress_gesture()

    def _on_progress_capture_lost(self, event: object | None = None) -> None:
        self._cancel_progress_gesture(lost=True)

    def _on_progress_focus_lost(self, event: object | None = None) -> None:
        self._cancel_progress_gesture()
        _skip(event)

    def _on_progress_destroy(self, event: object | None = None) -> None:
        self._cancel_progress_gesture(repaint=False, lost=True)
        cast(ProgressSurface, self).close()
        _skip(event)

    def _seek_from_progress_ratio(self, ratio: float) -> bool:
        """Private compatibility action, still requires a current cached anchor."""
        if not self._gesture_ready() or not self._begin_progress():
            return False
        return self._show_progress_preview(ratio) and self._finish_progress()

    _seek_from_ratio = _seek_from_progress_ratio
