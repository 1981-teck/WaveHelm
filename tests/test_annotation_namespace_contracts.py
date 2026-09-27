"""Concrete runtime type resolution without namespace injection or backend substitutes."""
from __future__ import annotations

import ast
import importlib
import importlib.util
import inspect
import json
from pathlib import Path
import subprocess
import sys
import sysconfig
from typing import Dict, Optional, Union, get_type_hints

import pytest

from src.audio.effects import EffectsEngine
from src.audio.equalizer import Equalizer
from src.controller.component_player.player_event_handler import PlayerEventHandler

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'src/audio/audio_engine_helpers.py'
HANDLER = ROOT / 'src/controller/component_player/player_event_handler.py'

CONTROLLER_PROBE = '''
import importlib, json, sys, threading
from typing import get_type_hints
sys.path[:0] = [sys.argv[1], sys.argv[2]]
before = {id(t) for t in threading.enumerate()}
for name in json.loads(sys.argv[3]):
    importlib.import_module(name)
h = importlib.import_module('src.controller.component_player.player_event_handler')
resolved = get_type_hints(h.PlayerEventHandler.__init__)['player_controller_facade']
p = importlib.import_module('src.controller.player_controller')
assert resolved is p.PlayerController
assert p.PlayerEventHandler is h.PlayerEventHandler
assert not [t for t in threading.enumerate() if id(t) not in before]
assert 'pygame' not in sys.modules and 'wx' not in sys.modules
print(json.dumps({'resolved': resolved.__module__ + '.' + resolved.__qualname__,
                  'handler_identity': True, 'new_threads': 0}))
'''

AUDIO_PROBE = '''
import importlib, json, sys, threading
from typing import Optional, get_type_hints
sys.path[:0] = [sys.argv[1], sys.argv[2]]
before = {id(t) for t in threading.enumerate()}
for name in json.loads(sys.argv[3]):
    importlib.import_module(name)
h = importlib.import_module('src.audio.audio_engine_helpers')
resolved = get_type_hints(h.bind_dsp_processors)
a = importlib.import_module('src.audio.audio_engine')
e = importlib.import_module('src.audio.effects')
q = importlib.import_module('src.audio.equalizer')
p = importlib.import_module('pygame')
assert resolved == {'effects_engine': Optional[e.EffectsEngine],
                   'equalizer': Optional[q.Equalizer], 'return': type(None)}
assert a.AudioEngine.bind_dsp_processors is h.bind_dsp_processors
assert get_type_hints(a.AudioEngine.bind_dsp_processors) == resolved
assert not [t for t in threading.enumerate() if id(t) not in before]
assert not p.get_init() and p.mixer.get_init() is None
assert p.base.__file__.endswith(('.pyd', '.so'))
print(json.dumps({'canonical_types': True, 'owner_identity': True,
                  'pygame_native': p.base.__file__, 'mixer_initialized': False}))
'''


def require_audio() -> None:
    """Missing real pygame is an explicit unexecuted gate, never a substitute."""
    if importlib.util.find_spec('pygame') is None:
        pytest.skip('Real pygame required; helper/owner runtime gate is NOT RUN')
    importlib.import_module('pygame')


def run_probe(code: str, names: tuple[str, ...]) -> dict[str, object]:
    """Run imports in a fresh interpreter without host startup or inherited source paths."""
    args = [sys.executable, '-I', '-S', '-B', '-c', code, str(ROOT),
            sysconfig.get_path('purelib'), json.dumps(names)]
    process = subprocess.run(args, capture_output=True, timeout=30, check=False)
    assert process.returncode == 0, (process.stdout, process.stderr)
    lines = process.stdout.decode('utf-8').splitlines()
    return json.loads(lines[-1])


def test_equalizer_annotation_resolves_to_original_meaning() -> None:
    assert get_type_hints(Equalizer.get_all_bands) == {
        'return': Dict[str, Dict[str, Union[float, int]]]}


def test_handler_annotation_resolves_without_supplied_namespace() -> None:
    from src.controller.player_controller import PlayerController
    assert get_type_hints(PlayerEventHandler.__init__)['player_controller_facade'] is PlayerController


def test_handler_resolves_even_before_explicit_owner_import() -> None:
    result = run_probe(CONTROLLER_PROBE,
                       ('src.controller.component_player.player_event_handler',))
    assert result['handler_identity'] is True


@pytest.mark.parametrize('first', [
    'src.controller.player_controller',
    'src.controller.component_player.engine_controller',
    'src.controller.component_player.progress_tracker',
    'src.controller.component_player.player_event_handler_video',
], ids=['owner', 'engine', 'tracker', 'video-methods'])
def test_controller_import_orders_resolve_same_real_class(first: str) -> None:
    result = run_probe(CONTROLLER_PROBE, (first,))
    assert result['resolved'] == 'src.controller.player_controller.PlayerController'
    assert result['new_threads'] == 0


