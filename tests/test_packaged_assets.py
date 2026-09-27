"""Asset packaging contracts, not evidence of installed GUI rendering."""
from __future__ import annotations

import fnmatch
import json
from pathlib import Path
import tomllib
from xml.etree import ElementTree

import pytest

ROOT = Path(__file__).resolve().parents[1]
ICONS = ('add', 'favorite', 'favorite_border', 'fullscreen', 'pause', 'play_arrow',
         'repeat', 'repeat_one', 'shuffle', 'skip_next', 'skip_previous', 'stop',
         'volume_off', 'volume_up')
REPAIRED = ('resources/ambient_profile.json',) + tuple(f'resources/icons/{name}.svg' for name in ICONS)


@pytest.mark.parametrize('relative', REPAIRED)
def test_previously_omitted_assets_have_explicit_wheel_patterns(relative: str) -> None:
    config = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
    patterns = config['tool']['setuptools']['package-data']['src']
    assert (ROOT / 'src' / relative).is_file()
    assert any(fnmatch.fnmatchcase(relative, pattern) for pattern in patterns)


def test_sdist_explicitly_keeps_all_resource_families() -> None:
    manifest = (ROOT / 'MANIFEST.in').read_text(encoding='utf-8')
    assert 'recursive-include src/resources *.json *.svg' in manifest
    assert 'recursive-include src/resources/manual *' in manifest
    assert 'recursive-include src/resources/legal *' in manifest
    # A stale historical root manifest must not be promoted as current attestation.
    assert 'include SOURCE_MANIFEST_R5.json' not in manifest.splitlines()


@pytest.mark.parametrize('name', ICONS)
def test_packaged_icon_is_real_xml_svg(name: str) -> None:
    path = ROOT / 'src/resources/icons' / f'{name}.svg'
    document = ElementTree.fromstring(path.read_bytes())
    assert document.tag == '{http://www.w3.org/2000/svg}svg'
    assert document.attrib.get('viewBox')


def test_ambient_profile_remains_nonempty_json_object() -> None:
    document = json.loads((ROOT / 'src/resources/ambient_profile.json').read_text(encoding='utf-8'))
    assert isinstance(document, dict) and document
