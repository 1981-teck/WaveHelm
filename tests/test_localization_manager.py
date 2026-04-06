from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

from src.model.localization_manager import LocalizationManager, _resolve_locales_dir

WORKSPACE_TMP = Path(__file__).resolve().parent / '_tmp_locales'
WORKSPACE_TMP.mkdir(exist_ok=True)


def _make_locales_dir() -> Path:
    path = WORKSPACE_TMP / f'locales_{uuid.uuid4().hex}'
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_get_text_keeps_template_when_formatting_fails(caplog):
    tmp_path = _make_locales_dir()
    (tmp_path / 'en.json').write_text(json.dumps({'greet': 'Hello {name}'}), encoding='utf-8')

    manager = LocalizationManager(locales_dir=tmp_path, fallback_language='en')
    caplog.set_level(logging.DEBUG)

    assert manager.get_text('greet', name='Marco') == 'Hello Marco'
    assert manager.get_text('greet', other='x') == 'Hello {name}'
    assert any('Localization formatting failed for key greet' in record.message for record in caplog.records)


def test_load_language_file_returns_empty_on_invalid_json(caplog):
    tmp_path = _make_locales_dir()
    (tmp_path / 'en.json').write_text(json.dumps({'ok': 'yes'}), encoding='utf-8')
    (tmp_path / 'bad.json').write_text('{ invalid', encoding='utf-8')

    manager = LocalizationManager(locales_dir=tmp_path, fallback_language='en')
    caplog.set_level(logging.DEBUG)

    assert manager._load_language_file('bad') == {}
    assert any('Failed to load locale file for language bad' in record.message for record in caplog.records)


def test_resolve_locales_dir_uses_wavehelm_env_var(monkeypatch):
    tmp_path = _make_locales_dir()

    monkeypatch.setenv('WAVEHELM_LOCALES', str(tmp_path))

    assert _resolve_locales_dir() == tmp_path.resolve()


def test_notify_callbacks_supports_zero_and_one_argument_and_ignores_failures():
    tmp_path = _make_locales_dir()
    (tmp_path / 'en.json').write_text(json.dumps({'hello': 'Hello'}), encoding='utf-8')
    (tmp_path / 'fr.json').write_text(json.dumps({'hello': 'Bonjour'}), encoding='utf-8')

    manager = LocalizationManager(locales_dir=tmp_path, fallback_language='en')
    calls = []

    def callback_no_args():
        calls.append(('no-args', None))

    def callback_with_language(language):
        calls.append(('with-language', language))

    def failing_callback():
        raise RuntimeError('boom')

    manager.register_language_change_callback(callback_no_args)
    manager.register_language_change_callback(callback_with_language)
    manager.register_language_change_callback(failing_callback)

    manager.set_language('fr')

    assert manager.get_current_language() == 'fr'
    assert ('no-args', None) in calls
    assert ('with-language', 'fr') in calls


def test_notify_callbacks_does_not_retry_internal_type_error_as_signature_fallback(caplog):
    tmp_path = _make_locales_dir()
    (tmp_path / 'en.json').write_text(json.dumps({'hello': 'Hello'}), encoding='utf-8')
    (tmp_path / 'fr.json').write_text(json.dumps({'hello': 'Bonjour'}), encoding='utf-8')

    manager = LocalizationManager(locales_dir=tmp_path, fallback_language='en')
    calls = []

    def callback_with_internal_type_error():
        calls.append('bad')
        raise TypeError('internal bug')

    def callback_with_language(language):
        calls.append(('with-language', language))

    manager.register_language_change_callback(callback_with_internal_type_error)
    manager.register_language_change_callback(callback_with_language)

    with caplog.at_level(logging.DEBUG):
        manager.set_language('fr')

    assert calls.count('bad') == 1
    assert ('with-language', 'fr') in calls
    assert any('Language change callback failed' in record.message for record in caplog.records)


def test_unregister_missing_callback_logs_debug(caplog):
    tmp_path = _make_locales_dir()
    (tmp_path / 'en.json').write_text(json.dumps({'hello': 'Hello'}), encoding='utf-8')
    manager = LocalizationManager(locales_dir=tmp_path, fallback_language='en')
    caplog.set_level(logging.DEBUG)

    def callback():
        return None

    manager.unregister_language_change_callback(callback)

    assert any('Language change callback already absent during unregister' in record.message for record in caplog.records)


def test_shipped_locales_include_library_favorites_and_video_labels():
    required_keys = {
        'library_search_label',
        'library_filter_label',
        'library_sort_label',
        'library_column_title',
        'library_column_artist',
        'library_column_album',
        'library_column_duration',
        'library_column_type',
        'library_column_path',
        'library_play_selected_button',
        'library_refresh_button',
        'favorites_remove_button',
        'favorites_select_all_button',
        'favorites_col_title',
        'favorites_col_artist',
        'favorites_col_duration',
        'favorites_col_path',
        'playlist_col_name',
        'playlist_col_count',
        'playlist_col_title',
        'playlist_col_artist',
        'playlist_col_duration',
        'playlist_col_path',
        'playlist_tracks_title',
        'effect_echo',
        'effect_reverb',
        'effect_vintage_filter',
        'effects_toggle',
        'effect_echo_delay',
        'effect_echo_decay',
        'effect_reverb_decay',
        'effect_reverb_wet',
        'effect_vintage_cutoff',
        'effect_vintage_resonance',
        'video_external_window',
        'video_toggle_fullscreen',
        'video_refresh_surface',
        'video_audio_track',
        'video_subtitles',
        'video_surface_ready',
    }
    locales_dir = Path(__file__).resolve().parents[1] / 'src' / 'locales'
    for language in ('it', 'en', 'es', 'fr'):
        data = json.loads((locales_dir / f'{language}.json').read_text(encoding='utf-8'))
        missing = sorted(required_keys.difference(data))
        assert not missing, f'{language} locale missing keys: {missing}'


def test_spanish_and_french_locales_translate_playlist_and_favorites_visible_labels():
    locales_dir = Path(__file__).resolve().parents[1] / 'src' / 'locales'
    es = json.loads((locales_dir / 'es.json').read_text(encoding='utf-8'))
    fr = json.loads((locales_dir / 'fr.json').read_text(encoding='utf-8'))

    assert es['favorites_col_title'] == 'Título'
    assert es['playlist_col_title'] == 'Título'
    assert es['playlist_tracks_title'] == 'Pistas'
    assert es['playlist_tab'] == 'Lista de reproducción'
    assert es['module_playlists'] == 'Listas de reproducción'

    assert fr['favorites_col_title'] == 'Titre'
    assert fr['playlist_col_title'] == 'Titre'
    assert fr['playlist_tracks_title'] == 'Pistes'
    assert fr['playlist_tab'] == 'Liste de lecture'
    assert fr['module_favorites'] == 'Favoris'
    assert fr['module_playlists'] == 'Listes de lecture'
