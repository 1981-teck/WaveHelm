"""Legacy COM diagnostic contracts; DLL results are controlled unit fixtures.

Signed failures must remain failures, missing exports retain typed diagnostics,
and process-control errors propagate. No fixture initializes or uninitializes COM.
The genuine notification provider is exercised only via its Python callback, not
QueryInterface, native Media Engine, apartment setup or physical devices.
"""
from __future__ import annotations

import ctypes
import gc
import inspect
import logging
from types import SimpleNamespace

import pytest

from src.boot import com_bootstrap, com_policy
from src.video import mf_media_engine_notify as shim
from src.video.component_adapter import media_engine_events as events


class CoInitializeResult:
    """Record one native-boundary call without making a native call."""

    def __init__(self, result: int) -> None:
        self.result = result
        self.calls: list[tuple[object, int]] = []
        self.argtypes: object = None
        self.restype: object = None

    def __call__(self, reserved: object, mode: int) -> int:
        self.calls.append((reserved, mode))
        return self.result


def inject_result(monkeypatch: pytest.MonkeyPatch, result: int) -> CoInitializeResult:
    function = CoInitializeResult(result)

    def forbid_uninitialize() -> None:
        raise AssertionError("This diagnostic helper must not take lifetime ownership")

    ole32 = SimpleNamespace(CoInitializeEx=function, CoUninitialize=forbid_uninitialize)
    monkeypatch.setattr(com_bootstrap.ctypes, "windll", SimpleNamespace(ole32=ole32), raising=False)
    return function


def records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == com_bootstrap.logger.name]


def assert_binding(function: CoInitializeResult) -> None:
    assert function.calls == [(None, com_policy.COINIT_APARTMENTTHREADED)]
    assert function.argtypes == [ctypes.c_void_p, ctypes.c_ulong]
    assert function.restype is ctypes.c_long


@pytest.mark.parametrize("result", [-2147417850, 0x80010106])
def test_changed_mode_signed_and_unsigned_bits_remain_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, result: int,
) -> None:
    caplog.set_level(logging.DEBUG, logger=com_bootstrap.logger.name)
    function = inject_result(monkeypatch, result)
    assert com_bootstrap.init_com() is None
    assert_binding(function)
    observed = records(caplog)
    assert len(observed) == 1 and observed[0].levelno == logging.WARNING
    assert observed[0].getMessage() == (
        "[COM] CoInitializeEx(STA) failed: thread mode was already set "
        "(likely MTA by another component)."
    )
    assert observed[0].exc_info is None


@pytest.mark.parametrize("result, hexadecimal", [
    (-2147467259, "80004005"), (0x80004005, "80004005"),
    (-2147024809, "80070057"), (-2147418113, "8000FFFF"),
    (-2147483648, "80000000"), (-1, "FFFFFFFF"),
])
def test_unknown_failure_is_error_with_unsigned_32_bit_hex(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    result: int, hexadecimal: str,
) -> None:
    caplog.set_level(logging.DEBUG, logger=com_bootstrap.logger.name)
    function = inject_result(monkeypatch, result)
    assert com_bootstrap.init_com() is None
    assert_binding(function)
    observed = records(caplog)
    assert len(observed) == 1 and observed[0].levelno == logging.ERROR
    assert observed[0].getMessage() == (
        "[COM] CoInitializeEx(STA) failed with unexpected HRESULT: 0x" + hexadecimal
    )
    assert observed[0].exc_info is None


@pytest.mark.parametrize("result, ending", [
    (0, "successful."), (1, "already initialized on this thread."),
])
def test_existing_success_branches_return_none_without_lifetime_change(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    result: int, ending: str,
) -> None:
    # These are unit branch controls, NOT evidence of successful native initialization.
    caplog.set_level(logging.DEBUG, logger=com_bootstrap.logger.name)
    function = inject_result(monkeypatch, result)
    assert com_bootstrap.init_com() is None
    assert_binding(function)
    observed = records(caplog)
    assert len(observed) == 1 and observed[0].levelno == logging.INFO
    assert observed[0].getMessage() == "[COM] CoInitializeEx(STA) " + ending


