from __future__ import annotations

from types import SimpleNamespace

from src.audio.audio_events import AudioEventType
from src.controller.component_player.player_event_handler import PlayerEventHandler
from src.controller.component_player.playback_state_manager import PlayerState


class DummyStateManager:
    def __init__(self, state=PlayerState.STOPPED, playing=False, paused=False):
        self.state = state
        self._playing = playing
        self._paused = paused

    def is_playing(self):
        return self._playing

    def is_paused(self):
        return self._paused


class DummyQueueManager:
    current_track = None


class DummyEngineController:
    def __init__(self, fail_volume=False, fail_seek=False):
        self.fail_volume = fail_volume
        self.fail_seek = fail_seek
        self.volume_calls = []
        self.seek_calls = []

    def set_volume(self, value):
        if self.fail_volume:
            raise RuntimeError('volume fail')
        self.volume_calls.append(value)

    def seek(self, value):
        if self.fail_seek:
            raise RuntimeError('seek fail')
        self.seek_calls.append(value)


class DummyPlayerFacade:
    def __init__(self):
        self.calls = []

    def resume(self):
        self.calls.append('resume')

    def play_action(self):
        self.calls.append('play_action')

    def pause(self):
        self.calls.append('pause')

    def stop(self):
        self.calls.append('stop')

    def previous(self):
        self.calls.append('previous')

    def next(self):
        self.calls.append('next')


class DummySubscription:
    def __init__(self, handler):
        self.handler = handler
        self.subscription_id = f'sub_{id(self)}'
        self.deactivated = False

    def deactivate(self):
        self.deactivated = True


class DummyEventBus:
    def __init__(self, fail_subscribe=None, fail_unsubscribe=False, no_subscribe=False, no_unsubscribe=False, return_subscription=False):
        self.fail_subscribe = set(fail_subscribe or [])
        self.fail_unsubscribe = fail_unsubscribe
        self.return_subscription = return_subscription
        self.subscribed = []
        self.unsubscribed = []
        if no_subscribe:
            self.subscribe = None
        if no_unsubscribe:
            self.unsubscribe = None

    def subscribe(self, event_type, handler):
        if event_type in self.fail_subscribe:
            raise RuntimeError('subscribe fail')
        self.subscribed.append((event_type, handler))
        if self.return_subscription:
            return DummySubscription(handler)
        return None

    def unsubscribe(self, event_type, subscription=None, callback=None):
        if self.fail_unsubscribe:
            raise RuntimeError('unsubscribe fail')
        self.unsubscribed.append((event_type, subscription, callback))


class FakeSignal:
    def __init__(self, fail_connect=False, fail_disconnect=False):
        self.fail_connect = fail_connect
        self.fail_disconnect = fail_disconnect
        self.connected = []
        self.disconnected = []

    def connect(self, handler):
        if self.fail_connect:
            raise RuntimeError('connect fail')
        self.connected.append(handler)

    def disconnect(self, handler):
        if self.fail_disconnect:
            raise RuntimeError('disconnect fail')
        self.disconnected.append(handler)


class DummyMainView:
    def __init__(self, signal):
        self.video_playback_ready = signal


