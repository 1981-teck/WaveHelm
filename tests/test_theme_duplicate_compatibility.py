"""Protect the kept pairs callback and independent active theme duplicate guards.

Edge cases: escaped keys collide after decoding; equal keys in separate objects
are valid; normalized theme names can collide without duplicate JSON keys.
Real temporary files verify write blocking. No native service or fake parser is used.
The public hook handles one object's pairs, not deep copies or resource budgets.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
import json
from pathlib import Path
from typing import get_type_hints

import pytest

from src.audio.audio_events import AudioEventType
import src.model.theme_manager as manager_module
from src.model.theme_manager import THEME_JSON_LIMITS, ThemeManager
from src.model.theme_manager_builtins import get_builtin_themes
import src.model.theme_schema as schema
from src.utils.bounded_json import BoundedJsonError, parse_json_text
from src.utils.exceptions import SettingsError

DUPLICATE_DOCUMENTS = (
    '{"x":1,"x":1}',
    '{"x":1,"x":2}',
    '{"outer":{"x":1,"x":2}}',
    '{"outer":[{"x":1,"x":2}]}',
    r'{"a":1,"\u0061":2}',
    '{"é":1,"\\u00e9":2}',
    '{"🎵":1,"\\ud83c\\udfb5":2}',
)
DISTINCT_NAMES = (('A', 'a'), ('é', 'e\u0301'), ('A', 'Ａ'))
OPERATIONS: tuple[Callable[[ThemeManager], object], ...] = (
    lambda manager: manager.save_custom_themes(),
    lambda manager: manager.set_custom_theme_color('bg_color', '#123456'),
    lambda manager: manager.reset_custom_theme_colors(),
)


class RecordingBus:
    """Observe manager publication without starting an event worker."""

    def __init__(self) -> None:
        self.calls: list[tuple[AudioEventType, object]] = []

    def publish(self, kind: AudioEventType, payload: object) -> bool:
        self.calls.append((kind, payload))
        return True


def _legacy_trap(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    raise AssertionError('Active parser called the legacy pairs callback')


def _palette_text() -> str:
    return json.dumps(get_builtin_themes()['dark'], separators=(',', ':'))


def _duplicate_theme_file(kind: str) -> bytes:
    palette = _palette_text()
    if kind == 'catalog':
        text = '{"custom":' + palette + ',"custom":' + palette + '}'
    else:
        name = 'bg_color' if kind == 'palette' else r'\u0062g_color'
        duplicate_palette = palette[:-1] + ',"' + name + '":"#123456"}'
        text = '{"custom":' + duplicate_palette + '}'
    return text.encode('utf-8')


def _assert_blocked_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: bytes,
    expected_error: str, operation: Callable[[ThemeManager], object],
) -> None:
    path = tmp_path / 'custom_themes.json'
    path.write_bytes(payload)
    monkeypatch.setattr(manager_module, 'get_app_data_path', lambda: tmp_path)
    monkeypatch.setattr(schema, 'reject_duplicate_object_pairs', _legacy_trap)
    bus = RecordingBus()
    manager = ThemeManager(event_bus=bus)
    before = manager.themes
    published = list(bus.calls)
    assert expected_error in (manager._custom_theme_write_block or '')
    assert 'custom' not in before
    with pytest.raises(SettingsError, match='writes are blocked'):
        operation(manager)
    assert path.read_bytes() == payload
    assert manager.themes == before and bus.calls == published
    assert {p.name for p in tmp_path.iterdir()} == {'custom_themes.json'}


def test_public_pairs_callback_signature_and_identity() -> None:
    from src.model.theme_schema import reject_duplicate_object_pairs
    assert reject_duplicate_object_pairs is schema.reject_duplicate_object_pairs
    assert get_type_hints(reject_duplicate_object_pairs) == {
        'pairs': Sequence[tuple[str, object]], 'return': dict[str, object],
    }


@pytest.mark.parametrize('key', ('x', '', 'café', '海洋', 'a\nb', 'e\u0301'))
def test_callback_exact_error_and_input_preservation(key: str) -> None:
    pairs = [(key, object()), ('different', object()), (key, object())]
    before = list(pairs)
    with pytest.raises(ValueError) as caught:
        schema.reject_duplicate_object_pairs(pairs)
    assert type(caught.value) is ValueError
    assert str(caught.value) == f'duplicate JSON key: {key!r}'
    assert pairs == before


def test_unique_pairs_preserve_order_identity_and_shallow_contract() -> None:
    value = {'nested': ['same object']}
    pairs: list[tuple[str, object]] = [('z', value), ('a', None)]
    result = schema.reject_duplicate_object_pairs(pairs)
    assert list(result) == ['z', 'a'] and result['z'] is value
    assert pairs == [('z', value), ('a', None)]
    result['new'] = True
    assert len(pairs) == 2


@pytest.mark.parametrize('document', DUPLICATE_DOCUMENTS,
                         ids=('equal', 'different', 'nested', 'array', 'escape', 'accent', 'surrogate'))
def test_stdlib_hook_rejects_decoded_duplicates_at_nested_depths(document: str) -> None:
    with pytest.raises(ValueError, match='duplicate JSON key'):
        json.loads(document, object_pairs_hook=schema.reject_duplicate_object_pairs)


@pytest.mark.parametrize('document', (
    '{}', '{"a":{"x":1},"b":{"x":2}}', '[{"x":1},{"x":2}]',
    '{"x":null,"y":false,"z":{"x":true}}',
))
def test_duplicate_scope_is_one_object_not_entire_document(document: str) -> None:
    expected = json.loads(document)
    assert json.loads(document, object_pairs_hook=schema.reject_duplicate_object_pairs) == expected
    assert parse_json_text(document, limits=THEME_JSON_LIMITS) == expected


@pytest.mark.parametrize(('first', 'second'), DISTINCT_NAMES)
def test_json_key_equality_does_not_apply_theme_name_normalization(first: str, second: str) -> None:
    text = json.dumps({first: 1, second: 2}, ensure_ascii=False)
    expected = {first: 1, second: 2}
    assert json.loads(text, object_pairs_hook=schema.reject_duplicate_object_pairs) == expected
    assert parse_json_text(text, limits=THEME_JSON_LIMITS) == expected


def test_pairs_hook_takes_priority_over_object_hook() -> None:
    def object_trap(value: dict[str, object]) -> object:
        raise AssertionError('object_hook must not replace the pairs hook')
    with pytest.raises(ValueError, match='duplicate JSON key'):
        json.loads('{"x":1,"x":2}', object_hook=object_trap,
                   object_pairs_hook=schema.reject_duplicate_object_pairs)


def test_direct_hook_cannot_recover_already_collapsed_nested_duplicates() -> None:
    collapsed = json.loads('{"x":1,"x":2}')
    result = schema.reject_duplicate_object_pairs([('nested', collapsed)])
    assert result == {'nested': {'x': 2}} and result['nested'] is collapsed


def test_hook_does_not_enforce_object_root() -> None:
    assert json.loads('[1,2]', object_pairs_hook=schema.reject_duplicate_object_pairs) == [1, 2]
    with pytest.raises(BoundedJsonError, match='root must be an object'):
        parse_json_text('[1,2]', limits=THEME_JSON_LIMITS, root='object')


@pytest.mark.parametrize('document', DUPLICATE_DOCUMENTS,
                         ids=('equal', 'different', 'nested', 'array', 'escape', 'accent', 'surrogate'))
def test_active_duplicate_guard_is_independent_of_public_hook(
    monkeypatch: pytest.MonkeyPatch, document: str,
) -> None:
    monkeypatch.setattr(schema, 'reject_duplicate_object_pairs', _legacy_trap)
    with pytest.raises(BoundedJsonError, match='Duplicate JSON key'):
        parse_json_text(document, limits=THEME_JSON_LIMITS, root='object')


@pytest.mark.parametrize('kind', ('catalog', 'palette', 'escaped-palette'))
@pytest.mark.parametrize('operation', OPERATIONS, ids=('save', 'color', 'reset'))
def test_duplicate_theme_bytes_block_mutations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
    operation: Callable[[ThemeManager], object],
) -> None:
    _assert_blocked_file(tmp_path, monkeypatch, _duplicate_theme_file(kind),
                         'BoundedJsonError: Duplicate JSON key', operation)


@pytest.mark.parametrize(('first', 'second'), DISTINCT_NAMES)
@pytest.mark.parametrize('operation', OPERATIONS, ids=('save', 'color', 'reset'))
def test_distinct_json_keys_with_canonical_theme_collision_block_mutations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, first: str, second: str,
    operation: Callable[[ThemeManager], object],
) -> None:
    palette = _palette_text()
    text = '{' + json.dumps(first) + ':' + palette + ',' + json.dumps(second) + ':' + palette + '}'
    assert len(parse_json_text(text, limits=THEME_JSON_LIMITS, root='object')) == 2
    _assert_blocked_file(tmp_path, monkeypatch, text.encode('utf-8'),
                         'ValueError: duplicate canonical custom theme name', operation)


@pytest.mark.parametrize('names', (('café', '海洋'), ('sunset', 'ocean')))
def test_valid_separate_palettes_roundtrip_without_legacy_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, names: tuple[str, str],
) -> None:
    palette = get_builtin_themes()['dark']
    catalog = {name: dict(palette) for name in names}
    path = tmp_path / 'custom_themes.json'
    path.write_bytes(json.dumps(catalog, ensure_ascii=False).encode('utf-8'))
    monkeypatch.setattr(manager_module, 'get_app_data_path', lambda: tmp_path)
    monkeypatch.setattr(schema, 'reject_duplicate_object_pairs', _legacy_trap)
    manager = ThemeManager(initial_mode=names[0])
    assert manager._custom_theme_write_block is None
    assert all(manager.themes[name] == palette for name in names)
    before = manager.themes
    manager.save_custom_themes()
    assert ThemeManager(initial_mode=names[0]).themes == before
    assert parse_json_text(path.read_text(encoding='utf-8'), limits=THEME_JSON_LIMITS) == catalog
