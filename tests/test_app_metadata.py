from __future__ import annotations

import json
from pathlib import Path

from src.config import app_metadata


RUNTIME_DIR = Path(__file__).resolve().parent / '_app_metadata_runtime'
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)


def test_read_app_metadata_normalizes_legacy_github_url():
    legacy_path = RUNTIME_DIR / 'legacy_app_info.json'
    legacy_path.write_text(
        json.dumps(
            {
                'general': {
                    'app_name': 'WaveHelm',
                    'version': '3.0.0',
                    'github_url': 'https://legacy.example/app',
                },
            }
        ),
        encoding='utf-8',
    )

    metadata = app_metadata.read_app_metadata(legacy_path)

    assert metadata['general']['website_url'] == 'https://legacy.example/app'
    assert metadata['general']['organization_name'] == 'WaveHelm'
    assert 'github_url' not in metadata['general']


def test_merge_app_metadata_preserves_existing_general_fields():
    current = {
        'general': {
            'app_name': 'WaveHelm',
            'organization_name': 'WaveHelm',
            'version': '1.0',
            'website_url': 'https://example.com',
        },
        'modules_enabled': ['audio_engine'],
    }

    merged = app_metadata.merge_app_metadata(current, {'general': {'app_name': 'WaveHelm QA'}})

    assert merged['general']['app_name'] == 'WaveHelm QA'
    assert merged['general']['version'] == '1.0'
    assert merged['general']['organization_name'] == 'WaveHelm'
    assert merged['modules_enabled'] == ['audio_engine']


def test_load_app_metadata_returns_defaults_for_invalid_json():
    invalid_path = RUNTIME_DIR / 'broken_app_info.json'
    invalid_path.write_text('{broken', encoding='utf-8')

    metadata = app_metadata.load_app_metadata(invalid_path)

    assert metadata['general']['app_name'] == app_metadata.APP_NAME_FALLBACK
    assert metadata['general']['organization_name'] == app_metadata.APP_NAME_FALLBACK
