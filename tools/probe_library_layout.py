"""Isolated real-wx Library geometry probe; no player, media, DB or user settings.

The default parent bounds the child to 90 seconds and preserves partial evidence.
Only a Windows interactive session can provide native qualification for this tool.
Font multipliers exercise native metrics, not a simulation of monitor DPI changes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCHEMA = 'wavehelm-native-library-layout-v1'
CONTROL_NAMES = ('search_label', 'search_text', 'filter_label', 'filter_choice',
                 'sort_label', 'sort_choice')
SOURCE_PATHS = (
    'src/ui_wx/common.py', 'src/ui_wx/layout_support.py',
    'src/ui_wx/library_view.py', 'src/ui_wx/library_view_layout.py',
    'src/ui_wx/main_view_shell.py', 'src/ui_wx/view_presentation_support.py',
    'src/ui_wx/collection_view_support.py', 'tools/probe_library_layout.py',
    'src/locales/en.json', 'src/locales/it.json',
    'src/locales/es.json', 'src/locales/fr.json',
)
SIZES = ((1280, 680), (1024, 680), (880, 680), (880, 900),
         (1024, 680), (1280, 680))


def rectangle_findings(client: tuple[int, int], rects: dict[str, list[int]]) -> list[str]:
    """Independent constraints on rectangles read from real wx, not estimated layout."""
    findings = []
    for name, (x, y, w, h) in rects.items():
        if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > client[0] or y + h > client[1]:
            findings.append(f'out-of-client:{name}')
    items = list(rects.items())
    for i, (a, (x, y, w, h)) in enumerate(items):
        for b, (u, v, s, t) in items[i + 1:]:
            if max(x, u) < min(x + w, u + s) and max(y, v) < min(y + h, v + t):
                findings.append(f'overlap:{a}:{b}')
    return findings


class ProbeLocale:
    def __init__(self) -> None:
        self.language = 'en'
        self.texts: dict[str, str] = {}

    def set_language(self, language: str) -> None:
        self.language = language
        self.texts = json.loads((ROOT / 'src/locales' / f'{language}.json').read_text(encoding='utf-8'))

    def get_text(self, key: str, default: str | None = None, **kwargs: object) -> str:
        text = self.texts.get(key, default or key)
        return text.format(**kwargs) if kwargs else text


def _source_hashes() -> dict[str, str]:
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCE_PATHS}


def _shell(wx):
    from src.ui_wx.main_view_shell import build_center_area
    frame = wx.Frame(None, title='WaveHelm - Library layout verification')
    container = wx.Panel(frame)
    host = SimpleNamespace(_wx=wx, _sidebar_buttons={}, _make_sidebar_handler=lambda name: lambda event: None)
    center = build_center_area(host, container)
    sizer = wx.BoxSizer(wx.VERTICAL)
    sizer.Add(center, 1, wx.EXPAND)
    container.SetSizer(sizer)
    other = wx.Panel(host._content_book)
    hidden_sizer = wx.BoxSizer(wx.HORIZONTAL)
    hidden_sizer.Add(wx.StaticText(other, label='X' * 240), 0, wx.ALL, 8)
    other.SetSizer(hidden_sizer)
    host._content_book.AddPage(other, 'Other page')
    return frame, host


def _inspect(wx, view, expected_ids: list[int], focus_before) -> dict:
    controls = [getattr(view, name) for name in CONTROL_NAMES]
    rects = {name: list(control.GetRect()) for name, control in zip(CONTROL_NAMES, controls)}
    rects['table'] = list(view.table.GetRect())
    client = tuple(view.panel.GetClientSize())
    findings = rectangle_findings(client, rects)
    if [control.GetId() for control in controls] != expected_ids:
        findings.append('controls-recreated')
    if view.search_text.GetValue() != 'layout probe' or view.filter_choice.GetSelection() != 2 or view.sort_choice.GetSelection() != 3:
        findings.append('input-state-changed')
    if focus_before is view.search_text and wx.Window.FindFocus() is not view.search_text:
        findings.append('search-focus-lost')
    ancestors, current = [], view.panel
    for _ in range(8):
        if current is None:
            break
        area, virtual = tuple(current.GetClientSize()), tuple(current.GetVirtualSize())
        ancestors.append({'type': type(current).__name__, 'client': area, 'virtual': virtual})
        if virtual[0] > area[0]:
            findings.append(f'virtual-width:{type(current).__name__}')
        current = current.GetParent()
    return {'client': client, 'rectangles': rects, 'ancestors': ancestors,
            'focus_checked': focus_before is view.search_text, 'findings': findings}


def _font_controls(view) -> list:
    return [getattr(view, name) for name in CONTROL_NAMES] + [
        view.add_files_button, view.add_folder_button, view.refresh_button,
        view.play_button, view.favorite_button, view.remove_button,
    ]


def _measure_cases(wx, app, frame, host, view, locale, report: dict) -> None:
    controls = _font_controls(view)
    original_fonts = [wx.Font(control.GetFont()) for control in controls]
    ids = [getattr(view, name).GetId() for name in CONTROL_NAMES]
    for factor in (1.0, 1.5):
        for control, original in zip(controls, original_fonts):
            font = wx.Font(original)
            font.SetFractionalPointSize(original.GetFractionalPointSize() * factor)
            control.SetFont(font)
        for language in ('en', 'it', 'fr', 'es', 'en'):
            locale.set_language(language)
            view.update_localization()
            # Hidden-page roundtrip uses the real Simplebook, not a reconstructed page.
            host._content_book.SetSelection(0)
            host._content_book.SetSelection(1)
            view.search_text.SetFocus()
            for size in SIZES:
                before = wx.Window.FindFocus()
                frame.SetClientSize(frame.FromDIP(size))
                for _ in range(3):
                    app.Yield(True)
                case = _inspect(wx, view, ids, before)
                case.update(language=language, font_factor=factor, requested_client_dip=size)
                report['cases'].append(case)


def run_gui(report: dict) -> None:
    import wx
    from src.ui_wx.library_view import LibraryView
    from src.ui_wx.common import autosize_labeled_control
    app = wx.App(False)
    frame, host = _shell(wx)
    locale = ProbeLocale()
    locale.set_language('en')
    view = None
    try:
        view = LibraryView(host._content_book, localization_manager=locale)
        host._content_book.AddPage(view.panel, 'Library', select=True)
        view.search_text.ChangeValue('layout probe')
        view.filter_choice.SetSelection(2)
        view.sort_choice.SetSelection(3)
        report.update(wx_version=wx.version(), dpi=list(frame.GetDPI()), platform=sys.platform)
        frame.SetClientSize(frame.FromDIP(SIZES[0]))
        frame.Show()
        _measure_cases(wx, app, frame, host, view, locale, report)
        scrolled = wx.ScrolledWindow(host._content_book)
        scrolled.SetScrollRate(10, 10)
        content = wx.BoxSizer(wx.VERTICAL)
        button = wx.Button(scrolled, label='Scroll extent control')
        content.Add(button, 0)
        content.AddSpacer(2000)
        scrolled.SetSizer(content)
        scrolled.SetClientSize((300, 200))
        autosize_labeled_control(button)
        report['scroll_extent_preserved'] = scrolled.GetVirtualSize().height > scrolled.GetClientSize().height
        scrolled.Destroy()
    finally:
        if view is not None:
            view.shutdown()
        frame.Destroy()
        app.Yield(True)


def _worker(output: Path) -> int:
    report = {'schema': SCHEMA, 'status': 'FAIL', 'cases': [], 'source_before': _source_hashes()}
    try:
        if sys.platform != 'win32':
            raise RuntimeError('Native Windows graphical session required; not executed on this host.')
        run_gui(report)
        report['source_after'] = _source_hashes()
        if (len(report['cases']) == 60 and not any(c['findings'] for c in report['cases'])
                and report['scroll_extent_preserved'] and report['source_before'] == report['source_after']):
            report['status'] = 'NATIVE_LIBRARY_LAYOUT_PASS'
    except (ImportError, OSError, RuntimeError, AttributeError, TypeError, ValueError, AssertionError):
        report['exception'] = traceback.format_exc(limit=12)
    finally:
        (output / 'geometry.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if report['status'] == 'NATIVE_LIBRARY_LAYOUT_PASS' else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    output = args.output.resolve()
    if output.is_relative_to(ROOT):
        parser.error('Evidence must be outside the source tree.')
    output.mkdir(parents=True, exist_ok=True)
    if args.worker:
        return _worker(output)
    if any(output.iterdir()):
        parser.error('Use a new empty evidence directory; previous evidence is preserved.')
    command = [sys.executable, '-I', '-B', str(Path(__file__).resolve()), '--worker', '--output', str(output)]
    with (output / 'stdout.log').open('w', encoding='utf-8') as out, (output / 'stderr.log').open('w', encoding='utf-8') as err:
        try:
            result = subprocess.run(command, stdout=out, stderr=err, timeout=90, check=False)
            code = result.returncode
        except subprocess.TimeoutExpired:
            code = 124
    child_code = code
    if code == 0:
        try:
            geometry = json.loads((output / 'geometry.json').read_text(encoding='utf-8'))
            if geometry.get('status') != 'NATIVE_LIBRARY_LAYOUT_PASS' or len(geometry.get('cases', [])) != 60:
                code = 1
        except (OSError, ValueError, TypeError, AttributeError):
            code = 1
    record = {'schema': SCHEMA, 'command': command, 'exit_code': code, 'child_exit_code': child_code,
              'scope': 'real wx rectangles; no media/COM/DB playback; not release approval'}
    (output / 'command.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    manifest = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in output.iterdir() if path.is_file()}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps(record, indent=2))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
