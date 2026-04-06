from __future__ import annotations

import logging
from pathlib import Path
import shutil

from src.utils import logging_config

RUNTIME_ROOT = Path(__file__).resolve().parent / "_bootstrap_runtime"


def _reset_root_logger() -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except OSError:
            pass


def _make_runtime_dir(name: str) -> Path:
    path = RUNTIME_ROOT / name
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_get_log_level_uses_wavehelm_env_var(monkeypatch):
    monkeypatch.setenv("WAVEHELM_LOG_LEVEL", "error")

    assert logging_config.get_log_level() == logging.ERROR


def test_setup_logging_uses_primary_wavehelm_log():
    runtime_dir = _make_runtime_dir("logging_primary")
    try:
        log_file = logging_config.setup_logging(runtime_dir)

        assert log_file == runtime_dir / "logs" / "wavehelm.log"
        assert log_file.exists()
    finally:
        _reset_root_logger()


def test_setup_logging_falls_back_to_pid_log_when_primary_unavailable(monkeypatch):
    runtime_dir = _make_runtime_dir("logging_fallback")
    created_paths: list[Path] = []

    class DummyRotatingFileHandler(logging.Handler):
        def __init__(self, filename, maxBytes, backupCount, encoding):
            path = Path(filename)
            if path.name == "wavehelm.log":
                raise OSError("locked")
            super().__init__()
            self.baseFilename = str(path)
            created_paths.append(path)

        def emit(self, record):
            return None

    monkeypatch.setattr(logging_config, "RotatingFileHandler", DummyRotatingFileHandler)

    try:
        log_file = logging_config.setup_logging(runtime_dir)

        assert log_file.parent == runtime_dir / "logs"
        assert log_file.name.startswith("wavehelm.")
        assert log_file.suffix == ".log"
        assert created_paths == [log_file]
    finally:
        _reset_root_logger()


def test_shutdown_log_cleanup_prunes_old_fallback_logs_when_dir_exceeds_cap():
    runtime_dir = _make_runtime_dir("logging_cleanup")
    logs_dir = runtime_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    active_log = logs_dir / "wavehelm.log"
    active_log.write_text("a" * 400, encoding="utf-8")
    old_a = logs_dir / "wavehelm.111.log"
    old_a.write_text("b" * 400, encoding="utf-8")
    old_b = logs_dir / "wavehelm.222.log"
    old_b.write_text("c" * 400, encoding="utf-8")

    original_cap = logging_config.MAX_TOTAL_LOG_BYTES
    logging_config.MAX_TOTAL_LOG_BYTES = 700
    try:
        logging_config._prune_logs_if_needed(logs_dir, active_log)

        assert active_log.exists()
        remaining = sorted(path.name for path in logs_dir.iterdir())
        assert remaining == ["wavehelm.log"]
    finally:
        logging_config.MAX_TOTAL_LOG_BYTES = original_cap


def test_setup_logging_uses_reduced_rotation_defaults():
    assert logging_config.MAX_LOG_SIZE == 512 * 1024
    assert logging_config.BACKUP_COUNT == 1
    assert logging_config.MAX_TOTAL_LOG_BYTES == 1024 * 1024
