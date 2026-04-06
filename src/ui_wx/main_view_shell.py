from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.ui_wx.mini_player import MiniPlayer
from src.ui_wx.view_factory import create_page_widget

VIEW_LABELS: tuple[tuple[str, str], ...] = (
    ("library", "Library"),
    ("playlist", "Playlist"),
    ("favorites", "Favorites"),
    ("equalizer", "Equalizer"),
    ("effects", "Effects"),
    ("visualizer", "Visualizer"),
    ("ambient", "Ambient"),
    ("settings", "Settings"),
    ("readmi", "Readmi"),
    ("about", "About"),
)


@dataclass(frozen=True)
class WxViewPage:
    name: str
    title: str
    panel: Any
    label: Any


class _SimplebookFallback:
    def __init__(self, wx_module: Any, parent: Any) -> None:
        self._wx = wx_module
        self._parent = parent
        self._pages: list[Any] = []
        self._selection = -1

    def AddPage(self, page: Any, _text: str) -> None:
        hide = getattr(page, "Hide", None)
        if callable(hide):
            hide()
        self._pages.append(page)

    def GetPageCount(self) -> int:
        return len(self._pages)

    def SetSelection(self, index: int) -> None:
        if not 0 <= index < len(self._pages):
            raise IndexError("Simplebook selection out of range.")
        for page_index, page in enumerate(self._pages):
            show = getattr(page, "Show", None)
            if callable(show):
                show(page_index == index)
        self._selection = index


class _CloseEventAdapter:
    def __init__(self, raw_event: Any | None) -> None:
        self._raw_event = raw_event
        self._skipped = False

    def CanVeto(self) -> bool:
        method = getattr(self._raw_event, "CanVeto", None)
        return bool(method()) if callable(method) else False

    def Veto(self) -> None:
        method = getattr(self._raw_event, "Veto", None)
        if callable(method):
            method()

    def Skip(self) -> None:
        self._skipped = True
        method = getattr(self._raw_event, "Skip", None)
        if callable(method):
            method()



def _create_simplebook(wx_module: Any, parent: Any) -> Any:
    simplebook_cls = getattr(wx_module, "Simplebook", None)
    if simplebook_cls is None:
        return _SimplebookFallback(wx_module, parent)
    return simplebook_cls(parent)



def build_main_frame(main_view: Any) -> Any:
    wx = main_view._wx
    frame = wx.Frame(None, title="WaveHelm")
    frame.SetSize((1280, 760))
    frame.SetMinSize((1024, 640))

    container = wx.Panel(frame)
    root_sizer = wx.BoxSizer(wx.VERTICAL)
    header = build_header(main_view, container)
    center = build_center_area(main_view, container)
    footer = build_footer(main_view, container)

    root_sizer.Add(header, 0, wx.ALL | wx.EXPAND, 0)
    root_sizer.Add(center, 1, wx.ALL | wx.EXPAND, 0)
    root_sizer.Add(footer, 0, wx.ALL | wx.EXPAND, 0)
    container.SetSizer(root_sizer)
    frame.Bind(wx.EVT_CLOSE, main_view._handle_close_event)
    centre = getattr(frame, "Centre", None)
    if callable(centre):
        centre()
    return frame



def build_header(main_view: Any, parent: Any) -> Any:
    """Build the main header without migration-status helper text.

    Edge cases handled:
    1. The header must remain valid when no secondary status label is present.
    2. Theme/localization refreshes must tolerate ``_status_label`` being ``None``.
    3. The top bar must keep the WaveHelm title aligned after shell-only text removal.
    """
    wx = main_view._wx
    panel = wx.Panel(parent)
    sizer = wx.BoxSizer(wx.HORIZONTAL)
    title = wx.StaticText(panel, label="WaveHelm")
    sizer.Add(title, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 12)
    panel.SetSizer(sizer)
    main_view._title_label = title
    main_view._status_label = None
    return panel



