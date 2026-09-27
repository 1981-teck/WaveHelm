from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
import logging

from src.audio.audio_event_models import AudioEventType
from src.ui_wx.common import (
    apply_colors,
    get_theme_colors,
    persist_listctrl_column_widths,
    restore_listctrl_column_widths,
    set_listctrl_column_label,
    unregister_callback,
)

ExceptionTypes = tuple[type[BaseException], ...]
ColumnSpec = Sequence[tuple[str, str]]
ColumnWidthGroup = tuple[object, str, int]
Translator = Callable[[str, str], str]
LifecycleCallback = Callable[..., object]


def set_translated_column_labels(
    table: object,
    columns: ColumnSpec,
    translate: Translator,
) -> None:
    """Apply translated labels to a ListCtrl-like table."""
    for index, (name, key) in enumerate(columns):
        set_listctrl_column_label(table, index, translate(key, name.title()))


def restore_column_width_groups(
    settings_manager: object,
    groups: Iterable[ColumnWidthGroup],
) -> None:
    """Restore one or more persisted ListCtrl column-width groups."""
    for table, settings_key, column_count in groups:
        restore_listctrl_column_widths(table, settings_manager, settings_key, column_count)


def persist_column_width_groups(
    settings_manager: object,
    groups: Iterable[ColumnWidthGroup],
) -> None:
    """Persist one or more live ListCtrl column-width groups."""
    for table, settings_key, column_count in groups:
        persist_listctrl_column_widths(table, settings_manager, settings_key, column_count)


def apply_collection_view_theme(
    theme_manager: object,
    *,
    widgets: Iterable[object],
    buttons: Iterable[object],
) -> dict[str, str]:
    """Apply the shared collection-view palette and return the resolved colors."""
    colors = get_theme_colors(theme_manager)
    background = colors.get('panel_bg') or colors.get('bg_color')
    foreground = colors.get('text_color')
    accent = colors.get('button_color') or background
    for widget in widgets:
        apply_colors(widget, background=background, foreground=foreground)
    for button in buttons:
        apply_colors(button, background=accent, foreground=foreground)
    return colors


def release_view_lifecycle(
    *,
    event_bus: object,
    subscriptions: list[tuple[AudioEventType, object]],
    exceptions: ExceptionTypes,
    localization_manager: object,
    localization_callback: LifecycleCallback,
    theme_manager: object,
    theme_callback: LifecycleCallback,
    logger: logging.Logger,
    view_name: str,
) -> None:
    """Release event subscriptions and manager callbacks for a wx collection view."""
    unsubscribe = getattr(event_bus, 'unsubscribe', None)
    if callable(unsubscribe):
        for event_type, subscription in tuple(subscriptions):
            try:
                unsubscribe(event_type, subscription=subscription)
            except exceptions:
                logger.debug('Unable to unsubscribe wx %s.', view_name, exc_info=True)
    subscriptions.clear()
    unregister_callback(
        localization_manager,
        'unregister_language_change_callback',
        localization_callback,
        logger=logger,
        message=f'Unable to unregister wx {view_name} language callback.',
    )
    unregister_callback(
        theme_manager,
        'unregister_theme_change_callback',
        theme_callback,
        logger=logger,
        message=f'Unable to unregister wx {view_name} theme callback.',
    )
