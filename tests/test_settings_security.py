from __future__ import annotations

import json
import logging
from pathlib import Path
from types import MethodType

import pytest

import src.controller.settings_controller_cleanup as cleanup_module
import src.model.setting_manager as setting_manager_module
from src.model.setting_manager import MANAGED_DIRECTORY_MARKER, SettingsManager
from src.utils.exceptions import SettingsError


class DummySettingsManager:
    def __init__(self, settings_file: Path, cache_dir: Path) -> None:
        self.SETTINGS_FILE = settings_file
        self.values: dict[str, object] = {"cache_dir": str(cache_dir)}

    def get_setting(self, key: str, default: object = None) -> object:
        return self.values.get(key, default)


class DummyAudioEngine:
    def __init__(self, processed_audio_dir: Path) -> None:
        self._processed_audio_dir = processed_audio_dir
        self._current_file: str | None = None
        self._playback_source_file: str | None = None


class CleanupHarness:
    CLEANUP_EXCEPTIONS = (AttributeError, OSError, RuntimeError, TypeError, ValueError)
    LOG_HANDLER_EXCEPTIONS = (AttributeError, OSError, RuntimeError, TypeError, ValueError)

    def __init__(self, settings_manager: DummySettingsManager, audio_engine: DummyAudioEngine) -> None:
        self.settings_manager = settings_manager
        self.audio_engine = audio_engine

    def _get_localized_text(self, translation_key: str, **kwargs: object) -> str:
        default = kwargs.get("default", translation_key)
        return str(default)


def _bind_cleanup_methods(harness: CleanupHarness) -> CleanupHarness:
    bindings = {
        "get_app_data_dir": cleanup_module.get_app_data_dir,
        "get_processed_audio_dir": cleanup_module.get_processed_audio_dir,
        "get_logs_dir": cleanup_module.get_logs_dir,
        "_clear_directory_contents": cleanup_module._clear_directory_contents,
        "_truncate_active_log_file": cleanup_module._truncate_active_log_file,
        "is_processed_audio_cache_in_use": cleanup_module.is_processed_audio_cache_in_use,
        "get_processed_audio_cleanup_block_message": (
            cleanup_module.get_processed_audio_cleanup_block_message
        ),
    }
    for name, function in bindings.items():
        setattr(harness, name, MethodType(function, harness))
    return harness


def _make_cleanup_harness(tmp_path: Path) -> tuple[CleanupHarness, Path]:
    app_data_dir = tmp_path / "app_data"
    app_data_dir.mkdir()
    settings_file = app_data_dir / "settings.json"
    settings_file.write_text("{}", encoding="utf-8")

    cache_dir = app_data_dir / "cache"
    cache_dir.mkdir()
    (cache_dir / MANAGED_DIRECTORY_MARKER).write_text(
        "WaveHelm managed cache\n",
        encoding="utf-8",
    )
    processed_audio_dir = app_data_dir / "processed_audio"
    settings = DummySettingsManager(settings_file, cache_dir)
    audio = DummyAudioEngine(processed_audio_dir)
    return _bind_cleanup_methods(CleanupHarness(settings, audio)), app_data_dir


def _make_manager(monkeypatch: pytest.MonkeyPatch, app_data_dir: Path) -> SettingsManager:
    monkeypatch.setattr(
        setting_manager_module,
        "get_user_data_dir",
        lambda: app_data_dir,
    )
    return SettingsManager()


def _create_symlink(link: Path, target: Path, *, directory: bool) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as error:
        pytest.skip(f"symlinks are unavailable: {error}")


def test_setting_write_failure_preserves_memory_and_disk(monkeypatch, tmp_path):
    manager = _make_manager(monkeypatch, tmp_path / "app_data")
    original_snapshot = manager.get_all_settings()
    original_file = manager.SETTINGS_FILE.read_bytes()

    def fail_replace(source: object, destination: object) -> None:
        raise PermissionError("simulated replace failure")

    monkeypatch.setattr(setting_manager_module, "durable_replace", fail_replace)

    with pytest.raises(SettingsError, match="Unable to persist settings"):
        manager.set_setting("volume", 25)

    assert manager.get_all_settings() == original_snapshot
    assert manager.SETTINGS_FILE.read_bytes() == original_file
    assert list(manager.SETTINGS_FILE.parent.glob(".settings.json.*.tmp")) == []



def test_post_replace_directory_sync_failure_keeps_memory_and_disk_aligned(
    monkeypatch, tmp_path, caplog
):
    manager = _make_manager(monkeypatch, tmp_path / "app_data")

    def fail_directory_sync(_parent: Path) -> None:
        raise OSError("simulated directory sync failure")

    monkeypatch.setattr(setting_manager_module, "sync_parent_directory", fail_directory_sync)
    caplog.set_level(logging.WARNING, logger=setting_manager_module.logger.name)

    manager.set_setting("volume", 25)

    persisted = json.loads(manager.SETTINGS_FILE.read_text(encoding="utf-8"))
    assert manager.get_setting("volume") == 25
    assert persisted["volume"] == 25
    assert "parent directory sync failed" in caplog.text
    assert list(manager.SETTINGS_FILE.parent.glob(".settings.json.*.tmp")) == []

