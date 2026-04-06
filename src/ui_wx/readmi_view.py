from __future__ import annotations

import json
import logging
import re
from html import unescape
from importlib import resources
from typing import Any

from src.ui_wx.common import (
    apply_colors,
    get_current_language,
    get_localized_text,
    get_theme_colors,
    register_callback,
    set_label_text,
)

logger = logging.getLogger(__name__)

RESOURCE_EXCEPTIONS = (AttributeError, FileNotFoundError, ModuleNotFoundError, RuntimeError, TypeError, ValueError)
_FALLBACK_MESSAGES = {
    'en': 'Unable to load the user manual resource.',
    'it': 'Impossibile caricare la risorsa del manuale utente.',
    'es': 'No se pudo cargar el recurso del manual del usuario.',
    'fr': "Impossible de charger la ressource du manuel d\'utilisation.",
}
_TAG_BREAK_RE = re.compile(r'<\s*(?:br|/p|/h1|/h2|/h3|/li|/section|/div)\s*/?\s*>', re.IGNORECASE)
_TAG_OPEN_LI_RE = re.compile(r'<\s*li\b[^>]*>', re.IGNORECASE)
_TAG_OPEN_H_RE = re.compile(r'<\s*h([1-3])\b[^>]*>', re.IGNORECASE)
_TAG_RE = re.compile(r'<[^>]+>')
_WHITESPACE_RE = re.compile(r'[\t\r\f\v ]+')
_BLANK_LINE_RE = re.compile(r'\n{3,}')
_BODY_RE = re.compile(r'<\s*body\b[^>]*>(.*?)<\s*/body\s*>', re.IGNORECASE | re.DOTALL)
_STYLE_RE = re.compile(r'<\s*style\b[^>]*>.*?<\s*/style\s*>', re.IGNORECASE | re.DOTALL)
_SCRIPT_RE = re.compile(r'<\s*script\b[^>]*>.*?<\s*/script\s*>', re.IGNORECASE | re.DOTALL)


def _read_manual_resource(filename: str) -> str:
    resource = resources.files('src.resources.manual').joinpath(filename)
    return resource.read_text(encoding='utf-8')


def load_manual_manifest() -> dict[str, Any]:
    try:
        return json.loads(_read_manual_resource('manual_manifest.json'))
    except (json.JSONDecodeError, *RESOURCE_EXCEPTIONS):
        logger.debug('Failed loading wx manual manifest.', exc_info=True)
        return {'default_language': 'en', 'manuals': {}}


def _normalize_manual_plain_text(text: str) -> str:
    normalized_lines = []
    for raw_line in str(text or '').splitlines():
        compact = _WHITESPACE_RE.sub(' ', raw_line).strip()
        normalized_lines.append(compact)
    joined = '\n'.join(normalized_lines)
    joined = _BLANK_LINE_RE.sub('\n\n', joined)
    return joined.strip()


def _extract_manual_body_html(html_text: str) -> str:
    body = str(html_text or '')
    match = _BODY_RE.search(body)
    if match is not None:
        body = match.group(1)
    body = _STYLE_RE.sub('', body)
    body = _SCRIPT_RE.sub('', body)
    return body.strip()


def _html_manual_to_plain_text(html_text: str) -> str:
    body = _extract_manual_body_html(html_text)
    body = _TAG_OPEN_LI_RE.sub('• ', body)
    body = _TAG_OPEN_H_RE.sub(lambda match: '\n' + ('#' * int(match.group(1))) + ' ', body)
    body = _TAG_BREAK_RE.sub('\n', body)
    body = _TAG_RE.sub('', body)
    body = unescape(body)
    return _normalize_manual_plain_text(body)


