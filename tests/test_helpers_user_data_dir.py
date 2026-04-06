from __future__ import annotations

from pathlib import Path
import shutil

from src.utils import helpers


RUNTIME_ROOT = Path(__file__).resolve().parent / '_helpers_runtime'


def _make_runtime_dir(name: str) -> Path:
    path = RUNTIME_ROOT / name
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_resolve_user_config_root_supports_injected_windows_context():
    runtime_dir = _make_runtime_dir('windows_root')
    appdata = runtime_dir / 'roaming'
    resolved = helpers.resolve_user_config_root(
        os_name='nt',
        env={'APPDATA': str(appdata)},
        home_dir=runtime_dir / 'home',
    )

    assert resolved == appdata


def test_resolve_user_config_root_supports_injected_macos_context():
    runtime_dir = _make_runtime_dir('macos_root')
    home = runtime_dir / 'home'
    resolved = helpers.resolve_user_config_root(
        os_name='posix',
        sys_platform='darwin',
        env={},
        home_dir=home,
    )

    assert resolved == home / 'Library' / 'Application Support'


def test_get_user_data_dir_uses_injected_windows_context():
    runtime_dir = _make_runtime_dir('windows_data_dir')
    appdata = runtime_dir / 'roaming'
    user_data_dir = helpers.get_user_data_dir(
        os_name='nt',
        env={'APPDATA': str(appdata)},
        home_dir=runtime_dir / 'home',
        app_name='WaveHelm',
    )

    assert user_data_dir == appdata / 'WaveHelm'
    assert user_data_dir.is_dir()


def test_get_user_data_dir_uses_injected_linux_xdg_context():
    runtime_dir = _make_runtime_dir('linux_xdg')
    home = runtime_dir / 'home'
    xdg_config_home = runtime_dir / 'xdg-config'
    user_data_dir = helpers.get_user_data_dir(
        os_name='posix',
        sys_platform='linux',
        env={'XDG_CONFIG_HOME': str(xdg_config_home)},
        home_dir=home,
        app_name='WaveHelm',
    )

    assert user_data_dir == xdg_config_home / 'WaveHelm'
    assert user_data_dir.is_dir()


def test_get_user_data_dir_uses_canonical_metadata_name(monkeypatch):
    runtime_dir = _make_runtime_dir('canonical_metadata_name')
    appdata = runtime_dir / 'roaming'
    monkeypatch.setattr(helpers, 'get_app_name', lambda app_info_path=None: 'WaveHelmQA')

    user_data_dir = helpers.get_user_data_dir(
        os_name='nt',
        env={'APPDATA': str(appdata)},
        home_dir=runtime_dir / 'home',
    )

    assert user_data_dir == appdata / 'WaveHelmQA'
    assert user_data_dir.is_dir()


def test_get_user_data_dir_falls_back_to_home_scoped_dir_when_primary_creation_fails(monkeypatch):
    runtime_dir = _make_runtime_dir('fallback_home_scoped')
    home = runtime_dir / 'home'
    appdata = runtime_dir / 'roaming'
    primary_dir = appdata / 'WaveHelmQA'
    fallback_dir = home / '.wavehelmqa_data'
    mkdir_calls: list[Path] = []

    def fake_mkdir(self, parents=True, exist_ok=True):
        path = Path(self)
        if path == primary_dir:
            raise OSError('primary fail')
        mkdir_calls.append(path)

    monkeypatch.setattr(helpers.Path, 'mkdir', fake_mkdir)

    user_data_dir = helpers.get_user_data_dir(
        os_name='nt',
        env={'APPDATA': str(appdata)},
        home_dir=home,
        app_name='WaveHelmQA',
    )

    assert user_data_dir == fallback_dir
    assert fallback_dir in mkdir_calls
