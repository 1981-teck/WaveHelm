from __future__ import annotations

import pytest

from src.ui_wx.ambient_view import AmbientView
from src.ui_wx.effects_view import EffectsView
from src.utils.helpers import safe_filename, windows_filename_collision_key


def _utf16_units(value: str) -> int:
    return len(value.encode('utf-16-le')) // 2


@pytest.mark.parametrize(
    ('source', 'expected'),
    [
        ('CON', '_CON'),
        ('con.txt', '_con.txt'),
        ('PRN.', '_PRN'),
        ('AUX ', '_AUX'),
        ('NUL.log', '_NUL.log'),
        ('COM1', '_COM1'),
        ('com9.wav', '_com9.wav'),
        ('LPT1', '_LPT1'),
        ('lpt9.txt', '_lpt9.txt'),
    ],
)
def test_safe_filename_protects_windows_device_names(source: str, expected: str) -> None:
    assert safe_filename(source) == expected


def test_safe_filename_keeps_non_reserved_similar_names() -> None:
    assert safe_filename('COM0') == 'COM0'
    assert safe_filename('COM10.txt') == 'COM10.txt'
    assert safe_filename('LPT10') == 'LPT10'


def test_safe_filename_normalizes_invalid_characters_whitespace_and_trailing_marks() -> None:
    result = safe_filename('  report<>:"/\\|?*\x00  final...  ')

    assert result == 'report_final'
    assert not result.endswith((' ', '.'))


def test_safe_filename_uses_fallback_for_empty_or_invalid_only_input() -> None:
    assert safe_filename(' .  ') == 'untitled'
    assert safe_filename('***', fallback='playlist_7') == 'playlist_7'
    assert safe_filename('***', fallback='CON') == '_CON'
    assert safe_filename('***', fallback='***') == 'untitled'


def test_safe_filename_removes_mixed_trailing_spaces_and_dots() -> None:
    assert safe_filename('report .') == 'report'
    assert safe_filename('CON .txt') == 'CON_.txt'


def test_safe_filename_replaces_unpaired_surrogates_deterministically() -> None:
    assert safe_filename('bad\ud800name') == 'bad_name'


def test_safe_filename_bounds_pathological_input_before_normalization() -> None:
    result = safe_filename('a' * 1_000_000, max_length=255)

    assert len(result) == 255
    assert result == 'a' * 255


def test_safe_filename_applies_nfkc_unicode_normalization() -> None:
    assert safe_filename('Ｒｅｐｏｒｔ.txt') == 'Report.txt'
    assert safe_filename('e\u0301tude.txt') == 'étude.txt'


def test_windows_collision_key_is_case_and_unicode_insensitive() -> None:
    composed = windows_filename_collision_key('Résumé.txt. ')
    decomposed = windows_filename_collision_key('RE\u0301SUME\u0301.TXT')

    assert composed == decomposed


def test_safe_filename_resolves_case_insensitive_collisions_before_extension() -> None:
    result = safe_filename(
        'report.txt',
        existing_names=['REPORT.TXT', 'report_2.txt'],
    )

    assert result == 'report_3.txt'


def test_safe_filename_resolves_canonical_unicode_collision() -> None:
    result = safe_filename(
        're\u0301sume\u0301.TXT',
        existing_names=['Résumé.txt'],
    )

    assert result == 'résumé_2.TXT'


def test_safe_filename_resolves_extensionless_and_tiny_budget_collisions() -> None:
    assert safe_filename('name', existing_names=['NAME']) == 'name_2'
    assert safe_filename('a', max_length=1, existing_names=['A']) == '2'
    assert safe_filename('a.x', max_length=3, existing_names=['A.X']) == 'a_2'


def test_safe_filename_honors_utf16_component_budget_without_splitting_emoji() -> None:
    result = safe_filename('a' * 254 + '😀', max_length=255)

    assert result == 'a' * 254
    assert _utf16_units(result) == 254


def test_safe_filename_collision_suffix_stays_within_utf16_budget() -> None:
    original = 'a' * 251 + '.txt'
    result = safe_filename(original, max_length=255, existing_names=[original])

    assert result.endswith('_2.txt')
    assert _utf16_units(result) == 255


def test_default_budget_keeps_existing_wx_composed_names_windows_safe() -> None:
    class Track:
        title = 'x' * 400
        path = 'track.wav'

    class Player:
        current_track = Track()

    class AmbientHarness:
        player_controller = Player()

        @staticmethod
        def _selected_sound() -> str:
            return 'y' * 400

        @staticmethod
        def _get_current_sound_name() -> str:
            return 'ambient'

    class EffectsHarness:
        player_controller = Player()

        @staticmethod
        def _resolve_export_source() -> None:
            return None

    ambient_name = AmbientView._build_default_mix_filename(AmbientHarness())
    effects_name = EffectsView._build_default_export_filename(EffectsHarness())

    assert _utf16_units(ambient_name) <= 255
    assert _utf16_units(effects_name) <= 255


def test_existing_wx_composed_names_protect_reserved_device_segments() -> None:
    class Track:
        title = 'CON'
        path = 'track.wav'

    class Player:
        current_track = Track()

    class AmbientHarness:
        player_controller = Player()

        @staticmethod
        def _selected_sound() -> str:
            return 'AUX'

        @staticmethod
        def _get_current_sound_name() -> str:
            return 'ambient'

    class EffectsHarness:
        player_controller = Player()

        @staticmethod
        def _resolve_export_source() -> None:
            return None

    ambient_name = AmbientView._build_default_mix_filename(AmbientHarness())
    effects_name = EffectsView._build_default_export_filename(EffectsHarness())

    assert ambient_name == '_CON__AUX_mix.wav'
    assert effects_name == '_CON_effects.wav'


def test_safe_filename_rechecks_reserved_name_after_truncation() -> None:
    result = safe_filename('CONSOLE', max_length=3)

    assert result == '_CO'
    assert windows_filename_collision_key(result) != 'con'


@pytest.mark.parametrize('max_length', [0, 256, -1])
def test_safe_filename_rejects_out_of_range_limits(max_length: int) -> None:
    with pytest.raises(ValueError):
        safe_filename('name', max_length=max_length)


def test_safe_filename_rejects_non_integer_limit_and_string_collision_iterable() -> None:
    with pytest.raises(TypeError):
        safe_filename('name', max_length=True)
    with pytest.raises(TypeError):
        safe_filename('name', existing_names='name')


def test_safe_filename_bounds_existing_collision_candidates() -> None:
    names = ('same' for _ in range(65_537))

    with pytest.raises(ValueError, match='collision-candidate limit'):
        safe_filename('new', existing_names=names)


def test_safe_filename_rejects_non_text_inputs() -> None:
    with pytest.raises(TypeError):
        safe_filename(123)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        safe_filename('name', fallback=123)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        safe_filename('name', existing_names=['valid', 7])  # type: ignore[list-item]
