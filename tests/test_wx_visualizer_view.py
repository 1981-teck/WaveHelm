from __future__ import annotations

import sys
import threading

from src.audio.audio_event_models import AudioEventType
from src.ui_wx.visualizer_view import (
    VisualizerView,
    _band_frequency_labels,
    _build_band_center_frequencies,
    _format_frequency_label,
    _visualizer_percent_ticks,
    _bar_color_for_level,
)
from tests.wx_fakes import FakeApp, FakeWxModule


class DummyLocalizationManager:
    def __init__(self) -> None:
        self.language_callbacks = []

    def register_language_change_callback(self, callback):
        self.language_callbacks.append(callback)

    def unregister_language_change_callback(self, callback):
        if callback in self.language_callbacks:
            self.language_callbacks.remove(callback)

    def get_text(self, key, default=None, **kwargs):
        mapping = {
            'tab_visualizer': 'Visualizzatore',
            'audio_visualizer_title': 'Visualizzatore audio',
            'start_visualizer_button': 'Avvia Visualizer',
            'stop_visualizer_button': 'Ferma Visualizer',
            'toggle_external_window_button': 'Attiva/Disattiva finestra esterna',
            'visualizer_fullscreen': 'Schermo intero (F11)',
            'visualizer_toggle': 'Sempre in primo piano',
            'visualizer_enabled': 'Visualizzatore spettro audio abilitato.',
            'visualizer_disabled': 'Visualizzatore spettro audio disabilitato.',
            'status_idle': 'In attesa',
            'status_paused': 'In pausa',
            'status_playing': 'In riproduzione',
            'status_stopped': 'Fermato',
        }
        return mapping.get(key, default or key)


class DummyThemeManager:
    def __init__(self) -> None:
        self.theme_callbacks = []

    def register_theme_change_callback(self, callback):
        self.theme_callbacks.append(callback)

    def unregister_theme_change_callback(self, callback):
        if callback in self.theme_callbacks:
            self.theme_callbacks.remove(callback)

    def get_current_theme_colors(self):
        return {
            'bg_color': '#111111',
            'panel_bg': '#161616',
            'text_color': '#f5f5f5',
            'button_color': '#252525',
        }


class DummyEventBus:
    def __init__(self) -> None:
        self.subscriptions = {}
        self.published = []

    def subscribe(self, event_type, callback):
        self.subscriptions.setdefault(event_type, []).append(callback)
        return callback

    def unsubscribe(self, event_type, subscription=None):
        callbacks = self.subscriptions.get(event_type, [])
        if subscription in callbacks:
            callbacks.remove(subscription)
            return True
        return False

    def publish(self, event_type, payload):
        self.published.append((event_type, payload))
        for callback in list(self.subscriptions.get(event_type, [])):
            callback(payload)


class DummyAudioEngine:
    pass


def build_visualizer_view(monkeypatch, wx_module=FakeWxModule):
    monkeypatch.setitem(sys.modules, 'wx', wx_module)
    localization = DummyLocalizationManager()
    theme = DummyThemeManager()
    event_bus = DummyEventBus()
    frame = wx_module.Frame(None, title='Host')
    parent = wx_module.Panel(frame)
    view = VisualizerView(
        parent,
        audio_engine=DummyAudioEngine(),
        event_bus=event_bus,
        localization_manager=localization,
        theme_manager=theme,
    )
    return view, frame, event_bus, localization, theme



def test_wx_visualizer_view_loads_localized_controls_and_fallback_backend(monkeypatch):
    view, _frame, _event_bus, _localization, _theme = build_visualizer_view(monkeypatch)

    assert view.title_label.label == 'Visualizzatore'
    assert view.toggle_button.label == 'Avvia Visualizer'
    assert view.external_window_button.label == 'Attiva/Disattiva finestra esterna'
    assert view.band_choice.GetStringSelection() == '32'
    assert view.band_choice.items == ['16', '32']
    assert view.backend_label.label.startswith('Backend: ')
    assert view._plot_backend_name == 'fallback'
    assert 'disabilitato' in view.status_label.label.lower()



def test_wx_visualizer_view_renders_spectrum_and_reacts_to_controls(monkeypatch):
    view, _frame, event_bus, _localization, _theme = build_visualizer_view(monkeypatch)

    event_bus.publish(AudioEventType.PLAYBACK_STARTED, {})
    event_bus.publish(AudioEventType.SPECTRUM_DATA_UPDATED, {'spectrum_data': [0.1, 0.4, 0.8, 1.2]})

    assert view._render_timer is None
    view.toggle_button.click()
    assert view.toggle_button.label == 'Ferma Visualizer'
    assert view._render_timer is not None

    view._render_timer.run()
    assert len(view._plot_canvas.last_values) == 32
    assert max(view._plot_canvas.last_values) > 0.0
    assert 'abilitato' in view.status_label.label.lower()

    view.band_choice.SetStringSelection('16')
    view._on_band_count_changed()
    assert len(view._plot_canvas.last_values) == 16

    view.external_window_button.click()
    assert view._external_window is not None
    assert view._external_window.stop_button.label == 'Ferma Visualizer'
    assert view._external_window.fullscreen_button.label == 'Schermo intero (F11)'

    view.external_window_button.click()
    assert view._external_window is None

    view.toggle_button.click()
    assert view.toggle_button.label == 'Avvia Visualizer'
    assert 'disabilitato' in view.status_label.label.lower()



