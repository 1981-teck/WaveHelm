from __future__ import annotations

import inspect
import shutil
import threading
from pathlib import Path

import pytest

import src.model.component_database.db_core as db_core_module
from src.model.component_database.db_core import DbCore
from src.model.component_database.db_primitives import DbWriteResult
from src.model.component_database.eq_preset_manager import EqPresetManager
from src.model.component_database.favorites_manager import FavoritesManager
from src.model.component_database.library_manager import LibraryManager
from src.utils.exceptions import DatabaseError, IntegrityError, NotFoundError

RUNTIME_ROOT = Path(__file__).resolve().parent / "_db_write_consumers_runtime"


class DummyLocalization:
    def get_text(self, key: str, **kwargs: object) -> str:
        return key


@pytest.fixture(autouse=True)
def reset_db_core_singleton() -> None:
    instance = getattr(DbCore, "_instance", None)
    if instance is not None and getattr(instance, "conn", None) is not None:
        instance.close()
    DbCore._instance = None
    yield
    instance = getattr(DbCore, "_instance", None)
    if instance is not None and getattr(instance, "conn", None) is not None:
        instance.close()
    DbCore._instance = None


def _make_core(monkeypatch: pytest.MonkeyPatch, name: str) -> DbCore:
    target = RUNTIME_ROOT / name
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_core_module, "get_user_data_dir", lambda: target)
    return DbCore(db_path="consumer.db", localization_manager=DummyLocalization())


def test_production_consumers_use_direct_results_and_legacy_shim_is_removed() -> None:
    """The migrated managers use typed write results and no shim remains."""
    managers = (FavoritesManager, LibraryManager, EqPresetManager)
    assert not hasattr(DbCore, "_last_changes")
    assert not hasattr(DbCore, "_clear_legacy_write_result")
    for manager in managers:
        source = inspect.getsource(manager)
        assert "._last_changes(" not in source
        assert "._execute_write(" in source


def test_favorites_add_and_remove_use_direct_write_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core = _make_core(monkeypatch, "favorites_direct")
    manager = FavoritesManager(core)

    manager.add_favorite("C:/Temp/Song.wav", "Song", "audio", 12.0, {"artist": "A"})
    assert manager.is_favorite("c:\\temp\\song.wav")
    manager.remove_favorite("c:\\temp\\song.wav")
    assert not manager.is_favorite("C:/Temp/Song.wav")

    with pytest.raises(NotFoundError):
        manager.remove_favorite("C:/Temp/Song.wav")



def test_equivalent_favorite_writers_commit_only_one_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core = _make_core(monkeypatch, "favorites_concurrent_equivalent")
    manager = FavoritesManager(core)
    start = threading.Barrier(3)
    outcome_lock = threading.Lock()
    outcomes: list[str] = []

    def add(path: str) -> None:
        start.wait(timeout=3.0)
        try:
            manager.add_favorite(path, "Race", "audio", 1.0)
            result = "inserted"
        except IntegrityError:
            result = "duplicate"
        with outcome_lock:
            outcomes.append(result)

    threads = [
        threading.Thread(target=add, args=("C:/Temp/Race.wav",)),
        threading.Thread(target=add, args=("c:\\temp\\race.wav",)),
    ]
    for thread in threads:
        thread.start()
    start.wait(timeout=3.0)
    for thread in threads:
        thread.join(3.0)

    assert all(not thread.is_alive() for thread in threads)
    assert sorted(outcomes) == ["duplicate", "inserted"]
    rows = core._execute_query("SELECT path FROM favorites", fetch_all=True)
    assert rows is not None and len(rows) == 1

def test_favorites_atomic_duplicate_result_is_integrity_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core = _make_core(monkeypatch, "favorites_zero")
    manager = FavoritesManager(core)
    monkeypatch.setattr(
        core,
        "_execute_write",
        lambda query, params=None: DbWriteResult(rowcount=0, lastrowid=None),
    )

    with pytest.raises(IntegrityError, match="already exists"):
        manager.add_favorite("missing.wav", "Missing", "audio", 1.0)



def test_library_and_eq_inserts_reject_zero_row_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core = _make_core(monkeypatch, "insert_zero_results")
    library = LibraryManager(core)
    eq_presets = EqPresetManager(core)
    monkeypatch.setattr(
        core,
        "_execute_write",
        lambda query, params=None: DbWriteResult(rowcount=0, lastrowid=None),
    )

    with pytest.raises(DatabaseError, match="unexpected number of rows"):
        library.add_library_item("missing.wav", "Missing", "audio", 1.0, {})
    with pytest.raises(DatabaseError, match="unexpected number of rows"):
        eq_presets.add_custom_eq_preset("Missing", {})


