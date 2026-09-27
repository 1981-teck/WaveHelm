"""Control sizing and container layout; keep virtual extents scroll-only."""
from __future__ import annotations

import logging
import sys
from typing import Any, Iterable

WX_CALLBACK_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)
logger = logging.getLogger(__name__)

def _coerce_size(value: Any) -> tuple[int, int] | None:
    if value is None:
        return None
    width = getattr(value, "width", None)
    height = getattr(value, "height", None)
    if width is None or height is None:
        try:
            width, height = value
        except (TypeError, ValueError):
            return None
    try:
        return (max(1, int(width)), max(1, int(height)))
    except (TypeError, ValueError):
        return None


def _estimate_text_size(text: str, *, base_width: int = 24, char_width: int = 8, height: int = 28) -> tuple[int, int]:
    normalized = str(text or '').strip()
    lines = normalized.splitlines() or ['']
    width = max(len(line) for line in lines) * char_width + base_width
    return (max(base_width, width), max(height, 24 * len(lines)))


def _is_scrolled_container(widget: object) -> bool:
    """Use actual toolkit types, never the mere presence of FitInside on wx.Window."""
    wx = sys.modules.get('wx')
    types = tuple(cls for name in ('Scrolled', 'ScrolledWindow', 'ScrolledCanvas')
                  if isinstance(cls := getattr(wx, name, None), type))
    return bool(types) and isinstance(widget, types)


def _relayout_ancestors(widget: Any, *, max_depth: int = 6) -> None:
    """Keep ordinary panels/book/frame client-sized; preserve real scroll extents.

    The bounded walk tolerates destruction during relayout. It does not call Fit,
    SetVirtualSize or SetSizeHints on ordinary containers or reset column settings.
    """
    current = widget
    for _ in range(max(0, max_depth)):
        if current is None:
            break
        try:
            layout = getattr(current, 'Layout', None)
            if callable(layout):
                layout()
            fit = getattr(current, 'FitInside', None)
            if _is_scrolled_container(current) and callable(fit):
                fit()
            parent = getattr(current, 'GetParent', None)
            current = parent() if callable(parent) else None
        except WX_CALLBACK_EXCEPTIONS:
            logger.debug('Layout ancestor became unavailable.', exc_info=True)
            break


def _widget_class_name(widget: Any) -> str:
    return getattr(getattr(widget, '__class__', None), '__name__', '').lower()


def _supports_label_autosize(widget: Any) -> bool:
    class_name = _widget_class_name(widget)
    return any(token in class_name for token in ('button', 'checkbox', 'toggle'))


def _unconstrained_best_size(widget: Any) -> tuple[int, int] | None:
    """Measure afresh without feeding a previously auto-assigned minimum back in."""
    get_min = getattr(widget, 'GetMinSize', None)
    old_min = get_min() if callable(get_min) else getattr(widget, 'min_size', None)
    set_min = getattr(widget, 'SetMinSize', None)
    try:
        if callable(set_min):
            set_min((-1, -1))
        invalidate = getattr(widget, 'InvalidateBestSize', None)
        if callable(invalidate):
            invalidate()
        getter = getattr(widget, 'GetBestSize', None)
        return _coerce_size(getter()) if callable(getter) else None
    finally:
        if callable(set_min):
            set_min(old_min if old_min is not None else (-1, -1))


def _apply_size(widget: Any, size: tuple[int, int]) -> None:
    for name in ('SetMinSize', 'SetInitialSize'):
        setter = getattr(widget, name, None)
        if callable(setter):
            setter(size)


def autosize_labeled_control(widget: Any, text: str | None = None) -> None:
    if widget is None or not _supports_label_autosize(widget):
        return
    try:
        best = _unconstrained_best_size(widget)
        if best is None:
            best = _estimate_text_size(str(text or getattr(widget, 'label', '')))
        _apply_size(widget, best)
    except WX_CALLBACK_EXCEPTIONS:
        logger.debug('Label sizing incomplete.', exc_info=True)
    _relayout_ancestors(widget)


def dip_size(widget: Any, pixels: int) -> int:
    """Convert a design spacing to toolkit units; never scale a native best-size twice."""
    converter = getattr(widget, 'FromDIP', None)
    if callable(converter):
        try:
            value = int(converter(pixels))
            if value > 0:
                return value
        except WX_CALLBACK_EXCEPTIONS:
            logger.debug('DIP conversion unavailable.', exc_info=True)
    return pixels


def _choice_labels(choice: Any, items: Iterable[str] | None) -> list[str]:
    if items is not None:
        return [str(item) for item in items]
    counter, getter = getattr(choice, 'GetCount', None), getattr(choice, 'GetString', None)
    if callable(counter) and callable(getter):
        return [str(getter(index)) for index in range(counter())]
    return [str(item) for item in getattr(choice, 'items', ())]


def _natural_choice_size(choice: Any, texts: list[str]) -> tuple[int, int]:
    """Keep native chrome and glyph metrics; scale only design-sized minimums."""
    best = _unconstrained_best_size(choice)
    measured = _measure_choices(choice, texts)
    if best is None and measured is None:
        longest = max(texts, key=len, default='')
        return _estimate_text_size(longest, base_width=64)
    width, height = best or (1, dip_size(choice, 28))
    return (max(width, dip_size(choice, 80), measured or 0),
            max(height, dip_size(choice, 28)))


def _measure_choices(choice: Any, texts: list[str]) -> int | None:
    measure = getattr(choice, 'GetTextExtent', None)
    if not callable(measure):
        return None
    widths = []
    for text in texts:
        try:
            size = _coerce_size(measure(text))
        except WX_CALLBACK_EXCEPTIONS:
            logger.debug('Choice glyph measurement unavailable.', exc_info=True)
            return None
        if size is not None:
            widths.append(size[0])
    return max(widths, default=0) + dip_size(choice, 36)


def autosize_choice_control(choice: Any, items: Iterable[str] | None = None) -> None:
    """Size the existing Choice from native metrics, preserving contents and selection."""
    try:
        size = _natural_choice_size(choice, _choice_labels(choice, items))
        _apply_size(choice, size)
    except WX_CALLBACK_EXCEPTIONS:
        logger.debug('Choice sizing incomplete.', exc_info=True)
    _relayout_ancestors(choice)


def create_flow_sizer(wx_module: Any) -> Any:
    wrap_sizer_cls = getattr(wx_module, 'WrapSizer', None)
    if wrap_sizer_cls is not None:
        try:
            return wrap_sizer_cls(getattr(wx_module, 'HORIZONTAL', 0))
        except WX_CALLBACK_EXCEPTIONS:
            pass
    return wx_module.BoxSizer(getattr(wx_module, 'HORIZONTAL', 0))
