"""Guard the final two historical audio-import candidates and their real contracts.

Structural tests do not qualify runtime. Runtime cases import real pygame or report
an explicit skip; no replacement pygame is installed. Test-owned bus faults cover
successful publication, declared exceptions and undeclared errors. Canonical video
routing, provider identity and owner attachments must survive the cleanup.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
import importlib
import importlib.util
import inspect
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Callable, get_type_hints

import pytest

ROOT = Path(__file__).resolve().parents[1]
OWNER = 'src/audio/audio_engine.py'
PLAYBACK = 'src/audio/audio_engine_playback.py'
SUPPORT = 'src/audio/audio_engine_playback_support.py'
REMOVED = ((OWNER, 'VIDEO_EXTS'), (PLAYBACK, 'EVENT_BUS_EXCEPTIONS'))
RETAINED = tuple((OWNER, name) for name in ('helpers', 'dsp', 'playback', 'progress')) + tuple(
    (PLAYBACK, name) for name in ('VIDEO_EXTS', 'MIXER_EXCEPTIONS', 'publish_bus_event',
                                'safe_unsubscribe', 'safe_quit_mixer'))


def syntax(path: str) -> ast.Module:
    """Read actual UTF-8 source without importing a missing runtime backend."""
    return ast.parse((ROOT / path).read_bytes(), filename=path)


def imports(path: str) -> set[str]:
    """Collect declared binding names, not a proof of runtime reachability."""
    names: set[str] = set()
    for node in ast.walk(syntax(path)):
        if isinstance(node, ast.Import):
            names.update(alias.asname or alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names)
    return names


def attachments() -> dict[str, tuple[str, str]]:
    """Read the real owner table; reject ambiguous assignment shapes in the test."""
    found: dict[str, tuple[str, str]] = {}
    for node in syntax(OWNER).body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target, value = node.targets[0], node.value
        if not isinstance(target, ast.Attribute) or not isinstance(target.value, ast.Name):
            continue
        if target.value.id != 'AudioEngine':
            continue
        assert isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name)
        assert target.attr not in found
        found[target.attr] = (value.value.id, value.attr)
    return found


@pytest.mark.parametrize('path,name', REMOVED, ids=['owner-video-alias', 'playback-event-alias'])
def test_reviewed_incidental_import_is_absent(path: str, name: str) -> None:
    """These two absence guards are implementation-detail cleanup checks."""
    assert name not in imports(path)


@pytest.mark.parametrize('path,name', RETAINED, ids=[name for _, name in RETAINED])
def test_required_provider_import_remains(path: str, name: str) -> None:
    """Do not remove the actual consumer imports or delegation providers."""
    assert name in imports(path)


def test_owner_public_export_contract_is_unchanged() -> None:
    """The documented owner export is AudioEngine, not the incidental video set."""
    exports = [node.value for node in syntax(OWNER).body if isinstance(node, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == '__all__' for t in node.targets)]
    assert len(exports) == 1
    assert ast.literal_eval(exports[0]) == ['AudioEngine']


def test_owner_attachment_table_keeps_all_providers() -> None:
    """All 48 method attachments retain their provider families and key methods."""
    table = attachments()
    assert len(table) == 48
    assert {provider for provider, _ in table.values()} == {'helpers', 'dsp', 'playback', 'progress'}
    assert table['set_file'] == ('playback', 'set_file')
    assert table['close'] == ('playback', 'close')
    assert table['get_volume'] == ('progress', 'get_volume')


def test_actual_event_exception_policy_stays_in_support() -> None:
    """The tuple consumed by publish/unsubscribe is not removed from its provider."""
    policy = [node.value for node in syntax(SUPPORT).body if isinstance(node, ast.AnnAssign)
              and isinstance(node.target, ast.Name) and node.target.id == 'EVENT_BUS_EXCEPTIONS']
    assert len(policy) == 1 and isinstance(policy[0], ast.Tuple)
    assert [node.id for node in policy[0].elts if isinstance(node, ast.Name)] == [
        'AttributeError', 'RuntimeError', 'TypeError']


def test_shared_video_extensions_remain_canonical() -> None:
    """The defining set is preserved; import removal does not retire video support."""
    from src.audio.audio_engine_shared import VIDEO_EXTS
    assert VIDEO_EXTS == {'.mp4', '.m4v', '.mov', '.avi', '.mkv', '.webm', '.flv', '.wmv'}


@pytest.fixture
def audio_modules() -> dict[str, ModuleType]:
    """Require real pygame; missing backend is explicitly unqualified, not mocked."""
    if importlib.util.find_spec('pygame') is None:
        pytest.skip('Real pygame is missing; current audio runtime is NOT RUN')
    importlib.import_module('pygame')
    names = {'owner': 'audio_engine', 'playback': 'audio_engine_playback',
             'support': 'audio_engine_playback_support', 'shared': 'audio_engine_shared',
             'dsp': 'audio_engine_dsp', 'progress': 'audio_engine_progress',
             'helpers': 'audio_engine_helpers'}
    return {key: importlib.import_module('src.audio.' + name) for key, name in names.items()}


@pytest.mark.parametrize('key,name', [('owner', 'VIDEO_EXTS'), ('playback', 'EVENT_BUS_EXCEPTIONS')])
def test_real_runtime_namespaces_drop_only_incidental_aliases(
    audio_modules: dict[str, ModuleType], key: str, name: str,
) -> None:
    """Runtime absence supplements the source guard and is never inferred from it."""
    assert name not in vars(audio_modules[key])


def test_real_consumers_keep_canonical_video_set(audio_modules: dict[str, ModuleType]) -> None:
    """Playback, DSP and progress must reference the same original set object."""
    for key in ('playback', 'dsp', 'progress'):
        assert audio_modules[key].VIDEO_EXTS is audio_modules['shared'].VIDEO_EXTS


def test_real_support_exports_and_policy_are_preserved(audio_modules: dict[str, ModuleType]) -> None:
    """Delegation still reaches real support functions and the narrow error tuple."""
    support = audio_modules['support']
    assert support.EVENT_BUS_EXCEPTIONS == (AttributeError, RuntimeError, TypeError)
    for name in ('publish_bus_event', 'safe_unsubscribe', 'safe_quit_mixer', 'MIXER_EXCEPTIONS'):
        assert getattr(audio_modules['playback'], name) is getattr(support, name)


def test_real_owner_attachments_keep_identity(audio_modules: dict[str, ModuleType]) -> None:
    """Compare every declared assignment to the real imported function object."""
    owner = audio_modules['owner'].AudioEngine
    for name, (provider, method) in attachments().items():
        assert getattr(owner, name) is getattr(audio_modules[provider], method)


def test_owner_defined_annotations_resolve(audio_modules: dict[str, ModuleType]) -> None:
    """Owner-local properties and methods need no removed binding for typing."""
    module = audio_modules['owner']
    checked = 0
    for member in vars(module.AudioEngine).values():
        target = member.fget if isinstance(member, property) else member
        if inspect.isfunction(target) and target.__module__ == module.__name__:
            get_type_hints(target, globalns=vars(module))
            checked += 1
    assert checked == 6


@dataclass
class ControlledBus:
    """Observable test boundary, not a substitute audio backend."""
    failure: Exception | None = None
    published: list[tuple[object, dict[str, object]]] = field(default_factory=list)
    unsubscribed: list[tuple[object, object]] = field(default_factory=list)

    def publish(self, event: object, payload: dict[str, object]) -> None:
        """Record successful publication or raise the requested precise exception."""
        if self.failure is not None:
            raise self.failure
        self.published.append((event, payload))

    def unsubscribe(self, event: object, *, callback: Callable[..., object]) -> None:
        """Record subscription removal or expose the requested boundary failure."""
        if self.failure is not None:
            raise self.failure
        self.unsubscribed.append((event, callback))


@pytest.mark.parametrize('error_type', [AttributeError, RuntimeError, TypeError])
def test_declared_event_failures_keep_false_result(
    audio_modules: dict[str, ModuleType], error_type: type[Exception],
) -> None:
    """The existing support policy catches exactly these bus failures."""
    owner = SimpleNamespace(event_bus=ControlledBus(error_type('controlled event failure')))
    result = audio_modules['playback']._publish_bus_event(owner, object(), {}, context='M-test')
    assert result is False
    assert owner.event_bus.published == []


@pytest.mark.parametrize('error_type', [ValueError, OSError, KeyError])
def test_undeclared_event_errors_still_propagate(
    audio_modules: dict[str, ModuleType], error_type: type[Exception],
) -> None:
    """Removing an alias must not broaden the runtime exception policy."""
    owner = SimpleNamespace(event_bus=ControlledBus(error_type('controlled uncaught failure')))
    with pytest.raises(error_type):
        audio_modules['playback']._publish_bus_event(owner, object(), {}, context='M-test')


@pytest.mark.parametrize('error_type', [AttributeError, RuntimeError, TypeError])
def test_declared_unsubscribe_failures_are_contained(
    audio_modules: dict[str, ModuleType], error_type: type[Exception],
) -> None:
    """Existing teardown bus failures remain contained by the canonical helper."""
    owner = SimpleNamespace(event_bus=ControlledBus(error_type('controlled unsubscribe failure')))
    assert audio_modules['playback']._safe_unsubscribe(owner, object(), lambda: None, label='M') is None
    assert owner.event_bus.unsubscribed == []


@pytest.mark.parametrize('error_type', [ValueError, OSError, KeyError])
def test_undeclared_unsubscribe_errors_still_propagate(
    audio_modules: dict[str, ModuleType], error_type: type[Exception],
) -> None:
    """A private-import cleanup must not turn unknown teardown errors into success."""
    owner = SimpleNamespace(event_bus=ControlledBus(error_type('controlled uncaught failure')))
    with pytest.raises(error_type):
        audio_modules['playback']._safe_unsubscribe(owner, object(), lambda: None, label='M')


def test_successful_bus_delegation_preserves_payload(audio_modules: dict[str, ModuleType]) -> None:
    """Successful event publication and unsubscribe remain observable and singular."""
    bus = ControlledBus(); owner = SimpleNamespace(event_bus=bus)
    event = object(); payload: dict[str, object] = {'position': 0.0}
    callback = lambda: None
    assert audio_modules['playback']._publish_bus_event(owner, event, payload, context='M') is True
    audio_modules['playback']._safe_unsubscribe(owner, event, callback, label='M')
    assert bus.published == [(event, payload)]
    assert bus.published[0][1] is payload
    assert bus.unsubscribed == [(event, callback)]
