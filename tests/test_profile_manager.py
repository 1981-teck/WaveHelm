from __future__ import annotations

from collections.abc import Callable
import json
import logging
import os
from pathlib import Path
import threading
import time

import pytest

import src.model.profile_manager as profile_module
import src.utils.durable_io as durable_io
from src.model.profile_manager import ProfileManager
from src.utils.durable_io import (
    DurabilityStatus,
    SerializedCommitGate,
    write_bytes_atomic_durable,
)
from src.utils.exceptions import ProfileError


ProfileOperation = Callable[[ProfileManager], DurabilityStatus]
PROFILE_MUTATORS: tuple[tuple[str, ProfileOperation], ...] = (
    ("increment_stat", lambda manager: manager.increment_stat("plays")),
    ("set_effects", lambda manager: manager.set_effects_settings({"echo": True})),
    ("set_eq", lambda manager: manager.set_eq_settings({"gain": 2})),
    ("save_eq", lambda manager: manager.save_custom_eq_preset("Rock", {"60": 3})),
    ("delete_eq", lambda manager: manager.delete_custom_eq_preset("Rock")),
    (
        "save_effects",
        lambda manager: manager.save_custom_effects_setting("Wide", {"reverb": True}),
    ),
    (
        "delete_effects",
        lambda manager: manager.delete_custom_effects_setting("Wide"),
    ),
    ("home_prefs", lambda manager: manager.set_home_stats_prefs({"plays": True})),
)


def _make_manager(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str
) -> tuple[ProfileManager, Path]:
    runtime_dir = tmp_path / name
    runtime_dir.mkdir(parents=True, exist_ok=True)
    profile_path = runtime_dir / "profile.json"
    monkeypatch.setattr(profile_module, "PROFILE_PATH", profile_path)
    return ProfileManager(), profile_path