def test_wx_visualizer_view_handles_feedback_playback_stop_and_shutdown(monkeypatch):
    view, _frame, event_bus, localization, theme = build_visualizer_view(monkeypatch)

    view.toggle_button.click()
    event_bus.publish(AudioEventType.FEEDBACK_MESSAGE, {'message': 'Visualizer updated'})
    event_bus.publish(AudioEventType.PLAYBACK_STOPPED, {})
    assert view._render_timer is not None
    view._render_timer.run()

    assert view.feedback_label.label == 'Visualizer updated'
    assert all(value == 0.0 for value in view._plot_canvas.last_values)

    view.shutdown()

    assert localization.language_callbacks == []
    assert theme.theme_callbacks == []
    assert event_bus.subscriptions.get(AudioEventType.SPECTRUM_DATA_UPDATED) == []



def test_wx_main_view_includes_visualizer_page(monkeypatch):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    from src.ui_wx.main_view import MainView

    main_view = MainView(FakeApp())

    assert 'visualizer' in main_view._pages


class QueuedWxModule(FakeWxModule):
    _ui_queue = []

    @staticmethod
    def reset_ui_queue():
        QueuedWxModule._ui_queue = []

    @staticmethod
    def CallAfter(callback, *args):
        QueuedWxModule._ui_queue.append((callback, args))

    @staticmethod
    def flush_ui_queue():
        while QueuedWxModule._ui_queue:
            callback, args = QueuedWxModule._ui_queue.pop(0)
            callback(*args)


def test_wx_visualizer_view_marshals_worker_spectrum_updates_to_ui_thread(monkeypatch):
    QueuedWxModule.reset_ui_queue()
    view, _frame, event_bus, _localization, _theme = build_visualizer_view(monkeypatch, wx_module=QueuedWxModule)

    call_later_threads = []
    original_call_later = QueuedWxModule.CallLater

    def tracking_call_later(delay, callback, *args):
        call_later_threads.append(threading.get_ident())
        return original_call_later(delay, callback, *args)

    monkeypatch.setattr(QueuedWxModule, 'CallLater', staticmethod(tracking_call_later))

    worker = threading.Thread(
        target=lambda: event_bus.publish(AudioEventType.SPECTRUM_DATA_UPDATED, {'spectrum_data': [0.2, 0.6, 1.0]}),
        daemon=True,
    )
    worker.start()
    worker.join(timeout=1.0)

    assert view._pending_values is None
    assert call_later_threads == []

    QueuedWxModule.flush_ui_queue()

    assert view._pending_values is not None
    assert call_later_threads == []

    view.toggle_button.click()
    assert call_later_threads == [threading.get_ident()]
    view._render_timer.run()

    assert max(view._plot_canvas.last_values) > 0.0


def test_wx_visualizer_view_marshals_worker_stop_event_without_starting_timer_off_thread(monkeypatch):
    QueuedWxModule.reset_ui_queue()
    view, _frame, event_bus, _localization, _theme = build_visualizer_view(monkeypatch, wx_module=QueuedWxModule)

    event_bus.publish(AudioEventType.SPECTRUM_DATA_UPDATED, {'spectrum_data': [0.5, 0.5, 0.5]})
    QueuedWxModule.flush_ui_queue()
    view.toggle_button.click()
    view._render_timer.run()
    assert max(view._plot_canvas.last_values) > 0.0

    call_later_threads = []
    original_call_later = QueuedWxModule.CallLater

    def tracking_call_later(delay, callback, *args):
        call_later_threads.append(threading.get_ident())
        return original_call_later(delay, callback, *args)

    monkeypatch.setattr(QueuedWxModule, 'CallLater', staticmethod(tracking_call_later))

    worker = threading.Thread(
        target=lambda: event_bus.publish(AudioEventType.PLAYBACK_STOPPED, {}),
        daemon=True,
    )
    worker.start()
    worker.join(timeout=1.0)

    assert call_later_threads == []
    QueuedWxModule.flush_ui_queue()
    assert call_later_threads == [threading.get_ident()]
    view._render_timer.run()

    assert all(value == 0.0 for value in view._plot_canvas.last_values)





def test_wx_visualizer_band_choices_are_reduced_for_readability(monkeypatch):
    view, _frame, _event_bus, _localization, _theme = build_visualizer_view(monkeypatch)

    assert view.band_choice.items == ['16', '32']


def test_wx_visualizer_axis_percent_ticks_are_fixed_and_readable():
    tick_values, tick_labels = _visualizer_percent_ticks()

    assert tick_values == [0.0, 0.25, 0.50, 0.75, 1.0]
    assert tick_labels == ['0%', '25%', '50%', '75%', '100%']



def test_wx_visualizer_frequency_helpers_cover_the_configured_audio_range():
    frequencies = _build_band_center_frequencies(32)
    labels = _band_frequency_labels(32)

    assert len(frequencies) == 32
    assert len(labels) == 32
    assert all(left < right for left, right in zip(frequencies, frequencies[1:]))
    assert frequencies[0] >= 20.0
    assert 14900.0 <= frequencies[-1] <= 15001.0
    assert labels[0] == _format_frequency_label(frequencies[0])
    assert labels[-1] == _format_frequency_label(frequencies[-1])
    assert labels[-1].endswith('kHz')



def test_wx_visualizer_frequency_label_formatting_is_clear_for_hz_and_khz_ranges():
    assert _format_frequency_label(20.0) == '20 Hz'
    assert _format_frequency_label(1000.0) == '1 kHz'
    assert _format_frequency_label(1600.0) == '1.6 kHz'
    assert _format_frequency_label(12500.0) == '12.5 kHz'



def test_wx_visualizer_bar_colors_follow_requested_thresholds():
    assert _bar_color_for_level(0.00) == '#2ecc71'
    assert _bar_color_for_level(0.24) == '#2ecc71'
    assert _bar_color_for_level(0.25) == '#f1c40f'
    assert _bar_color_for_level(0.50) == '#e67e22'
    assert _bar_color_for_level(0.75) == '#e74c3c'
