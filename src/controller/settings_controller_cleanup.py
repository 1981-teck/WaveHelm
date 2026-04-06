from __future__ import annotations

import atexit
import logging
import shutil
from pathlib import Path

from src.utils.legal_notices import ensure_third_party_notices_dir
from typing import Any, Dict

logger = logging.getLogger("src.controller.settings_controller")

_PENDING_RUNTIME_CLEANUP: dict[str, Any] | None = None
_RUNTIME_CLEANUP_REGISTERED = False


"""Cleanup helpers extracted from settings_controller.

Edge cases handled by this module:
- SETTINGS_FILE or cache_dir may be missing, malformed, or not resolvable at runtime.
- Active log files may still be owned by live logging handlers during cleanup.
- Cleanup targets may overlap or point to the currently used processed-audio cache.

Mitigations:
- Resolve paths defensively and fall back to deterministic application-local defaults.
- Truncate active log files through their handlers instead of unlinking live files.
- Refuse destructive cleanup when processed audio is still backing current playback.
"""


def get_app_data_dir(self) -> Path:
    settings_file = getattr(self.settings_manager, "SETTINGS_FILE", None)
    if settings_file is not None:
        try:
            return Path(settings_file).resolve().parent
        except self.CLEANUP_EXCEPTIONS as error:
            logger.debug(
                "Failed to resolve app data dir from SETTINGS_FILE: %s",
                error,
                exc_info=True,
            )

    cache_dir = self.settings_manager.get_setting("cache_dir", None)
    if cache_dir:
        try:
            return Path(str(cache_dir)).resolve().parent
        except self.CLEANUP_EXCEPTIONS as error:
            logger.debug(
                "Failed to resolve app data dir from cache_dir: %s",
                error,
                exc_info=True,
            )

    return Path.cwd()


def get_processed_audio_dir(self) -> Path:
    directory = getattr(self.audio_engine, "_processed_audio_dir", None)
    if directory is not None:
        try:
            return Path(directory).resolve()
        except self.CLEANUP_EXCEPTIONS as error:
            logger.debug(
                "Failed to resolve processed audio dir from audio engine: %s",
                error,
                exc_info=True,
            )
    return (self.get_app_data_dir() / "processed_audio").resolve()


def get_logs_dir(self) -> Path:
    return (self.get_app_data_dir() / "logs").resolve()


def get_third_party_notices_dir(self) -> Path:
    return ensure_third_party_notices_dir(self.get_app_data_dir())


def _clear_directory_contents(self, directory: Path) -> int:
    removed = 0
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except self.CLEANUP_EXCEPTIONS:
        return 0

    for item in directory.iterdir():
        try:
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=False)
            else:
                item.unlink()
            removed += 1
        except self.CLEANUP_EXCEPTIONS:
            logger.debug("Skipping cleanup of %s", item, exc_info=True)
    return removed


def _truncate_active_log_file(self, handler: Any, path: Path) -> int:
    acquire = getattr(handler, "acquire", None)
    release = getattr(handler, "release", None)
    try:
        if callable(acquire):
            acquire()
        flush = getattr(handler, "flush", None)
        if callable(flush):
            flush()
        close = getattr(handler, "close", None)
        if callable(close):
            close()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        opener = getattr(handler, "_open", None)
        if callable(opener):
            handler.stream = opener()
        return 1
    except self.LOG_HANDLER_EXCEPTIONS:
        logger.debug("Unable to truncate active log file %s", path, exc_info=True)
        return 0
    finally:
        try:
            if callable(release):
                release()
        except self.LOG_HANDLER_EXCEPTIONS:
            logger.debug("Unable to release log handler for %s", path, exc_info=True)


def _resolve_active_handlers() -> dict[Path, Any]:
    active_handlers: dict[Path, Any] = {}
    for handler in logging.getLogger().handlers:
        filename = getattr(handler, "baseFilename", None)
        if not filename:
            continue
        try:
            active_handlers[Path(str(filename)).resolve()] = handler
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            continue
    return active_handlers


def _clear_logs_dir(self, logs_dir: Path) -> int:
    removed = 0
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
    except self.CLEANUP_EXCEPTIONS:
        return 0

    active_handlers = _resolve_active_handlers()
    for item in logs_dir.iterdir():
        try:
            resolved = item.resolve()
        except self.CLEANUP_EXCEPTIONS:
            resolved = item

        handler = active_handlers.get(resolved)
        if handler is not None and item.is_file():
            removed += self._truncate_active_log_file(handler, item)
            continue

        try:
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=False)
            else:
                item.unlink()
            removed += 1
        except self.CLEANUP_EXCEPTIONS:
            logger.debug("Skipping log cleanup of %s", item, exc_info=True)
    return removed


