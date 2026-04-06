from __future__ import annotations

import importlib
import logging
import math
import threading
from typing import Any, Callable

from src.audio.audio_event_models import AudioEventType
from src.ui_wx.common import (
    apply_colors,
    autosize_choice_control,
    create_flow_sizer,
    get_localized_text,
    get_theme_colors,
    register_callback,
    set_label_text,
    unregister_callback,
)

logger = logging.getLogger(__name__)

VISUALIZER_VIEW_EXCEPTIONS = (AttributeError, ImportError, RuntimeError, TypeError, ValueError)


def _read_widget_text(widget: Any) -> str:
    getter = getattr(widget, 'GetLabel', None)
    if callable(getter):
        try:
            value = getter()
            return str(value or '')
        except VISUALIZER_VIEW_EXCEPTIONS:
            logger.debug('Unable to read widget label via GetLabel.', exc_info=True)
    value = getattr(widget, 'label', '')
    return str(value or '')



def _visualizer_percent_ticks() -> tuple[list[float], list[str]]:
    return ([0.0, 0.25, 0.50, 0.75, 1.0], ['0%', '25%', '50%', '75%', '100%'])



def _build_band_center_frequencies(
    band_count: int,
    minimum_hz: float = 20.0,
    maximum_hz: float = 15000.0,
    sample_rate_hz: float = 44100.0,
) -> list[float]:
    normalized_band_count = max(1, int(band_count))
    nyquist_hz = max(1.0, float(sample_rate_hz) / 2.0)
    lower_hz = max(1.0, float(minimum_hz))
    upper_hz = min(float(maximum_hz), nyquist_hz)
    if upper_hz <= lower_hz:
        return [lower_hz] * normalized_band_count
    if normalized_band_count == 1:
        return [lower_hz]
    log_min = math.log10(lower_hz)
    log_max = math.log10(upper_hz)
    step = (log_max - log_min) / float(normalized_band_count - 1)
    return [10.0 ** (log_min + (step * index)) for index in range(normalized_band_count)]



def _format_frequency_label(frequency_hz: float) -> str:
    bounded_frequency = max(1.0, float(frequency_hz))
    if bounded_frequency >= 1000.0:
        kilohertz = bounded_frequency / 1000.0
        if abs(kilohertz - round(kilohertz)) < 0.05:
            return f'{int(round(kilohertz))} kHz'
        if kilohertz >= 10.0:
            return f'{kilohertz:.1f} kHz'
        return f'{kilohertz:.1f} kHz'
    return f'{int(round(bounded_frequency))} Hz'



def _band_frequency_labels(band_count: int) -> list[str]:
    return [_format_frequency_label(frequency) for frequency in _build_band_center_frequencies(band_count)]



def _bar_color_for_level(level: float) -> str:
    bounded_level = max(0.0, min(1.0, float(level)))
    if bounded_level < 0.25:
        return '#2ecc71'
    if bounded_level < 0.50:
        return '#f1c40f'
    if bounded_level < 0.75:
        return '#e67e22'
    return '#e74c3c'


class _FallbackSpectrumCanvas:
    def __init__(self, wx_module: Any, parent: Any, band_count: int) -> None:
        self.backend_name = 'fallback'
        self.widget = wx_module.Panel(parent)
        self._label = wx_module.StaticText(self.widget, label='wxAgg backend unavailable.')
        sizer = wx_module.BoxSizer(wx_module.VERTICAL)
        sizer.Add(self._label, 0, wx_module.ALL | wx_module.EXPAND, 10)
        self.widget.SetSizer(sizer)
        self.last_values: list[float] = [0.0] * max(1, int(band_count))

    def resize_band_count(self, band_count: int) -> None:
        self.last_values = [0.0] * max(1, int(band_count))

    def update(self, values: list[float]) -> None:
        self.last_values = [float(value) for value in values]
        set_label_text(self._label, f'Visualizer fallback backend active ({len(self.last_values)} bands).')

    def apply_theme(self, colors: dict[str, str]) -> None:
        background = colors.get('panel_bg') or colors.get('bg_color')
        foreground = colors.get('text_color')
        apply_colors(self.widget, background=background, foreground=foreground)
        apply_colors(self._label, background=background, foreground=foreground)

    def destroy(self) -> None:
        return None


