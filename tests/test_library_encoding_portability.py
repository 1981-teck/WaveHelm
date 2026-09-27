"""Explicit UTF-8 at the Library qualification boundary.

Exercise actual locale/resize assertions under cp1252 and ASCII defaults; preserve
Unicode exactly; reject invalid bytes rather than dropping characters. The default
is emulated only at locale Path.read_text calls, never changed globally or in src.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from tests import test_library_toolbar_contract as toolbar


@pytest.mark.parametrize('language', ['en', 'it', 'fr', 'es'])
@pytest.mark.parametrize('default_encoding', ['cp1252', 'ascii'])
def test_locale_roundtrip_survives_non_utf8_defaults(monkeypatch, language, default_encoding):
    target = Path(toolbar.__file__).resolve().parents[1] / 'src/locales' / f'{language}.json'
    raw = target.read_bytes()
    expected = json.loads(raw.decode('utf-8'))
    reads = []
    original = Path.read_text

    def read(path, encoding=None, errors=None, **kwargs):
        if path == target:
            actual = encoding if encoding is not None else default_encoding
            text = original(path, encoding=actual, errors=errors, **kwargs)
            reads.append((actual, json.loads(text)))
            return text
        return original(path, encoding=encoding, errors=errors, **kwargs)

    monkeypatch.setattr(Path, 'read_text', read)
    toolbar.test_locale_resize_roundtrip_preserves_controls_and_inputs(monkeypatch, language)
    assert reads == [('utf-8', expected)]


@pytest.mark.parametrize('language', ['en', 'it', 'fr', 'es'])
def test_invalid_locale_bytes_are_not_silently_replaced(monkeypatch, language):
    target = Path(toolbar.__file__).resolve().parents[1] / 'src/locales' / f'{language}.json'
    original = Path.read_text
    observed = []

    def read(path, encoding=None, errors=None, **kwargs):
        if path == target:
            observed.append((encoding, errors))
            return b'{"title":"\xff"}'.decode(encoding or 'cp1252', errors or 'strict')
        return original(path, encoding=encoding, errors=errors, **kwargs)

    monkeypatch.setattr(Path, 'read_text', read)
    with pytest.raises(UnicodeDecodeError):
        toolbar.test_locale_resize_roundtrip_preserves_controls_and_inputs(monkeypatch, language)
    assert observed == [('utf-8', None)]


@pytest.mark.parametrize('relative', [
    'tests/test_library_toolbar_contract.py',
    'tests/test_library_layout_probe.py',
    'tools/probe_library_layout.py',
])
def test_library_qualification_text_calls_specify_encoding(relative):
    """Bounded static guard for named qualification files, not a global I/O audit."""
    root = Path(toolbar.__file__).resolve().parents[1]
    tree = ast.parse((root / relative).read_text(encoding='utf-8'))
    calls = [node for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
             and node.func.attr in ('read_text', 'write_text')]
    assert calls
    for call in calls:
        encoding = next((item.value for item in call.keywords if item.arg == 'encoding'), None)
        assert isinstance(encoding, ast.Constant) and encoding.value == 'utf-8', (relative, call.lineno)
