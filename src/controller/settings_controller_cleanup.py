from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Protocol

from src.model.setting_manager import MANAGED_DIRECTORY_MARKER
from src.utils.legal_notices import ensure_third_party_notices_dir

logger = logging.getLogger("src.controller.settings_controller")

"""Fail-closed cleanup helpers for application-managed directories.

Edge cases:
- A corrupted or imported ``cache_dir`` may point outside the application data root.
- A symlink, junction, or path traversal may escape the managed cleanup directory.
- A missing ownership marker may make an otherwise canonical cache path ambiguous.
- Active log files may still be owned by live logging handlers during cleanup.

Mitigations:
- Derive the trusted root only from the canonical ``settings.json`` location.
- Require cleanup targets to be exact, immediate children with fixed names.
- Re-resolve every target immediately before deletion and preserve the cache marker.
- Truncate active logs through their handlers and report deterministic item counts.
"""

_MANAGED_DIRECTORY_NAMES = frozenset({"cache", "pycache", "processed_audio"})


class SettingsManagerLike(Protocol):
    SETTINGS_FILE: Path

    def get_setting(self, key: str, default: object = None) -> object:
        ...


class CleanupController(Protocol):
    settings_manager: SettingsManagerLike
    audio_engine: object
    CLEANUP_EXCEPTIONS: tuple[type[Exception], ...]
    LOG_HANDLER_EXCEPTIONS: tuple[type[Exception], ...]

    def get_app_data_dir(self) -> Path:
        ...

    def get_processed_audio_dir(self) -> Path:
        ...

    def get_logs_dir(self) -> Path:
        ...

    def _clear_directory_contents(self, directory: Path) -> int:
        ...

    def _truncate_active_log_file(self, handler: object, path: Path) -> int:
        ...

    def _get_localized_text(self, translation_key: str, **kwargs: object) -> str:
        ...

    def is_processed_audio_cache_in_use(self) -> bool:
        ...

    def get_processed_audio_cleanup_block_message(self) -> str:
        ...


def get_app_data_dir(self: CleanupController) -> Path:
    settings_file = getattr(self.settings_manager, "SETTINGS_FILE", None)
    if not isinstance(settings_file, (str, os.PathLike)):
        raise RuntimeError("The settings file path is unavailable.")
    try:
        lexical_file = _absolute_without_symlink_resolution(Path(settings_file))
        resolved_file = lexical_file.resolve(strict=False)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise RuntimeError("The settings file path cannot be resolved.") from error
    if lexical_file.is_symlink():
        raise RuntimeError("The canonical settings file cannot be a link.")
    if resolved_file != lexical_file:
        raise RuntimeError("The canonical settings file escapes application data.")
    if resolved_file.name.lower() != "settings.json" or not resolved_file.is_file():
        raise RuntimeError("The canonical settings file is missing.")
    return resolved_file.parent


def get_processed_audio_dir(self: CleanupController) -> Path:
    directory = getattr(self.audio_engine, "_processed_audio_dir", None)
    if directory is None:
        return self.get_app_data_dir() / "processed_audio"
    try:
        return Path(directory).resolve(strict=False)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise RuntimeError("The processed-audio directory is invalid.") from error


def get_logs_dir(self: CleanupController) -> Path:
    return self.get_app_data_dir() / "logs"


def get_third_party_notices_dir(self: CleanupController) -> Path:
    return ensure_third_party_notices_dir(self.get_app_data_dir())


