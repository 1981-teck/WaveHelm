from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
import json
import logging
from pathlib import Path
import threading
import time

import pytest

from src.audio.audio_events import AudioEventType
import src.model.theme_manager as theme_manager_module
from src.model.theme_manager import ThemeManager
from src.model.theme_manager_builtins import get_builtin_themes
from src.model.theme_schema import THEME_COLOR_KEY_SET
from src.utils.durable_io import DurabilityStatus, write_bytes_atomic_durable
from src.utils.exceptions import SettingsError


class DummyLocalizationManager:
    def __init__(
        self, mapping: dict[str, str] | None = None, fail: bool = False
    ) -> None:
        self.mapping = mapping or {}
        self.fail = fail

    def get_text(self, key: str, **kwargs: object) -> str:
        if self.fail:
            raise ValueError("boom")
        text = self.mapping.get(key, key)
        return text.format(**kwargs) if kwargs else text


class RaisingLocalizationManager:
    def get_text(self, key: str, **kwargs: object) -> str:
        del key, kwargs
        raise OSError("localization provider failed")


class DummyBus:
    def __init__(self) -> None:
        self.calls: list[tuple[AudioEventType, object]] = []

    def publish(self, event_type: AudioEventType, payload: object) -> bool:
        self.calls.append((event_type, payload))
        return True


class RaisingBus:
    def publish(self, event_type: AudioEventType, payload: object) -> bool:
        del event_type, payload
        raise KeyError("event bus failed")


ThemeOperation = Callable[[ThemeManager], DurabilityStatus]
THEME_MUTATORS: tuple[tuple[str, ThemeOperation], ...] = (
    ("save", lambda manager: manager.save_custom_themes()),
    (
        "set_color",
        lambda manager: manager.set_custom_theme_color("bg_color", "#222222"),
    ),
    ("reset", lambda manager: manager.reset_custom_theme_colors()),
)


def _make_manager(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    subdir: str,
    localization_manager: DummyLocalizationManager | None = None,
    bus: DummyBus | None = None,
    builtins: dict[str, dict[str, str]] | None = None,
) -> ThemeManager:
    runtime_dir = tmp_path / subdir
    runtime_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        theme_manager_module, "get_app_data_path", lambda: runtime_dir
    )
    production = get_builtin_themes()
    catalog = builtins or {
        "dark": production["dark"],
        "light": production["light"],
    }
    monkeypatch.setattr(
        theme_manager_module,
        "get_builtin_themes",
        lambda: deepcopy(catalog),
    )
    monkeypatch.setattr(
        theme_manager_module, "BUILTIN_COLOR_THEME_NAMES", ["blue", "green"]
    )
    return ThemeManager(
        localization_manager=localization_manager,
        event_bus=bus,
    )


def test_get_localized_text_falls_back_cleanly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    manager = _make_manager(
        monkeypatch,
        tmp_path,
        "localized",
        localization_manager=DummyLocalizationManager(
            {"theme_manager_initialized": "ok"}
        ),
    )
    assert manager._get_localized_text("theme_manager_initialized") == "ok"

    caplog.set_level(logging.DEBUG, logger=theme_manager_module.logger.name)
    broken = _make_manager(
        monkeypatch,
        tmp_path,
        "localized_broken",
        localization_manager=DummyLocalizationManager(fail=True),
    )
    assert broken._get_localized_text("missing", value="x") == "[missing]"
    assert "ThemeManager localization fallback for missing" in caplog.text


