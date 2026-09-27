"""Small import cleanup: keep real type and dynamic-binding contracts intact."""
from __future__ import annotations

from collections.abc import Callable
import importlib
import inspect
from pathlib import Path
from types import ModuleType
from typing import get_origin, get_type_hints

import pytest


TARGETS = (
    ('src.boot.runtime_bootstrap', 'sys'),
    ('src.controller.playlist_controller_playlists', 'Callable'),
    ('src.model.profile_manager', 'Path'),
)


def _own_annotated_objects(module: ModuleType) -> list[object]:
    objects: list[object] = [module]
    for value in vars(module).values():
        owner = value.fget if isinstance(value, property) else value
        if getattr(owner, '__module__', None) != module.__name__:
            continue
        if inspect.isfunction(value):
            objects.append(value)
        elif inspect.isclass(value):
            objects.append(value)
            for member in vars(value).values():
                if isinstance(member, property):
                    objects.extend(fn for fn in (member.fget, member.fset) if fn)
                elif inspect.isfunction(member):
                    objects.append(member)
        elif isinstance(value, property) and value.fget is not None:
            objects.append(value.fget)
    return objects


@pytest.mark.parametrize('module_name,removed_name', TARGETS)
def test_selected_residual_import_is_not_a_module_binding(
    module_name: str, removed_name: str
) -> None:
    module = importlib.import_module(module_name)
    assert removed_name not in vars(module)


@pytest.mark.parametrize('module_name,_removed_name', TARGETS)
def test_deferred_annotations_still_resolve(
    module_name: str, _removed_name: str
) -> None:
    module = importlib.import_module(module_name)
    objects = _own_annotated_objects(module)
    assert len(objects) > 5
    for value in objects:
        assert isinstance(get_type_hints(value, globalns=vars(module)), dict)


def test_bootstrap_retains_app_alias_and_used_path_type() -> None:
    from src.boot import runtime_bootstrap as module

    assert module.MainApp is module.WxMainApp
    assert module.Path is Path
    hints = get_type_hints(module._ensure_app_dirs)
    assert hints['base_dir'] is Path
    assert module._normalize_ui_backend('wx', env={}) == 'wx'


def test_playlist_installer_preserves_every_real_method_and_property() -> None:
    from src.controller import playlist_controller_playlists as module

    class Owner:
        pass

    module.attach_playlist_controller_playlists_behavior(Owner)
    expected = dict(module._PLAYLIST_CONTROLLER_PLAYLISTS_METHODS)
    assert len(expected) == 10
    for name, value in expected.items():
        assert vars(Owner)[name] is value
    module.install_playlist_controller_playlists_behavior(Owner)
    for name, value in expected.items():
        assert vars(Owner)[name] is value
    assert isinstance(vars(Owner)['current_playlist_id'], property)


def test_profile_keeps_callable_contract_and_real_path_persistence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from src.model import profile_manager as module

    assert module.Callable is Callable
    assert get_origin(module.ProfileMutation) is Callable
    assert isinstance(module.PROFILE_PATH, Path)
    profile_path = tmp_path / 'profile.json'
    monkeypatch.setattr(module, 'PROFILE_PATH', profile_path)
    manager = module.ProfileManager()
    manager.increment_stat('plays', 2)
    assert module.ProfileManager().get_stat('plays') == 2
    assert profile_path.is_file()
