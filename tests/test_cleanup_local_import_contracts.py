"""Import cleanup must preserve annotations, re-exports and view error contracts.

Edge cases: quoted annotations need their canonical global type; a name with no
local call can be a required re-export; a view can use its own wider exception
policy. GUI faults use the existing test-owned wx boundary, not native windows.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import get_type_hints

import pytest

from src.controller import player_controller, video_controller_playback
from src.controller import video_controller_transport, video_terminal
from src.controller.video_controller import VideoController
from src.playback_observation import ProgressSnapshot
from src.ui_wx import common, favorites_view, view_factory
from tests.test_wx_favorites_view import build_favorites_view


def test_favorites_does_not_reexport_unused_common_exception_policy() -> None:
    assert 'WX_CALLBACK_EXCEPTIONS' not in vars(favorites_view)


def test_progress_snapshot_quoted_annotation_keeps_canonical_binding() -> None:
    assert player_controller.ProgressSnapshot is ProgressSnapshot
    method = player_controller.PlayerController.get_progress_snapshot
    assert get_type_hints(method)['return'] == ProgressSnapshot | None


def test_progress_snapshot_binding_is_required_for_annotation_resolution() -> None:
    method = player_controller.PlayerController.get_progress_snapshot
    namespace = dict(vars(player_controller))
    del namespace['ProgressSnapshot']
    with pytest.raises(NameError, match='ProgressSnapshot'):
        get_type_hints(method, globalns=namespace)


@pytest.mark.parametrize('owner', (
    video_controller_transport, video_controller_playback, VideoController,
))
def test_video_terminal_reexport_and_owner_attachment_are_canonical(owner: object) -> None:
    assert getattr(owner, 'finalize_video_end') is video_terminal.finalize_video_end


def test_video_terminal_behavior_installer_preserves_required_method() -> None:
    class Controller:
        """Fresh attachment target; no native backend is created."""

    video_controller_playback.install_video_controller_playback_behavior(Controller)
    assert getattr(Controller, 'finalize_video_end') is video_terminal.finalize_video_end


def test_video_terminal_without_ticket_remains_fail_closed() -> None:
    controller = SimpleNamespace(_video_end_ticket=None)
    assert video_controller_transport.finalize_video_end(controller) is False


def test_favorites_uses_its_own_unchanged_exception_policy() -> None:
    assert favorites_view.FAVORITES_VIEW_EXCEPTIONS == (
        AttributeError, OSError, RuntimeError, TypeError, ValueError,
    )
    assert common.WX_CALLBACK_EXCEPTIONS == (
        AttributeError, RuntimeError, TypeError, ValueError,
    )


def test_favorites_annotations_resolve_for_all_declared_methods() -> None:
    methods = [method for method in vars(favorites_view.FavoritesView).values()
               if inspect.isfunction(method)]
    assert methods
    for method in methods:
        assert isinstance(get_type_hints(method), dict)


@pytest.mark.parametrize('name', (
    'create_flow_sizer', 'format_duration', 'get_selected_indices',
    'refresh_now_playing_highlight', 'set_view_feedback', 'subscribe_event',
    'get_localized_text', 'register_callback', 'set_label_text',
))
def test_favorites_keeps_used_common_helper_identities(name: str) -> None:
    assert getattr(favorites_view, name) is getattr(common, name)


def test_view_factory_retains_canonical_favorites_class() -> None:
    assert view_factory.FavoritesView is favorites_view.FavoritesView


@pytest.mark.parametrize('error_type', (
    AttributeError, OSError, RuntimeError, TypeError, ValueError,
))
def test_favorites_confirmation_fault_never_authorizes_removal(
    monkeypatch: pytest.MonkeyPatch, error_type: type[Exception],
) -> None:
    view, database, _, _, _ = build_favorites_view(monkeypatch)
    error = error_type('controlled confirmation fault')

    def fail_dialog(*args: object, **kwargs: object) -> int:
        raise error

    try:
        monkeypatch.setattr(view._wx, 'MessageBox', fail_dialog)
        assert view._confirm_removal(1) is False
        assert database.removed == []
    finally:
        view.shutdown()


def test_favorites_does_not_broaden_unhandled_confirmation_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view, database, _, _, _ = build_favorites_view(monkeypatch)
    error = KeyError('outside the existing view policy')

    def fail_dialog(*args: object, **kwargs: object) -> int:
        raise error

    try:
        monkeypatch.setattr(view._wx, 'MessageBox', fail_dialog)
        with pytest.raises(KeyError) as caught:
            view._confirm_removal(1)
        assert caught.value is error
        assert database.removed == []
    finally:
        view.shutdown()


def test_favorites_shutdown_releases_existing_callback_contracts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view, _, _, bus, _ = build_favorites_view(monkeypatch)
    assert view.localization_manager.language_callbacks == [view.update_localization]
    assert view.theme_manager.theme_callbacks == [view.update_theme_colors]
    view.shutdown()
    assert view.localization_manager.language_callbacks == []
    assert view.theme_manager.theme_callbacks == []
    assert all(not callbacks for callbacks in bus.subscriptions.values())
