"""Real file/console redaction checks; no user files or native services are used."""
from __future__ import annotations

import io
import logging
import sys
from pathlib import Path

import pytest

from src.utils import logging_config as lc

MARKER = "WH_SYNTHETIC_08C_VALUE"


@pytest.fixture
def protected_setup(tmp_path: Path, monkeypatch):
    root = logging.getLogger()
    previous_handlers, previous_level = list(root.handlers), root.level
    old_active = (lc._ACTIVE_LOG_DIR, lc._ACTIVE_LOG_FILE)
    noisy = {name: logging.getLogger(name).level for name in lc.NOISY_LOGGERS}
    for handler in previous_handlers:
        root.removeHandler(handler)
    stream = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(lc, "_register_cleanup_hook", lambda: None)
    monkeypatch.setenv(lc.ENV_LOG_LEVEL_VAR, "DEBUG")
    try:
        log_file = lc.setup_logging(tmp_path)
        yield root, stream, log_file
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
            handler.close()
        root.setLevel(previous_level)
        for handler in previous_handlers:
            root.addHandler(handler)
        for name, level in noisy.items():
            logging.getLogger(name).setLevel(level)
        lc._ACTIVE_LOG_DIR, lc._ACTIVE_LOG_FILE = old_active


def _flush(root: logging.Logger) -> None:
    for handler in root.handlers:
        handler.flush()


@pytest.mark.parametrize("reverse", [False, True])
def test_setup_protects_both_handlers_in_either_order(protected_setup, reverse: bool) -> None:
    root, stream, path = protected_setup
    if reverse:
        root.handlers.reverse()
    log = logging.getLogger("08C.child")
    log.info("token=%(token)s, count=%(count)d", {"token": MARKER, "count": 8})
    _flush(root)
    for text in (stream.getvalue(), path.read_text(encoding="utf-8")):
        assert MARKER not in text and "token=***REDACTED***, count=8" in text


def test_console_alone_is_protected(protected_setup) -> None:
    root, stream, _ = protected_setup
    file_handler = next(h for h in root.handlers if isinstance(h, lc.RotatingFileHandler))
    root.removeHandler(file_handler)
    file_handler.close()
    logging.info("PASSWORD=%s", MARKER)
    _flush(root)
    assert MARKER not in stream.getvalue()
    assert "PASSWORD=***REDACTED***" in stream.getvalue()


def test_levels_and_ordinary_format_are_preserved(protected_setup) -> None:
    root, stream, path = protected_setup
    logging.debug("debug detail count=%d", 9)
    logging.info("Loaded %d tracks", 3)
    _flush(root)
    disk = path.read_text(encoding="utf-8")
    assert "debug detail count=9" in disk and "debug detail" not in stream.getvalue()
    assert "Loaded 3 tracks" in disk and "Loaded 3 tracks" in stream.getvalue()
    assert root.level == logging.DEBUG


def test_rotation_keeps_both_files_redacted(protected_setup) -> None:
    root, stream, path = protected_setup
    handler = next(h for h in root.handlers if isinstance(h, lc.RotatingFileHandler))
    handler.maxBytes = 180
    for number in range(8):
        logging.info("event=%d, token=%s", number, MARKER)
    _flush(root)
    assert Path(str(path) + ".1").exists()
    for log in path.parent.iterdir():
        assert MARKER not in log.read_text(encoding="utf-8")
    assert MARKER not in stream.getvalue()


@pytest.mark.parametrize("raise_exceptions", [True, False])
def test_mapping_and_malformed_records_do_not_leak_via_handler_error(
    protected_setup, monkeypatch, capsys, raise_exceptions: bool
) -> None:
    root, stream, path = protected_setup
    monkeypatch.setattr(logging, "raiseExceptions", raise_exceptions)
    logging.info("token=%(token)s", {"token": MARKER})
    logging.info("token=%d", MARKER)
    _flush(root)
    text = stream.getvalue() + path.read_text(encoding="utf-8")
    assert MARKER not in text
    assert "LOG_FORMAT_ERROR" in text and "***REDACTED***" in text
    assert capsys.readouterr().err == ""


def test_exception_and_stack_text_are_sanitized_but_objects_are_unchanged(tmp_path: Path) -> None:
    try:
        raise ValueError("token=" + MARKER)
    except ValueError:
        info = sys.exc_info()
    record = logging.LogRecord("review", logging.ERROR, __file__, 1, "Operation failed", (), info)
    record.stack_info = "Stack details\npassword=" + MARKER
    path = tmp_path / "exception.log"
    handler = lc._build_file_handler(path, logging.Formatter("%(levelname)s %(message)s"))
    try:
        handler.handle(record)
        handler.flush()
    finally:
        handler.close()
    text = path.read_text(encoding="utf-8")
    assert MARKER not in text and "ValueError" in text and "Traceback" in text
    assert "ERROR Operation failed" in text and "Stack details" in text
    assert record.exc_info is info and record.exc_text is None
    assert str(info[1]) == "token=" + MARKER


def test_preexisting_exception_cache_is_not_modified_or_emitted_raw(tmp_path: Path) -> None:
    record = logging.LogRecord("review", logging.ERROR, __file__, 1, "Failed", (), None)
    cached = "ValueError: secret=" + MARKER
    record.exc_text = cached
    path = tmp_path / "cache.log"
    handler = lc._build_file_handler(path, logging.Formatter("%(message)s"))
    try:
        handler.handle(record)
    finally:
        handler.close()
    assert record.exc_text == cached
    assert MARKER not in path.read_text(encoding="utf-8")


