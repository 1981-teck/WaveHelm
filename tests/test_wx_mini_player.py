from __future__ import annotations

import sys
from dataclasses import dataclass

from src.audio.audio_event_models import AudioEventType
from src.model.media_file import MediaType
from src.ui_wx.mini_player import MiniPlayer
from tests.wx_fakes import FakeApp, FakeMouseEvent, FakeWxModule


class DummyEventBus:
    def __init__(self) -> None:
        self.subscriptions: dict[AudioEventType, list] = {}
        self.unsubscribed: list[tuple[AudioEventType, object]] = []
        self.published_events: list[tuple[AudioEventType, object]] = []

    def subscribe(self, event_type, callback):
        self.subscriptions.setdefault(event_type, []).append(callback)
        return callback

    def unsubscribe(self, event_type, subscription=None):
        callbacks = self.subscriptions.get(event_type, [])
        if subscription in callbacks:
            callbacks.remove(subscription)
            self.unsubscribed.append((event_type, subscription))
            return True
        return False

    def publish(self, event_type, payload):
        self.published_events.append((event_type, payload))
        for callback in list(self.subscriptions.get(event_type, [])):
            callback(payload)


class DummyThemeManager:
    def __init__(self) -> None:
        self.theme_callbacks = []

    def register_theme_change_callback(self, callback):
        self.theme_callbacks.append(callback)

    def get_current_theme_colors(self):
        return {
            'panel_bg': '#111111',
            'footer_bg': '#161616',
            'text_color': '#f4f4f5',
            'button_color': '#252525',
        }


class DummyLocalizationManager:
    def __init__(self) -> None:
        self.language_callbacks = []

    def register_language_change_callback(self, callback):
        self.language_callbacks.append(callback)

    def get_text(self, key, default=None, **kwargs):
        mapping = {
            'btn_prev': 'Precedente',
            'btn_play': 'Riproduci',
            'btn_pause': 'Pausa',
            'btn_next': 'Successiva',
            'btn_stop': 'Stop',
            'btn_volume': 'Volume',
            'btn_shuffle': 'Casuale',
            'btn_loop': 'Ripeti',
            'tooltip_mute': 'Muto',
            'mini_player_no_media': 'Nessun media caricato nel MiniPlayer.',
            'mini_player_state_playing': 'Stato MiniPlayer: in riproduzione.',
            'mini_player_state_paused_stopped': 'Stato MiniPlayer: in pausa/fermato.',
        }
        text = mapping.get(key, default or key)
        return text.format(**kwargs) if kwargs else text


class DummyAudioEngine:
    def __init__(self) -> None:
        self.volume = 0.35
        self.muted = False
        self.set_mute_calls = []
        self.seek_calls = []

    def get_volume(self):
        return self.volume

    def is_muted(self):
        return self.muted

    def set_mute(self, muted):
        self.muted = bool(muted)
        self.set_mute_calls.append(self.muted)

    def seek(self, seconds):
        self.seek_calls.append(float(seconds))


@dataclass
class DummyTrack:
    title: str
    path: str
    media_type: MediaType = MediaType.AUDIO
    duration: float = 120.0


class DummyPlayerController:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.volume_calls: list[float] = []
        self.seek_calls: list[float] = []
        self.current_track = DummyTrack('Initial Track', '/tmp/initial.mp3')
        self.position = 12.0
        self.duration = 120.0

    def toggle_shuffle(self):
        self.calls.append('toggle_shuffle')

    def toggle_loop(self):
        self.calls.append('toggle_loop')

    def previous(self):
        self.calls.append('previous')

    def play_action(self):
        self.calls.append('play_action')

    def pause(self):
        self.calls.append('pause')

    def next(self):
        self.calls.append('next')

    def stop(self):
        self.calls.append('stop')

    def set_volume(self, value):
        self.volume_calls.append(float(value))

    def get_position(self):
        return self.position

    def get_duration(self):
        return self.duration

    def get_current_player_state_payload(self):
        return {
            'current_track': {
                'title': self.current_track.title,
                'path': self.current_track.path,
            },
            'playing': False,
            'play': 'enabled',
            'pause': 'enabled',
            'stop': 'enabled',
            'next': 'enabled',
            'prev': 'enabled',
        }

    @property
    def video_controller(self):
        return self

    def seek(self, seconds):
        self.seek_calls.append(float(seconds))
        self.position = float(seconds)
        return True



def build_mini_player(monkeypatch, *, event_bus=None):
    monkeypatch.setitem(sys.modules, 'wx', FakeWxModule)
    player = DummyPlayerController()
    resolved_event_bus = DummyEventBus() if event_bus is None else event_bus
    audio_engine = DummyAudioEngine()
    player.audio_engine = audio_engine
    mini_player = MiniPlayer(
        FakeWxModule.Panel(None),
        player_controller=player,
        event_bus=resolved_event_bus,
        audio_engine=audio_engine,
        localization_manager=DummyLocalizationManager(),
        theme_manager=DummyThemeManager(),
    )
    return mini_player, player, resolved_event_bus, audio_engine



def test_wx_mini_player_localizes_labels_and_buttons(monkeypatch):
    mini_player, _, _, _ = build_mini_player(monkeypatch)

    assert mini_player.prev_button.label == 'Precedente'
    assert mini_player.play_button.label == 'Riproduci'
    assert mini_player.track_label.label == 'Initial Track'
    assert mini_player.state_label.label == 'Stato MiniPlayer: in pausa/fermato.'



