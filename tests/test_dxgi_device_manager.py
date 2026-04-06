from __future__ import annotations

import logging
import pytest

import src.video.dxgi_device_manager as dxgi_mod


def test_to_nonzero_int_ptr_raises_value_error_on_bad_pointer(monkeypatch):
    monkeypatch.setattr(
        dxgi_mod.ctypes,
        'cast',
        lambda *args, **kwargs: (_ for _ in ()).throw(TypeError('bad cast')),
    )

    with pytest.raises(ValueError, match='pointer'):
        dxgi_mod._to_nonzero_int_ptr(object(), 'device')


def test_addref_and_release_iunknown_return_zero_on_cast_failure(monkeypatch):
    monkeypatch.setattr(
        dxgi_mod.ctypes,
        'cast',
        lambda *args, **kwargs: (_ for _ in ()).throw(TypeError('bad cast')),
    )

    assert dxgi_mod._addref_iunknown(object()) == 0
    assert dxgi_mod._release_iunknown(object()) == 0


def test_release_clears_state_and_tolerates_safe_release_failure(monkeypatch):
    manager = dxgi_mod.DXGIDeviceManager.__new__(dxgi_mod.DXGIDeviceManager)
    manager._manager = object()
    manager._reset_token = 7

    monkeypatch.setattr(
        dxgi_mod,
        'safe_release',
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('release fail')),
    )

    manager.release()

    assert manager._manager is None
    assert manager._reset_token is None


def test_del_never_raises(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger=dxgi_mod.logger.name)
    manager = dxgi_mod.DXGIDeviceManager.__new__(dxgi_mod.DXGIDeviceManager)
    monkeypatch.setattr(
        dxgi_mod.DXGIDeviceManager,
        'release',
        lambda self: (_ for _ in ()).throw(RuntimeError('release fail')),
    )

    dxgi_mod.DXGIDeviceManager.__del__(manager)
    assert '__del__ release failed' in caplog.text
