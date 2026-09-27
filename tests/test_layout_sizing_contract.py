"""Sizing/ownership contracts with explicit native-size and scroll-type boundaries."""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from src.ui_wx import common
from tests.wx_fakes import FakeButton, FakeChoice, FakePanel, FakeScrolledWindow, FakeWxModule


@pytest.mark.parametrize('width', [180, 400, 800])
def test_best_size_can_shrink_after_locale_change(monkeypatch, width):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    choice = FakeChoice()
    choice.SetItems(['first', 'second'])
    choice.SetSelection(1)
    choice.SetMinSize((width, 40))
    choice.GetMinSize = lambda: choice.min_size
    choice.GetBestSize = lambda: (max(90, choice.min_size[0]), max(28, choice.min_size[1]))
    contents = choice.items
    common.autosize_choice_control(choice)
    assert choice.min_size == (90, 28)
    assert choice.items is contents and choice.GetSelection() == 1


@pytest.mark.parametrize('kind', ['button', 'choice'])
def test_font_or_translation_decrease_does_not_preserve_our_old_minimum(monkeypatch, kind):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    widget = FakeButton() if kind == 'button' else FakeChoice()
    widget.GetMinSize = lambda: widget.min_size or (-1, -1)
    width = [380]
    widget.GetBestSize = lambda: (max(width[0], (widget.min_size or (-1, -1))[0]), 28)
    autosize = common.autosize_labeled_control if kind == 'button' else common.autosize_choice_control
    autosize(widget)
    assert widget.min_size[0] == 380
    width[0] = 100
    autosize(widget)
    assert widget.min_size[0] == 100


@pytest.mark.parametrize('scale', [1, 2])
def test_measured_text_and_dip_chrome_are_used(monkeypatch, scale):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    choice = FakeChoice()
    choice.SetItems(['iiiiii', 'WWW'])
    choice.FromDIP = lambda n: n * scale
    choice.GetTextExtent = lambda text: (60 * scale if text == 'WWW' else 12 * scale, 28 * scale)
    choice.GetBestSize = lambda: (50 * scale, 28 * scale)
    common.autosize_choice_control(choice)
    assert choice.min_size == ((60 + 36) * scale, 28 * scale)


def test_failed_secondary_text_measurement_does_not_discard_native_best(monkeypatch):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    choice = FakeChoice()
    choice.SetItems(['Audio'])
    choice.GetBestSize = lambda: (350, 40)
    def fail(_):
        raise RuntimeError('text measurement unavailable')
    choice.GetTextExtent = fail
    common.autosize_choice_control(choice)
    assert choice.min_size == (350, 40)


def test_failed_best_size_preserves_old_minimum(monkeypatch):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    choice = FakeChoice()
    choice.SetMinSize((123, 31))
    choice.GetMinSize = lambda: choice.min_size
    def fail():
        raise RuntimeError('destroyed during capture')
    choice.GetBestSize = fail
    common.autosize_choice_control(choice)
    assert choice.min_size == (123, 31)


@pytest.mark.parametrize('depth', [0, 1, 6])
def test_layout_walk_is_bounded_even_for_parent_cycle(monkeypatch, depth):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    panel = FakePanel()
    panel.parent = panel
    calls = []
    panel.Layout = lambda: calls.append('layout')
    panel.FitInside = lambda: pytest.fail('ordinary panels must not FitInside')
    common._relayout_ancestors(panel, max_depth=depth)
    assert len(calls) == depth


def test_only_scrolling_ancestor_receives_fit_inside(monkeypatch):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    frame = FakePanel()
    scroll = FakeScrolledWindow(frame)
    child = FakeChoice(scroll)
    called = []
    for name, widget in [('frame', frame), ('scroll', scroll), ('choice', child)]:
        widget.FitInside = lambda name=name: called.append(name)
    common._relayout_ancestors(child)
    assert called == ['scroll']


def test_destroyed_parent_stops_walk_without_touching_later_controls(monkeypatch):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    panel = FakePanel()
    def fail():
        raise RuntimeError('window destroyed')
    panel.GetParent = fail
    common._relayout_ancestors(panel)


def test_scrolling_is_type_based_not_name_or_method_presence(monkeypatch):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    window = SimpleNamespace(FitInside=lambda: pytest.fail('not a scrolling container'), GetParent=lambda: None)
    common._relayout_ancestors(window)
