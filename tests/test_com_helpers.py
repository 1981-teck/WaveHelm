from __future__ import annotations

import logging
import src.video.component_base.com_helpers as com_helpers


def test_hr_to_uint32_invalid_value_returns_zero():
    assert com_helpers._hr_to_uint32('bad-value') == 0


def test_get_hresult_system_message_returns_empty_on_windll_failure(monkeypatch):
    monkeypatch.setattr(
        com_helpers.ctypes,
        'WinDLL',
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError('boom')),
    )
    assert com_helpers.get_hresult_system_message(0x80004005) == ''


def test_coerce_ptr_value_returns_zero_for_unknown_object():
    assert com_helpers._coerce_ptr_value(object()) == 0


def test_comptr_add_ref_and_release_tolerate_cast_failures(monkeypatch):
    ptr = com_helpers.ComPtr(123)
    monkeypatch.setattr(
        com_helpers.ctypes,
        'cast',
        lambda *args, **kwargs: (_ for _ in ()).throw(TypeError('cast fail')),
    )

    assert ptr.add_ref() == 0
    assert ptr.release() == 0
    assert ptr.ptr is not None
    assert ptr.ptr.value == 123


def test_comptr_del_never_raises(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger=com_helpers.logger.name)
    monkeypatch.setattr(
        com_helpers.ComPtr,
        'release',
        lambda self: (_ for _ in ()).throw(RuntimeError('release fail')),
    )
    ptr = com_helpers.ComPtr()
    com_helpers.ComPtr.__del__(ptr)
    assert 'ComPtr.__del__ release failed' in caplog.text