def test_load_custom_themes_merges_valid_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime_dir = tmp_path / "load_custom"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    production = get_builtin_themes()
    ocean = deepcopy(production["dark"])
    ocean["bg_color"] = "#ff00ff"
    (runtime_dir / "custom_themes.json").write_text(
        json.dumps({"ocean": ocean}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        theme_manager_module, "get_app_data_path", lambda: runtime_dir
    )
    monkeypatch.setattr(
        theme_manager_module,
        "get_builtin_themes",
        lambda: {
            "dark": deepcopy(production["dark"]),
            "light": deepcopy(production["light"]),
        },
    )
    monkeypatch.setattr(
        theme_manager_module, "BUILTIN_COLOR_THEME_NAMES", ["blue"]
    )

    manager = ThemeManager()

    assert manager.themes["ocean"]["bg_color"] == "#ff00ff"


@pytest.mark.parametrize("invalid_payload", ["[]", "{broken", '{"pink": []}'])
def test_load_custom_themes_rejects_invalid_catalog_and_blocks_overwrite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, invalid_payload: str
) -> None:
    runtime_dir = tmp_path / "load_invalid"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "custom_themes.json").write_text(
        invalid_payload, encoding="utf-8"
    )
    monkeypatch.setattr(
        theme_manager_module, "get_app_data_path", lambda: runtime_dir
    )

    original_payload = (runtime_dir / "custom_themes.json").read_bytes()
    manager = ThemeManager()

    assert "custom" not in manager.themes
    with pytest.raises(SettingsError, match="writes are blocked"):
        manager.set_custom_theme_color("bg_color", "#222222")
    assert (runtime_dir / "custom_themes.json").read_bytes() == original_payload


