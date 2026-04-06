from __future__ import annotations

import importlib
from typing import Any, Callable

logger = __import__('logging').getLogger(__name__)

DispatcherCallback = Callable[[], Any]
SUPPORTED_UI_BACKENDS = frozenset({"wx"})


class UiDispatcher:
    """Abstract dispatcher boundary used by the event bus and UI callbacks."""

    def dispatch(self, callback: DispatcherCallback) -> None:  # pragma: no cover - interface only
        raise NotImplementedError


class WxUiDispatcher(UiDispatcher):
    """Dispatch callbacks onto the wx UI loop using ``wx.CallAfter``."""

    def __init__(self, root: Any = None, *, wx_module: Any | None = None) -> None:
        self._root = root
        self._wx = wx_module if wx_module is not None else _import_wx_module()
        call_after = getattr(self._wx, "CallAfter", None)
        if not callable(call_after):
            raise RuntimeError("wx.CallAfter is not available for the UI dispatcher.")
        self._call_after = call_after

    def dispatch(self, callback: DispatcherCallback) -> None:
        if callable(callback):
            self._call_after(callback)


def _import_wx_module() -> Any:
    try:
        return importlib.import_module("wx")
    except ImportError as exc:
        raise RuntimeError("Unable to import wx for the UI dispatcher.") from exc


def _normalize_ui_backend_name(ui_backend: str | None) -> str | None:
    if ui_backend is None:
        return None
    normalized = ui_backend.strip().lower()
    if not normalized:
        raise ValueError("UI backend cannot be blank for the UI dispatcher.")
    if normalized != "wx":
        raise ValueError("Unsupported UI dispatcher backend '" + normalized + "'. Supported values: wx.")
    return normalized


def resolve_ui_backend(root: Any = None, *, ui_backend: str | None = None) -> str:
    normalized = _normalize_ui_backend_name(ui_backend)
    if normalized is not None:
        return normalized
    return "wx"


def create_ui_dispatcher(root: Any = None, *, ui_backend: str | None = None) -> UiDispatcher:
    """Create the framework-specific UI dispatcher used by the application shell.

    Edge cases handled deterministically:
    1. An explicit backend selection is accepted only for the wx runtime.
    2. Blank or unsupported backend names fail fast before the event bus registers the dispatcher.
    3. Missing ``wx.CallAfter`` raises ``RuntimeError`` instead of silently degrading.
    """
    resolve_ui_backend(root, ui_backend=ui_backend)
    return WxUiDispatcher(root)


def build_ui_dispatcher(root: Any = None, *, ui_backend: str | None = None) -> Callable[[DispatcherCallback], None]:
    return create_ui_dispatcher(root, ui_backend=ui_backend).dispatch


__all__ = [
    "SUPPORTED_UI_BACKENDS",
    "UiDispatcher",
    "WxUiDispatcher",
    "build_ui_dispatcher",
    "create_ui_dispatcher",
    "resolve_ui_backend",
]
