"""Overlay import cleanup without changing public-owner or native-call contracts.

Structural absence checks describe this cleanup, not a public export guarantee.
Behavioral edge cases cover unavailable Win32 APIs, failed native calls, and
idempotent teardown. Test-owned wx/Win32 boundaries never call the host OS GUI.
"""
from __future__ import annotations

from collections.abc import Iterator
import ctypes
from dataclasses import dataclass
import inspect
import sys
from types import ModuleType, SimpleNamespace
from typing import get_type_hints

import pytest

from src.audio.audio_event_models import AudioEventType
from src.ui_wx import common
from src.ui_wx import video_overlay_controls as controls
from src.ui_wx import video_overlay_layout as layout
from src.ui_wx import video_overlay_native as native
from tests.test_wx_video_view import (
    DummyEventBus, DummyLocalizationManager, DummyThemeManager,
)
from tests.wx_fakes import FakeWxModule


_REMOVED_SHARED = (
    'AudioEventType', 'apply_colors', 'get_theme_colors',
    'register_callback', 'set_label_text', 'unregister_callback',
)
_REMOVED_BINDINGS = (
    [(controls, 'ctypes'), (layout, 'ctypes')]
    + [(module, name) for module in (layout, native) for name in _REMOVED_SHARED]
)


@dataclass
class OverlayRig:
    """Own the test boundary and the managers whose callbacks must be released."""

    widget: controls.ExternalVideoControlOverlay
    bus: DummyEventBus
    theme: DummyThemeManager
    localization: DummyLocalizationManager


@pytest.fixture
def overlay_rig(monkeypatch: pytest.MonkeyPatch) -> Iterator[OverlayRig]:
    """Keep native calls disabled unless a test installs an explicit fake API."""
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    monkeypatch.setattr(native.ctypes, 'windll', None, raising=False)
    bus = DummyEventBus()
    theme = DummyThemeManager()
    localization = DummyLocalizationManager()
    widget = controls.ExternalVideoControlOverlay(
        FakeWxModule, FakeWxModule.Frame(None), event_bus=bus,
        theme_manager=theme, localization_manager=localization)
    try:
        yield OverlayRig(widget, bus, theme, localization)
    finally:
        widget.close()


@pytest.mark.parametrize(
    'module,name', _REMOVED_BINDINGS,
    ids=[f'{module.__name__.rsplit(".", 1)[-1]}-{name}'
         for module, name in _REMOVED_BINDINGS],
)
def test_selected_unused_overlay_binding_is_absent(module: ModuleType, name: str) -> None:
    """Guard the exact, reviewed import-only cleanup perimeter."""
    assert name not in vars(module)


@pytest.mark.parametrize('module,owner', [
    (layout, layout.VideoOverlayLayout), (native, native.VideoOverlayNative),
    (controls, controls.ExternalVideoControlOverlay),
])
def test_overlay_annotations_resolve_in_their_defining_module(
    module: ModuleType, owner: type,
) -> None:
    """Deferred annotations still resolve without relying on deleted bindings."""
    assert isinstance(get_type_hints(module), dict)
    for method in vars(owner).values():
        if inspect.isfunction(method):
            assert isinstance(get_type_hints(method, globalns=vars(module)), dict)


@pytest.mark.parametrize('mixin', [layout.VideoOverlayLayout, native.VideoOverlayNative])
def test_public_owner_inherits_the_original_mixin_methods(mixin: type) -> None:
    """MRO and inherited callable identities stay unchanged."""
    owner = controls.ExternalVideoControlOverlay
    assert mixin in owner.__mro__
    for name, method in vars(mixin).items():
        if inspect.isfunction(method):
            assert getattr(owner, name) is method


def test_used_imports_and_exception_tuple_remain_at_their_actual_owners() -> None:
    """Retain real event, appearance, exception, and ctypes dependencies."""
    assert native.ctypes is ctypes
    assert controls.AudioEventType is AudioEventType
    for name in _REMOVED_SHARED[1:]:
        assert getattr(controls, name) is getattr(common, name)
    for module in (controls, layout, native):
        assert module.WX_CALLBACK_EXCEPTIONS is common.WX_CALLBACK_EXCEPTIONS