def _build_themed_manual_html(html_text: str, colors: dict[str, str]) -> str:
    body_html = _extract_manual_body_html(html_text)
    page_background = colors.get('bg_color') or colors.get('panel_bg') or '#121212'
    surface_background = colors.get('panel_bg') or colors.get('bg_color') or '#1b1b1b'
    text_color = colors.get('text_color') or '#f3f3f3'
    accent_color = colors.get('selection_bg') or colors.get('button_color') or '#4f8cff'
    muted_color = colors.get('button_color') or '#7f8c8d'

    return f'''<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    html, body {{
      margin: 0;
      padding: 0;
      background: {page_background};
      color: {text_color};
      font-family: "Segoe UI", Arial, sans-serif;
      line-height: 1.62;
    }}
    body {{
      padding: 0;
    }}
    .manual-shell {{
      max-width: 980px;
      margin: 0 auto;
      padding: 28px 30px 40px;
      box-sizing: border-box;
      background: {surface_background};
    }}
    h1 {{
      margin: 0 0 10px;
      font-size: 28px;
      color: {accent_color};
    }}
    h2 {{
      margin: 28px 0 10px;
      padding-top: 2px;
      font-size: 19px;
      color: {accent_color};
      border-top: 1px solid {muted_color};
    }}
    h3 {{
      margin: 20px 0 8px;
      font-size: 16px;
      color: {accent_color};
    }}
    p {{
      margin: 0 0 12px;
      font-size: 14px;
    }}
    p.meta {{
      color: {text_color};
      opacity: 0.78;
      font-size: 12px;
      margin-bottom: 12px;
    }}
    p.lead {{
      font-size: 15px;
      margin-bottom: 18px;
    }}
    section {{
      margin: 0 0 8px;
    }}
    ul, ol {{
      margin: 0 0 14px 0;
      padding-left: 22px;
    }}
    li {{
      margin: 0 0 8px;
      font-size: 14px;
    }}
    strong {{
      color: {text_color};
    }}
    code {{
      font-family: Consolas, "Courier New", monospace;
      background: {page_background};
      padding: 1px 4px;
      border-radius: 4px;
    }}
  </style>
</head>
<body>
  <div class="manual-shell">{body_html}</div>
</body>
</html>
'''