def is_processed_audio_cache_in_use(self) -> bool:
    current_file = getattr(self.audio_engine, "_current_file", None)
    playback_source = getattr(self.audio_engine, "_playback_source_file", None)
    if not current_file or not playback_source:
        return False

    try:
        processed_dir = self.get_processed_audio_dir()
        playback_path = Path(str(playback_source)).resolve()
    except self.CLEANUP_EXCEPTIONS:
        return False

    return playback_path == processed_dir or processed_dir in playback_path.parents


def get_processed_audio_cleanup_block_message(self) -> str:
    if not self.is_processed_audio_cache_in_use():
        return ""
    return self._get_localized_text(
        "settings_processed_audio_busy",
        default="Stop the current audio playback before clearing processed audio cache.",
    )


def clear_processed_audio_cache(self) -> int:
    message = self.get_processed_audio_cleanup_block_message()
    if message:
        raise RuntimeError(message)
    return self._clear_directory_contents(self.get_processed_audio_dir())


def _resolve_runtime_cache_dir(self, app_data_dir: Path) -> Path:
    cache_dir_value = self.settings_manager.get_setting(
        "cache_dir",
        str(app_data_dir / "cache"),
    )
    try:
        return Path(str(cache_dir_value)).resolve()
    except self.CLEANUP_EXCEPTIONS:
        return (app_data_dir / "cache").resolve()


def clear_runtime_artifacts(self) -> Dict[str, int]:
    app_data_dir = self.get_app_data_dir()
    logs_dir = self.get_logs_dir()
    pycache_dir = (app_data_dir / "pycache").resolve()
    cache_dir = _resolve_runtime_cache_dir(self, app_data_dir)

    cache_dirs: list[Path] = []
    seen_dirs: set[Path] = set()
    for directory in (cache_dir, pycache_dir):
        if directory in seen_dirs:
            continue
        cache_dirs.append(directory)
        seen_dirs.add(directory)

    _schedule_runtime_cleanup(
        self,
        cache_dirs=tuple(cache_dirs),
        logs_dir=logs_dir,
    )
    return {
        "processed_audio_removed": 0,
        "cache_removed": 0,
        "logs_removed": 0,
        "total_removed": 0,
    }


def _register_runtime_cleanup_hook() -> None:
    global _RUNTIME_CLEANUP_REGISTERED
    if _RUNTIME_CLEANUP_REGISTERED:
        return
    atexit.register(_run_pending_runtime_cleanup)
    _RUNTIME_CLEANUP_REGISTERED = True


def _schedule_runtime_cleanup(
    self,
    *,
    cache_dirs: tuple[Path, ...],
    logs_dir: Path,
) -> None:
    global _PENDING_RUNTIME_CLEANUP
    _register_runtime_cleanup_hook()
    _PENDING_RUNTIME_CLEANUP = {
        "controller": self,
        "cache_dirs": cache_dirs,
        "logs_dir": logs_dir,
    }


def _run_pending_runtime_cleanup() -> None:
    global _PENDING_RUNTIME_CLEANUP
    request = _PENDING_RUNTIME_CLEANUP
    _PENDING_RUNTIME_CLEANUP = None
    if not isinstance(request, dict):
        return

    controller = request.get("controller")
    if controller is None:
        return

    cache_dirs = request.get("cache_dirs", ())
    logs_dir = request.get("logs_dir")
    removed_total = 0
    seen_dirs: set[Path] = set()

    for directory in cache_dirs:
        if not isinstance(directory, Path) or directory in seen_dirs:
            continue
        try:
            removed_total += _clear_directory_contents(controller, directory)
            seen_dirs.add(directory)
        except controller.CLEANUP_EXCEPTIONS:
            logger.debug(
                "Deferred cache cleanup failed for %s",
                directory,
                exc_info=True,
            )

    if isinstance(logs_dir, Path):
        try:
            removed_total += _clear_logs_dir(controller, logs_dir)
        except controller.CLEANUP_EXCEPTIONS:
            logger.debug(
                "Deferred log cleanup failed for %s",
                logs_dir,
                exc_info=True,
            )