def test_set_theme_publishes_and_handles_no_change(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bus = DummyBus()
    manager = _make_manager(monkeypatch, tmp_path, "set_theme", bus=bus)

    assert manager.set_theme("dark", "blue") is False
    assert manager.set_theme("light", "green") is True
    assert manager.mode == "light"
    assert manager.color_theme == "green"
    assert bus.calls[-1] == (
        AudioEventType.THEME_CHANGED,
        {"mode": "light", "color_theme": "green"},
    )


def test_system_theme_falls_back_to_default_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = _make_manager(monkeypatch, tmp_path, "system_mode")
    manager.mode = "system"
    manager.default_mode = "light"

    assert manager.get_current_theme_colors()["bg_color"] == "#ffffff"


def test_custom_theme_save_and_reset_report_durability(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = _make_manager(monkeypatch, tmp_path, "custom_theme")

    assert (
        manager.set_custom_theme_color("bg_color", "#222222")
        is DurabilityStatus.DURABLE
    )
    saved = json.loads(manager.custom_themes_file.read_text(encoding="utf-8"))
    assert saved["custom"]["bg_color"] == "#222222"
    assert manager.mode == "custom"

    assert manager.reset_custom_theme_colors() is DurabilityStatus.DURABLE
    assert set(manager.themes["custom"]) == THEME_COLOR_KEY_SET
    assert manager.mode == "custom"


def test_precommit_failure_preserves_catalog_mode_and_notifications(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bus = DummyBus()
    manager = _make_manager(monkeypatch, tmp_path, "precommit", bus=bus)
    callbacks: list[str] = []
    manager.register_theme_change_callback(lambda: callbacks.append("changed"))
    initial_bus_calls = list(bus.calls)

    def fail_write(_target: Path, _payload: bytes) -> bool:
        raise PermissionError("replace blocked")

    monkeypatch.setattr(theme_manager_module, "write_bytes_atomic_durable", fail_write)

    with pytest.raises(SettingsError, match="persist"):
        manager.set_custom_theme_color("bg_color", "#222222")

    assert "custom" not in manager.themes
    assert manager.mode == "dark"
    assert callbacks == []
    assert bus.calls == initial_bus_calls
    assert not manager.custom_themes_file.exists()


@pytest.mark.parametrize(("operation_name", "operation"), THEME_MUTATORS)
def test_every_custom_theme_mutator_propagates_precommit_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    operation_name: str,
    operation: ThemeOperation,
) -> None:
    manager = _make_manager(monkeypatch, tmp_path, f"mutator_{operation_name}")
    before_catalog = deepcopy(manager.themes)
    before_mode = manager.mode

    def fail_write(_target: Path, _payload: bytes) -> bool:
        raise PermissionError("write blocked")

    monkeypatch.setattr(theme_manager_module, "write_bytes_atomic_durable", fail_write)

    with pytest.raises(SettingsError, match="persist"):
        operation(manager)

    assert manager.themes == before_catalog
    assert manager.mode == before_mode
    assert not manager.custom_themes_file.exists()


def test_postcommit_sync_failure_commits_matching_catalog_and_reports_degraded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = _make_manager(monkeypatch, tmp_path, "degraded")

    def committed_without_directory_sync(target: Path, payload: bytes) -> bool:
        target.write_bytes(payload)
        return False

    monkeypatch.setattr(
        theme_manager_module,
        "write_bytes_atomic_durable",
        committed_without_directory_sync,
    )

    status = manager.set_custom_theme_color("bg_color", "#333333")
    persisted = json.loads(manager.custom_themes_file.read_text(encoding="utf-8"))

    assert status is DurabilityStatus.COMMITTED_WITHOUT_DIRECTORY_SYNC
    assert manager.themes["custom"] == persisted["custom"]
    assert manager.mode == "custom"
    reopened = _make_manager(monkeypatch, tmp_path, "degraded")
    assert reopened.themes["custom"] == persisted["custom"]


def test_postcommit_notification_failures_do_not_reclassify_persistence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = _make_manager(monkeypatch, tmp_path, "notification_boundary")
    manager.event_bus = RaisingBus()
    manager.localization_manager = RaisingLocalizationManager()

    def fail_callback() -> None:
        raise KeyError("callback failed")

    manager.register_theme_change_callback(fail_callback)
    status = manager.set_custom_theme_color("bg_color", "#444444")
    persisted = json.loads(manager.custom_themes_file.read_text(encoding="utf-8"))

    assert status is DurabilityStatus.DURABLE
    assert manager.mode == "custom"
    assert manager.themes["custom"] == persisted["custom"]


def test_active_custom_palette_change_emits_one_refresh_after_commit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bus = DummyBus()
    manager = _make_manager(monkeypatch, tmp_path, "active_refresh", bus=bus)
    manager.set_custom_theme_color("bg_color", "#222222")
    bus.calls.clear()
    callbacks: list[str] = []
    manager.register_theme_change_callback(lambda: callbacks.append("changed"))

    manager.set_custom_theme_color("text_color", "#eeeeee")

    assert callbacks == ["changed"]
    assert bus.calls == [
        (
            AudioEventType.THEME_CHANGED,
            {"mode": "custom", "color_theme": "blue"},
        )
    ]


def test_concurrent_custom_theme_writes_preserve_all_committed_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = _make_manager(monkeypatch, tmp_path, "concurrent")
    original_write = write_bytes_atomic_durable
    active = 0
    maximum_active = 0
    counter_lock = threading.Lock()
    start = threading.Barrier(3)
    errors: list[Exception] = []

    def delayed_write(target: Path, payload: bytes) -> bool:
        nonlocal active, maximum_active
        with counter_lock:
            active += 1
            maximum_active = max(maximum_active, active)
        try:
            time.sleep(0.02)
            return original_write(target, payload)
        finally:
            with counter_lock:
                active -= 1

    def update(key: str, value: str) -> None:
        start.wait()
        try:
            manager.set_custom_theme_color(key, value)
        except Exception as error:  # explicit thread boundary
            errors.append(error)

    monkeypatch.setattr(theme_manager_module, "write_bytes_atomic_durable", delayed_write)
    threads = [
        threading.Thread(target=update, args=("bg_color", "#222222")),
        threading.Thread(target=update, args=("text_color", "#eeeeee")),
    ]
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join(timeout=5)

    persisted = json.loads(manager.custom_themes_file.read_text(encoding="utf-8"))
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert maximum_active == 1
    assert persisted["custom"]["bg_color"] == "#222222"
    assert persisted["custom"]["text_color"] == "#eeeeee"
    assert manager.themes["custom"] == persisted["custom"]


def test_callbacks_register_notify_unregister(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = _make_manager(
        monkeypatch,
        tmp_path,
        "callbacks",
        localization_manager=DummyLocalizationManager(
            {
                "registered_theme_callback": "{callback}",
                "unregistered_theme_callback": "{callback}",
                "notifying_theme_change": "{count}",
                "error_in_theme_callback": "{callback}:{error}",
                "theme_manager_initialized": "{theme}:{color}",
            }
        ),
    )
    calls: list[str] = []

    def good() -> None:
        calls.append("good")

    def bad() -> None:
        raise RuntimeError("bad")

    manager.register_theme_change_callback(good)
    manager.register_theme_change_callback(bad)
    manager.notify_theme_change()
    manager.unregister_theme_change_callback(good)

    assert calls == ["good"]
    assert good not in manager._theme_change_callbacks


def test_close_clears_callbacks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = _make_manager(monkeypatch, tmp_path, "close_case")
    manager.register_theme_change_callback(lambda: None)
    manager.close()
    assert manager._theme_change_callbacks == []
