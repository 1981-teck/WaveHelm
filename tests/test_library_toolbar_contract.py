"""Construction/retention contracts, not claims about native pixel geometry."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.ui_wx.common import _relayout_ancestors
from tests.test_wx_library_view import build_library_view
from tests.wx_fakes import FakeSizer, FakeWxModule


@pytest.mark.parametrize('language', ['en', 'it', 'fr', 'es'])
def test_locale_resize_roundtrip_preserves_controls_and_inputs(monkeypatch, language):
    view, _, _, _, settings = build_library_view(monkeypatch)
    names = ('search_label', 'search_text', 'filter_label', 'filter_choice', 'sort_label', 'sort_choice')
    controls = tuple(getattr(view, name) for name in names)
    view.search_text.SetValue('query')
    view.search_text.SetFocus()
    view.filter_choice.SetSelection(2)
    view.sort_choice.SetSelection(3)
    widths = [view.table.GetColumnWidth(i) for i in range(6)]
    bundle = json.loads((Path(__file__).parents[1] / 'src/locales' / f'{language}.json').read_text(encoding='utf-8'))
    view.localization_manager.get_text = lambda key, default=None, **kw: bundle.get(key, default or key).format(**kw)
    view.update_localization()
    calls = []
    view.panel.FitInside = lambda: calls.append('forbidden')
    for width, height in [(1280, 600), (820, 600), (820, 900), (1000, 600), (1440, 600), (820, 600)]:
        view.panel.SetClientSize((width, height))
        _relayout_ancestors(view.panel)
        assert tuple(getattr(view, name) for name in names) == controls
        assert view.search_text.GetValue() == 'query'
        assert view.filter_choice.GetSelection() == 2
        assert view.sort_choice.GetSelection() == 3
        assert view.search_text.focus_calls == 1
        assert [view.table.GetColumnWidth(i) for i in range(6)] == widths
    assert calls == []
    assert settings.values == {}


def test_native_wrap_is_used_for_grouped_selectors(monkeypatch):
    class Wrap(FakeSizer):
        pass
    monkeypatch.setattr(FakeWxModule, 'WrapSizer', Wrap)
    view, *_ = build_library_view(monkeypatch)
    toolbar = view.panel.sizer.items[1][0]
    assert isinstance(toolbar.items[1][0], Wrap)
    assert len(toolbar.items[1][0].items) == 2


def test_unavailable_wrap_uses_stacked_not_clipped_horizontal_fallback(monkeypatch):
    monkeypatch.setattr(FakeWxModule, 'WrapSizer', None)
    view, *_ = build_library_view(monkeypatch)
    toolbar = view.panel.sizer.items[1][0]
    assert toolbar.items[1][0].orientation == FakeWxModule.VERTICAL


def test_layout_does_not_change_table_column_persistence(monkeypatch):
    view, _, _, _, settings = build_library_view(monkeypatch)
    view.table.SetColumnWidth(0, 333)
    view._persist_column_widths()
    saved = dict(settings.values)
    for width in (1440, 700, 1440):
        view.panel.SetSize((width, 600))
        _relayout_ancestors(view.panel)
        assert view.table.GetColumnWidth(0) == 333
        assert settings.values == saved