class ReadmiView:
    """wx-based localized manual viewer with theme-aware rich formatting.

    Edge cases handled deterministically:
    1. Missing manual resources fall back to an inline localized message instead of leaving the page blank.
    2. Unsupported languages fall back to the manifest default language.
    3. Theme refreshes re-render the current manual so heading hierarchy and paragraph spacing remain readable in the active theme.
    """

    def __init__(self, parent: Any, localization_manager: Any = None, theme_manager: Any = None, **_: Any) -> None:
        self.localization_manager = localization_manager
        self.theme_manager = theme_manager
        self._wx = self._import_wx_module()
        self.panel = self._wx.Panel(parent)
        self.panel.owner = self
        self._manual_manifest = load_manual_manifest()
        self._browser_mode = 'text'
        self._current_language = ''
        self._current_manual_html = ''
        self._current_text = ''
        self.title_label = self._wx.StaticText(self.panel, label='')
        self.browser = self._build_browser()
        self._build_ui()
        self._register_callbacks()
        self.update_localization()
        self.update_theme_colors()

    @staticmethod
    def _import_wx_module() -> Any:
        import importlib
        return importlib.import_module('wx')

    def _build_browser(self) -> Any:
        html_module = getattr(self._wx, 'html', None)
        html_window_cls = getattr(html_module, 'HtmlWindow', None) if html_module is not None else None
        if callable(html_window_cls):
            self._browser_mode = 'html'
            try:
                return html_window_cls(self.panel)
            except TypeError:
                logger.debug('Unable to instantiate wx HtmlWindow for ReadmiView, falling back to text browser.', exc_info=True)
                self._browser_mode = 'text'

        text_ctrl_cls = getattr(self._wx, 'TextCtrl', None)
        if callable(text_ctrl_cls):
            style = 0
            style |= getattr(self._wx, 'TE_MULTILINE', 0)
            style |= getattr(self._wx, 'TE_READONLY', 0)
            style |= getattr(self._wx, 'TE_RICH2', 0)
            try:
                browser = text_ctrl_cls(self.panel, value='', style=style)
            except TypeError:
                browser = text_ctrl_cls(self.panel, value='')
            editable_setter = getattr(browser, 'SetEditable', None)
            if callable(editable_setter):
                try:
                    editable_setter(False)
                except RESOURCE_EXCEPTIONS:
                    logger.debug('Unable to disable editing on wx ReadmiView browser.', exc_info=True)
            return browser

        return self._wx.StaticText(self.panel, label='')

    def _build_ui(self) -> None:
        root = self._wx.BoxSizer(self._wx.VERTICAL)
        root.Add(self.title_label, 0, self._wx.ALL | self._wx.EXPAND, 12)
        root.Add(self.browser, 1, self._wx.ALL | self._wx.EXPAND, 12)
        self.panel.SetSizer(root)

    def _register_callbacks(self) -> None:
        register_callback(
            self.theme_manager,
            'register_theme_change_callback',
            self.update_theme_colors,
            logger=logger,
            message='Unable to register wx ReadmiView theme callback.',
        )
        register_callback(
            self.localization_manager,
            'register_language_change_callback',
            self.update_localization,
            logger=logger,
            message='Unable to register wx ReadmiView language callback.',
        )

    def _resolve_manual_filename(self, language: str) -> str | None:
        manuals = self._manual_manifest.get('manuals', {})
        if not isinstance(manuals, dict):
            manuals = {}
        normalized = str(language or '').lower()
        fallback = str(self._manual_manifest.get('default_language', 'en') or 'en').lower()
        return manuals.get(normalized) or manuals.get(normalized.split('-')[0]) or manuals.get(fallback)

    def _build_unavailable_html(self, language: str) -> str:
        normalized = str(language or 'en').lower().split('-')[0]
        message = _FALLBACK_MESSAGES.get(normalized, _FALLBACK_MESSAGES['en'])
        title = get_localized_text(self.localization_manager, 'nav_readmi', 'Readmi')
        return (
            '<html><body>'
            f'<h1>{title}</h1>'
            f'<p class="lead">{message}</p>'
            '</body></html>'
        )

    def _load_manual_html(self, language: str) -> str:
        filename = self._resolve_manual_filename(language)
        if not filename:
            return self._build_unavailable_html(language)
        try:
            return _read_manual_resource(str(filename))
        except RESOURCE_EXCEPTIONS:
            logger.debug('Failed reading wx manual resource %s.', filename, exc_info=True)
            return self._build_unavailable_html(language)

    def _render_browser_content(self) -> None:
        if self._browser_mode == 'html':
            set_page = getattr(self.browser, 'SetPage', None)
            if callable(set_page):
                themed_html = _build_themed_manual_html(self._current_manual_html, get_theme_colors(self.theme_manager))
                set_page(themed_html)
                return
        setter = getattr(self.browser, 'SetValue', None)
        if callable(setter):
            setter(self._current_text)
            return
        set_label_text(self.browser, self._current_text)

    def update_localization(self, *_: Any) -> None:
        set_label_text(self.title_label, get_localized_text(self.localization_manager, 'nav_readmi', 'Readmi'))
        language = get_current_language(self.localization_manager, self._manual_manifest.get('default_language', 'en'))
        self._current_language = language
        self._current_manual_html = self._load_manual_html(language)
        self._current_text = _html_manual_to_plain_text(self._current_manual_html)
        self._render_browser_content()

    def update_theme_colors(self, *_: Any) -> None:
        colors = get_theme_colors(self.theme_manager)
        background = colors.get('panel_bg') or colors.get('bg_color')
        foreground = colors.get('text_color')
        apply_colors(self.panel, background=background, foreground=foreground)
        apply_colors(self.title_label, background=background, foreground=foreground)
        apply_colors(self.browser, background=background, foreground=foreground)
        self._render_browser_content()

    def shutdown(self) -> None:
        return None

    def show(self) -> None:
        self.panel.Show(True)