def test_delegate_formatter_failure_is_explicit_without_raw_echo(tmp_path: Path, capsys) -> None:
    class BrokenFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            raise ValueError("token=" + MARKER)
    path = tmp_path / "broken.log"
    handler = lc._build_file_handler(path, BrokenFormatter())
    try:
        handler.handle(logging.LogRecord("review", logging.INFO, __file__, 1,
                                         "token=%s", (MARKER,), None))
    finally:
        handler.close()
    text = path.read_text(encoding="utf-8")
    assert "LOG_FORMAT_ERROR" in text and "formatter failed" in text
    assert MARKER not in text and capsys.readouterr().err == ""


def test_formatter_added_fields_are_sanitized(tmp_path: Path) -> None:
    path = tmp_path / "extra.log"
    handler = lc._build_file_handler(path, logging.Formatter("%(message)s, token=%(token)s"))
    record = logging.LogRecord("review", logging.INFO, __file__, 1, "Request failed", (), None)
    record.token = MARKER
    try:
        handler.handle(record)
    finally:
        handler.close()
    assert MARKER not in path.read_text(encoding="utf-8")
    assert record.token == MARKER


def test_workspace_fallback_retains_redaction(tmp_path: Path, monkeypatch) -> None:
    original = lc._build_file_handler
    primary = tmp_path / "primary"
    primary.mkdir()
    monkeypatch.chdir(tmp_path)
    def build(path: Path, formatter: logging.Formatter):
        if path.parent == primary:
            raise PermissionError("token=" + MARKER)
        return original(path, formatter)
    monkeypatch.setattr(lc, "_build_file_handler", build)
    handler, path, notice = lc._resolve_file_handler(primary, logging.Formatter("%(message)s"))
    try:
        assert path.parent == tmp_path / "_wavehelm_runtime_logs"
        assert notice is not None
        handler.handle(logging.LogRecord("review", logging.WARNING, __file__, 1, notice, (), None))
    finally:
        handler.close()
    text = path.read_text(encoding="utf-8")
    assert MARKER not in text and "fallback" in text.lower()


def test_shutdown_flushes_without_changing_active_file(protected_setup) -> None:
    root, _, path = protected_setup
    logging.warning("secret=%s", MARKER)
    handlers_before = tuple(root.handlers)
    lc._shutdown_log_cleanup()
    assert tuple(root.handlers) == handlers_before
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert MARKER not in text and "***REDACTED***" in text


@pytest.mark.parametrize("kind", ["plain", "chain", "group", "trailing-newline"])
def test_ordinary_traceback_rendering_matches_standard_formatter(tmp_path: Path, kind: str) -> None:
    try:
        if kind == "chain":
            try:
                raise ValueError("ordinary root cause")
            except ValueError as cause:
                raise RuntimeError("ordinary outer error") from cause
        if kind == "group":
            raise ExceptionGroup("ordinary group", [ValueError("one"), TypeError("two")])
        raise RuntimeError("ordinary error\n\n" if kind == "trailing-newline" else "ordinary error")
    except Exception:
        info = sys.exc_info()
    record = logging.LogRecord("review", logging.ERROR, __file__, 1, "Failed %d", (2,), info)
    formatter = logging.Formatter("%(levelname)s %(message)s")
    expected = formatter.format(record)
    record.exc_text = None
    handler = lc._build_file_handler(tmp_path / "normal.log", formatter)
    try:
        assert handler.format(record) == expected
        assert record.exc_text is None
    finally:
        handler.close()


def test_multiline_exception_value_and_both_handlers_are_protected(protected_setup) -> None:
    root, stream, path = protected_setup
    try:
        raise ValueError('token="' + MARKER + '\nSECOND_SYNTHETIC_SECRET"')
    except ValueError:
        logging.exception("Operation failed")
    _flush(root)
    for text in (stream.getvalue(), path.read_text(encoding="utf-8")):
        assert MARKER not in text and "SECOND_SYNTHETIC_SECRET" not in text
        assert "ValueError" in text and "Operation failed" in text


def test_custom_exception_formatter_contract_is_preserved(tmp_path: Path) -> None:
    class Custom(logging.Formatter):
        def formatException(self, ei) -> str:
            return "CustomError: token=" + MARKER + ", operation=write"
    try:
        raise ValueError("ordinary cause")
    except ValueError:
        info = sys.exc_info()
    handler = lc._build_file_handler(tmp_path / "custom.log", Custom("%(message)s"))
    try:
        text = handler.format(logging.LogRecord("review", logging.ERROR, __file__,
                                               1, "Failed", (), info))
        assert "CustomError" in text and "operation=write" in text and MARKER not in text
    finally:
        handler.close()


def test_formatter_process_control_exception_is_not_swallowed(tmp_path: Path) -> None:
    class Interrupted(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            raise KeyboardInterrupt()
    handler = lc._build_file_handler(tmp_path / "interrupted.log", Interrupted())
    try:
        with pytest.raises(KeyboardInterrupt):
            handler.format(logging.LogRecord("review", logging.INFO, __file__, 1, "OK", (), None))
    finally:
        handler.close()


def test_concurrent_records_keep_argument_objects_and_outputs_separate(protected_setup) -> None:
    from concurrent.futures import ThreadPoolExecutor
    root, stream, path = protected_setup
    payload = {"token": MARKER}
    def write(number: int) -> None:
        logging.info("record=%d, token=%s", number, payload["token"])
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(40)))
    _flush(root)
    assert payload == {"token": MARKER}
    for text in (stream.getvalue(), path.read_text(encoding="utf-8")):
        assert MARKER not in text
        assert text.count("token=***REDACTED***") == 40
