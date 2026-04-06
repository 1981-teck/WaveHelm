from pathlib import Path
import json


def test_manual_manifest_and_files_exist():
    manual_root = Path('src/resources/manual')
    manifest = json.loads((manual_root / 'manual_manifest.json').read_text(encoding='utf-8'))

    assert manifest['default_language'] == 'en'
    assert manifest['content_type'] == 'text/html'
    assert set(manifest['manuals']) == {'en', 'it', 'es', 'fr'}

    for lang_code, filename in manifest['manuals'].items():
        path = manual_root / filename
        assert path.is_file(), f'missing manual resource for {lang_code}'
        content = path.read_text(encoding='utf-8')
        assert '<html' in content.lower()
        assert 'WaveHelm' in content
        assert '<h1>' in content
        assert '<h2>' in content
