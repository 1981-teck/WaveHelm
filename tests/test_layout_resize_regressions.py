"""Visible layout prerequisites; fake geometry is not a native wx acceptance."""
from __future__ import annotations

import sys

from src.ui_wx import common
from tests.test_wx_library_view import build_library_view
from tests.wx_fakes import FakeChoice, FakePanel, FakeScrolledWindow, FakeWxModule


def test_plain_panel_must_not_pin_wide_virtual_area(monkeypatch):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    panel = FakePanel()
    panel.virtual_width = 0
    panel.FitInside = lambda: setattr(panel, 'virtual_width', panel.GetClientSize()[0])
    panel.SetSize((1280, 600))
    common._relayout_ancestors(panel)
    panel.SetSize((820, 600))
    assert max(panel.GetClientSize()[0], panel.virtual_width) == 820


def test_scrolled_panel_still_updates_virtual_extent(monkeypatch):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    panel = FakeScrolledWindow()
    calls = []
    panel.FitInside = lambda: calls.append('fit')
    common._relayout_ancestors(panel)
    assert calls == ['fit']


def test_choice_must_respect_native_best_width(monkeypatch):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    choice = FakeChoice()
    choice.SetItems(['All', 'Audio', 'Video'])
    choice.GetBestSize = lambda: (400, 50)
    common.autosize_choice_control(choice)
    assert choice.min_size == (400, 50)


def test_library_search_has_separate_row_from_selectors(monkeypatch):
    view, *_ = build_library_view(monkeypatch)
    toolbar = view.panel.sizer.items[1][0]
    assert toolbar.orientation == FakeWxModule.VERTICAL
    assert len(toolbar.items) == 2
    search, options = (entry[0] for entry in toolbar.items)
    assert [entry[0] for entry in search.items] == [view.search_label, view.search_text]
    assert len(options.items) == 2
    assert [entry[0] for entry in options.items[0][0].items] == [view.filter_label, view.filter_choice]
    assert [entry[0] for entry in options.items[1][0].items] == [view.sort_label, view.sort_choice]