class MinimalHandler(PlayerEventHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.reset_reasons = []

    def _reset_video_start_signature(self, reason: str) -> None:
        self.reset_reasons.append(reason)



def _make_handler(*, state=None, event_bus=None, engine=None):
    return MinimalHandler(
        state_manager=state or DummyStateManager(),
        queue_manager=DummyQueueManager(),
        engine_controller=engine or DummyEngineController(),
        event_bus=event_bus if event_bus is not None else DummyEventBus(),
        player_controller_facade=DummyPlayerFacade(),
    )


def test_setup_event_subscriptions_handles_partial_failures_and_missing_bus():
    bus = DummyEventBus(fail_subscribe={AudioEventType.NEXT_REQUESTED})
    handler = _make_handler(event_bus=bus)

    handler.setup_event_subscriptions()

    subscribed_events = [event for event, _ in bus.subscribed]
    assert AudioEventType.PLAY_REQUESTED in subscribed_events
    assert AudioEventType.NEXT_REQUESTED not in handler._subscriptions
    assert handler._subscriptions_active is True

    no_bus = _make_handler(event_bus=object())
    no_bus.setup_event_subscriptions()
    assert no_bus._subscriptions_active is False


def test_unsubscribe_and_shutdown_are_idempotent_and_best_effort():
    bus = DummyEventBus(fail_unsubscribe=True)
    handler = _make_handler(event_bus=bus)
    handler._subscriptions = {
        AudioEventType.PLAY_REQUESTED: handler._on_play_requested,
        AudioEventType.PAUSE_REQUESTED: handler._on_pause_requested,
    }
    handler._subscriptions_active = True

    signal = FakeSignal(fail_disconnect=True)
    handler.connect_main_view_signals(DummyMainView(signal))
    assert handler._video_ready_connected is True

    handler.shutdown()
    assert handler._is_shutting_down is True
    assert handler.event_bus is None
    assert handler._subscriptions == {}
    assert handler._subscriptions_active is False
    assert handler._main_view_signal_ref is None
    assert handler._video_ready_connected is False

    handler.shutdown()


def test_connect_main_view_signals_connects_once_and_tolerates_failures():
    handler = _make_handler()
    signal = FakeSignal()
    main_view = DummyMainView(signal)

    handler.connect_main_view_signals(main_view)
    handler.connect_main_view_signals(main_view)
    assert signal.connected == [handler._on_video_playback_ready_signal]

    failing = _make_handler()
    failing.connect_main_view_signals(DummyMainView(FakeSignal(fail_connect=True)))
    assert failing._video_ready_connected is False


def test_play_pause_stop_previous_next_routes_to_facade():
    playing = _make_handler(state=DummyStateManager(playing=True))
    playing._on_play_requested()
    assert playing._player.calls == []

    paused = _make_handler(state=DummyStateManager(paused=True))
    paused._on_play_requested()
    assert paused._player.calls == ['resume']

    stopped = _make_handler(state=DummyStateManager())
    stopped._on_play_requested()
    stopped._on_pause_requested()
    stopped._on_stop_requested()
    stopped._on_previous_requested()
    stopped._on_next_requested()
    assert stopped._player.calls == ['play_action', 'stop', 'previous', 'next']
    assert stopped.reset_reasons == ['stop_requested', 'previous_requested', 'next_requested']

    active = _make_handler(state=DummyStateManager(playing=True))
    active._on_pause_requested()
    assert active._player.calls == ['pause']


def test_volume_and_seek_requests_validate_payload_and_loading_state():
    engine = DummyEngineController()
    handler = _make_handler(engine=engine)

    handler._on_volume_change_requested({'volume': '0.5'})
    handler._on_volume_change_requested({'volume': 'bad'})
    handler._on_volume_change_requested(None)
    assert engine.volume_calls == [0.5]

    handler._on_seek_requested({'position': '15.25'})
    handler._on_seek_requested({'position': 'bad'})
    assert engine.seek_calls == [15.25]

    loading = _make_handler(state=DummyStateManager(state=PlayerState.LOADING), engine=DummyEngineController())
    loading._on_seek_requested({'position': 22})
    assert loading.engine_controller.seek_calls == []


def test_coerce_bool_handles_common_payload_shapes():
    handler = _make_handler()
    assert handler._coerce_bool(True) is True
    assert handler._coerce_bool('true') is True
    assert handler._coerce_bool('off') is False
    assert handler._coerce_bool(1) is True
    assert handler._coerce_bool(0) is False
    assert handler._coerce_bool(None) is False


def test_shutdown_unsubscribes_by_subscription_object_when_bus_returns_one():
    bus = DummyEventBus(return_subscription=True)
    handler = _make_handler(event_bus=bus)

    handler.setup_event_subscriptions()
    handler.shutdown()

    assert any(subscription is not None and callback is None for _event, subscription, callback in bus.unsubscribed)
