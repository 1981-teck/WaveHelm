"""Regression contracts for the first conservative cleanup repair.

Run real helpers, view consumers and shell routing; only wx/storage faults are
controlled boundaries. These tests do not launch native windows or user profiles.
"""
from __future__ import annotations

import logging
import sys
from types import SimpleNamespace

import pytest

from src.ui_wx import common, main_view_shell
from src.ui_wx.favorites_view import FavoritesView
from src.ui_wx.library_view import LibraryView
from src.ui_wx.playlist_view import PlaylistView
from tests.wx_fakes import FakeWxModule

_CAUGHT_ERRORS = (AttributeError, RuntimeError, TypeError, ValueError)
_VIEW_CLASSES = (LibraryView, PlaylistView, FavoritesView)


def _check_diagnostic(caplog: pytest.LogCaptureFixture, error: Exception, text: str) -> None:
    records = [r for r in caplog.records if r.name == common.__name__]
    assert len(records) == 1
    assert records[0].levelno == logging.DEBUG
    assert records[0].getMessage() == text
    assert records[0].exc_info is not None
    assert records[0].exc_info[1] is error


@pytest.mark.parametrize('error_type', _CAUGHT_ERRORS)
def test_restore_logs_caught_fault_and_continues_next_column(error_type, caplog):
    error = error_type('controlled column write fault')
    calls = []

    def set_width(index, width):
        calls.append((index, width))
        if index == 0:
            raise error

    settings = SimpleNamespace(get_setting=lambda *_: [120, 140])
    with caplog.at_level(logging.DEBUG, logger=common.__name__):
        result = common.restore_listctrl_column_widths(
            SimpleNamespace(SetColumnWidth=set_width), settings, 'widths', 2)
    assert result is None
    assert calls == [(0, 120), (1, 140)]
    _check_diagnostic(caplog, error, 'Unable to restore persisted ListCtrl column width.')


@pytest.mark.parametrize('error_type', _CAUGHT_ERRORS)
def test_persist_logs_caught_fault_without_retry_or_reset(error_type, caplog):
    error = error_type('controlled settings write fault')
    calls = []

    def set_value(key, value):
        calls.append((key, value))
        raise error

    with caplog.at_level(logging.DEBUG, logger=common.__name__):
        result = common.persist_listctrl_column_widths(
            SimpleNamespace(GetColumnWidth=lambda i: 120 + i * 20),
            SimpleNamespace(set_setting=set_value), 'widths', 2)
    assert result is None
    assert calls == [('widths', [120, 140])]
    _check_diagnostic(caplog, error, 'Unable to persist ListCtrl column widths.')


def test_successful_column_paths_preserve_clamping_validation_and_input(caplog):
    original = [0, '36', None, -1, 999]
    restored = []
    saved = []
    settings = SimpleNamespace(
        get_setting=lambda *_: original,
        set_setting=lambda k, v: saved.append((k, v)))
    with caplog.at_level(logging.DEBUG, logger=common.__name__):
        common.restore_listctrl_column_widths(
            SimpleNamespace(SetColumnWidth=lambda i, w: restored.append((i, w))),
            settings, 'widths', 4)
        common.persist_listctrl_column_widths(
            SimpleNamespace(GetColumnWidth=lambda i: [10, '128'][i]), settings, 'widths', 2)
    assert original == [0, '36', None, -1, 999]
    assert restored == [(0, 24), (1, 36), (3, 24)]
    assert saved == [('widths', [24, 128])]
    assert not [r for r in caplog.records if r.name == common.__name__]


@pytest.mark.parametrize('operation', ('restore', 'persist'))
@pytest.mark.parametrize('error_type', (OSError, KeyError))
def test_unhandled_exception_contract_is_not_broadened(operation, error_type):
    error = error_type('outside the existing helper exception contract')

    def fail(*_):
        raise error

    table = SimpleNamespace(SetColumnWidth=fail, GetColumnWidth=lambda _: 120)
    settings = SimpleNamespace(get_setting=lambda *_: [120], set_setting=fail)
    function = getattr(common, operation + '_listctrl_column_widths')
    with pytest.raises(error_type) as caught:
        function(table, settings, 'widths', 1)
    assert caught.value is error


