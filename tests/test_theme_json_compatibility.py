"""Protect the kept public callback and the independent active theme JSON boundary.

Edge cases: named non-finites and exponent overflow; invalid files must not be
replaced; valid quoted values and Unicode catalogs remain usable. The callback
alone is not a bounded parser. Temporary files are real; no native service is used.
"""
from __future__ import annotations

from collections.abc import Callable
import json
import math
from pathlib import Path
from typing import get_type_hints

import pytest

from src.audio.audio_events import AudioEventType
import src.model.theme_manager as manager_module
from src.model.theme_manager import THEME_JSON_LIMITS, ThemeManager
from src.model.theme_manager_builtins import get_builtin_themes
import src.model.theme_schema as schema
from src.utils.bounded_json import (
    BoundedJsonError, parse_json_bytes, parse_json_text, serialize_json_bytes,
)
from src.utils.exceptions import SettingsError

CONSTANTS = ('NaN', 'Infinity', '-Infinity')
NONFINITE_TOKENS = (*CONSTANTS, '1e999', '-1e999')
OPERATIONS: tuple[Callable[[ThemeManager], object], ...] = (
    lambda manager: manager.save_custom_themes(),
    lambda manager: manager.set_custom_theme_color('bg_color', '#123456'),
    lambda manager: manager.reset_custom_theme_colors(),
)


class RecordingBus:
    """Observe actual ThemeManager publication without starting an event worker."""

    def __init__(self) -> None:
        self.calls: list[tuple[AudioEventType, object]] = []

    def publish(self, kind: AudioEventType, payload: object) -> bool:
        self.calls.append((kind, payload))
        return True


def _callback_trap(value: str) -> object:
    raise AssertionError('Active parser unexpectedly called legacy callback: ' + value)


def _payload_with_numeric_color(token: str) -> bytes:
    palette = get_builtin_themes()['dark']
    text = json.dumps({'custom': palette}, separators=(',', ':'))
    old = '"bg_color":' + json.dumps(palette['bg_color'])
    return text.replace(old, '"bg_color":' + token, 1).encode('utf-8')


def test_kept_callback_remains_importable_with_original_annotations() -> None:
    from src.model.theme_schema import reject_nonstandard_json_number
    assert reject_nonstandard_json_number is schema.reject_nonstandard_json_number
    assert get_type_hints(reject_nonstandard_json_number) == {'value': str, 'return': object}


@pytest.mark.parametrize('token', CONSTANTS)
def test_callback_preserves_exact_valueerror_contract(token: str) -> None:
    with pytest.raises(ValueError) as caught:
        schema.reject_nonstandard_json_number(token)
    assert type(caught.value) is ValueError
    assert str(caught.value) == 'non-standard JSON number: ' + token


@pytest.mark.parametrize('token', CONSTANTS)
@pytest.mark.parametrize('container', ('TOKEN', '{"value":TOKEN}', '[{"value":TOKEN}]'))
def test_callback_works_as_stdlib_parse_constant(token: str, container: str) -> None:
    with pytest.raises(ValueError, match='non-standard JSON number'):
        json.loads(container.replace('TOKEN', token),
                   parse_constant=schema.reject_nonstandard_json_number)


@pytest.mark.parametrize(('text', 'expected'), (
    ('null', None), ('true', True), ('false', False), ('0', 0), ('-0.0', -0.0), ('1.25', 1.25),
))
def test_callback_does_not_reject_standard_json_values(text: str, expected: object) -> None:
    assert json.loads(text, parse_constant=schema.reject_nonstandard_json_number) == expected


@pytest.mark.parametrize('token', ('1e999', '-1e999'))
def test_exponent_overflow_requires_active_float_guard(token: str) -> None:
    legacy = json.loads(token, parse_constant=schema.reject_nonstandard_json_number)
    assert isinstance(legacy, float) and math.isinf(legacy)
    with pytest.raises(BoundedJsonError, match='must be finite'):
        parse_json_text(token, limits=THEME_JSON_LIMITS)


@pytest.mark.parametrize('token', NONFINITE_TOKENS)
@pytest.mark.parametrize('container', ('{"unused":TOKEN}', '{"custom":{"unused":TOKEN}}'))
def test_active_parser_is_independent_of_legacy_callback(
    monkeypatch: pytest.MonkeyPatch, token: str, container: str,
) -> None:
    monkeypatch.setattr(schema, 'reject_nonstandard_json_number', _callback_trap)
    with pytest.raises(BoundedJsonError):
        parse_json_text(container.replace('TOKEN', token), limits=THEME_JSON_LIMITS, root='object')