def test_handler_parameter_shape_is_preserved() -> None:
    params = inspect.signature(PlayerEventHandler.__init__).parameters
    assert list(params) == ['self', 'state_manager', 'queue_manager', 'engine_controller',
                            'event_bus', 'player_controller_facade']
    assert all(p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD for p in params.values())
    assert all(p.default is inspect.Parameter.empty for p in params.values())


@pytest.mark.parametrize('gain', [0.0, -4.5, 7.0], ids=['zero', 'negative', 'positive'])
def test_band_values_and_copy_contract_remain(gain: float) -> None:
    eq = object.__new__(Equalizer)
    eq._band_gains = {'31Hz': gain}
    first = eq.get_all_bands()
    assert len(first) == 10 and first['31Hz'] == {'freq': 31, 'gain': gain, 'q': 1.0}
    first['31Hz']['gain'] = 99.0
    assert eq.get_all_bands()['31Hz']['gain'] == gain


@pytest.mark.parametrize('name', ['EffectsEngine', 'Equalizer'])
def test_audio_types_have_unconditional_canonical_imports(name: str) -> None:
    tree = ast.parse(HELPER.read_bytes())
    expected = {'EffectsEngine': 'src.audio.effects', 'Equalizer': 'src.audio.equalizer'}
    imports = {(node.module, item.name) for node in tree.body if isinstance(node, ast.ImportFrom)
               for item in node.names}
    assert (expected[name], name) in imports


def test_no_new_annotation_bypass_decorator() -> None:
    for target in (HELPER, HANDLER, ROOT / 'src/audio/equalizer.py'):
        tree = ast.parse(target.read_bytes())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                assert not any(isinstance(d, ast.Name) and d.id == 'no_type_check'
                               for d in node.decorator_list)


@pytest.mark.parametrize('name', ['effects_engine', 'equalizer'])
def test_real_helper_types_resolve_without_owner_namespace(name: str) -> None:
    require_audio()
    helper = importlib.import_module('src.audio.audio_engine_helpers')
    types = {'effects_engine': EffectsEngine, 'equalizer': Equalizer}
    assert get_type_hints(helper.bind_dsp_processors)[name] == Optional[types[name]]


def test_real_bound_owner_function_has_same_hints_and_identity() -> None:
    require_audio()
    helper = importlib.import_module('src.audio.audio_engine_helpers')
    owner = importlib.import_module('src.audio.audio_engine')
    assert owner.AudioEngine.bind_dsp_processors is helper.bind_dsp_processors
    assert get_type_hints(owner.AudioEngine.bind_dsp_processors) == get_type_hints(helper.bind_dsp_processors)


@pytest.mark.parametrize('first', [
    'src.audio.audio_engine_helpers', 'src.audio.audio_engine',
    'src.audio.effects', 'src.audio.equalizer',
], ids=['helper', 'owner', 'effects', 'equalizer'])
def test_real_audio_import_orders_do_not_initialize_mixer(first: str) -> None:
    require_audio()
    result = run_probe(AUDIO_PROBE, (first,))
    assert result['canonical_types'] is True and result['mixer_initialized'] is False


class BindingOwner:
    """Observable receiver for a real helper; no backend or successful native service."""
    def __init__(self, active: bool, path: str | None) -> None:
        self.effects_engine: EffectsEngine | None = None
        self.equalizer: Equalizer | None = None
        self._current_file = path
        self.active = active
        self.scheduled = 0

    def _is_dsp_processing_active(self) -> bool:
        return self.active

    def _schedule_dsp_refresh(self) -> None:
        self.scheduled += 1


@pytest.mark.parametrize('active,path,expected', [
    (False, None, 0), (True, None, 0), (False, 'fixture.wav', 0), (True, 'fixture.wav', 1),
], ids=['empty', 'no-file', 'inactive', 'refresh'])
def test_real_binding_behavior_remains(active: bool, path: str | None, expected: int) -> None:
    require_audio()
    helper = importlib.import_module('src.audio.audio_engine_helpers')
    owner = BindingOwner(active, path)
    effect = object.__new__(EffectsEngine)
    equalizer = object.__new__(Equalizer)
    assert helper.bind_dsp_processors(owner, effects_engine=effect, equalizer=equalizer) is None
    assert owner.effects_engine is effect and owner.equalizer is equalizer
    assert owner.scheduled == expected
    helper.bind_dsp_processors(owner, effects_engine=effect, equalizer=equalizer)
    helper.bind_dsp_processors(owner)
    assert owner.scheduled == expected