def build_center_area(main_view: Any, parent: Any) -> Any:
    wx = main_view._wx
    panel = wx.Panel(parent)
    sizer = wx.BoxSizer(wx.HORIZONTAL)
    sidebar = build_sidebar(main_view, panel)
    content = build_content_area(main_view, panel)
    sizer.Add(sidebar, 0, wx.ALL | wx.EXPAND, 0)
    sizer.Add(content, 1, wx.ALL | wx.EXPAND, 0)
    panel.SetSizer(sizer)
    return panel



def build_sidebar(main_view: Any, parent: Any) -> Any:
    wx = main_view._wx
    panel = wx.Panel(parent)
    sizer = wx.BoxSizer(wx.VERTICAL)
    for view_name, title in VIEW_LABELS:
        button = wx.Button(panel, label=title)
        button.Bind(wx.EVT_BUTTON, main_view._make_sidebar_handler(view_name))
        sizer.Add(button, 0, wx.ALL | wx.EXPAND, 8)
        main_view._sidebar_buttons[view_name] = button
    panel.SetSizer(sizer)
    return panel



def build_content_area(main_view: Any, parent: Any) -> Any:
    wx = main_view._wx
    panel = wx.Panel(parent)
    sizer = wx.BoxSizer(wx.VERTICAL)
    main_view._content_book = _create_simplebook(wx, panel)
    sizer.Add(main_view._content_book, 1, wx.ALL | wx.EXPAND, 0)
    panel.SetSizer(sizer)
    return panel



def build_footer(main_view: Any, parent: Any) -> Any:
    """Build the footer without migration-status helper text.

    Edge cases handled:
    1. The footer must stay compact when the shell-status line is removed.
    2. Mini player layout must remain expandable without a preceding text row.
    3. Theme/localization refreshes must tolerate ``_footer_label`` being ``None``.
    """
    wx = main_view._wx
    panel = wx.Panel(parent)
    sizer = wx.BoxSizer(wx.VERTICAL)
    mini_player = MiniPlayer(panel, **main_view._dependencies)
    sizer.Add(mini_player.panel, 0, wx.ALL | wx.EXPAND, 4)
    panel.SetSizer(sizer)
    main_view._footer_label = None
    main_view._mini_player = mini_player
    return panel



def create_placeholder_pages(main_view: Any) -> None:
    for view_name, title in VIEW_LABELS:
        page = build_runtime_page(main_view, view_name, title)
        main_view._content_book.AddPage(page.panel, title)
        main_view._pages[view_name] = page


def build_runtime_page(main_view: Any, view_name: str, title: str) -> WxViewPage:
    widget = create_page_widget(main_view, view_name)
    if widget is None:
        return build_placeholder_page(main_view, view_name, title)
    return WxViewPage(name=view_name, title=title, panel=widget.panel, label=getattr(widget, 'title_label', None))



def build_placeholder_page(main_view: Any, view_name: str, title: str) -> WxViewPage:
    wx = main_view._wx
    panel = wx.Panel(main_view._content_book)
    sizer = create_flow_sizer(wx)
    headline = wx.StaticText(panel, label=title)
    description = wx.StaticText(
        panel,
        label=f"{title} is not ported yet. This wx shell keeps navigation and lifecycle stable during migration.",
    )
    sizer.Add(headline, 0, wx.ALL | wx.EXPAND, 16)
    sizer.Add(description, 0, wx.ALL | wx.EXPAND, 16)
    panel.SetSizer(sizer)
    return WxViewPage(name=view_name, title=title, panel=panel, label=headline)



def switch_page(main_view: Any, view_name: str) -> None:
    if view_name not in main_view._pages:
        raise KeyError(f"Unknown wx main view page: {view_name}")
    page_names = list(main_view._pages.keys())
    main_view._content_book.SetSelection(page_names.index(view_name))
    main_view._current_view = view_name
    main_view.view_changed.emit(view_name)



def update_header_status(main_view: Any) -> None:
    """No-op placeholder kept for compatibility with existing shell wiring."""
    _ = main_view
    return



def wrap_close_event(raw_event: Any | None) -> _CloseEventAdapter:
    return _CloseEventAdapter(raw_event)