class _WxAggSpectrumCanvas:
    def __init__(self, parent: Any, band_count: int) -> None:
        matplotlib = importlib.import_module('matplotlib')
        matplotlib.use('WXAgg', force=True)
        figure_module = importlib.import_module('matplotlib.figure')
        backend_module = importlib.import_module('matplotlib.backends.backend_wxagg')
        self._figure = figure_module.Figure(figsize=(8.0, 3.2), dpi=100)
        self._axes = self._figure.add_subplot(111)
        canvas_class = getattr(backend_module, 'FigureCanvasWxAgg')
        self.widget = canvas_class(parent, -1, self._figure)
        self.backend_name = 'WXAgg'
        self._bar_artists: list[Any] = []
        self.resize_band_count(band_count)

    def resize_band_count(self, band_count: int) -> None:
        band_total = max(1, int(band_count))
        label_font_size = self._label_font_size_for_band_count(band_total)
        self._axes.clear()
        self._axes.set_ylim(0.0, 1.0)
        self._axes.set_xlim(-0.5, max(0, band_total - 0.5))
        percentage_ticks, percentage_labels = _visualizer_percent_ticks()
        self._axes.set_yticks(percentage_ticks)
        self._axes.set_yticklabels(percentage_labels, fontsize=label_font_size)
        self._axes.set_xticks(list(range(band_total)))
        self._axes.set_xticklabels(
            _band_frequency_labels(band_total),
            rotation=90,
            fontsize=label_font_size,
            ha='center',
            va='top',
            rotation_mode='anchor',
        )
        self._axes.tick_params(axis='x', pad=1, length=0)
        self._axes.tick_params(axis='y', pad=4)
        self._axes.grid(axis='y', linestyle='--', linewidth=0.6, alpha=0.35)
        self._axes.spines['top'].set_visible(False)
        self._axes.spines['right'].set_visible(False)
        self._axes.set_axisbelow(True)
        self._figure.subplots_adjust(left=0.13, right=0.99, top=0.98, bottom=self._bottom_margin_for_band_count(band_total))
        self._bar_artists = list(
            self._axes.bar(
                range(band_total),
                [0.0] * band_total,
                width=0.82,
                linewidth=0.6,
                edgecolor='#0f0f0f',
            )
        )
        self.update([0.0] * band_total)

    def update(self, values: list[float]) -> None:
        if len(values) != len(self._bar_artists):
            self.resize_band_count(len(values))
            return
        for artist, value in zip(self._bar_artists, values):
            bounded_value = max(0.0, min(1.0, float(value)))
            setter = getattr(artist, 'set_height', None)
            if callable(setter):
                setter(bounded_value)
            set_facecolor = getattr(artist, 'set_facecolor', None)
            if callable(set_facecolor):
                set_facecolor(_bar_color_for_level(bounded_value))
        self._draw_idle()

    def apply_theme(self, colors: dict[str, str]) -> None:
        background = colors.get('panel_bg') or colors.get('bg_color') or '#161616'
        foreground = colors.get('text_color') or '#f4f4f4'
        grid_color = colors.get('button_color') or foreground
        self._figure.patch.set_facecolor(background)
        self._axes.set_facecolor(background)
        for spine in self._axes.spines.values():
            spine.set_color(foreground)
        self._axes.tick_params(colors=foreground)
        for line in self._axes.get_ygridlines():
            line.set_color(grid_color)
            line.set_alpha(0.25)
        self._draw_idle()

    def destroy(self) -> None:
        clear = getattr(self._figure, 'clear', None)
        if callable(clear):
            clear()

    @staticmethod
    def _label_font_size_for_band_count(band_count: int) -> int:
        if band_count >= 32:
            return 7
        return 9

    @staticmethod
    def _bottom_margin_for_band_count(band_count: int) -> float:
        if band_count >= 32:
            return 0.34
        return 0.24

    def _draw_idle(self) -> None:
        draw_idle = getattr(self.widget, 'draw_idle', None)
        if callable(draw_idle):
            draw_idle()
            return
        draw = getattr(self.widget, 'draw', None)
        if callable(draw):
            draw()


