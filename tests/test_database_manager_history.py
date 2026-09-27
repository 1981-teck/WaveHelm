from __future__ import annotations

from collections.abc import Mapping
import inspect
from pathlib import Path
import shutil

import pytest

import src.model.component_database.db_core as db_core_module
from src.model.component_database.db_core import DbCore
from src.model.database_manager import DatabaseManager


RUNTIME_ROOT = Path(__file__).resolve().parent / "_database_manager_history_runtime"


class DummyLocalization:
    def get_text(self, key: str, **kwargs: object) -> str:
        return key


class RecordingHistoryManager:
    def __init__(self) -> None:
        self.add_calls: list[dict[str, object]] = []
        self.get_limits: list[int | None] = []
        self.clear_calls = 0

    def add_history_entry(
        self,
        *,
        path: str,
        title: str,
        media_type: str,
        duration: float,
        metadata: Mapping[str, object] | None,
        additional_data: Mapping[str, object] | None,
    ) -> int:
        self.add_calls.append(
            {
                "path": path,
                "title": title,
                "media_type": media_type,
                "duration": duration,
                "metadata": dict(metadata or {}),
                "additional_data": dict(additional_data or {}),
            }
        )
        return 41

    def get_history(self, limit: int | None) -> list[dict[str, object]]:
        self.get_limits.append(limit)
        return [{"id": 1, "path": "track.wav"}]

    def clear_history(self) -> int:
        self.clear_calls += 1
        return 7


@pytest.fixture(autouse=True)
def reset_db_core_singleton() -> None:
    _reset_core()
    yield
    _reset_core()


def _reset_core() -> None:
    instance = getattr(DbCore, "_instance", None)
    if instance is not None and getattr(instance, "conn", None) is not None:
        instance.close()
    DbCore._instance = None


def _facade(history: RecordingHistoryManager) -> DatabaseManager:
    facade = object.__new__(DatabaseManager)
    facade.history = history  # type: ignore[assignment]
    return facade


def test_facade_delegates_path_first_contract_by_keyword() -> None:
    history = RecordingHistoryManager()
    facade = _facade(history)

    result = facade.add_history_entry(
        "track.wav",
        "Track",
        "audio",
        5.0,
        {"artist": "A"},
        {"source": "library"},
    )

    assert result == 41
    assert history.add_calls == [
        {
            "path": "track.wav",
            "title": "Track",
            "media_type": "audio",
            "duration": 5.0,
            "metadata": {"artist": "A"},
            "additional_data": {"source": "library"},
        }
    ]


def test_facade_get_and_clear_preserve_explicit_results() -> None:
    history = RecordingHistoryManager()
    facade = _facade(history)

    assert facade.get_history(limit=12) == [{"id": 1, "path": "track.wav"}]
    assert facade.clear_history() == 7
    assert history.get_limits == [12]
    assert history.clear_calls == 1


def test_facade_signature_and_implementation_do_not_swap_title_and_path() -> None:
    signature = inspect.signature(DatabaseManager.add_history_entry)
    assert list(signature.parameters)[:3] == ["self", "path", "title"]

    source = inspect.getsource(DatabaseManager.add_history_entry)
    assert "self.history.add_history_entry(" in source
    assert "path=path" in source
    assert "title=title" in source


def test_facade_manager_sqlite_contract_is_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = RUNTIME_ROOT / "end_to_end"
    shutil.rmtree(runtime, ignore_errors=True)
    runtime.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_core_module, "get_user_data_dir", lambda: runtime)
    facade = DatabaseManager(DummyLocalization())

    record_id = facade.add_history_entry(
        "end-to-end.wav",
        "End to end",
        "audio",
        7.5,
        {"artist": "A"},
        {"source": "facade"},
    )
    items = facade.get_history(limit=1)

    assert items == [
        {
            "id": record_id,
            "title": "End to end",
            "path": "end-to-end.wav",
            "media_type": "audio",
            "duration": 7.5,
            "metadata": {"artist": "A", "source": "facade"},
            "timestamp": items[0]["timestamp"],
        }
    ]
    assert facade.clear_history() == 1
    assert facade.get_history(limit=1) == []