def test_callbacks_and_timers_survive_cleanup_then_close_once(overlay_rig: OverlayRig) -> None:
    """Appearance/subscriptions remain live; teardown releases callbacks and timers."""
    rig, widget = overlay_rig, overlay_rig.widget
    assert rig.theme.theme_callbacks == [widget.update_theme_colors]
    assert rig.localization.language_callbacks == [widget.update_localization]
    assert set(rig.bus.subscriptions) == {
        AudioEventType.PLAYBACK_PROGRESS, AudioEventType.PLAYBACK_PRESENTATION_FINISHED,
        AudioEventType.PLAYER_STATE_CHANGED, AudioEventType.VIDEO_DURATION_UPDATE}
    rig.theme.theme_callbacks[0]()
    rig.localization.language_callbacks[0]()
    assert widget.panel.background == '#1b1b1b'
    assert widget.play_button.background == '#222222'
    assert widget.play_button.label == '▶'
    widget.show_temporarily()
    widget.set_bounds(640, 360)
    hide_timer, poll_timer = widget._hide_timer, widget._poll_timer
    assert hide_timer is not None and poll_timer is not None
    widget.close()
    assert hide_timer.stopped and poll_timer.stopped
    assert not rig.theme.theme_callbacks
    assert not rig.localization.language_callbacks
    assert all(not callbacks for callbacks in rig.bus.subscriptions.values())
    assert widget._subscriptions == []
    widget.close()
    assert widget._hide_timer is None and widget._poll_timer is None


def test_missing_win32_entrypoints_remain_safe(overlay_rig: OverlayRig) -> None:
    """Missing native APIs do not produce a false handle/coordinate result."""
    widget = overlay_rig.widget
    assert widget._resolve_set_window_pos() is None
    assert widget._get_native_pointer_position() is None
    assert widget._get_native_host_rect() is None
    widget._raise_overlay_native()


def test_native_raise_uses_the_original_ctypes_binding(
    overlay_rig: OverlayRig, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise dispatch through the native mixin, without real Win32 calls."""
    calls: list[tuple[int, ...]] = []

    def set_window_pos(*args: int) -> bool:
        calls.append(args)
        return True

    monkeypatch.setattr(native.ctypes, 'windll', SimpleNamespace(
        user32=SimpleNamespace(SetWindowPos=set_window_pos)))
    widget = overlay_rig.widget
    widget._raise_overlay_native()
    assert calls == [(widget._root_window.GetHandle(), 0, 0, 0, 0, 0,
                      0x0001 | 0x0002 | 0x0010 | 0x0200)]


def test_native_coordinate_callbacks_keep_their_real_ctypes_structures(
    overlay_rig: OverlayRig, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test-owned APIs populate production ctypes objects, not fabricated results."""
    handles: list[int] = []

    def get_cursor_pos(pointer: object) -> int:
        point = getattr(pointer, '_obj')
        setattr(point, 'x', 11)
        setattr(point, 'y', 23)
        return 1

    def get_window_rect(handle: int, pointer: object) -> int:
        handles.append(handle)
        rect = getattr(pointer, '_obj')
        for name, value in (('left', 5), ('top', 7), ('right', 105), ('bottom', 57)):
            setattr(rect, name, value)
        return 1

    monkeypatch.setattr(native.ctypes, 'windll', SimpleNamespace(user32=SimpleNamespace(
        GetCursorPos=get_cursor_pos, GetWindowRect=get_window_rect)))
    widget = overlay_rig.widget
    assert widget._get_native_pointer_position() == (11, 23)
    assert widget._get_native_host_rect() == (5, 7, 100, 50)
    assert handles == [widget._anchor_window.GetHandle()]


@pytest.mark.parametrize('raises', [False, True], ids=['zero-result', 'os-error'])
def test_failed_native_coordinate_calls_are_not_accepted(
    overlay_rig: OverlayRig, monkeypatch: pytest.MonkeyPatch, raises: bool,
) -> None:
    """Reject zero/error results even though ctypes is still available."""
    def fail_native(*args: object) -> int:
        if raises:
            raise OSError('test-owned Win32 failure')
        return 0

    monkeypatch.setattr(native.ctypes, 'windll', SimpleNamespace(user32=SimpleNamespace(
        GetCursorPos=fail_native, GetWindowRect=fail_native)))
    assert overlay_rig.widget._get_native_pointer_position() is None
    assert overlay_rig.widget._get_native_host_rect() is None
