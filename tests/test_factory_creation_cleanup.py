"""Factory coordination contracts; tokens below never model successful native COM."""
from __future__ import annotations

import threading
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from src.video import adapter_factory as factory


@dataclass
class FactoryToken:
    """Inert Python identity used only to observe caching and callback ownership."""

    _closed: bool = False
    shutdown_calls: int = 0

    def shutdown(self) -> None:
        self.shutdown_calls += 1
        self._closed = True


@pytest.fixture
def isolated_factory(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    lock = threading.RLock()
    monkeypatch.setattr(factory, "_adapter_lock", lock)
    monkeypatch.setattr(factory, "_adapter_cv", threading.Condition(lock))
    monkeypatch.setattr(factory, "_state", factory._FactoryState())
    yield


def install_failure(monkeypatch: pytest.MonkeyPatch, error: BaseException) -> list[object]:
    calls: list[object] = []

    def failing_constructor(event_bus: object = None) -> None:
        calls.append(event_bus)
        raise error

    monkeypatch.setattr(factory, "create_imf_media_engine_adapter", failing_constructor)
    return calls


ERROR_TYPES = (
    ImportError, AttributeError, OSError, RuntimeError, TypeError, ValueError,
    MemoryError, KeyError, KeyboardInterrupt, SystemExit, GeneratorExit,
)


@pytest.mark.parametrize("kind", ERROR_TYPES)
def test_constructor_error_releases_owner_and_retains_error(
    isolated_factory: None, monkeypatch: pytest.MonkeyPatch, kind: type[BaseException],
) -> None:
    error = kind("WH_SYNTHETIC_08G_constructor")
    bus = object()
    calls = install_failure(monkeypatch, error)
    callbacks: list[object] = []
    factory.set_adapter_creation_callback(callbacks.append)
    with pytest.raises(kind) as caught:
        factory.create_best_video_adapter(event_bus=bus)
    assert caught.value is error and calls == [bus] and callbacks == []
    trace = caught.value.__traceback__
    names: list[str] = []
    while trace is not None:
        names.append(trace.tb_frame.f_code.co_name)
        trace = trace.tb_next
    assert "failing_constructor" in names
    status = factory.get_adapter_status()
    assert status["creating"] is False and status["creator_tid"] is None
    assert status["created"] is False and status["has_instance"] is False
    assert status["callback_running"] is False and status["callback_tid"] is None
    assert status["creation_seq"] == 1
    expected = str(error) if isinstance(error, factory.FACTORY_IMPORT_EXCEPTIONS) else None
    assert status["last_error"] == expected


@pytest.mark.parametrize("kind", (MemoryError, KeyError, KeyboardInterrupt, SystemExit))
def test_retry_reaches_constructor_and_shutdown_needs_no_private_reset(
    isolated_factory: None, monkeypatch: pytest.MonkeyPatch, kind: type[BaseException],
) -> None:
    install_failure(monkeypatch, kind("first"))
    with pytest.raises(kind):
        factory.create_best_video_adapter()
    factory.shutdown_video_adapter()
    assert factory.get_adapter_status()["creating"] is False
    retry = OSError("retry reached constructor")
    calls = install_failure(monkeypatch, retry)
    with pytest.raises(OSError) as caught:
        factory.create_best_video_adapter()
    assert caught.value is retry and calls == [None]
    assert factory.get_adapter_status()["creation_seq"] == 2


@pytest.mark.parametrize("kind", (MemoryError, RuntimeError))
def test_initial_log_failure_cannot_leave_claimed_owner(
    isolated_factory: None, monkeypatch: pytest.MonkeyPatch, kind: type[Exception],
) -> None:
    error = kind("controlled initial logging failure")
    calls = install_failure(monkeypatch, OSError("must not be reached"))

    def fail_info(*args: object, **kwargs: object) -> None:
        raise error

    monkeypatch.setattr(factory.logger, "info", fail_info)
    with pytest.raises(kind) as caught:
        factory.create_best_video_adapter()
    assert caught.value is error and calls == []
    assert factory.get_adapter_status()["creating"] is False
    assert factory.get_adapter_status()["creator_tid"] is None


@pytest.mark.parametrize("owner_change", ("thread", "generation", "both"))
@pytest.mark.parametrize("kind", (MemoryError, ValueError))
def test_cleanup_does_not_erase_foreign_creation_state(
    isolated_factory: None, monkeypatch: pytest.MonkeyPatch,
    owner_change: str, kind: type[Exception],
) -> None:
    error = kind("old owner failure")
    replacement = FactoryToken()
    snapshots: list[dict[str, object]] = []

    def replace_owner(event_bus: object = None) -> None:
        with factory._adapter_lock:
            if owner_change in ("thread", "both"):
                factory._state.creator_tid = threading.get_ident() + 1
            if owner_change in ("generation", "both"):
                factory._state.creation_seq += 1
            factory._state.last_error = "other-owner-status"
            factory._state.instance = replacement
            snapshots.append(factory.get_adapter_status())
        raise error

    monkeypatch.setattr(factory, "create_imf_media_engine_adapter", replace_owner)
    with pytest.raises(kind) as caught:
        factory.create_best_video_adapter()
    assert caught.value is error
    assert factory.get_adapter_status() == snapshots[0]
    assert factory._state.instance is replacement


@pytest.mark.parametrize("kind", (OSError, RuntimeError, ValueError, MemoryError,
                                  KeyboardInterrupt, SystemExit))
def test_callback_error_retains_policy_cache_and_clears_callback_owner(
    isolated_factory: None, monkeypatch: pytest.MonkeyPatch, kind: type[BaseException],
) -> None:
    token = FactoryToken()
    error = kind("callback failure")
    calls: list[object] = []
    monkeypatch.setattr(factory, "create_imf_media_engine_adapter", lambda event_bus=None: token)

    def callback(adapter: object) -> None:
        calls.append(adapter)
        assert factory.get_adapter_status()["callback_running"] is True
        assert factory.get_adapter_status()["creating"] is False
        assert factory.get_video_adapter() is token
        raise error

    factory.set_adapter_creation_callback(callback)
    if isinstance(error, factory.FACTORY_STATE_EXCEPTIONS):
        assert factory.create_best_video_adapter() is token
    else:
        with pytest.raises(kind) as caught:
            factory.create_best_video_adapter()
        assert caught.value is error
    status = factory.get_adapter_status()
    assert status["callback_running"] is False and status["callback_tid"] is None
    assert status["creating"] is False and status["created"] is True
    assert factory.get_video_adapter() is token and calls == [token]
    assert token.shutdown_calls == 0


def test_reentrant_constructor_refusal_then_new_attempt(
    isolated_factory: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reentrant(event_bus: object = None) -> None:
        factory.create_best_video_adapter()

    monkeypatch.setattr(factory, "create_imf_media_engine_adapter", reentrant)
    with pytest.raises(factory.MediaEngineError, match="Re-entrant"):
        factory.create_best_video_adapter()
    assert factory.get_adapter_status()["creating"] is False
    error = MemoryError("independent next attempt")
    calls = install_failure(monkeypatch, error)
    with pytest.raises(MemoryError) as caught:
        factory.create_best_video_adapter()
    assert caught.value is error and calls == [None]


def test_caught_reentrant_refusal_does_not_release_outer_creation(
    isolated_factory: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = FactoryToken()

    def constructor(event_bus: object = None) -> FactoryToken:
        with pytest.raises(factory.MediaEngineError, match="Re-entrant"):
            factory.create_best_video_adapter()
        assert factory.get_adapter_status()["creating"] is True
        return token

    monkeypatch.setattr(factory, "create_imf_media_engine_adapter", constructor)
    assert factory.create_best_video_adapter() is token
    assert factory.get_adapter_status()["creating"] is False


def test_success_callback_can_shutdown_its_own_published_token(
    isolated_factory: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = FactoryToken()
    monkeypatch.setattr(factory, "create_imf_media_engine_adapter", lambda event_bus=None: token)
    factory.set_adapter_creation_callback(lambda adapter: factory.shutdown_video_adapter())
    assert factory.create_best_video_adapter() is token
    assert token.shutdown_calls == 1
    status = factory.get_adapter_status()
    assert status["has_instance"] is False and status["callback_running"] is False


def test_postcommit_logger_error_does_not_unpublish_completed_creation(
    isolated_factory: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = FactoryToken()
    error = RuntimeError("postcommit diagnostic failure")
    callbacks: list[object] = []

    def info(message: str, *args: object, **kwargs: object) -> None:
        if "created successfully" in message:
            raise error

    monkeypatch.setattr(factory.logger, "info", info)
    monkeypatch.setattr(factory, "create_imf_media_engine_adapter", lambda event_bus=None: token)
    factory.set_adapter_creation_callback(callbacks.append)
    with pytest.raises(RuntimeError) as caught:
        factory.create_best_video_adapter()
    assert caught.value is error
    assert factory.get_video_adapter() is token and callbacks == [token]
    assert factory.get_adapter_status()["created"] is True


def test_reset_after_aborted_attempt_invokes_new_constructor(
    isolated_factory: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_failure(monkeypatch, MemoryError("first"))
    with pytest.raises(MemoryError):
        factory.create_best_video_adapter()
    calls = install_failure(monkeypatch, RuntimeError("second"))
    assert factory.reset_adapter() is False and calls == [None]
    assert factory.get_adapter_status()["creation_seq"] == 2
