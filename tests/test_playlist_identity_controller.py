from __future__ import annotations

from src.controller import playlist_controller_support as support


class IdentityController:
    def __init__(self) -> None:
        self._playlists_cache: dict[int, dict[str, object]] = {}
        self._cache_valid = True


support.attach_playlist_controller_support_behavior(IdentityController)


def test_controller_uses_shared_unicode_identity_policy() -> None:
    controller = IdentityController()
    controller._playlists_cache = {
        1: {"name": "Straße"},
        2: {"name": "é"},
    }

    assert controller._validate_playlist_name("  STRASSE  ") is True
    assert controller._validate_playlist_name("bad\nname") is False
    assert controller._playlist_name_exists("STRASSE") is True
    assert controller._playlist_name_exists("e\u0301") is True
    assert controller._playlist_name_exists("STRASSE", exclude_id=1) is False


def test_invalid_cached_name_blocks_mutation_and_invalidates_cache() -> None:
    controller = IdentityController()
    controller._playlists_cache = {1: {"name": None}}

    assert controller._playlist_name_exists("valid") is True
    assert controller._cache_valid is False


def test_invalid_candidate_is_not_reported_as_duplicate() -> None:
    controller = IdentityController()
    controller._playlists_cache = {1: {"name": "Alpha"}}

    assert controller._playlist_name_exists("bad\nname") is False
