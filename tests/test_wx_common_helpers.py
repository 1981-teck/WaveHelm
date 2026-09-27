from __future__ import annotations

from types import SimpleNamespace

from src.ui_wx.common import (
    format_duration,
    get_selected_indices,
    lighten_hex_color,
    set_view_feedback,
)


class DummyListCtrl:
    def __init__(self, selected: list[int]):
        self.selected = list(selected)

    def GetFirstSelected(self) -> int:
        return self.selected[0] if self.selected else -1

    def GetNextSelected(self, current: int) -> int:
        try:
            position = self.selected.index(current) + 1
        except ValueError:
            return -1
        return self.selected[position] if position < len(self.selected) else -1


class DummyLabel:
    def __init__(self):
        self.label = ''

    def SetLabel(self, value: str) -> None:
        self.label = value


class DummyEventBus:
    def __init__(self):
        self.events: list[tuple[object, dict[str, str]]] = []

    def publish(self, event_type: object, payload: dict[str, str]) -> None:
        self.events.append((event_type, payload))


def test_lighten_hex_color_preserves_previous_view_behavior():
    assert lighten_hex_color('#000000') == '#474747'
    assert lighten_hex_color('#123456', blend=0.0) == '#123456'
    assert lighten_hex_color('#123456', blend=1.0) == '#e7ebee'
    assert lighten_hex_color('not-a-color') is None
    assert lighten_hex_color(None) is None


def test_format_duration_supports_compact_and_hour_aware_views():
    assert format_duration(-1) == '00:00'
    assert format_duration(65.9) == '01:05'
    assert format_duration(3665) == '61:05'
    assert format_duration(3665, include_hours=True) == '01:01:05'


def test_get_selected_indices_preserves_listctrl_iteration_order():
    assert get_selected_indices(DummyListCtrl([2, 4, 7])) == [2, 4, 7]
    assert get_selected_indices(DummyListCtrl([])) == []


def test_set_view_feedback_updates_label_and_event_bus():
    label = DummyLabel()
    event_bus = DummyEventBus()
    event_type = object()

    view = SimpleNamespace(
        feedback_label=label,
        event_bus=event_bus,
        FEEDBACK_EVENT_TYPE=event_type,
    )

    set_view_feedback(view, 'Done', 'green')

    assert label.label == 'Done'
    assert event_bus.events == [(event_type, {'message': 'Done', 'color': 'green'})]
