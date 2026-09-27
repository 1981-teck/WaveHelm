from __future__ import annotations

import atexit
import copy
import logging
import os
import re
import sys
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path

from src.utils.app_paths import get_app_data_dir

MAX_LOG_SIZE = 512 * 1024
BACKUP_COUNT = 1
MAX_TOTAL_LOG_BYTES = 1024 * 1024
LOG_LEVEL = logging.DEBUG
ENV_LOG_LEVEL_VAR = "WAVEHELM_LOG_LEVEL"
NOISY_LOGGERS = ["urllib3", "PIL", "matplotlib", "asyncio"]

logger = logging.getLogger(__name__)

_ACTIVE_LOG_DIR: Path | None = None
_ACTIVE_LOG_FILE: Path | None = None
_CLEANUP_HOOK_REGISTERED = False


class SensitiveDataFilter(logging.Filter):
    """Redact declared key/value fields after resolving logging arguments.

    Record message/args are normalized together; caller-owned mappings are not
    edited. This is not detection of unlabelled credentials or every secret type.
    """

    SENSITIVE_KEYS = ["password", "api_key", "token", "secret"]
    MAX_TEXT_CHARS = 64 * 1024
    REDACTED = "***REDACTED***"
    FORMAT_ERROR = "[LOG_FORMAT_ERROR: message omitted; invalid logging arguments]"
    SIZE_ERROR = "[LOG_REDACTION_LIMIT: text omitted; size budget exceeded]"

    def __init__(self, name: str = "") -> None:
        super().__init__(name)
        keys = "|".join(re.escape(key) for key in self.SENSITIVE_KEYS)
        self._fields = re.compile(
            rf"(?<![\w])(?P<quote>['\"]?)(?:{keys})(?P=quote)"
            r"(?![\w])\s*[:=]\s*",
            re.IGNORECASE,
        )

    def _redact(self, text: str) -> str:
        if len(text) > self.MAX_TEXT_CHARS:
            return self.SIZE_ERROR
        pieces: list[str] = []
        position = 0
        while match := self._fields.search(text, position):
            start = match.end()
            end = _sensitive_value_end(text, start)
            quote = text[start:start + 1]
            replacement = self.REDACTED
            if quote in ("'", '"'):
                replacement = quote + replacement + quote
            pieces.extend((text[position:start], replacement))
            position = end
        pieces.append(text[position:])
        result = "".join(pieces)
        return result if len(result) <= self.MAX_TEXT_CHARS else self.SIZE_ERROR

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = self._redact(record.getMessage())
        except Exception:
            # Dynamic logging boundary: never echo failed templates/arguments or
            # recurse into logging. Process-control BaseExceptions still propagate.
            message = self.FORMAT_ERROR
        record.msg = message
        record.args = ()
        if "message" in record.__dict__:
            record.message = message
        return True


def _quoted_value_end(text: str, start: int) -> int:
    quote = text[start]
    index = start + 1
    while index < len(text):
        if text[index] == "\\":
            index += 2
        elif text[index] == quote:
            return index + 1
        else:
            index += 1
    return len(text)


def _sensitive_value_end(text: str, start: int) -> int:
    """Find a bounded value span; malformed quoted/compound tails are concealed."""
    if start == len(text):
        return start
    index = start
    if text[start] in ("'", '"'):
        index = _quoted_value_end(text, start)
    elif text[start] in "[{(":
        index = _compound_value_end(text, start)
    if index > start and (index == len(text) or text[index].isspace()):
        return index
    while index < len(text) and text[index] not in ",;&\r\n}])":
        index += 1
    return index


def _compound_value_end(text: str, start: int) -> int:
    closing = {"[": "]", "{": "}", "(": ")"}
    stack: list[str] = []
    index = start
    while index < len(text):
        char = text[index]
        if char in ("'", '"'):
            index = _quoted_value_end(text, index)
            continue
        if char in closing:
            stack.append(closing[char])
        elif char in "]})":
            if not stack or stack.pop() != char:
                return len(text)
            if not stack:
                return index + 1
        index += 1
    return len(text)


class _SensitiveFormatter(logging.Formatter):
    """Sanitize the final handler text, including exception and stack rendering."""

    def __init__(self, delegate: logging.Formatter) -> None:
        super().__init__()
        self._delegate = delegate
        self._redactor = SensitiveDataFilter()

    def _exception_text(self, record: logging.LogRecord) -> str:
        info = record.exc_info
        if info is None:
            return ""
        formatter = self._delegate.formatException
        if getattr(formatter, "__func__", None) is not logging.Formatter.formatException:
            return self._redactor._redact(formatter(info))
        # Keep real exception chains and frames, but isolate source-code snippets:
        # an unterminated assignment in one frame must not consume later frames.
        chunks: list[str] = []
        for chunk in traceback.TracebackException(*info).format():
            body = chunk.rstrip("\n")
            chunks.append(self._redactor._redact(body) + chunk[len(body):])
        return "".join(chunks).removesuffix("\n")

    def format(self, record: logging.LogRecord) -> str:
        try:
            # The delegate may cache exception text. Do not change the original
            # traceback, cache or caller metadata while formatting another handler.
            snapshot = copy.copy(record)
            if snapshot.exc_info and snapshot.exc_text is None:
                snapshot.exc_text = self._exception_text(snapshot)
            rendered = self._delegate.format(snapshot)
            return self._redactor._redact(rendered)
        except Exception:
            # Dynamic formatter boundary: a formatter failure must not route raw
            # arguments to Handler.handleError. No exception text is echoed.
            return "[LOG_FORMAT_ERROR: record omitted; formatter failed]"


