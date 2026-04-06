from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from src.controller import ui_dispatcher


class DummyWxRoot:
    def MainLoop(self):  # pragma: no cover - interface hint only
        return 0


def test_create_ui_dispatcher_uses_wx_call_after(monkeypatch):
    calls = []

    class FakeWxModule:
        @staticmethod
        def CallAfter(callback):
            calls.append('callafter')
            callback()

    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    dispatcher = ui_dispatcher.create_ui_dispatcher(DummyWxRoot(), ui_backend='wx')
    seen = []

    dispatcher.dispatch(lambda: seen.append('ok'))

    assert seen == ['ok']
    assert calls == ['callafter']


def test_resolve_ui_backend_defaults_to_wx():
    assert ui_dispatcher.resolve_ui_backend(object()) == 'wx'


def test_create_ui_dispatcher_rejects_removed_qt_backend():
    with pytest.raises(ValueError, match='Supported values: wx'):
        ui_dispatcher.create_ui_dispatcher(object(), ui_backend='qt')


def test_create_ui_dispatcher_requires_call_after(monkeypatch):
    monkeypatch.setitem(sys.modules, 'wx', SimpleNamespace())

    with pytest.raises(RuntimeError, match='wx.CallAfter'):
        ui_dispatcher.create_ui_dispatcher(DummyWxRoot(), ui_backend='wx')
