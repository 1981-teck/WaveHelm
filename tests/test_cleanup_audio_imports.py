"""Review-only import guards plus real, pygame-independent shared-type contracts.

Absence/retention checks are implementation-detail tests for this cleanup, not
public export promises or substitutes for DSP runtime qualification. Behavioral
edge cases cover valid zero, unavailable readings and non-finite readings.
Deferred annotations and canonical class identity must survive import removal.
No pygame replacement, audio device, or native GUI is installed by this suite.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import get_type_hints

import pytest

from src.audio import audio_engine_shared as shared
from src import playback_observation as observation

ROOT = Path(__file__).resolve().parents[1]
REMOVED = (
    ('src/audio/audio_engine_dsp.py', 'AudioEventType'),
    ('src/audio/audio_engine_shared.py', 'ReadingStatus'),
)
RETAINED = (
    ('src/audio/audio_engine_dsp.py', 'pygame'),
    ('src/audio/audio_engine_dsp.py', 'np'),
    ('src/audio/audio_engine_dsp.py', 'sf'),
    ('src/audio/audio_engine_dsp.py', 'VIDEO_EXTS'),
    ('src/audio/audio_engine_shared.py', 'ClockObservation'),
    ('src/audio/audio_engine_shared.py', 'ClockOrigin'),
    ('src/audio/audio_engine_shared.py', 'ClockValue'),
    ('src/audio/audio_engine_shared.py', 'empty_clock'),
    ('src/audio/audio_engine_shared.py', 'finite_seconds'),
)


def imported_bindings(relative_path: str) -> set[str]:
    """Inspect real source without importing the unavailable DSP backend."""
    tree = ast.parse((ROOT / relative_path).read_text(encoding='utf-8-sig'))
    bindings: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bindings.update(alias.asname or alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            bindings.update(alias.asname or alias.name for alias in node.names)
    return bindings


@pytest.mark.parametrize('path,binding', REMOVED, ids=['dsp-event', 'shared-status'])
def test_selected_unused_audio_binding_is_absent(path: str, binding: str) -> None:
    """Guard the two reviewed binding removals, without executing DSP code."""
    assert binding not in imported_bindings(path)


@pytest.mark.parametrize('path,binding', RETAINED, ids=[item[1] for item in RETAINED])
def test_required_audio_import_binding_is_retained(path: str, binding: str) -> None:
    """Keep backend imports and the canonical observation dependencies."""
    assert binding in imported_bindings(path)


def test_shared_annotations_resolve_without_incidental_status_export() -> None:
    """Resolve annotations on locally defined functions, classes and methods."""
    for value in vars(shared).values():
        if getattr(value, '__module__', None) != shared.__name__:
            continue
        if inspect.isfunction(value) or inspect.isclass(value):
            assert isinstance(get_type_hints(value, globalns=vars(shared)), dict)
        if inspect.isclass(value):
            for member in vars(value).values():
                target = member.fget if isinstance(member, property) else member
                if inspect.isfunction(target):
                    assert isinstance(get_type_hints(target, globalns=vars(shared)), dict)


@pytest.mark.parametrize('name', [
    'ClockObservation', 'ClockOrigin', 'ClockValue', 'empty_clock', 'finite_seconds',
])
def test_shared_observation_bindings_keep_canonical_identity(name: str) -> None:
    """Import cleanup must not clone types or redirect their defining module."""
    assert getattr(shared, name) is getattr(observation, name)


@pytest.mark.parametrize('value,status', [
    (0.0, observation.ReadingStatus.KNOWN),
    (None, observation.ReadingStatus.UNAVAILABLE),
    (float('nan'), observation.ReadingStatus.ERROR),
], ids=['valid-zero', 'missing', 'non-finite'])
def test_shared_clock_values_keep_canonical_status(
    value: float | None, status: observation.ReadingStatus,
) -> None:
    """ReadingStatus remains authoritative in the module that owns ClockValue."""
    result = shared.ClockValue.read(value)
    assert result.status is status
    assert result.seconds == (0.0 if status is observation.ReadingStatus.KNOWN else None)