@pytest.mark.parametrize("error_type", [AttributeError, OSError, RuntimeError, TypeError, ValueError])
def test_declared_errors_keep_the_original_exception_and_diagnostic(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    error_type: type[Exception],
) -> None:
    failure = error_type("controlled legacy boundary failure")

    def fail(reserved: object, mode: int) -> int:
        raise failure

    monkeypatch.setattr(com_bootstrap.ctypes, "windll",
                        SimpleNamespace(ole32=SimpleNamespace(CoInitializeEx=fail)), raising=False)
    assert com_bootstrap.init_com() is None
    observed = records(caplog)
    assert len(observed) == 1 and observed[0].levelno == logging.ERROR
    assert observed[0].exc_info is not None and observed[0].exc_info[1] is failure
    assert observed[0].getMessage() == "[COM] Failed to explicitly initialize COM: " + str(failure)


@pytest.mark.parametrize("error_type", [KeyError, AssertionError, OverflowError, KeyboardInterrupt, SystemExit])
def test_undeclared_and_control_errors_are_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[Exception] | type[KeyboardInterrupt] | type[SystemExit],
) -> None:
    def fail(reserved: object, mode: int) -> int:
        raise error_type("control")

    monkeypatch.setattr(com_bootstrap.ctypes, "windll",
                        SimpleNamespace(ole32=SimpleNamespace(CoInitializeEx=fail)), raising=False)
    with pytest.raises(error_type, match="control"):
        com_bootstrap.init_com()


def test_missing_native_export_retains_typed_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(com_bootstrap.ctypes, "windll", SimpleNamespace(ole32=object()), raising=False)
    assert com_bootstrap.init_com() is None
    observed = records(caplog)
    assert len(observed) == 1 and observed[0].levelno == logging.ERROR
    assert observed[0].exc_info is not None
    assert isinstance(observed[0].exc_info[1], AttributeError)


def test_shared_policy_and_exception_contract_are_not_redefined() -> None:
    assert com_bootstrap.RPC_E_CHANGED_MODE == com_policy.RPC_E_CHANGED_MODE == 0x80010106
    assert ctypes.c_int32(com_bootstrap.RPC_E_CHANGED_MODE).value == -2147417850
    assert com_bootstrap.COINIT_APARTMENTTHREADED == 2
    assert com_bootstrap.COM_BOOTSTRAP_EXCEPTIONS == (AttributeError, OSError, RuntimeError, TypeError, ValueError)
    assert inspect.signature(com_bootstrap.init_com).return_annotation == "None"


class RecordingAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int, int]] = []

    def on_media_engine_event(self, event: int, first: int, second: int) -> None:
        self.calls.append((event, first, second))


def test_shim_identity_and_declared_api_are_preserved() -> None:
    assert shim.PyMediaEngineNotify is events._MediaEngineNotifyCOM
    assert shim.__all__ == ["PyMediaEngineNotify", "create_media_engine_notify", "get_event_name", "_EVENT_NAMES"]
    assert len(shim._EVENT_NAMES) == 22
    for value, name in shim._EVENT_NAMES.items():
        assert shim.get_event_name(value) == name


@pytest.mark.parametrize("values", [(7, 8, 9), ("7", "8", "9")])
def test_real_provider_factory_delegates_the_direct_python_callback(
    values: tuple[int | str, int | str, int | str],
) -> None:
    adapter = RecordingAdapter()
    notify = shim.create_media_engine_notify(adapter)
    assert isinstance(notify, events._MediaEngineNotifyCOM)
    assert notify.EventNotify(*values) == 0
    assert adapter.calls == [(7, 8, 9)]


def test_factory_does_not_hide_provider_construction_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(adapter: object) -> object:
        raise ValueError("controlled provider construction failure")

    monkeypatch.setattr(shim, "PyMediaEngineNotify", fail)
    with pytest.raises(ValueError, match="controlled provider construction failure"):
        shim.create_media_engine_notify(object())


def test_genuine_provider_keeps_weak_adapter_lifetime() -> None:
    adapter = RecordingAdapter()
    notify = shim.create_media_engine_notify(adapter)
    del adapter
    gc.collect()
    assert notify.EventNotify(7, 8, 9) == 0


def test_documentation_describes_both_existing_provider_paths() -> None:
    header = inspect.getdoc(shim) or ""
    factory = inspect.getdoc(shim.create_media_engine_notify) or ""
    assert "comtypes" in header and "ctypes fallback" in header
    assert "comtypes" in factory and "ctypes fallback" in factory
    assert "solleverà" not in factory