@pytest.mark.parametrize('token', NONFINITE_TOKENS)
@pytest.mark.parametrize('operation', OPERATIONS, ids=('save', 'color', 'reset'))
def test_nonfinite_theme_file_blocks_writes_and_preserves_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, token: str,
    operation: Callable[[ThemeManager], object],
) -> None:
    path = tmp_path / 'custom_themes.json'
    original = _payload_with_numeric_color(token)
    path.write_bytes(original)
    monkeypatch.setattr(manager_module, 'get_app_data_path', lambda: tmp_path)
    monkeypatch.setattr(schema, 'reject_nonstandard_json_number', _callback_trap)
    bus = RecordingBus()
    manager = ThemeManager(event_bus=bus)
    before = manager.themes
    calls = list(bus.calls)
    assert manager._custom_theme_write_block is not None
    assert manager._custom_theme_write_block.startswith('BoundedJsonError:')
    assert 'custom' not in before
    with pytest.raises(SettingsError, match='writes are blocked'):
        operation(manager)
    assert manager.themes == before and bus.calls == calls
    assert path.read_bytes() == original
    assert {item.name for item in tmp_path.iterdir()} == {'custom_themes.json'}


@pytest.mark.parametrize(('payload', 'message'), (
    (b'{"x":1,"x":2}', 'Duplicate JSON key'),
    (b'[]', 'root must be an object'),
    (b'{"x":"\xff"}', 'not valid UTF-8'),
    (b'{} {}', 'invalid JSON'),
    (b'{"a":{"b":{"c":{"d":{"e":0}}}}}', 'depth limit'),
    (b' ' * (THEME_JSON_LIMITS.max_bytes + 1), 'byte limit'),
    (b'{"n":' + b'1' * 129 + b'}', 'integer token limit'),
), ids=('duplicate', 'array-root', 'bad-utf8', 'trailing', 'depth', 'bytes', 'number-token'))
def test_other_active_rejections_preserve_file_and_block_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: bytes, message: str,
) -> None:
    path = tmp_path / 'custom_themes.json'
    path.write_bytes(payload)
    monkeypatch.setattr(manager_module, 'get_app_data_path', lambda: tmp_path)
    manager = ThemeManager()
    assert message in (manager._custom_theme_write_block or '')
    before = manager.themes
    with pytest.raises(SettingsError, match='writes are blocked'):
        manager.save_custom_themes()
    assert path.read_bytes() == payload and manager.themes == before


@pytest.mark.parametrize('value', (float('nan'), float('inf'), float('-inf')),
                         ids=('nan', 'inf', 'negative-inf'))
def test_active_serializer_rejects_nonfinite_memory_values(value: float) -> None:
    with pytest.raises(BoundedJsonError, match='must be finite'):
        serialize_json_bytes({'unused': value}, limits=THEME_JSON_LIMITS, root='object')


@pytest.mark.parametrize('token', CONSTANTS)
def test_quoted_tokens_are_text_not_nonfinite_numbers(token: str) -> None:
    payload = json.dumps({'label': token}).encode('utf-8')
    assert parse_json_bytes(payload, limits=THEME_JSON_LIMITS, root='object') == {'label': token}


@pytest.mark.parametrize('name', ('café', '海洋'))
def test_valid_unicode_theme_roundtrip_without_legacy_callback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str,
) -> None:
    palette = get_builtin_themes()['dark']
    path = tmp_path / 'custom_themes.json'
    path.write_bytes(json.dumps({name: palette}, ensure_ascii=False).encode('utf-8'))
    monkeypatch.setattr(manager_module, 'get_app_data_path', lambda: tmp_path)
    monkeypatch.setattr(schema, 'reject_nonstandard_json_number', _callback_trap)
    manager = ThemeManager(initial_mode=name)
    assert manager.get_current_theme_name() == name
    assert manager.get_current_theme_colors() == palette
    before = manager.themes
    manager.save_custom_themes()
    assert parse_json_bytes(path.read_bytes(), limits=THEME_JSON_LIMITS, root='object') == {name: palette}
    assert manager.themes == before
    assert ThemeManager(initial_mode=name).themes == before