def test_load_profile_reads_existing_json_and_rejects_invalid_roots(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "stats": {"plays": 2},
                "effects_settings": {"echo": {"enabled": True}},
                "eq_settings": {"band_gains": {"60": 1.0}},
                "custom_eq_presets": {"Rocky": {"60": 3}},
                "custom_effects_settings": {"Wide": {"reverb": True}},
                "home_stats_prefs": {"show_plays": True},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(profile_module, "PROFILE_PATH", profile_path)
    manager = ProfileManager()

    assert manager.get_stat("plays") == 2
    assert manager.get_effects_settings() == {"echo": {"enabled": True}}
    assert manager.get_eq_settings() == {"band_gains": {"60": 1.0}}
    assert manager.get_custom_eq_presets() == {"Rocky": {"60": 3}}
    assert manager.get_custom_effects_settings() == {"Wide": {"reverb": True}}
    assert manager.get_home_stats_prefs() == {"show_plays": True}

    invalid_payloads = (
        "{broken",
        "[]",
        '{"stats": []}',
        '{"stats": {}, "future_section": {"value": 1}}',
    )
    for invalid_payload in invalid_payloads:
        profile_path.write_text(invalid_payload, encoding="utf-8")
        original_payload = profile_path.read_bytes()
        broken = ProfileManager()
        assert broken.get_stat("plays") == 0
        assert broken.get_home_stats_prefs() == {}
        with pytest.raises(ProfileError, match="writes are blocked"):
            broken.increment_stat("plays")
        assert profile_path.read_bytes() == original_payload


def test_mutations_persist_all_sections_and_report_durability(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager, profile_path = _make_manager(monkeypatch, tmp_path, "save_all")

    results = [
        manager.increment_stat("plays", 3),
        manager.set_effects_settings({"echo": {"enabled": True}}),
        manager.set_eq_settings({"band_gains": {"60": 2.5}}),
        manager.save_custom_eq_preset("Rock", {"60": 3.0}),
        manager.save_custom_effects_setting("Wide", {"reverb": True}),
        manager.set_home_stats_prefs({"show_plays": True}),
    ]

    assert results == [DurabilityStatus.DURABLE] * len(results)
    data = json.loads(profile_path.read_text(encoding="utf-8"))
    assert data["stats"] == {"plays": 3}
    assert data["effects_settings"] == {"echo": {"enabled": True}}
    assert data["eq_settings"] == {"band_gains": {"60": 2.5}}
    assert data["custom_eq_presets"] == {"Rock": {"60": 3.0}}
    assert data["custom_effects_settings"] == {"Wide": {"reverb": True}}
    assert data["home_stats_prefs"] == {"show_plays": True}


def test_getters_and_inputs_are_detached_from_live_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager, _ = _make_manager(monkeypatch, tmp_path, "detached")
    input_settings = {"echo": {"enabled": True}}
    manager.set_effects_settings(input_settings)

    input_settings["echo"]["enabled"] = False
    returned = manager.get_effects_settings()
    returned["echo"]["enabled"] = False
    profile_snapshot = manager.user_profile
    profile_snapshot.effects_settings["echo"]["enabled"] = False

    assert manager.get_effects_settings() == {"echo": {"enabled": True}}


def test_delete_mutations_persist_the_removal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager, profile_path = _make_manager(monkeypatch, tmp_path, "deletes")
    manager.save_custom_eq_preset("Rock", {"60": 3.0})
    manager.save_custom_effects_setting("Wide", {"reverb": True})

    assert manager.delete_custom_eq_preset("Rock") is DurabilityStatus.DURABLE
    assert manager.delete_custom_effects_setting("Wide") is DurabilityStatus.DURABLE
    data = json.loads(profile_path.read_text(encoding="utf-8"))

    assert manager.get_custom_eq_presets() == {}
    assert manager.get_custom_effects_settings() == {}
    assert data["custom_eq_presets"] == {}
    assert data["custom_effects_settings"] == {}


@pytest.mark.parametrize("failure_stage", ["open", "partial_write", "file_fsync", "replace"])
def test_precommit_failure_stages_preserve_memory_file_and_reopen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure_stage: str
) -> None:
    manager, profile_path = _make_manager(
        monkeypatch, tmp_path, f"precommit_{failure_stage}"
    )
    manager.increment_stat("plays", 2)
    original_payload = profile_path.read_bytes()

    if failure_stage == "open":

        def fail_open(*_args: object, **_kwargs: object) -> tuple[int, str]:
            raise PermissionError("temporary open blocked")

        monkeypatch.setattr(durable_io.tempfile, "mkstemp", fail_open)
    elif failure_stage == "partial_write":

        def fail_partial_write(file_descriptor: int, payload: bytes) -> None:
            os.write(file_descriptor, payload[:1])
            raise OSError("partial write blocked")

        monkeypatch.setattr(durable_io, "_write_all", fail_partial_write)
    elif failure_stage == "file_fsync":

        def fail_fsync(_file_descriptor: int) -> None:
            raise OSError("fsync blocked")

        monkeypatch.setattr(durable_io.os, "fsync", fail_fsync)
    else:

        def fail_replace(_source: Path, _target: Path) -> None:
            raise PermissionError("replace blocked")

        monkeypatch.setattr(durable_io, "durable_replace", fail_replace)

    with pytest.raises(ProfileError, match="Unable to persist"):
        manager.set_eq_settings({"band_gains": {"60": 4.0}})

    assert manager.get_stat("plays") == 2
    assert manager.get_eq_settings() == {}
    assert profile_path.read_bytes() == original_payload
    assert list(profile_path.parent.glob(".profile.json.*.tmp")) == []
    reopened = ProfileManager()
    assert reopened.get_stat("plays") == 2
    assert reopened.get_eq_settings() == {}


@pytest.mark.parametrize(("operation_name", "operation"), PROFILE_MUTATORS)
def test_every_profile_mutator_propagates_precommit_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    operation_name: str,
    operation: ProfileOperation,
) -> None:
    manager, profile_path = _make_manager(monkeypatch, tmp_path, operation_name)
    before = manager.user_profile

    def fail_write(_target: Path, _payload: bytes) -> bool:
        raise PermissionError("write blocked")

    monkeypatch.setattr(profile_module, "write_bytes_atomic_durable", fail_write)

    with pytest.raises(ProfileError, match="Unable to persist"):
        operation(manager)

    assert manager.user_profile == before
    assert not profile_path.exists()


