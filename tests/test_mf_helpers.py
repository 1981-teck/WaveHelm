from __future__ import annotations

import pytest

import src.video.component_base.mf_helpers as mf_helpers


class BrokenCallable:
    def __setattr__(self, name, value):
        if name in {'argtypes', 'restype'}:
            raise TypeError('bad bind')
        object.__setattr__(self, name, value)


class BrokenDll:
    def __getattr__(self, name):
        if name == 'Target':
            return BrokenCallable()
        raise AttributeError(name)


def test_bind_export_returns_none_when_binding_fails():
    assert mf_helpers._bind_export(BrokenDll(), 'Target', argtypes=[], restype=object) is None


def test_mfstartup_restores_state_on_failure(monkeypatch):
    monkeypatch.setattr(mf_helpers, '_MFStartup', lambda version, flags: 0x80004005)
    monkeypatch.setattr(mf_helpers, '_mf_refcount', 0)
    monkeypatch.setattr(mf_helpers, '_mf_started', False)

    with pytest.raises(RuntimeError, match='MFStartup failed'):
        mf_helpers.MFStartup()

    assert mf_helpers._mf_refcount == 0
    assert mf_helpers._mf_started is False


def test_mfshutdown_restores_state_on_failure(monkeypatch):
    monkeypatch.setattr(mf_helpers, '_MFShutdown', lambda: 0x80004005)
    monkeypatch.setattr(mf_helpers, '_mf_refcount', 1)
    monkeypatch.setattr(mf_helpers, '_mf_started', True)

    with pytest.raises(RuntimeError, match='MFShutdown failed'):
        mf_helpers.MFShutdown()

    assert mf_helpers._mf_refcount == 1
    assert mf_helpers._mf_started is True