def test_import_is_atomic_and_ignores_protected_cache_path(monkeypatch, tmp_path):
    app_data_dir = tmp_path / "app_data"
    external_dir = tmp_path / "external"
    manager = _make_manager(monkeypatch, app_data_dir)

    result = manager.apply_imported_settings(
        {
            "language": "fr",
            "volume": 42,
            "cache_dir": str(external_dir),
        }
    )

    assert result.updated_keys == ("language", "volume")
    assert result.ignored_keys == ("cache_dir",)
    assert manager.get_setting("language") == "fr"
    assert manager.get_setting("volume") == 42
    assert manager.get_setting("cache_dir") == str((app_data_dir / "cache").resolve())
    assert not external_dir.exists()


def test_import_with_only_protected_settings_is_rejected(monkeypatch, tmp_path):
    manager = _make_manager(monkeypatch, tmp_path / "app_data")
    original_snapshot = manager.get_all_settings()

    with pytest.raises(SettingsError, match="no portable settings"):
        manager.apply_imported_settings({"cache_dir": str(tmp_path / "external")})

    assert manager.get_all_settings() == original_snapshot


def test_unknown_import_key_rejects_entire_transaction(monkeypatch, tmp_path):
    manager = _make_manager(monkeypatch, tmp_path / "app_data")
    original_snapshot = manager.get_all_settings()
    original_file = manager.SETTINGS_FILE.read_bytes()

    with pytest.raises(SettingsError, match="Unsupported imported setting"):
        manager.apply_imported_settings(
            {
                "language": "fr",
                "untrusted_runtime_path": str(tmp_path / "external"),
            }
        )

    assert manager.get_all_settings() == original_snapshot
    assert manager.SETTINGS_FILE.read_bytes() == original_file


def test_loading_repairs_external_cache_path_and_creates_marker(monkeypatch, tmp_path):
    app_data_dir = tmp_path / "app_data"
    external_dir = tmp_path / "external"
    app_data_dir.mkdir()
    settings_file = app_data_dir / "settings.json"
    settings_file.write_text(
        json.dumps({"language": "it", "cache_dir": str(external_dir)}),
        encoding="utf-8",
    )

    manager = _make_manager(monkeypatch, app_data_dir)
    canonical_cache = (app_data_dir / "cache").resolve()
    persisted = json.loads(settings_file.read_text(encoding="utf-8"))

    assert manager.get_setting("cache_dir") == str(canonical_cache)
    assert persisted["cache_dir"] == str(canonical_cache)
    assert (canonical_cache / MANAGED_DIRECTORY_MARKER).is_file()
    assert not external_dir.exists()


def test_managed_cache_symlink_never_writes_marker_outside_app_data(
    monkeypatch,
    tmp_path,
):
    app_data_dir = tmp_path / "app_data"
    external_dir = tmp_path / "external"
    app_data_dir.mkdir()
    external_dir.mkdir()
    cache_dir = app_data_dir / "cache"
    _create_symlink(cache_dir, external_dir, directory=True)

    manager = _make_manager(monkeypatch, app_data_dir)

    assert not (external_dir / MANAGED_DIRECTORY_MARKER).exists()
    with pytest.raises(SettingsError, match="cannot be a link"):
        manager.set_setting("volume", 25)


def test_exportable_settings_exclude_local_cache_path(monkeypatch, tmp_path):
    manager = _make_manager(monkeypatch, tmp_path / "app_data")

    exported = manager.get_exportable_settings()

    assert "cache_dir" not in exported
    assert exported["language"] == "en"


def test_runtime_cleanup_is_immediate_bounded_and_preserves_user_files(tmp_path):
    harness, app_data_dir = _make_cleanup_harness(tmp_path)
    cache_dir = app_data_dir / "cache"
    pycache_dir = app_data_dir / "pycache"
    logs_dir = app_data_dir / "logs"
    pycache_dir.mkdir()
    logs_dir.mkdir()
    (cache_dir / "cache.bin").write_text("cache", encoding="utf-8")
    (pycache_dir / "module.pyc").write_text("bytecode", encoding="utf-8")
    (logs_dir / "wavehelm.log").write_text("log", encoding="utf-8")
    (app_data_dir / "library.json").write_text("[]", encoding="utf-8")
    (app_data_dir / "wavehelm.db").write_text("db", encoding="utf-8")

    result = cleanup_module.clear_runtime_artifacts(harness)

    assert result == {
        "processed_audio_removed": 0,
        "cache_removed": 2,
        "logs_removed": 1,
        "total_removed": 3,
    }
    assert [item.name for item in cache_dir.iterdir()] == [MANAGED_DIRECTORY_MARKER]
    assert list(pycache_dir.iterdir()) == []
    assert list(logs_dir.iterdir()) == []
    assert (app_data_dir / "library.json").is_file()
    assert (app_data_dir / "wavehelm.db").is_file()