def test_favorites_database_failures_are_chained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core = _make_core(monkeypatch, "favorites_db_failure")
    manager = FavoritesManager(core)
    manager.add_favorite("existing.wav", "Existing", "audio", 1.0)

    def fail_write(query: str, params: tuple[object, ...] | None = None) -> DbWriteResult:
        raise DatabaseError("injected favorite write failure")

    monkeypatch.setattr(core, "_execute_write", fail_write)
    with pytest.raises(DatabaseError) as add_error:
        manager.add_favorite("new.wav", "New", "audio", 1.0)
    with pytest.raises(DatabaseError) as remove_error:
        manager.remove_favorite("existing.wav")

    assert isinstance(add_error.value.__cause__, DatabaseError)
    assert isinstance(remove_error.value.__cause__, DatabaseError)

def test_library_integrity_error_keeps_its_application_subtype(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core = _make_core(monkeypatch, "library_integrity")
    manager = LibraryManager(core)

    with pytest.raises(IntegrityError) as captured:
        manager.add_library_item(None, "Broken", "audio", 1.0, {})

    assert isinstance(captured.value.__cause__, IntegrityError)


def test_library_update_renames_in_place_without_duplicate_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core = _make_core(monkeypatch, "library_rename")
    manager = LibraryManager(core)

    manager.add_library_item("original.wav", "Original", "audio", 1.0, {})
    manager.update_library_item(
        "original.wav",
        {"path": "renamed.wav", "title": "Renamed"},
    )

    assert manager.get_library_item("original.wav") is None
    renamed = manager.get_library_item("renamed.wav")
    assert renamed is not None and renamed["title"] == "Renamed"
    assert len(manager.get_all_library_items()) == 1




def test_library_database_failures_are_chained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core = _make_core(monkeypatch, "library_db_failure")
    manager = LibraryManager(core)
    manager.add_library_item("existing.wav", "Existing", "audio", 1.0, {})

    def fail_write(query: str, params: tuple[object, ...] | None = None) -> DbWriteResult:
        raise DatabaseError("injected library write failure")

    monkeypatch.setattr(core, "_execute_write", fail_write)
    with pytest.raises(DatabaseError) as add_error:
        manager.add_library_item("new.wav", "New", "audio", 1.0, {})
    with pytest.raises(DatabaseError) as remove_error:
        manager.remove_library_item("existing.wav")
    with pytest.raises(DatabaseError) as update_error:
        manager.update_library_item("existing.wav", {"title": "Updated"})

    assert isinstance(add_error.value.__cause__, DatabaseError)
    assert isinstance(remove_error.value.__cause__, DatabaseError)
    assert isinstance(update_error.value.__cause__, DatabaseError)

def test_library_update_preserves_legacy_value_normalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core = _make_core(monkeypatch, "library_update_normalization")
    manager = LibraryManager(core)
    manager.add_library_item("item.wav", "Item", "audio", 4.0, {"artist": "A"})

    manager.update_library_item(
        "item.wav",
        {"title": None, "media_type": None, "duration": None, "metadata": None},
    )

    updated = manager.get_library_item("item.wav")
    assert updated is not None
    assert updated["title"] == ""
    assert updated["media_type"] == ""
    assert updated["duration"] == 0.0
    assert updated["metadata"] == {}

def test_library_update_does_not_resurrect_concurrently_removed_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core = _make_core(monkeypatch, "library_no_resurrection")
    manager = LibraryManager(core)
    manager.add_library_item("removed.wav", "Removed", "audio", 1.0, {})
    stale = manager.get_library_item("removed.wav")
    assert stale is not None
    core._execute_write("DELETE FROM library_items WHERE path = ?", ("removed.wav",))
    monkeypatch.setattr(manager, "get_library_item", lambda path: dict(stale))

    with pytest.raises(NotFoundError):
        manager.update_library_item("removed.wav", {"title": "Resurrected"})

    remaining = core._execute_query(
        "SELECT COUNT(*) AS total FROM library_items", fetch_one=True
    )
    assert remaining is not None and remaining["total"] == 0


def test_library_path_conflict_preserves_integrity_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core = _make_core(monkeypatch, "library_path_conflict")
    manager = LibraryManager(core)
    manager.add_library_item("first.wav", "First", "audio", 1.0, {})
    manager.add_library_item("second.wav", "Second", "audio", 2.0, {})

    with pytest.raises(IntegrityError) as captured:
        manager.update_library_item("first.wav", {"path": "second.wav"})

    assert isinstance(captured.value.__cause__, IntegrityError)
    assert manager.get_library_item("first.wav") is not None
    assert manager.get_library_item("second.wav") is not None


def test_library_unique_writes_reject_impossible_multi_row_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core = _make_core(monkeypatch, "library_multirow")
    manager = LibraryManager(core)
    manager.add_library_item("item.wav", "Item", "audio", 1.0, {})
    monkeypatch.setattr(
        core,
        "_execute_write",
        lambda query, params=None: DbWriteResult(rowcount=2, lastrowid=None),
    )

    with pytest.raises(DatabaseError, match="unexpected number of rows"):
        manager.remove_library_item("item.wav")


def test_eq_preset_operations_use_direct_write_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core = _make_core(monkeypatch, "eq_direct")
    manager = EqPresetManager(core)

    manager.add_custom_eq_preset("Night", {"60": 2.0})
    preset = manager.get_custom_eq_presets()[0]
    preset_id = int(preset["id"])
    manager.update_custom_eq_preset(preset_id, "Night 2", {"60": 3.0})
    manager.delete_custom_eq_preset(preset_id)

    with pytest.raises(NotFoundError):
        manager.update_custom_eq_preset(preset_id, "Missing", {})
    with pytest.raises(NotFoundError):
        manager.delete_custom_eq_preset(preset_id)



def test_eq_preset_database_failures_are_chained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core = _make_core(monkeypatch, "eq_db_failure")
    manager = EqPresetManager(core)
    manager.add_custom_eq_preset("Existing", {})
    preset_id = int(manager.get_custom_eq_presets()[0]["id"])

    def fail_write(query: str, params: tuple[object, ...] | None = None) -> DbWriteResult:
        raise DatabaseError("injected EQ write failure")

    monkeypatch.setattr(core, "_execute_write", fail_write)
    operations = (
        lambda: manager.add_custom_eq_preset("New", {}),
        lambda: manager.update_custom_eq_preset(preset_id, "Updated", {}),
        lambda: manager.delete_custom_eq_preset(preset_id),
    )
    for operation in operations:
        with pytest.raises(DatabaseError) as captured:
            operation()
        assert isinstance(captured.value.__cause__, DatabaseError)

def test_eq_preset_duplicate_preserves_integrity_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core = _make_core(monkeypatch, "eq_integrity")
    manager = EqPresetManager(core)
    manager.add_custom_eq_preset("Night", {})

    with pytest.raises(IntegrityError) as captured:
        manager.add_custom_eq_preset("Night", {})
    manager.add_custom_eq_preset("Day", {})
    day_id = next(
        int(row["id"])
        for row in manager.get_custom_eq_presets()
        if row["name"] == "Day"
    )
    with pytest.raises(IntegrityError) as update_error:
        manager.update_custom_eq_preset(day_id, "Night", {})

    assert isinstance(captured.value.__cause__, IntegrityError)
    assert isinstance(update_error.value.__cause__, IntegrityError)


def test_eq_unique_writes_reject_impossible_multi_row_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core = _make_core(monkeypatch, "eq_multirow")
    manager = EqPresetManager(core)
    manager.add_custom_eq_preset("Night", {})
    preset_id = int(manager.get_custom_eq_presets()[0]["id"])
    monkeypatch.setattr(
        core,
        "_execute_write",
        lambda query, params=None: DbWriteResult(rowcount=2, lastrowid=None),
    )

    with pytest.raises(DatabaseError, match="unexpected number of rows"):
        manager.update_custom_eq_preset(preset_id, "Night", {})
    with pytest.raises(DatabaseError, match="unexpected number of rows"):
        manager.delete_custom_eq_preset(preset_id)

@pytest.mark.parametrize("target", ["favorite", "library", "eq"])
def test_delete_integrity_errors_preserve_application_subtype(
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    core = _make_core(monkeypatch, f"delete_integrity_{target}")
    favorite = FavoritesManager(core)
    library = LibraryManager(core)
    eq_presets = EqPresetManager(core)
    favorite.add_favorite("item.wav", "Item", "audio", 1.0)
    library.add_library_item("item.wav", "Item", "audio", 1.0, {})
    eq_presets.add_custom_eq_preset("Item", {})
    preset_id = int(eq_presets.get_custom_eq_presets()[0]["id"])

    def fail_write(
        query: str, params: tuple[object, ...] | None = None
    ) -> DbWriteResult:
        raise IntegrityError("injected delete integrity failure")

    monkeypatch.setattr(core, "_execute_write", fail_write)
    operations = {
        "favorite": lambda: favorite.remove_favorite("item.wav"),
        "library": lambda: library.remove_library_item("item.wav"),
        "eq": lambda: eq_presets.delete_custom_eq_preset(preset_id),
    }
    with pytest.raises(IntegrityError) as captured:
        operations[target]()

    assert isinstance(captured.value.__cause__, IntegrityError)