class _ExternalVisualizerWindow:
    """Detached visualizer host synchronized with the embedded launcher page.

    Edge cases handled deterministically:
    1. wx key-hook support can be absent in test doubles or lightweight runtimes, so fullscreen shortcuts are bound only when the runtime exposes the required hooks.
    2. Closing the detached frame must not recurse into repeated teardown, so the owner receives a single close notification guarded by the frame reference.
    3. Theme or localization refresh can happen before the plot backend is ready, so UI refresh paths update only controls that are already initialized.
    """

    def __init__(self, owner: 'VisualizerView') -> None:
        self.owner = owner
        self._wx = owner._wx
        self.frame = self._wx.Frame(owner._resolve_host_frame(), title='')
        self.panel = self._wx.Panel(self.frame)
        self.stop_button: Any = None
        self.band_choice: Any = None
        self.fullscreen_button: Any = None
        self.status_label: Any = None
        self.feedback_label: Any = None
        self.backend_label: Any = None
        self.plot_host: Any = None
        self.plot_sizer: Any = None
        self._plot_canvas: Any = None
        self._plot_backend_name = 'uninitialized'
        self._build_ui()
        self._bind_events()
        self._create_plot_backend(owner._band_count)
        self.update_band_choice(owner._band_count)
        self.update_localization()
        self.update_theme_colors()
        self.update_status(_read_widget_text(owner.status_label))
        self.update_feedback(_read_widget_text(owner.feedback_label))
        self._sync_plot(owner._last_values)
        self.frame.Show(True)
        self._raise_window_if_possible()

    def _build_ui(self) -> None:
        wx = self._wx
        root = wx.BoxSizer(wx.VERTICAL)
        toolbar = create_flow_sizer(wx)
        self.stop_button = wx.Button(self.panel, label='')
        self.band_choice = wx.Choice(self.panel)
        self.fullscreen_button = wx.Button(self.panel, label='')
        self.backend_label = wx.StaticText(self.panel, label='')
        self.status_label = wx.StaticText(self.panel, label='')
        self.plot_host = wx.Panel(self.panel)
        self.plot_sizer = wx.BoxSizer(wx.VERTICAL)
        self.plot_host.SetSizer(self.plot_sizer)
        self.feedback_label = wx.StaticText(self.panel, label='')
        band_items = ['16', '32']
        self.band_choice.SetItems(band_items)
        autosize_choice_control(self.band_choice, band_items)
        for control in (self.stop_button, self.band_choice, self.fullscreen_button, self.backend_label):
            toolbar.Add(control, 0, wx.ALL | wx.EXPAND, 6)
        root.Add(toolbar, 0, wx.ALL | wx.EXPAND, 0)
        root.Add(self.status_label, 0, wx.ALL | wx.EXPAND, 8)
        root.Add(self.plot_host, 1, wx.ALL | wx.EXPAND, 8)
        root.Add(self.feedback_label, 0, wx.ALL | wx.EXPAND, 8)
        self.panel.SetSizer(root)
        set_client_size = getattr(self.frame, 'SetClientSize', None)
        if callable(set_client_size):
            set_client_size((1040, 620))

    def _bind_events(self) -> None:
        wx = self._wx
        self.stop_button.Bind(wx.EVT_BUTTON, self.owner._on_toggle_visualizer)
        self.band_choice.Bind(wx.EVT_CHOICE, self._on_band_count_changed)
        self.fullscreen_button.Bind(wx.EVT_BUTTON, self._on_toggle_fullscreen)
        self.frame.Bind(wx.EVT_CLOSE, self._on_close)
        self._bind_fullscreen_shortcut()

    def _bind_fullscreen_shortcut(self) -> None:
        event_name = getattr(self._wx, 'EVT_CHAR_HOOK', None)
        f11_value = getattr(self._wx, 'WXK_F11', None)
        if event_name is None or f11_value is None:
            return
        self.frame.Bind(event_name, self._on_key_down)

    def _create_plot_backend(self, band_count: int) -> None:
        self._plot_canvas = self.owner._build_canvas_backend_for_parent(self.plot_host, band_count)
        self._plot_backend_name = getattr(self._plot_canvas, 'backend_name', 'fallback')
        self.plot_sizer.Add(self._plot_canvas.widget, 1, self._wx.ALL | self._wx.EXPAND, 0)
        set_label_text(self.backend_label, f'Backend: {self._plot_backend_name}')

    def _on_key_down(self, event: Any) -> None:
        key_code_getter = getattr(event, 'GetKeyCode', None)
        key_code = key_code_getter() if callable(key_code_getter) else None
        if key_code == getattr(self._wx, 'WXK_F11', None):
            self._on_toggle_fullscreen()
            return
        skipper = getattr(event, 'Skip', None)
        if callable(skipper):
            skipper()

    def _on_band_count_changed(self, _event: Any | None = None) -> None:
        self.owner._apply_band_count_from_string(self.band_choice.GetStringSelection())

    def _on_toggle_fullscreen(self, _event: Any | None = None) -> None:
        is_fullscreen = bool(getattr(self.frame, 'IsFullScreen', lambda: False)())
        show_fullscreen = getattr(self.frame, 'ShowFullScreen', None)
        if callable(show_fullscreen):
            show_fullscreen(not is_fullscreen)

    def _on_close(self, event: Any | None = None) -> None:
        self.owner._handle_external_window_closed(event)

    def _sync_plot(self, values: list[float]) -> None:
        if self._plot_canvas is None:
            return
        self._plot_canvas.resize_band_count(len(values))
        self._plot_canvas.update(values)

    def update_band_choice(self, band_count: int) -> None:
        if self.band_choice is not None:
            self.band_choice.SetStringSelection(str(max(1, int(band_count))))

    def update_band_count(self, band_count: int, values: list[float]) -> None:
        if self._plot_canvas is not None:
            self._plot_canvas.resize_band_count(band_count)
            self._plot_canvas.update(values)
        self.update_band_choice(band_count)

    def update_plot(self, values: list[float]) -> None:
        if self._plot_canvas is not None:
            self._plot_canvas.update(values)

    def update_status(self, message: str) -> None:
        set_label_text(self.status_label, message)

    def update_feedback(self, message: str) -> None:
        set_label_text(self.feedback_label, message)

    def update_localization(self) -> None:
        set_title = getattr(self.frame, 'SetTitle', None)
        if callable(set_title):
            set_title(self.owner._t('tab_visualizer', self.owner._t('audio_visualizer_title', 'Visualizer')))
        toggle_key = 'stop_visualizer_button' if self.owner._visualizer_on else 'start_visualizer_button'
        toggle_default = 'Stop Visualizer' if self.owner._visualizer_on else 'Start Visualizer'
        set_label_text(self.stop_button, self.owner._t(toggle_key, toggle_default))
        set_label_text(self.fullscreen_button, self.owner._t('visualizer_fullscreen', 'Fullscreen (F11)'))
        set_label_text(self.backend_label, f'Backend: {self._plot_backend_name}')

    def update_theme_colors(self) -> None:
        colors = get_theme_colors(self.owner.theme_manager)
        background = colors.get('panel_bg') or colors.get('bg_color')
        foreground = colors.get('text_color')
        accent = colors.get('button_color') or background
        for widget in [self.frame, self.panel, self.status_label, self.feedback_label, self.backend_label, self.plot_host]:
            apply_colors(widget, background=background, foreground=foreground)
        for widget in [self.stop_button, self.band_choice, self.fullscreen_button]:
            apply_colors(widget, background=accent, foreground=foreground)
        if self._plot_canvas is not None:
            self._plot_canvas.apply_theme(colors)

    def is_alive(self) -> bool:
        return self.frame is not None

    def close(self) -> None:
        if self.frame is None:
            return
        if self._plot_canvas is not None:
            self._plot_canvas.destroy()
        destroy = getattr(self.frame, 'Destroy', None)
        if callable(destroy):
            destroy()
        self._plot_canvas = None
        self.frame = None

    def _raise_window_if_possible(self) -> None:
        raise_window = getattr(self.frame, 'Raise', None)
        if callable(raise_window):
            raise_window()


