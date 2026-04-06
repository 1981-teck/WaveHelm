from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

WX_CALLBACK_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)


def get_localized_text(localization_manager: Any, key: str, default: str, **kwargs: Any) -> str:
    if localization_manager is None:
        return default.format(**kwargs) if kwargs else default
    try:
        text = str(localization_manager.get_text(key, default=default, **kwargs))
    except WX_CALLBACK_EXCEPTIONS:
        text = default
    try:
        return text.format(**kwargs) if kwargs else text
    except (IndexError, KeyError, ValueError):
        return text


def get_current_language(localization_manager: Any, default_language: str = 'en') -> str:
    for attribute_name in ('get_current_language', 'get_language'):
        getter = getattr(localization_manager, attribute_name, None)
        if callable(getter):
            try:
                value = getter()
            except WX_CALLBACK_EXCEPTIONS:
                continue
            if value:
                return str(value).lower()
    return str(default_language or 'en').lower()


def get_theme_colors(theme_manager: Any) -> dict[str, str]:
    getter = getattr(theme_manager, 'get_current_theme_colors', None)
    if not callable(getter):
        return {}
    try:
        colors = getter()
    except WX_CALLBACK_EXCEPTIONS:
        return {}
    return dict(colors) if isinstance(colors, dict) else {}


def register_callback(manager: Any, register_name: str, callback: Any, *, logger: logging.Logger, message: str) -> None:
    register = getattr(manager, register_name, None)
    if not callable(register):
        return
    try:
        register(callback)
    except WX_CALLBACK_EXCEPTIONS:
        logger.debug(message, exc_info=True)


def unregister_callback(manager: Any, unregister_name: str, callback: Any, *, logger: logging.Logger, message: str) -> None:
    unregister = getattr(manager, unregister_name, None)
    if not callable(unregister):
        return
    try:
        unregister(callback)
    except WX_CALLBACK_EXCEPTIONS:
        logger.debug(message, exc_info=True)




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


def _relayout_ancestors(widget: Any, *, max_depth: int = 6) -> None:
    current = widget
    depth = 0
    while current is not None and depth < max_depth:
        layout = getattr(current, 'Layout', None)
        if callable(layout):
            try:
                layout()
            except WX_CALLBACK_EXCEPTIONS:
                pass
        fit_inside = getattr(current, 'FitInside', None)
        if callable(fit_inside):
            try:
                fit_inside()
            except WX_CALLBACK_EXCEPTIONS:
                pass
        parent_getter = getattr(current, 'GetParent', None)
        current = parent_getter() if callable(parent_getter) else None
        depth += 1


def _widget_class_name(widget: Any) -> str:
    return getattr(getattr(widget, '__class__', None), '__name__', '').lower()


def _supports_label_autosize(widget: Any) -> bool:
    class_name = _widget_class_name(widget)
    return any(token in class_name for token in ('button', 'checkbox', 'toggle'))


def autosize_labeled_control(widget: Any, text: str | None = None) -> None:
    if widget is None or not _supports_label_autosize(widget):
        return
    invalidate = getattr(widget, 'InvalidateBestSize', None)
    if callable(invalidate):
        try:
            invalidate()
        except WX_CALLBACK_EXCEPTIONS:
            pass
    best_size = None
    getter = getattr(widget, 'GetBestSize', None)
    if callable(getter):
        try:
            best_size = _coerce_size(getter())
        except WX_CALLBACK_EXCEPTIONS:
            best_size = None
    if best_size is None:
        best_size = _estimate_text_size(str(text or getattr(widget, 'label', '')))
    set_min_size = getattr(widget, 'SetMinSize', None)
    if callable(set_min_size):
        try:
            set_min_size(best_size)
        except WX_CALLBACK_EXCEPTIONS:
            pass
    set_initial_size = getattr(widget, 'SetInitialSize', None)
    if callable(set_initial_size):
        try:
            set_initial_size(best_size)
        except WX_CALLBACK_EXCEPTIONS:
            pass
    _relayout_ancestors(widget)


def autosize_choice_control(choice: Any, items: Iterable[str] | None = None) -> None:
    if choice is None:
        return
    texts = []
    if items is not None:
        texts.extend(str(item) for item in items)
    else:
        stored_items = getattr(choice, 'items', None)
        if isinstance(stored_items, list):
            texts.extend(str(item) for item in stored_items)
    get_selection = getattr(choice, 'GetStringSelection', None)
    if callable(get_selection):
        try:
            selection = get_selection()
        except WX_CALLBACK_EXCEPTIONS:
            selection = ''
        if selection:
            texts.append(str(selection))
    probe_text = max(texts, key=len) if texts else 'W'
    estimated_width, estimated_height = _estimate_text_size(probe_text, base_width=40, char_width=8, height=28)
    width = estimated_width + 24
    height = estimated_height
    getter = getattr(choice, 'GetBestSize', None)
    if callable(getter):
        try:
            best_size = _coerce_size(getter())
        except WX_CALLBACK_EXCEPTIONS:
            best_size = None
        if best_size is not None:
            height = max(height, best_size[1])
    stable_size = (width, height)
    set_min_size = getattr(choice, 'SetMinSize', None)
    if callable(set_min_size):
        try:
            set_min_size(stable_size)
        except WX_CALLBACK_EXCEPTIONS:
            pass
    set_initial_size = getattr(choice, 'SetInitialSize', None)
    if callable(set_initial_size):
        try:
            set_initial_size(stable_size)
        except WX_CALLBACK_EXCEPTIONS:
            pass
    _relayout_ancestors(choice)