def _absolute_without_symlink_resolution(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _is_directory_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    junction_check = getattr(path, "is_junction", None)
    return bool(callable(junction_check) and junction_check())


def _is_unsafe_cleanup_root(root: Path) -> bool:
    unsafe_roots = {
        Path(root.anchor).resolve(strict=False),
        Path.home().resolve(strict=False),
        Path.cwd().resolve(strict=False),
    }
    return root in unsafe_roots


def _validate_cleanup_target(
    self: CleanupController,
    directory: Path,
    *,
    expected_name: str,
    require_marker: bool,
) -> Path:
    root = self.get_app_data_dir().resolve(strict=False)
    if _is_unsafe_cleanup_root(root):
        raise RuntimeError(f"Refusing unsafe application data root: {root}")
    lexical_target = _absolute_without_symlink_resolution(Path(directory))
    expected_target = root / expected_name
    if lexical_target != expected_target:
        raise RuntimeError(f"Refusing unmanaged cleanup target: {lexical_target}")

    resolved_target = lexical_target.resolve(strict=False)
    if resolved_target != expected_target or resolved_target.parent != root:
        raise RuntimeError(f"Refusing cleanup path escape: {lexical_target}")
    if resolved_target == root:
        raise RuntimeError("Refusing to clean the application data root.")
    marker = resolved_target / MANAGED_DIRECTORY_MARKER
    if require_marker and (marker.is_symlink() or not marker.is_file()):
        raise RuntimeError("The managed cache marker is missing or invalid.")
    return resolved_target


def _delete_managed_item(directory: Path, item: Path) -> bool:
    if item.name == MANAGED_DIRECTORY_MARKER:
        return False
    if _is_directory_link(item):
        if item.is_symlink():
            item.unlink()
        else:
            item.rmdir()
        return True

    resolved_item = item.resolve(strict=False)
    if resolved_item.parent != directory and directory not in resolved_item.parents:
        raise RuntimeError(f"Refusing child cleanup path escape: {item}")
    if item.is_dir():
        shutil.rmtree(item, ignore_errors=False)
    else:
        item.unlink()
    return True


def _raise_cleanup_failures(
    directory: Path,
    failures: list[tuple[Path, str]],
) -> None:
    if not failures:
        return
    failed_names = ", ".join(item.name for item, _ in failures[:5])
    raise RuntimeError(
        f"Cleanup incomplete for {directory}; "
        f"{len(failures)} item(s) failed: {failed_names}"
    )


def _clear_directory_contents(self: CleanupController, directory: Path) -> int:
    directory_name = Path(directory).name
    if directory_name not in _MANAGED_DIRECTORY_NAMES:
        raise RuntimeError(f"Refusing unmanaged cleanup target: {directory}")
    target = _validate_cleanup_target(
        self,
        Path(directory),
        expected_name=directory_name,
        require_marker=directory_name == "cache",
    )
    target.mkdir(parents=True, exist_ok=True)
    target = _validate_cleanup_target(
        self,
        target,
        expected_name=directory_name,
        require_marker=directory_name == "cache",
    )

    removed = 0
    failures: list[tuple[Path, str]] = []
    for item in tuple(target.iterdir()):
        try:
            removed += int(_delete_managed_item(target, item))
        except self.CLEANUP_EXCEPTIONS as error:
            logger.error("Cleanup failed for %s: %s", item, error, exc_info=True)
            failures.append((item, str(error)))
    _raise_cleanup_failures(target, failures)
    return removed


def _truncate_active_log_file(
    self: CleanupController,
    handler: object,
    path: Path,
) -> int:
    acquire = getattr(handler, "acquire", None)
    release = getattr(handler, "release", None)
    acquired = False
    operation_error: Exception | None = None
    release_error: Exception | None = None
    try:
        if callable(acquire):
            acquire()
            acquired = True
        flush = getattr(handler, "flush", None)
        if callable(flush):
            flush()
        close = getattr(handler, "close", None)
        if callable(close):
            close()
        path.write_text("", encoding="utf-8")
        opener = getattr(handler, "_open", None)
        if callable(opener):
            setattr(handler, "stream", opener())
    except self.LOG_HANDLER_EXCEPTIONS as error:
        operation_error = error
    finally:
        if acquired and callable(release):
            try:
                release()
            except self.LOG_HANDLER_EXCEPTIONS as error:
                release_error = error

    if operation_error is not None:
        raise RuntimeError(f"Unable to truncate active log file: {path}") from operation_error
    if release_error is not None:
        raise RuntimeError(f"Unable to release active log handler: {path}") from release_error
    return 1


def _resolve_active_handlers() -> dict[Path, object]:
    active_handlers: dict[Path, object] = {}
    for handler in logging.getLogger().handlers:
        filename = getattr(handler, "baseFilename", None)
        if not filename:
            continue
        try:
            active_handlers[Path(str(filename)).resolve(strict=False)] = handler
        except (OSError, RuntimeError, TypeError, ValueError):
            continue
    return active_handlers


def _clear_logs_dir(self: CleanupController, logs_dir: Path) -> int:
    target = _validate_cleanup_target(
        self,
        Path(logs_dir),
        expected_name="logs",
        require_marker=False,
    )
    target.mkdir(parents=True, exist_ok=True)
    target = _validate_cleanup_target(
        self,
        target,
        expected_name="logs",
        require_marker=False,
    )
    active_handlers = _resolve_active_handlers()

    removed = 0
    failures: list[tuple[Path, str]] = []
    for item in tuple(target.iterdir()):
        try:
            if _is_directory_link(item):
                removed += int(_delete_managed_item(target, item))
                continue
            resolved_item = item.resolve(strict=False)
            if resolved_item.parent != target and target not in resolved_item.parents:
                raise RuntimeError(f"Refusing child cleanup path escape: {item}")
            handler = active_handlers.get(resolved_item)
            if handler is not None and item.is_file():
                removed += self._truncate_active_log_file(handler, item)
            else:
                removed += int(_delete_managed_item(target, item))
        except self.CLEANUP_EXCEPTIONS as error:
            logger.error("Log cleanup failed for %s: %s", item, error, exc_info=True)
            failures.append((item, str(error)))
    _raise_cleanup_failures(target, failures)
    return removed


def is_processed_audio_cache_in_use(self: CleanupController) -> bool:
    current_file = getattr(self.audio_engine, "_current_file", None)
    playback_source = getattr(self.audio_engine, "_playback_source_file", None)
    if not current_file or not playback_source:
        return False
    try:
        processed_dir = self.get_processed_audio_dir()
        playback_path = Path(str(playback_source)).resolve(strict=False)
    except (OSError, RuntimeError, TypeError, ValueError):
        return True
    return playback_path == processed_dir or processed_dir in playback_path.parents


def get_processed_audio_cleanup_block_message(self: CleanupController) -> str:
    if not self.is_processed_audio_cache_in_use():
        return ""
    return self._get_localized_text(
        "settings_processed_audio_busy",
        default="Stop the current audio playback before clearing processed audio cache.",
    )


def clear_processed_audio_cache(self: CleanupController) -> int:
    message = self.get_processed_audio_cleanup_block_message()
    if message:
        raise RuntimeError(message)
    processed_dir = _validate_cleanup_target(
        self,
        self.get_processed_audio_dir(),
        expected_name="processed_audio",
        require_marker=False,
    )
    return self._clear_directory_contents(processed_dir)


def _resolve_runtime_cache_dir(
    self: CleanupController,
    app_data_dir: Path,
) -> Path:
    cache_value = self.settings_manager.get_setting(
        "cache_dir",
        str(app_data_dir / "cache"),
    )
    try:
        cache_path = Path(str(cache_value))
    except (TypeError, ValueError) as error:
        raise RuntimeError("The configured cache directory is invalid.") from error
    return _validate_cleanup_target(
        self,
        cache_path,
        expected_name="cache",
        require_marker=True,
    )


def clear_runtime_artifacts(self: CleanupController) -> dict[str, int]:
    app_data_dir = self.get_app_data_dir()
    cache_dir = _resolve_runtime_cache_dir(self, app_data_dir)
    pycache_dir = _validate_cleanup_target(
        self,
        app_data_dir / "pycache",
        expected_name="pycache",
        require_marker=False,
    )
    logs_dir = _validate_cleanup_target(
        self,
        self.get_logs_dir(),
        expected_name="logs",
        require_marker=False,
    )

    cache_removed = self._clear_directory_contents(cache_dir)
    pycache_removed = self._clear_directory_contents(pycache_dir)
    logs_removed = _clear_logs_dir(self, logs_dir)
    total_removed = cache_removed + pycache_removed + logs_removed
    return {
        "processed_audio_removed": 0,
        "cache_removed": cache_removed + pycache_removed,
        "logs_removed": logs_removed,
        "total_removed": total_removed,
    }