class VisualizerView:
    """wx-based spectrum visualizer page.

    Edge cases handled deterministically:
    1. Spectrum payloads can be malformed or empty, so normalization falls back to zeroed bounded bands.
    2. Plot backend import can fail on headless or incomplete wx runtimes, so the UI keeps a fallback canvas instead of crashing.
    3. Event-bus callbacks can arrive during teardown, so timer rescheduling and UI updates stop once shutdown starts.
    """

    def __init__(
        self,
        parent: Any,
        audio_engine: Any = None,
        event_bus: Any = None,
        localization_manager: Any = None,
        theme_manager: Any = None,
        **_: Any,
    ) -> None:
        self._wx = self._import_wx_module()
        self.audio_engine = audio_engine
        self.event_bus = event_bus
        self.localization_manager = localization_manager
        self.theme_manager = theme_manager
        self.panel = self._wx.Panel(parent)
        self.panel.owner = self
        self._subscriptions: list[tuple[AudioEventType, Any]] = []
        self._is_shutting_down = False
        self._visualizer_on = False
        self._playback_active = False
        self._topmost_enabled = False
        self._band_count = 32
        self._ui_thread_ident = threading.get_ident()
        self._pending_values: list[float] | None = None
        self._last_values: list[float] = [0.0] * self._band_count
        self._render_timer: Any = None
        self._render_scheduled = False
        self._plot_canvas: Any = None
        self._plot_backend_name = 'uninitialized'
        self._status_token = 'status_stopped'
        self._external_window: _ExternalVisualizerWindow | None = None
        self._external_window_close_in_progress = False
        self._build_ui()
        self._bind_events()
        self._register_callbacks()
        self._subscribe_to_events()
        self._create_plot_backend()
        self.update_localization()
        self.update_theme_colors()
        self._set_status_token('status_stopped')
        self._refresh_status_label()

    @staticmethod
    def _import_wx_module() -> Any:
        try:
            return importlib.import_module('wx')
        except ImportError as exc:
            raise RuntimeError('wx is required to instantiate the wx VisualizerView.') from exc

    def _build_ui(self) -> None:
        wx = self._wx
        root = wx.BoxSizer(wx.VERTICAL)
        self.title_label = wx.StaticText(self.panel, label='')
        root.Add(self.title_label, 0, wx.ALL | wx.EXPAND, 10)
        toolbar = self._build_toolbar()
        root.Add(toolbar, 0, wx.ALL | wx.EXPAND, 0)
        self.status_label = wx.StaticText(self.panel, label='')
        root.Add(self.status_label, 0, wx.ALL | wx.EXPAND, 8)
        self.plot_host = wx.Panel(self.panel)
        self.plot_sizer = wx.BoxSizer(wx.VERTICAL)
        self.plot_host.SetSizer(self.plot_sizer)
        root.Add(self.plot_host, 1, wx.ALL | wx.EXPAND, 8)
        self.feedback_label = wx.StaticText(self.panel, label='')
        root.Add(self.feedback_label, 0, wx.ALL | wx.EXPAND, 8)
        self.panel.SetSizer(root)

    def _build_toolbar(self) -> Any:
        wx = self._wx
        sizer = create_flow_sizer(wx)
        self.toggle_button = wx.Button(self.panel, label='')
        self.external_window_button = wx.Button(self.panel, label='')
        self.band_choice = wx.Choice(self.panel)
        self.fullscreen_button: Any | None = None
        self.backend_label = wx.StaticText(self.panel, label='')
        band_items = ['16', '32']
        self.band_choice.SetItems(band_items)
        autosize_choice_control(self.band_choice, band_items)
        self.band_choice.SetStringSelection(str(self._band_count))
        for control in (
            self.toggle_button,
            self.external_window_button,
            self.band_choice,
            self.backend_label,
        ):
            sizer.Add(control, 0, wx.ALL | wx.EXPAND, 6)
        return sizer

    def _bind_events(self) -> None:
        wx = self._wx
        self.toggle_button.Bind(wx.EVT_BUTTON, self._on_toggle_visualizer)
        self.external_window_button.Bind(wx.EVT_BUTTON, self._on_toggle_external_window)
        self.band_choice.Bind(wx.EVT_CHOICE, self._on_band_count_changed)

    def _register_callbacks(self) -> None:
        register_callback(
            self.localization_manager,
            'register_language_change_callback',
            self.update_localization,
            logger=logger,
            message='Unable to register wx VisualizerView language callback.',
        )
        register_callback(
            self.theme_manager,
            'register_theme_change_callback',
            self.update_theme_colors,
            logger=logger,
            message='Unable to register wx VisualizerView theme callback.',
        )

    def _subscribe_to_events(self) -> None:
        self._subscribe(AudioEventType.SPECTRUM_DATA_UPDATED, self._on_spectrum_updated)
        self._subscribe(AudioEventType.PLAYBACK_STARTED, self._on_playback_started)
        self._subscribe(AudioEventType.PLAYBACK_STOPPED, self._on_playback_stopped)
        self._subscribe(AudioEventType.PLAYBACK_PAUSED, self._on_playback_paused)
        self._subscribe(AudioEventType.PLAYBACK_RESUMED, self._on_playback_resumed)
        self._subscribe(AudioEventType.FEEDBACK_MESSAGE, self._on_feedback_message)

    def _subscribe(self, event_type: AudioEventType, callback: Any) -> None:
        subscribe = getattr(self.event_bus, 'subscribe', None)
        if not callable(subscribe):
            return
        try:
            subscription = subscribe(event_type, callback)
        except VISUALIZER_VIEW_EXCEPTIONS:
            logger.debug('VisualizerView subscription failed for %s.', event_type, exc_info=True)
            return
        self._subscriptions.append((event_type, subscription))

    def _create_plot_backend(self) -> None:
        self._plot_canvas = self._build_canvas_backend_for_parent(self.plot_host, self._band_count)
        self._plot_backend_name = getattr(self._plot_canvas, 'backend_name', 'fallback')
        self.plot_sizer.Add(self._plot_canvas.widget, 1, self._wx.ALL | self._wx.EXPAND, 0)
        set_label_text(self.backend_label, f'Backend: {self._plot_backend_name}')

    def _build_canvas_backend_for_parent(self, parent: Any, band_count: int) -> Any:
        try:
            return _WxAggSpectrumCanvas(parent, band_count)
        except VISUALIZER_VIEW_EXCEPTIONS:
            logger.debug('wxAgg visualizer backend unavailable, using fallback canvas.', exc_info=True)
            return _FallbackSpectrumCanvas(self._wx, parent, band_count)

    def _t(self, key: str, default: str | None = None, **kwargs: Any) -> str:
        fallback = default if default is not None else key
        return get_localized_text(self.localization_manager, key, fallback, **kwargs)

    def update_localization(self, *_: Any) -> None:
        set_label_text(self.title_label, self._t('tab_visualizer', self._t('audio_visualizer_title', 'Visualizer')))
        toggle_key = 'stop_visualizer_button' if self._visualizer_on else 'start_visualizer_button'
        toggle_default = 'Stop Visualizer' if self._visualizer_on else 'Start Visualizer'
        set_label_text(self.toggle_button, self._t(toggle_key, toggle_default))
        set_label_text(
            self.external_window_button,
            self._t('toggle_external_window_button', 'Toggle External Window'),
        )
        set_label_text(self.backend_label, f'Backend: {self._plot_backend_name}')
        self._refresh_status_label()
        if self._external_window is not None:
            self._external_window.update_localization()

    def update_theme_colors(self, *_: Any) -> None:
        colors = get_theme_colors(self.theme_manager)
        background = colors.get('panel_bg') or colors.get('bg_color')
        foreground = colors.get('text_color')
        accent = colors.get('button_color') or background
        for widget in [self.panel, self.plot_host, self.title_label, self.status_label, self.feedback_label, self.backend_label]:
            apply_colors(widget, background=background, foreground=foreground)
        for widget in [self.toggle_button, self.external_window_button, self.band_choice]:
            apply_colors(widget, background=accent, foreground=foreground)
        if self._plot_canvas is not None:
            self._plot_canvas.apply_theme(colors)
        if self._external_window is not None:
            self._external_window.update_theme_colors()

    def _set_status_token(self, status_token: str) -> None:
        self._status_token = str(status_token or 'status_stopped')

    def _localized_status_label(self) -> str:
        status_defaults = {
            'status_idle': 'Idle',
            'status_paused': 'Paused',
            'status_playing': 'Playing',
            'status_stopped': 'Stopped',
        }
        token = str(self._status_token or 'status_stopped')
        default_text = status_defaults.get(token, 'Stopped')
        return self._t(token, default_text)

    def _refresh_status_label(self) -> None:
        prefix = self._t('visualizer_enabled', 'Audio spectrum visualizer enabled.') if self._visualizer_on else self._t('visualizer_disabled', 'Audio spectrum visualizer disabled.')
        message = f'{self._localized_status_label()} | {prefix}'
        set_label_text(self.status_label, message)
        if self._external_window is not None:
            self._external_window.update_status(message)

    def _publish_feedback(self, message: str, color: str = 'green') -> None:
        set_label_text(self.feedback_label, message)
        if self._external_window is not None:
            self._external_window.update_feedback(message)
        publish = getattr(self.event_bus, 'publish', None)
        if not callable(publish):
            return
        try:
            publish(AudioEventType.FEEDBACK_MESSAGE, {'message': message, 'color': color})
        except VISUALIZER_VIEW_EXCEPTIONS:
            logger.debug('Unable to publish VisualizerView feedback.', exc_info=True)

    def _is_ui_thread(self) -> bool:
        is_main_thread = getattr(self._wx, 'IsMainThread', None)
        if callable(is_main_thread):
            try:
                return bool(is_main_thread())
            except (AttributeError, RuntimeError, TypeError, ValueError):
                logger.debug('wx.IsMainThread failed in VisualizerView.', exc_info=True)
        return threading.get_ident() == self._ui_thread_ident

    def _dispatch_to_ui(self, callback: Callable[[], None]) -> None:
        if self._is_shutting_down or not callable(callback):
            return
        if self._is_ui_thread():
            callback()
            return
        call_after = getattr(self._wx, 'CallAfter', None)
        if callable(call_after):
            call_after(callback)
            return
        callback()

    def _current_band_count(self) -> int:
        selected = self.band_choice.GetStringSelection() if self.band_choice is not None else ''
        try:
            return max(1, int(selected or self._band_count))
        except (TypeError, ValueError):
            return self._band_count

    def _has_external_window(self) -> bool:
        return self._external_window is not None and self._external_window.is_alive()

    def _ensure_external_window(self) -> None:
        if self._is_shutting_down:
            return
        if self._has_external_window():
            return
        self._external_window = _ExternalVisualizerWindow(self)

    def _close_external_window(self) -> None:
        if self._external_window is None:
            return
        self._external_window_close_in_progress = True
        try:
            self._external_window.close()
        finally:
            self._external_window = None
            self._external_window_close_in_progress = False

    def _on_toggle_external_window(self, _event: Any | None = None) -> None:
        if self._has_external_window():
            self._close_external_window()
        else:
            self._ensure_external_window()
        self.update_localization()

    def _on_toggle_visualizer(self, _event: Any | None = None) -> None:
        self._visualizer_on = not self._visualizer_on
        if self._visualizer_on:
            if self._pending_values is not None:
                self._schedule_render_tick()
            self._set_status_token('status_playing' if self._playback_active else 'status_idle')
        else:
            self._pending_values = None
            self._last_values = [0.0] * self._band_count
            self._plot_canvas.update(self._last_values)
            if self._external_window is not None:
                self._external_window.update_plot(self._last_values)
            self._set_status_token('status_stopped')
        self.update_localization()

    def _apply_band_count_from_string(self, selected: Any) -> None:
        try:
            self._band_count = max(1, int(selected or self._band_count))
        except (TypeError, ValueError):
            self._band_count = max(1, int(self._band_count))
        self.band_choice.SetStringSelection(str(self._band_count))
        self._last_values = self._normalize_spectrum_values(self._last_values)
        self._plot_canvas.resize_band_count(self._band_count)
        self._plot_canvas.update(self._last_values)
        if self._external_window is not None:
            self._external_window.update_band_count(self._band_count, self._last_values)

    def _on_band_count_changed(self, _event: Any | None = None) -> None:
        self._apply_band_count_from_string(self._current_band_count())

    def _on_toggle_fullscreen(self, _event: Any | None = None) -> None:
        frame = self._resolve_host_frame()
        if frame is None:
            self._publish_feedback('Visualizer host frame unavailable.', color='orange')
            return
        is_fullscreen = bool(getattr(frame, 'IsFullScreen', lambda: False)())
        show_fullscreen = getattr(frame, 'ShowFullScreen', None)
        if callable(show_fullscreen):
            show_fullscreen(not is_fullscreen)

    def _resolve_host_frame(self) -> Any | None:
        current = getattr(self.panel, 'parent', None)
        for _ in range(8):
            if current is None:
                return None
            if hasattr(current, 'ShowFullScreen'):
                return current
            getter = getattr(current, 'GetParent', None)
            current = getter() if callable(getter) else getattr(current, 'parent', None)
        return None


    def _handle_external_window_closed(self, event: Any | None = None) -> None:
        if self._external_window_close_in_progress:
            return
        self._close_external_window()
        self.update_localization()
        skipper = getattr(event, 'Skip', None)
        if callable(skipper):
            skipper()

    def _on_spectrum_updated(self, payload: Any) -> None:
        if self._is_shutting_down:
            return
        values = self._extract_spectrum_values(payload)
        if values is None:
            return
        self._dispatch_to_ui(lambda: self._apply_spectrum_update(values))

    def _apply_spectrum_update(self, values: list[float]) -> None:
        if self._is_shutting_down:
            return
        self._pending_values = list(values)
        if self._visualizer_on:
            self._schedule_render_tick()

    def _extract_spectrum_values(self, payload: Any) -> list[float] | None:
        data = dict(payload or {}) if isinstance(payload, dict) else {}
        raw_values = data.get('spectrum_data')
        if raw_values is None:
            return None
        try:
            sequence = list(raw_values)
        except TypeError:
            return None
        if not sequence:
            return [0.0] * self._band_count
        return self._normalize_spectrum_values(sequence)

    def _normalize_spectrum_values(self, values: list[Any]) -> list[float]:
        clean_values = [self._bounded_float(value) for value in list(values or [])]
        if not clean_values:
            return [0.0] * self._band_count
        if len(clean_values) == self._band_count:
            return clean_values
        bucket_size = max(1, len(clean_values) / float(self._band_count))
        buckets: list[float] = []
        for band_index in range(self._band_count):
            start = int(band_index * bucket_size)
            end = max(start + 1, int((band_index + 1) * bucket_size))
            chunk = clean_values[start:end] or clean_values[-1:]
            buckets.append(sum(chunk) / float(len(chunk)))
        return buckets[:self._band_count]

    @staticmethod
    def _bounded_float(value: Any) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, parsed))

    def _schedule_render_tick(self) -> None:
        if self._is_shutting_down or self._render_scheduled:
            return
        if not self._is_ui_thread():
            self._dispatch_to_ui(self._schedule_render_tick)
            return
        call_later = getattr(self._wx, 'CallLater', None)
        if callable(call_later):
            self._render_scheduled = True
            self._render_timer = call_later(33, self._on_render_timer)
            return
        call_after = getattr(self._wx, 'CallAfter', None)
        if callable(call_after):
            self._render_scheduled = True
            call_after(self._on_render_timer)
            return
        self._on_render_timer()

    def _on_render_timer(self) -> None:
        self._render_scheduled = False
        if self._is_shutting_down or not self._visualizer_on:
            return
        values = self._pending_values
        if values is None and not self._playback_active:
            values = [0.0] * self._band_count
        if values is None:
            return
        self._pending_values = None
        self._last_values = list(values)
        self._plot_canvas.update(self._last_values)
        if self._external_window is not None:
            self._external_window.update_plot(self._last_values)
        self._set_status_token('status_playing' if self._playback_active else 'status_idle')
        self._refresh_status_label()

    def _on_playback_started(self, _payload: Any) -> None:
        self._dispatch_to_ui(self._apply_playback_started)

    def _apply_playback_started(self) -> None:
        self._playback_active = True
        self._set_status_token('status_playing')
        self._refresh_status_label()

    def _on_playback_resumed(self, _payload: Any) -> None:
        self._dispatch_to_ui(self._apply_playback_resumed)

    def _apply_playback_resumed(self) -> None:
        self._playback_active = True
        self._set_status_token('status_playing')
        self._refresh_status_label()
        if self._visualizer_on and self._pending_values is not None:
            self._schedule_render_tick()

    def _on_playback_paused(self, _payload: Any) -> None:
        self._dispatch_to_ui(self._apply_playback_paused)

    def _apply_playback_paused(self) -> None:
        self._playback_active = False
        self._set_status_token('status_paused')
        self._refresh_status_label()

    def _on_playback_stopped(self, _payload: Any) -> None:
        self._dispatch_to_ui(self._apply_playback_stopped)

    def _apply_playback_stopped(self) -> None:
        self._playback_active = False
        self._pending_values = [0.0] * self._band_count
        if self._visualizer_on:
            self._schedule_render_tick()
        self._set_status_token('status_stopped')
        self._refresh_status_label()

    def _on_feedback_message(self, payload: Any) -> None:
        message = ''
        if isinstance(payload, dict):
            message = str(payload.get('message') or '')
        self._dispatch_to_ui(lambda: self._apply_feedback_message(message))

    def _apply_feedback_message(self, message: str) -> None:
        set_label_text(self.feedback_label, message)
        if self._external_window is not None:
            self._external_window.update_feedback(message)

    def shutdown(self) -> None:
        if self._is_shutting_down:
            return
        self._is_shutting_down = True
        timer = self._render_timer
        stopper = getattr(timer, 'Stop', None)
        if callable(stopper):
            stopper()
        self._render_timer = None
        self._close_external_window()
        unregister_callback(
            self.localization_manager,
            'unregister_language_change_callback',
            self.update_localization,
            logger=logger,
            message='Unable to unregister wx VisualizerView language callback.',
        )
        unregister_callback(
            self.theme_manager,
            'unregister_theme_change_callback',
            self.update_theme_colors,
            logger=logger,
            message='Unable to unregister wx VisualizerView theme callback.',
        )
        unsubscribe = getattr(self.event_bus, 'unsubscribe', None)
        if callable(unsubscribe):
            for event_type, subscription in list(self._subscriptions):
                try:
                    unsubscribe(event_type, subscription=subscription)
                except VISUALIZER_VIEW_EXCEPTIONS:
                    logger.debug('VisualizerView unsubscribe failed for %s.', event_type, exc_info=True)
        self._subscriptions.clear()
        if self._plot_canvas is not None:
            self._plot_canvas.destroy()