def create_flow_sizer(wx_module: Any) -> Any:
    wrap_sizer_cls = getattr(wx_module, 'WrapSizer', None)
    if wrap_sizer_cls is not None:
        try:
            return wrap_sizer_cls(getattr(wx_module, 'HORIZONTAL', 0))
        except WX_CALLBACK_EXCEPTIONS:
            pass
    return wx_module.BoxSizer(getattr(wx_module, 'HORIZONTAL', 0))

def apply_colors(widget: Any, *, background: str | None = None, foreground: str | None = None) -> None:
    if background:
        setter = getattr(widget, 'SetBackgroundColour', None)
        if callable(setter):
            setter(background)
    if foreground:
        setter = getattr(widget, 'SetForegroundColour', None)
        if callable(setter):
            setter(foreground)
    refresh = getattr(widget, 'Refresh', None)
    if callable(refresh):
        refresh()


def set_label_text(widget: Any, text: str) -> None:
    setter = getattr(widget, 'SetLabel', None)
    if callable(setter):
        setter(text)
        autosize_labeled_control(widget, text)
        return
    value_setter = getattr(widget, 'SetValue', None)
    if callable(value_setter):
        value_setter(text)
        _relayout_ancestors(widget)



def set_listctrl_column_label(list_ctrl: Any, index: int, label: str) -> None:
    columns = getattr(list_ctrl, 'columns', None)
    if isinstance(columns, list) and index < len(columns):
        columns[index] = label

    getter = getattr(list_ctrl, 'GetColumn', None)
    setter = getattr(list_ctrl, 'SetColumn', None)
    if callable(getter) and callable(setter):
        item = None
        try:
            item = getter(index)
        except WX_CALLBACK_EXCEPTIONS:
            item = None
        if item is not None:
            text_setter = getattr(item, 'SetText', None)
            if callable(text_setter):
                text_setter(label)
            elif hasattr(item, 'text'):
                item.text = label
            elif hasattr(item, 'm_text'):
                item.m_text = label
            label_setter = getattr(item, 'SetLabel', None)
            if callable(label_setter):
                label_setter(label)
            try:
                setter(index, item)
                return
            except WX_CALLBACK_EXCEPTIONS:
                pass

    if callable(setter):
        try:
            setter(index, label)
            return
        except WX_CALLBACK_EXCEPTIONS:
            pass

    inserter = getattr(list_ctrl, 'InsertColumn', None)
    if callable(inserter):
        try:
            inserter(index, label)
        except WX_CALLBACK_EXCEPTIONS:
            return


def restore_listctrl_column_widths(
    list_ctrl: Any,
    settings_manager: Any,
    settings_key: str,
    expected_count: int,
) -> None:
    getter = getattr(settings_manager, 'get_setting', None)
    setter = getattr(list_ctrl, 'SetColumnWidth', None)
    if not callable(getter) or not callable(setter) or expected_count <= 0:
        return
    raw_widths = getter(settings_key, None)
    if not isinstance(raw_widths, list):
        return
    for index in range(min(expected_count, len(raw_widths))):
        try:
            width = max(24, int(raw_widths[index]))
        except WX_CALLBACK_EXCEPTIONS:
            continue
        try:
            setter(index, width)
        except WX_CALLBACK_EXCEPTIONS:
            logger.debug('Unable to restore persisted ListCtrl column width.', exc_info=True)



def persist_listctrl_column_widths(
    list_ctrl: Any,
    settings_manager: Any,
    settings_key: str,
    expected_count: int,
) -> None:
    getter = getattr(list_ctrl, 'GetColumnWidth', None)
    setter = getattr(settings_manager, 'set_setting', None)
    if not callable(getter) or not callable(setter) or expected_count <= 0:
        return
    widths: list[int] = []
    for index in range(expected_count):
        try:
            width = max(24, int(getter(index)))
        except WX_CALLBACK_EXCEPTIONS:
            return
        widths.append(width)
    try:
        setter(settings_key, widths)
    except WX_CALLBACK_EXCEPTIONS:
        logger.debug('Unable to persist ListCtrl column widths.', exc_info=True)


def open_directory(path: Path) -> None:
    target = str(path)
    if os.name == 'nt' and hasattr(os, 'startfile'):
        os.startfile(target)  # type: ignore[attr-defined]
        return
    command = ['open', target] if sys.platform == 'darwin' else ['xdg-open', target]
    subprocess.Popen(command)