def get_log_level() -> int:
    """Determina il livello di logging in base alle variabili d'ambiente."""
    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }

    env_level = os.getenv(ENV_LOG_LEVEL_VAR, "").upper()
    return level_map.get(env_level, LOG_LEVEL)


def _build_file_handler(log_file: Path, formatter: logging.Formatter) -> RotatingFileHandler:
    file_handler = RotatingFileHandler(
        filename=log_file,
        maxBytes=MAX_LOG_SIZE,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(_SensitiveFormatter(formatter))
    file_handler.addFilter(SensitiveDataFilter())
    return file_handler


def _resolve_file_handler(
    log_dir: Path,
    formatter: logging.Formatter,
) -> tuple[RotatingFileHandler, Path, str | None]:
    primary_log_file = log_dir / "wavehelm.log"

    try:
        return _build_file_handler(primary_log_file, formatter), primary_log_file, None
    except OSError as error:
        pid_fallback_log_file = log_dir / f"wavehelm.{os.getpid()}.log"
        try:
            return (
                _build_file_handler(pid_fallback_log_file, formatter),
                pid_fallback_log_file,
                (
                    f"Primary log file unavailable ({primary_log_file}): {error}. "
                    f"Using fallback log file {pid_fallback_log_file}."
                ),
            )
        except OSError as fallback_error:
            workspace_log_dir = Path.cwd() / "_wavehelm_runtime_logs"
            workspace_log_dir.mkdir(parents=True, exist_ok=True)
            workspace_log_file = workspace_log_dir / f"wavehelm.{os.getpid()}.log"
            return (
                _build_file_handler(workspace_log_file, formatter),
                workspace_log_file,
                (
                    f"Primary log file unavailable ({primary_log_file}): {error}. "
                    f"Secondary fallback unavailable ({pid_fallback_log_file}): {fallback_error}. "
                    f"Using workspace fallback log file {workspace_log_file}."
                ),
            )


def _iter_managed_log_files(log_dir: Path) -> list[Path]:
    files: list[Path] = []
    try:
        for item in log_dir.iterdir():
            if item.is_file() and item.name.startswith("wavehelm") and item.suffix == ".log":
                files.append(item)
    except OSError:
        return []
    files.sort(key=lambda path: (path.stat().st_mtime, path.name), reverse=True)
    return files


def _prune_logs_if_needed(log_dir: Path, active_log_file: Path | None = None) -> None:
    """Trim managed logs on shutdown when the directory exceeds the global cap.

    Edge cases handled:
    - The active log file may still be open and must never be deleted.
    - Fallback per-process logs can accumulate across launches and should be pruned first.
    - Files may disappear between enumeration and unlink and must not break shutdown.
    """
    files = _iter_managed_log_files(log_dir)
    if not files:
        return

    try:
        total_size = sum(path.stat().st_size for path in files)
    except OSError:
        return

    if total_size <= MAX_TOTAL_LOG_BYTES:
        return

    protected = active_log_file.resolve() if active_log_file is not None else None
    for path in sorted(files, key=lambda item: (item.stat().st_mtime, item.name)):
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if protected is not None and resolved == protected:
            continue
        try:
            file_size = path.stat().st_size
            path.unlink()
            total_size -= file_size
            if total_size <= MAX_TOTAL_LOG_BYTES:
                break
        except OSError:
            continue


def _register_cleanup_hook() -> None:
    global _CLEANUP_HOOK_REGISTERED
    if _CLEANUP_HOOK_REGISTERED:
        return
    atexit.register(_shutdown_log_cleanup)
    _CLEANUP_HOOK_REGISTERED = True


def _shutdown_log_cleanup() -> None:
    log_dir = _ACTIVE_LOG_DIR
    if log_dir is None:
        return

    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        try:
            flush = getattr(handler, "flush", None)
            if callable(flush):
                flush()
        except OSError:
            continue

    _prune_logs_if_needed(log_dir, _ACTIVE_LOG_FILE)


def setup_logging(base_dir: Path | None = None) -> Path:
    """
    Configura il sistema di logging per l'applicazione WaveHelm.

    Restituisce il file di log effettivamente in uso.
    """
    global _ACTIVE_LOG_DIR, _ACTIVE_LOG_FILE

    log_level = get_log_level()
    app_data_dir = Path(base_dir) if base_dir is not None else get_app_data_dir()
    log_dir = app_data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    file_formatter = logging.Formatter(
        fmt="%(asctime)s.%(msecs)03d [%(levelname)-8s] [%(name)s:%(lineno)d] - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler, log_file, fallback_notice = _resolve_file_handler(log_dir, file_formatter)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.addFilter(SensitiveDataFilter())
    console_handler.setFormatter(
        _SensitiveFormatter(logging.Formatter(
            fmt="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        ))
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
        try:
            handler.close()
        except OSError as error:
            logger.debug("Failed closing existing log handler %r: %s", handler, error, exc_info=True)

    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    for logger_name in NOISY_LOGGERS:
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    _ACTIVE_LOG_DIR = log_dir
    _ACTIVE_LOG_FILE = log_file
    _register_cleanup_hook()

    if fallback_notice:
        logging.warning(fallback_notice)
    logging.info("Sistema di logging configurato")
    logging.info("Livello di log: %s", logging.getLevelName(log_level))
    logging.info("File di log: %s", log_file)

    return log_file
