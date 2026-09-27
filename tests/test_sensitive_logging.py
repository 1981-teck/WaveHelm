"""Synthetic-only regression for the declared log key/value redaction contract."""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from src.utils.logging_config import SensitiveDataFilter, _build_file_handler

MARKER = "WH_SYNTHETIC_08C_VALUE"
MASK = "***REDACTED***"


def _record(message: object, args: tuple[object, ...] = ()) -> logging.LogRecord:
    return logging.LogRecord("review", logging.INFO, __file__, 17, message, args, None)


def _filtered(message: object, args: tuple[object, ...] = ()) -> str:
    record = _record(message, args)
    assert SensitiveDataFilter().filter(record) is True
    return logging.Formatter("%(message)s").format(record)


@pytest.mark.parametrize("key", ["password", "PASSWORD", "api_key", "API_KEY",
                                  "token", "ToKeN", "secret", "SECRET"])
@pytest.mark.parametrize("style", ["equals", "colon", "json", "repr"])
def test_declared_value_is_redacted_and_key_preserved(key: str, style: str) -> None:
    formats = {"equals": f"event {key}={MARKER}, count=3",
               "colon": f"event {key}: {MARKER}; count=3",
               "json": f'{{"{key}": "{MARKER}", "count": 3}}',
               "repr": f"{{'{key}': '{MARKER}', 'count': 3}}"}
    result = _filtered(formats[style])
    assert MARKER not in result
    assert MASK in result and key in result and "count" in result and "3" in result


@pytest.mark.parametrize("value", [
    '"WH_SYNTHETIC_08C_VALUE with space"',
    "'WH_SYNTHETIC_08C_VALUE, ; & trailing'",
    '"WH_SYNTHETIC_08C_VALUE\\\"escaped"',
    '"WH_SYNTHETIC_08C_VALUE\nsecond line"',
    '{"nested": ["WH_SYNTHETIC_08C_VALUE", {"x": "secret=other"}]}',
    "['WH_SYNTHETIC_08C_VALUE', (1, 2)]",
    "WH_SYNTHETIC_08C_VALUE trailing ambiguous prose",
    '"WH_SYNTHETIC_08C_VALUE',  # Unterminated quote: conceal the tail.
    '{"nested": ["WH_SYNTHETIC_08C_VALUE"',  # Unterminated container.
    '{"nested": "WH_SYNTHETIC_08C_VALUE"]',  # Mismatched delimiters.
], ids=["quoted-space", "quoted-delimiters", "escaped-quote", "multiline",
        "nested", "repr-container", "unquoted-space", "unclosed-quote",
        "unclosed-container", "mismatched-container"])
def test_values_and_malformed_tails_do_not_escape(value: str) -> None:
    result = _filtered("operation token=" + value)
    assert MARKER not in result and MASK in result
    assert result.startswith("operation token=")


@pytest.mark.parametrize("value", ['"WH_SYNTHETIC_08C_VALUE"suffix',
                                   '["WH_SYNTHETIC_08C_VALUE"]suffix'])
def test_malformed_adjacent_suffix_is_not_retained(value: str) -> None:
    result = _filtered("token=" + value + ", count=4")
    assert MARKER not in result and "suffix" not in result and "count=4" in result


def test_multiple_fields_and_repeated_filtering_are_idempotent() -> None:
    record = _record(f"token={MARKER}&PASSWORD='{MARKER}'; secret={MARKER}, n=2")
    redactor = SensitiveDataFilter()
    redactor.filter(record)
    first = record.getMessage()
    for _ in range(4):
        assert redactor.filter(record) is True
        assert record.getMessage() == first
    assert first.count(MASK) == 3 and MARKER not in first and "n=2" in first


@pytest.mark.parametrize("message,args", [
    ("token=%s, n=%d", (MARKER, 3)),
    ("token=%r, n=%d", (MARKER, 3)),
    ("token=%(token)s, n=%(n)d", ({"token": MARKER, "n": 3},)),
    ("password=%(password)r; progress=100%%", ({"password": MARKER},)),
    ("%s=%s, n=3", ("token", MARKER)),
    ("token=%08d, n=3", (12345678,)),
], ids=["positional", "repr", "mapping", "mapping-repr", "computed-key", "numeric"])
def test_arguments_are_formatted_before_redaction(message: str,
                                                 args: tuple[object, ...]) -> None:
    result = _filtered(message, args)
    assert MARKER not in result and "12345678" not in result
    assert MASK in result