def test_wx_mini_player_dispatches_local_actions_and_transport_events(monkeypatch):
    mini_player, player, event_bus, _ = build_mini_player(monkeypatch)

    mini_player.shuffle_button.click()
    mini_player.loop_button.click()
    mini_player.prev_button.click()
    mini_player.play_button.click()
    mini_player.pause_button.click()
    mini_player.next_button.click()
    mini_player.stop_button.click()

    assert player.calls == [
        'toggle_shuffle',
        'toggle_loop',
        'play_action',
        'pause',
    ]
    assert event_bus.published_events == [
        (AudioEventType.PREVIOUS_REQUESTED, {'source': 'mini_player'}),
        (AudioEventType.NEXT_REQUESTED, {'source': 'mini_player'}),
        (AudioEventType.STOP_REQUESTED, {'source': 'mini_player'}),
    ]



def test_wx_mini_player_transport_buttons_publish_interruption_events(monkeypatch):
    mini_player, player, event_bus, _ = build_mini_player(monkeypatch)

    mini_player.prev_button.click()
    mini_player.next_button.click()
    mini_player.stop_button.click()

    assert player.calls == []
    assert event_bus.published_events == [
        (AudioEventType.PREVIOUS_REQUESTED, {'source': 'mini_player'}),
        (AudioEventType.NEXT_REQUESTED, {'source': 'mini_player'}),
        (AudioEventType.STOP_REQUESTED, {'source': 'mini_player'}),
    ]


def test_wx_mini_player_non_transport_buttons_do_not_publish_interruption_events(monkeypatch):
    mini_player, player, event_bus, _ = build_mini_player(monkeypatch)

    mini_player.shuffle_button.click()
    mini_player.loop_button.click()
    mini_player.play_button.click()
    mini_player.pause_button.click()

    assert player.calls == ['toggle_shuffle', 'toggle_loop', 'play_action', 'pause']
    assert event_bus.published_events == []

def test_wx_mini_player_transport_buttons_fall_back_without_event_bus(monkeypatch):
    mini_player, player, _, _ = build_mini_player(monkeypatch, event_bus=None)
    mini_player.event_bus = None

    mini_player.prev_button.click()
    mini_player.next_button.click()
    mini_player.stop_button.click()

    assert player.calls == ['previous', 'next', 'stop']


def test_wx_mini_player_updates_from_event_bus_and_seek(monkeypatch):
    mini_player, player, event_bus, _ = build_mini_player(monkeypatch)

    event_bus.publish(
        AudioEventType.PLAYER_STATE_CHANGED,
        {
            'current_track': {'title': 'Song A', 'path': '/tmp/song_a.mp3'},
            'playing': True,
            'play': 'disabled',
            'pause': 'enabled',
            'stop': 'enabled',
            'next': 'enabled',
            'prev': 'disabled',
            'shuffle_enabled': True,
            'loop_enabled': True,
        },
    )
    event_bus.publish(
        AudioEventType.PLAYBACK_PROGRESS,
        {'path': '/tmp/song_a.mp3', 'current_time': 30.0, 'total_duration': 120.0},
    )

    assert mini_player.track_label.label == 'Song A'
    assert mini_player.state_label.label == 'Stato MiniPlayer: in riproduzione.'
    assert mini_player.shuffle_button.label.endswith('✓')
    assert mini_player.loop_button.label.endswith('✓')
    assert mini_player.play_button.enabled is False
    assert mini_player.prev_button.enabled is False
    assert mini_player.time_left_label.label == '0:30'
    assert mini_player.time_right_label.label == '2:00'
    assert mini_player.progress_slider.GetValue() == 250

    mini_player.progress_slider.SetValue(500)
    mini_player._on_progress_slider_changed()

    assert player.audio_engine.seek_calls == [60.0]
    assert mini_player.time_left_label.label == '1:00'



def test_wx_mini_player_click_seek_uses_pointer_position(monkeypatch):
    mini_player, player, event_bus, _ = build_mini_player(monkeypatch)

    event_bus.publish(
        AudioEventType.PLAYER_STATE_CHANGED,
        {
            'current_track': {'title': 'Song B', 'path': '/tmp/song_b.mp3'},
            'playing': True,
            'play': 'disabled',
            'pause': 'enabled',
            'stop': 'enabled',
            'next': 'enabled',
            'prev': 'enabled',
        },
    )
    event_bus.publish(
        AudioEventType.PLAYBACK_PROGRESS,
        {'path': '/tmp/song_b.mp3', 'current_time': 0.0, 'total_duration': 200.0},
    )

    mini_player.progress_slider.SetClientSize((201, 24))
    mini_player._on_progress_slider_pointer_down(FakeMouseEvent(x=150))

    assert player.audio_engine.seek_calls == [150.0]
    assert mini_player.progress_slider.GetValue() == 750
    assert mini_player.time_left_label.label == '2:30'
    assert mini_player.time_right_label.label == '3:20'



def test_wx_mini_player_handles_volume_and_mute(monkeypatch):
    mini_player, player, _, audio_engine = build_mini_player(monkeypatch)

    mini_player.volume_slider.SetValue(80)
    mini_player._on_volume_slider_changed()
    mini_player.mute_button.click()

    assert player.volume_calls[0] == 0.8
    assert audio_engine.set_mute_calls == [True]
    assert mini_player.mute_button.label.endswith('✓')



def test_wx_mini_player_close_unsubscribes_and_stops_timer(monkeypatch):
    mini_player, _, event_bus, _ = build_mini_player(monkeypatch)

    assert mini_player._progress_timer is not None
    mini_player.close()

    assert mini_player._progress_timer is None
    assert len(event_bus.unsubscribed) == 7