def test_failed_width_read_never_persists_a_partial_list():
    calls = []

    def get_width(index):
        if index == 1:
            raise RuntimeError('controlled second-column read fault')
        return 120

    common.persist_listctrl_column_widths(
        SimpleNamespace(GetColumnWidth=get_width),
        SimpleNamespace(set_setting=lambda *args: calls.append(args)), 'widths', 2)
    assert calls == []


@pytest.mark.parametrize('operation', ('restore', 'persist'))
@pytest.mark.parametrize('count', (0, -1, 2))
def test_missing_capability_or_empty_count_remains_noop(operation, count):
    function = getattr(common, operation + '_listctrl_column_widths')
    assert function(object(), object(), 'widths', count) is None


@pytest.mark.parametrize('view_class', _VIEW_CLASSES)
def test_real_collection_view_restore_survives_widget_fault(view_class, monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    settings = SimpleNamespace(get_setting=lambda _key, default=None: default)
    view = view_class(None, settings_manager=settings)
    table, key, count = view._column_width_groups()[0]
    error = RuntimeError('controlled view column fault')
    setter = table.SetColumnWidth
    calls = []

    def set_width(index, width):
        calls.append((index, width))
        if index == 0:
            raise error
        setter(index, width)

    monkeypatch.setattr(table, 'SetColumnWidth', set_width)
    settings.get_setting = lambda k, default=None: [160] * count if k == key else default
    with caplog.at_level(logging.DEBUG, logger=common.__name__):
        view._restore_column_widths()
    assert calls == [(i, 160) for i in range(count)]
    _check_diagnostic(caplog, error, 'Unable to restore persisted ListCtrl column width.')


@pytest.mark.parametrize('view_class', _VIEW_CLASSES)
def test_real_view_shutdown_releases_subscriptions_after_save_fault(view_class, monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    settings = SimpleNamespace(get_setting=lambda _key, default=None: default)
    view = view_class(None, settings_manager=settings)
    calls = []
    releases = []
    error = RuntimeError('controlled first group save fault')

    def set_value(key, widths):
        calls.append((key, widths))
        if len(calls) == 1:
            raise error

    settings.set_setting = set_value
    token = object()
    view._subscriptions = [('controlled-event', token)]
    view.event_bus = SimpleNamespace(
        unsubscribe=lambda event, *, subscription: releases.append((event, subscription)))
    with caplog.at_level(logging.DEBUG, logger=common.__name__):
        view.shutdown()
    assert len(calls) == len(view._column_width_groups())
    assert releases == [('controlled-event', token)]
    assert view._subscriptions == []
    _check_diagnostic(caplog, error, 'Unable to persist ListCtrl column widths.')


@pytest.mark.parametrize('wrap', (True, False), ids=('wrap', 'box-fallback'))
def test_missing_factory_builds_complete_placeholder_via_real_route(wrap):
    class Toolkit(FakeWxModule):
        pass
    if not wrap:
        Toolkit.WrapSizer = None
    book = object()
    owner = SimpleNamespace(_wx=Toolkit, _content_book=book)
    page = main_view_shell.build_runtime_page(owner, 'cleanup-unregistered', 'Fallback é')
    assert page.name == 'cleanup-unregistered'
    assert page.title == 'Fallback é'
    assert page.panel.parent is book
    assert page.label.label == 'Fallback é'
    children = [item[0] for item in page.panel.sizer.items]
    assert children[0] is page.label
    assert len(children) == 2
    assert children[1].parent is page.panel
    assert children[1].label.startswith('Fallback é is not ported yet.')


def test_registered_runtime_page_does_not_enter_placeholder(monkeypatch):
    from src.ui_wx import view_factory
    sentinel_panel = object()
    sentinel_title = object()
    calls = []

    class RegisteredView:
        def __init__(self, **kwargs):
            calls.append(kwargs)
            self.panel = sentinel_panel
            self.title_label = sentinel_title

    monkeypatch.setitem(view_factory._VIEW_CLASSES, 'cleanup-registered', RegisteredView)
    book = object()
    owner = SimpleNamespace(_content_book=book, _dependencies={'value': 42})
    page = main_view_shell.build_runtime_page(owner, 'cleanup-registered', 'Registered')
    assert page.panel is sentinel_panel
    assert page.label is sentinel_title
    assert calls == [{'parent': book, 'value': 42}]


def test_all_current_pages_still_have_factories():
    from src.ui_wx.view_factory import _VIEW_CLASSES
    assert all(name in _VIEW_CLASSES for name, _ in main_view_shell.VIEW_LABELS)