def test_argument_objects_and_record_metadata_are_preserved() -> None:
    payload = {"token": MARKER, "count": 8}
    record = _record("token=%(token)s, count=%(count)d", (payload,))
    before = {k: v for k, v in record.__dict__.items() if k not in {"msg", "args"}}
    record.message = "stale " + MARKER
    SensitiveDataFilter().filter(record)
    assert payload == {"token": MARKER, "count": 8}
    assert record.args == () and record.message == record.msg
    assert {k: v for k, v in record.__dict__.items()
            if k not in {"msg", "args", "message"}} == before


@pytest.mark.parametrize("message,args", [
    ("Loaded %d tracks from %s", (3, "C:/music/Secret Garden.mp3")),
    ("password reset requested", ()),
    ("token_count=3, secretive=True, mypassword=ok", ()),
    ("progress=%(n)d%%", ({"n": 42},)),
    ("Привет 音楽 🌊 e\u0301", ()),
    ("100% complete", ()),
], ids=["normal-parameters", "key-prose", "nonkey", "percent", "unicode", "no-args"])
def test_ordinary_diagnostics_are_unchanged(message: str, args: tuple[object, ...]) -> None:
    expected = _record(message, args).getMessage()
    assert _filtered(message, args) == expected


def test_mapping_message_not_just_string_is_sanitized_without_mutation() -> None:
    message = {"token": MARKER, "count": 7}
    result = _filtered(message)
    assert MARKER not in result and "count" in result
    assert message == {"token": MARKER, "count": 7}


@pytest.mark.parametrize("message,args", [
    ("token=%(missing)s", ({"token": MARKER},)),
    ("token=%d", (MARKER,)),
    ("token=%s %s", (MARKER,)),
    ("token=%q", (MARKER,)),
], ids=["missing-key", "wrong-type", "arity", "bad-specifier"])
def test_invalid_logging_arguments_emit_explicit_safe_failure(message: str,
                                                              args: tuple[object, ...]) -> None:
    result = _filtered(message, args)
    assert "LOG_FORMAT_ERROR" in result and MARKER not in result
    assert "invalid logging arguments" in result


def test_message_conversion_exception_is_safe() -> None:
    class BrokenMessage:
        def __str__(self) -> str:
            raise RuntimeError("token=" + MARKER)
    assert "LOG_FORMAT_ERROR" in _filtered(BrokenMessage())


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit])
def test_process_control_exceptions_are_not_swallowed(error_type: type[BaseException]) -> None:
    class InterruptedMessage:
        def __str__(self) -> str:
            raise error_type()
    with pytest.raises(error_type):
        _filtered(InterruptedMessage())


def test_message_object_is_converted_only_once_across_handlers(tmp_path: Path) -> None:
    class Message:
        calls = 0
        def __str__(self) -> str:
            self.calls += 1
            return "token=" + MARKER
    obj = Message()
    record = _record(obj)
    handlers = [_build_file_handler(tmp_path / f"{i}.log", logging.Formatter("%(message)s"))
                for i in range(2)]
    try:
        for handler in handlers:
            handler.handle(record)
        assert obj.calls == 1
    finally:
        for handler in handlers:
            handler.close()


@pytest.mark.parametrize("message", ["x" * 65537, "token=" + MARKER + "x" * 65537],
                         ids=["ordinary-oversize", "secret-oversize"])
def test_oversized_text_is_replaced_by_explicit_budget_diagnostic(message: str) -> None:
    result = _filtered(message)
    assert "LOG_REDACTION_LIMIT" in result and MARKER not in result


def test_redaction_expansion_cannot_exceed_output_budget() -> None:
    result = _filtered("token=, " * 7000)
    assert len(result) <= 65536
    assert "LOG_REDACTION_LIMIT" in result


def test_current_keys_are_not_mutated() -> None:
    before = list(SensitiveDataFilter.SENSITIVE_KEYS)
    _filtered(f"password={MARKER}")
    assert SensitiveDataFilter.SENSITIVE_KEYS == before


@pytest.mark.parametrize("secret", ["café €", "音楽 🌊", "e\u0301 Кириллица"])
def test_unicode_secret_values_are_not_retained(secret: str) -> None:
    result = _filtered("request=3, token=" + repr(secret) + ", status=failed")
    assert secret not in result and "status=failed" in result and MASK in result


@pytest.mark.parametrize("message", ["token=", "password: ", "secret='', n=1"])
def test_empty_sensitive_fields_terminate_and_are_idempotent(message: str) -> None:
    first = _filtered(message)
    assert first == _filtered(first) and MASK in first


def test_exact_budget_ordinary_text_is_unchanged() -> None:
    message = "x" * 65536
    assert _filtered(message) == message