def test_runtime_cleanup_reports_partial_failure_instead_of_false_success(
    monkeypatch,
    tmp_path,
):
    harness, app_data_dir = _make_cleanup_harness(tmp_path)
    cache_dir = app_data_dir / "cache"
    removable = cache_dir / "remove.bin"
    blocked = cache_dir / "blocked.bin"
    removable.write_text("remove", encoding="utf-8")
    blocked.write_text("keep", encoding="utf-8")
    original_delete = cleanup_module._delete_managed_item

    def fail_one_item(directory: Path, item: Path) -> bool:
        if item.name == "blocked.bin":
            raise PermissionError("simulated cleanup failure")
        return original_delete(directory, item)

    monkeypatch.setattr(cleanup_module, "_delete_managed_item", fail_one_item)

    with pytest.raises(RuntimeError, match="Cleanup incomplete"):
        cleanup_module._clear_directory_contents(harness, cache_dir)

    assert not removable.exists()
    assert blocked.is_file()


def test_runtime_cleanup_rejects_external_configured_cache(tmp_path):
    harness, app_data_dir = _make_cleanup_harness(tmp_path)
    external_dir = tmp_path / "external"
    external_dir.mkdir()
    sentinel = external_dir / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    harness.settings_manager.values["cache_dir"] = str(external_dir)

    with pytest.raises(RuntimeError, match="unmanaged cleanup target"):
        cleanup_module.clear_runtime_artifacts(harness)

    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert (app_data_dir / "cache" / MANAGED_DIRECTORY_MARKER).is_file()


def test_runtime_cleanup_validates_all_targets_before_deleting_anything(tmp_path):
    harness, app_data_dir = _make_cleanup_harness(tmp_path)
    cache_file = app_data_dir / "cache" / "cache.bin"
    cache_file.write_text("keep until validation completes", encoding="utf-8")
    external_dir = tmp_path / "external_pycache"
    external_dir.mkdir()
    sentinel = external_dir / "sentinel.pyc"
    sentinel.write_text("keep", encoding="utf-8")
    pycache_dir = app_data_dir / "pycache"
    _create_symlink(pycache_dir, external_dir, directory=True)

    with pytest.raises(RuntimeError, match="cleanup path escape"):
        cleanup_module.clear_runtime_artifacts(harness)

    assert cache_file.is_file()
    assert sentinel.is_file()


def test_runtime_cleanup_rejects_cache_symlink_escape(tmp_path):
    harness, app_data_dir = _make_cleanup_harness(tmp_path)
    cache_dir = app_data_dir / "cache"
    external_dir = tmp_path / "external"
    external_dir.mkdir()
    sentinel = external_dir / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    (external_dir / MANAGED_DIRECTORY_MARKER).write_text("forged", encoding="utf-8")
    cache_dir.unlink() if cache_dir.is_symlink() else None
    if cache_dir.exists():
        for item in cache_dir.iterdir():
            item.unlink()
        cache_dir.rmdir()
    _create_symlink(cache_dir, external_dir, directory=True)

    with pytest.raises(RuntimeError, match="cleanup path escape"):
        cleanup_module.clear_runtime_artifacts(harness)

    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_cleanup_unlinks_child_symlink_without_touching_external_target(tmp_path):
    harness, app_data_dir = _make_cleanup_harness(tmp_path)
    cache_dir = app_data_dir / "cache"
    external_dir = tmp_path / "external"
    external_dir.mkdir()
    sentinel = external_dir / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    link = cache_dir / "external-link"
    _create_symlink(link, external_dir, directory=True)

    removed = cleanup_module._clear_directory_contents(harness, cache_dir)

    assert removed == 1
    assert not link.exists()
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_processed_audio_cleanup_rejects_external_directory(tmp_path):
    harness, _ = _make_cleanup_harness(tmp_path)
    external_dir = tmp_path / "external_processed"
    external_dir.mkdir()
    sentinel = external_dir / "sentinel.wav"
    sentinel.write_text("keep", encoding="utf-8")
    harness.audio_engine._processed_audio_dir = external_dir

    with pytest.raises(RuntimeError, match="unmanaged cleanup target"):
        cleanup_module.clear_processed_audio_cache(harness)

    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_runtime_cleanup_truncates_and_reopens_active_log_handler(tmp_path):
    harness, app_data_dir = _make_cleanup_harness(tmp_path)
    (app_data_dir / "pycache").mkdir()
    logs_dir = app_data_dir / "logs"
    logs_dir.mkdir()
    log_path = logs_dir / "wavehelm.log"
    log_path.write_text("before", encoding="utf-8")

    handler = logging.FileHandler(log_path, encoding="utf-8")
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    try:
        result = cleanup_module.clear_runtime_artifacts(harness)
        assert result["logs_removed"] == 1
        assert log_path.read_text(encoding="utf-8") == ""

        record = logging.LogRecord("test", logging.INFO, __file__, 0, "after", (), None)
        handler.emit(record)
        handler.flush()
        assert "after" in log_path.read_text(encoding="utf-8")
    finally:
        root_logger.removeHandler(handler)
        handler.close()