def test_postcommit_sync_failure_updates_matching_memory_and_returns_degraded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager, profile_path = _make_manager(monkeypatch, tmp_path, "degraded")

    def committed_without_directory_sync(target: Path, payload: bytes) -> bool:
        target.write_bytes(payload)
        return False

    monkeypatch.setattr(
        profile_module,
        "write_bytes_atomic_durable",
        committed_without_directory_sync,
    )

    status = manager.set_effects_settings({"echo": {"enabled": True}})
    persisted = json.loads(profile_path.read_text(encoding="utf-8"))

    assert status is DurabilityStatus.COMMITTED_WITHOUT_DIRECTORY_SYNC
    assert manager.get_effects_settings() == persisted["effects_settings"]
    reopened = ProfileManager()
    assert reopened.get_effects_settings() == persisted["effects_settings"]


def test_serialization_failure_is_typed_and_does_not_commit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager, profile_path = _make_manager(monkeypatch, tmp_path, "serialization")
    cyclic: dict[str, object] = {}
    cyclic["cycle"] = cyclic

    with pytest.raises(ProfileError, match="serialize"):
        manager.set_effects_settings(cyclic)  # type: ignore[arg-type]

    assert manager.get_effects_settings() == {}
    assert not profile_path.exists()


def test_concurrent_mutations_are_serialized_and_keep_disk_aligned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager, profile_path = _make_manager(monkeypatch, tmp_path, "concurrent")
    original_write = write_bytes_atomic_durable
    active = 0
    maximum_active = 0
    counter_lock = threading.Lock()
    start = threading.Barrier(9)
    errors: list[Exception] = []
    statuses: list[DurabilityStatus] = []

    def delayed_write(target: Path, payload: bytes) -> bool:
        nonlocal active, maximum_active
        with counter_lock:
            active += 1
            maximum_active = max(maximum_active, active)
        try:
            time.sleep(0.01)
            return original_write(target, payload)
        finally:
            with counter_lock:
                active -= 1

    def increment() -> None:
        start.wait()
        try:
            statuses.append(manager.increment_stat("plays"))
        except Exception as error:  # explicit thread boundary
            errors.append(error)

    monkeypatch.setattr(profile_module, "write_bytes_atomic_durable", delayed_write)
    threads = [threading.Thread(target=increment) for _ in range(8)]
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join(timeout=5)

    persisted = json.loads(profile_path.read_text(encoding="utf-8"))
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert statuses == [DurabilityStatus.DURABLE] * 8
    assert maximum_active == 1
    assert manager.get_stat("plays") == 8
    assert persisted["stats"] == {"plays": 8}


def test_serialized_commit_gate_rejects_reentry_and_recovers() -> None:
    gate = SerializedCommitGate()

    with gate.transaction():
        with pytest.raises(RuntimeError, match="reentrant"):
            with gate.transaction():
                raise AssertionError("unreachable")

    with pytest.raises(ValueError, match="body failed"):
        with gate.transaction():
            raise ValueError("body failed")

    with gate.transaction():
        pass


def test_stat_validation_and_profile_setting_compatibility(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager, _ = _make_manager(monkeypatch, tmp_path, "validation")

    with pytest.raises(ProfileError, match="integers"):
        manager.increment_stat("broken", True)

    manager.profile = {"language": "it"}
    manager.settings = {"theme": "dark"}
    assert manager.get_profile_setting("language", "en") == "it"
    assert manager.get_profile_setting("theme", "light") == "dark"
    assert manager.get_profile_setting("missing", "fallback") == "fallback"


def test_get_profile_setting_logs_debug_on_broken_mapping(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    manager, _ = _make_manager(monkeypatch, tmp_path, "broken_profile")
    caplog.set_level(logging.DEBUG, logger=profile_module.logger.name)

    class BrokenDict(dict[str, str]):
        def __contains__(self, key: object) -> bool:
            raise RuntimeError("broken contains")

    manager.profile = BrokenDict()

    assert manager.get_profile_setting("language", "en") == "en"
    assert "get_profile_setting fallback for language" in caplog.text
