from __future__ import annotations

import ctypes
import logging

import src.video.component_base.utils as utils


class BadInt:
    def __int__(self):
        raise ValueError("bad int")


class FakeComPtr:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls = 0

    def release(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("boom")


class Releasable:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls = 0

    def Release(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("boom")


def test_env_helpers_and_is_success(monkeypatch):
    monkeypatch.setenv("WAVEHELM_UTILS_FLOAT", " 1.5 ")
    monkeypatch.setenv("WAVEHELM_UTILS_BAD_FLOAT", "oops")
    monkeypatch.setenv("WAVEHELM_UTILS_INT", " 9 ")
    monkeypatch.setenv("WAVEHELM_UTILS_BAD_INT", "oops")

    assert utils._env_float("WAVEHELM_UTILS_FLOAT", 0.0) == 1.5
    assert utils._env_float("WAVEHELM_UTILS_BAD_FLOAT", 2.5) == 2.5
    assert utils._env_int("WAVEHELM_UTILS_INT", 0) == 9
    assert utils._env_int("WAVEHELM_UTILS_BAD_INT", 7) == 7

    assert utils.is_success(0) is True
    assert utils.is_success(0x80004005) is False
    assert utils.is_success(ctypes.c_long(0)) is True
    assert utils.is_success(BadInt()) is False


def test_to_void_p_normalizes_supported_inputs():
    assert utils._to_void_p(None) is None
    assert utils._to_void_p(0) is None
    assert utils._to_void_p(1234).value == 1234
    assert utils._to_void_p(ctypes.c_void_p(5678)).value == 5678
    assert utils._to_void_p(object()) is None


def test_safe_release_handles_comptr_release_and_release_method(monkeypatch):
    monkeypatch.setattr(utils, "ComPtr", FakeComPtr)

    comptr = FakeComPtr()
    utils.safe_release(comptr, "comptr")
    assert comptr.calls == 1

    failing_comptr = FakeComPtr(fail=True)
    utils.safe_release(failing_comptr, "failing_comptr")
    assert failing_comptr.calls == 1

    releasable = Releasable()
    utils.safe_release(releasable, "releasable")
    assert releasable.calls == 1

    failing_releasable = Releasable(fail=True)
    utils.safe_release(failing_releasable, "failing_releasable")
    assert failing_releasable.calls == 1


def test_safe_release_falls_back_to_iunknown_helper(monkeypatch):
    calls: list[tuple[object, str]] = []

    def fake_try_release(obj, name="object"):
        calls.append((obj, name))
        return 0

    monkeypatch.setattr(utils, "_try_release_iunknown", fake_try_release)

    marker = object()
    utils.safe_release(marker, "marker")

    assert calls == [(marker, "marker")]


def test_registry_trim_logs_when_sorting_fails(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger=utils.logger.name)
    monkeypatch.setattr(utils, '_RELEASED_PTR_MAX_SIZE', 1)
    monkeypatch.setattr(utils, '_RELEASED_PTR_TTL_SEC', 60.0)
    utils._released_ptrs.clear()
    utils._released_ptrs.update({1: 90.0, 2: 95.0})
    monkeypatch.setattr(utils, 'sorted', lambda *args, **kwargs: (_ for _ in ()).throw(TypeError('sort fail')), raising=False)

    with utils._released_ptrs_lock:
        utils._maybe_purge_released_registry_locked(100.0)

    assert 'Released-ptr registry trim skipped' in caplog.text