def test_cleanup_root_resolution_fails_closed_without_settings_file(tmp_path):
    harness, app_data_dir = _make_cleanup_harness(tmp_path)
    (app_data_dir / "settings.json").unlink()

    with pytest.raises(RuntimeError, match="canonical settings file is missing"):
        cleanup_module.get_app_data_dir(harness)


def test_nested_settings_are_isolated_from_callers(monkeypatch, tmp_path):
    manager = _make_manager(monkeypatch, tmp_path / "app_data")
    presets = {"rain": [0.2]}
    manager.set_setting("ambient_presets", presets)
    presets["rain"][0] = 0.9
    snapshot = manager.get_all_settings()
    assert snapshot["ambient_presets"] == {"rain": [0.2]}
    snapshot["ambient_presets"]["rain"][0] = 0.7
    assert manager.get_setting("ambient_presets") == {"rain": [0.2]}

def test_invalid_cache_marker_type_blocks_managed_writes(monkeypatch, tmp_path):
    app_data_dir = tmp_path / "app_data"
    marker = app_data_dir / "cache" / MANAGED_DIRECTORY_MARKER
    marker.mkdir(parents=True)
    manager = _make_manager(monkeypatch, app_data_dir)
    with pytest.raises(SettingsError, match="regular file"):
        manager.set_setting("volume", 25)
    assert not manager.SETTINGS_FILE.exists()


def test_symlinked_settings_file_is_repaired_and_rejected_for_cleanup(monkeypatch, tmp_path):
    app_data_dir = tmp_path / "app_data"
    external_file = tmp_path / "external" / "settings.json"
    app_data_dir.mkdir()
    external_file.parent.mkdir()
    external_file.write_text('{"language": "fr"}', encoding="utf-8")
    settings_link = app_data_dir / "settings.json"
    _create_symlink(settings_link, external_file, directory=False)
    manager = _make_manager(monkeypatch, app_data_dir)
    assert not manager.SETTINGS_FILE.is_symlink()
    assert json.loads(external_file.read_text(encoding="utf-8")) == {"language": "fr"}
    settings_link.unlink()
    _create_symlink(settings_link, external_file, directory=False)
    settings = DummySettingsManager(settings_link, app_data_dir / "cache")
    audio = DummyAudioEngine(app_data_dir / "processed_audio")
    harness = _bind_cleanup_methods(CleanupHarness(settings, audio))
    with pytest.raises(RuntimeError, match="cannot be a link"):
        cleanup_module.get_app_data_dir(harness)

def test_active_log_symlink_is_removed_without_truncating_external_file(tmp_path):
    harness, app_data_dir = _make_cleanup_harness(tmp_path)
    logs_dir = app_data_dir / "logs"
    logs_dir.mkdir()
    external_log = tmp_path / "external.log"
    external_log.write_text("keep", encoding="utf-8")
    log_link = logs_dir / "wavehelm.log"
    _create_symlink(log_link, external_log, directory=False)
    handler = logging.FileHandler(external_log, encoding="utf-8")
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    try:
        result = cleanup_module.clear_runtime_artifacts(harness)
        assert result["logs_removed"] == 1
        assert not log_link.exists()
        assert external_log.read_text(encoding="utf-8") == "keep"
    finally:
        root_logger.removeHandler(handler)
        handler.close()

def test_processed_audio_resolution_failure_blocks_cleanup(tmp_path):
    harness, _ = _make_cleanup_harness(tmp_path)
    harness.audio_engine._current_file = "current.wav"
    harness.audio_engine._playback_source_file = "processed.wav"
    def fail_resolution() -> Path:
        raise OSError("simulated resolution failure")
    harness.get_processed_audio_dir = fail_resolution  # type: ignore[method-assign]
    assert cleanup_module.is_processed_audio_cache_in_use(harness) is True
    with pytest.raises(RuntimeError, match="Stop the current audio playback"):
        cleanup_module.clear_processed_audio_cache(harness)
