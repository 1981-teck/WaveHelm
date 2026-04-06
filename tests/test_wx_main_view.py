from __future__ import annotations

import sys
from types import SimpleNamespace

from src.ui_wx.main_view import MainView
from tests.wx_fakes import FakeApp, FakeCloseEvent, FakeWxModule


def test_wx_main_view_builds_placeholder_shell(monkeypatch):
    monkeypatch.setitem(sys.modules, "wx", FakeWxModule)
    root = FakeApp()

    main_view = MainView(root)

    assert main_view.current_view == "library"
    assert set(main_view._pages) >= {"library", "playlist", "favorites"}
    assert main_view.window.size == (1280, 760)
    assert main_view._mini_player is not None



def test_wx_main_view_switches_views_without_shell_status_text(monkeypatch):
    monkeypatch.setitem(sys.modules, "wx", FakeWxModule)
    main_view = MainView(FakeApp())
    changed = []
    main_view.view_changed.connect(changed.append)

    main_view.show_view("about")

    assert main_view.current_view == "about"
    assert changed == ["about"]
    assert main_view._status_label is None
    assert main_view._footer_label is None



def test_wx_main_view_close_event_emits_shutdown_request(monkeypatch):
    monkeypatch.setitem(sys.modules, "wx", FakeWxModule)
    main_view = MainView(FakeApp())
    emitted = []
    main_view.shutdown_requested.connect(lambda: emitted.append("shutdown"))
    main_view._request_app_shutdown = lambda: None
    event = FakeCloseEvent(can_veto=True)

    main_view._handle_close_event(event)

    assert emitted == ["shutdown"]
    assert event.vetoed is True
    assert event.skipped is False



def test_wx_main_view_show_sets_top_window(monkeypatch):
    monkeypatch.setitem(sys.modules, "wx", FakeWxModule)
    root = FakeApp()
    main_view = MainView(root)

    main_view.show()

    assert root.top_window is main_view.window
    assert main_view.window._shown is True


def test_wx_main_view_external_only_runtime_does_not_register_video_page(monkeypatch):
    monkeypatch.setitem(sys.modules, "wx", FakeWxModule)
    main_view = MainView(FakeApp())

    assert "video" not in main_view._pages
    assert "video" not in main_view._sidebar_buttons


def test_wx_main_view_rejects_embedded_host_toggle_in_external_only_mode(monkeypatch):
    monkeypatch.setitem(sys.modules, "wx", FakeWxModule)
    main_view = MainView(FakeApp())

    main_view._deferred_external_window_close = True
    main_view._deferred_external_window_close_on_user_interrupt = True

    main_view.set_use_external_video_window(False)

    assert main_view._use_external_video_window is True
    assert main_view._deferred_external_window_close is False
    assert main_view._deferred_external_window_close_on_user_interrupt is False
