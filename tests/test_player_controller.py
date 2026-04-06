from __future__ import annotations

from types import SimpleNamespace

from src.audio.audio_events import AudioEventType
from src.controller.player_controller import PlayerController
from src.model.media_file import MediaType


class DummyTrack:
    def __init__(self, path: str, media_type=MediaType.AUDIO):
        self.path = path
        self.media_type = media_type

    def to_dict(self):
        return {'path': self.path, 'media_type': self.media_type.value}


class DummyStateManager:
    def __init__(self):
        self._stopped = False
        self._video = False
        self._paused = False
        self._playing = False
        self._loop = False
        self.loop_enabled = False
        self.shuffle_enabled = False
        self.playlist = []
        self.index = -1
        self.current_track = None
        self.state = SimpleNamespace(name='IDLE')
        self.updated_states = []
        self.contexts = []

    def is_stopped(self):
        return self._stopped

    def is_video(self):
        return self._video

    def is_paused(self):
        return self._paused

    def is_playing(self):
        return self._playing

    def is_audio(self):
        return not self._video

    def set_context(self, playlist, index, track):
        self.playlist = list(playlist)
        self.index = index
        self.current_track = track
        self.contexts.append((list(playlist), index, track))

    def update_state(self, state):
        self.updated_states.append(state)
        self.state = SimpleNamespace(name=getattr(state, 'name', str(state)))

    def toggle_loop(self):
        self.loop_enabled = not self.loop_enabled
        self._loop = self.loop_enabled
        return self.loop_enabled

    def toggle_shuffle(self):
        self.shuffle_enabled = not self.shuffle_enabled
        return self.shuffle_enabled


class DummyQueueManager:
    def __init__(self):
        self.playlist = []
        self.index = -1
        self.current_track = None
        self.received = []
        self.shuffle = []

    def set_context(self, items, start_index):
        self.received.append((list(items), start_index))
        self.playlist = list(items)
        self.index = int(start_index)
        self.current_track = items[self.index] if items else None
        return self.current_track

    def get_playlist_info(self):
        return {'count': len(self.playlist), 'index': self.index}

    def set_shuffle(self, enabled):
        self.shuffle.append(enabled)

    def next(self):
        if not self.playlist:
            return None
        self.index = (self.index + 1) % len(self.playlist)
        self.current_track = self.playlist[self.index]
        return self.current_track

    def previous(self):
        if not self.playlist:
            return None
        self.index = (self.index - 1) % len(self.playlist)
        self.current_track = self.playlist[self.index]
        return self.current_track


class DummyEngineController:
    def __init__(self):
        self.play_calls = []
        self.stop_calls = 0
        self.pause_calls = 0
        self.resume_calls = 0
        self.volume_calls = []
        self.seek_calls = []
        self.loop_calls = []
        self.raise_on_stop = None
        self.raise_on_factory = None
        self.factory = None
        self.repeat_current = None

    def play(self, track, loop):
        self.play_calls.append((track, loop))
        return True

    def stop(self):
        self.stop_calls += 1
        if self.raise_on_stop:
            raise self.raise_on_stop

    def pause(self):
        self.pause_calls += 1

    def resume(self):
        self.resume_calls += 1

    def seek(self, value):
        self.seek_calls.append(value)

    def set_volume(self, value):
        self.volume_calls.append(value)

    def toggle_mute(self):
        pass

    def set_loop(self, enabled):
        self.loop_calls.append(enabled)

    def set_video_controller_factory(self, factory):
        if self.raise_on_factory:
            raise self.raise_on_factory
        self.factory = factory

    def should_repeat_current_track(self):
        if self.repeat_current is None:
            return False
        return self.repeat_current


class DummyProgressTracker:
    def __init__(self):
        self.started = 0
        self.stopped = 0
        self.raise_on_stop = None

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1
        if self.raise_on_stop:
            raise self.raise_on_stop

    def get_duration(self):
        return 12.5

    def get_position(self):
        return 4.0


class DummyEventHandler:
    def __init__(self):
        self.setup_calls = 0
        self.main_views = []
        self.shutdown_calls = 0

    def setup_event_subscriptions(self):
        self.setup_calls += 1

    def connect_main_view_signals(self, main_view):
        self.main_views.append(main_view)

    def shutdown(self):
        self.shutdown_calls += 1


class DummyEventBus:
    def __init__(self, dispatcher=None):
        self.published = []
        self._ui_dispatcher = dispatcher

    def publish(self, event_type, payload):
        self.published.append((event_type, payload))


class EngineWithoutFactorySetter:
    def __init__(self):
        self._video_controller_factory = None


class EngineWithFailingFactorySetter:
    def set_video_controller_factory(self, factory):
        raise RuntimeError('factory fail')


def _make_controller(**kwargs):
    state = kwargs.pop('state_manager', DummyStateManager())
    queue = kwargs.pop('queue_manager', DummyQueueManager())
    engine = kwargs.pop('engine_controller', DummyEngineController())
    progress = kwargs.pop('progress_tracker', DummyProgressTracker())
    event_bus = kwargs.pop('event_bus', DummyEventBus())
    controller = PlayerController(state, queue, engine, progress, event_bus)
    return controller, state, queue, engine, progress, event_bus


def test_set_event_handler_and_main_view_wiring_are_forwarded():
    controller, _, _, _, progress, _ = _make_controller()
    handler = DummyEventHandler()

    controller.set_event_handler(handler)
    controller.set_main_view('main-view')

    assert handler.setup_calls == 1
    assert progress.started == 1
    assert handler.main_views == ['main-view']


def test_set_playback_context_coerces_index_for_switch_logic_and_publishes_playlist():
    controller, state, queue, engine, _, event_bus = _make_controller()
    state._stopped = False
    state._video = True
    tracks = [DummyTrack('a.mp3'), DummyTrack('b.mp4', MediaType.VIDEO)]

    controller.set_playback_context(tracks, '1')

    assert engine.stop_calls == 1
    assert queue.received[-1][1] == '1'
    assert state.current_track is tracks[1]
    assert any(event_type == AudioEventType.PLAYLIST_CHANGED for event_type, _ in event_bus.published)
    assert any(event_type == AudioEventType.PREPARE_VIDEO_PLAYBACK for event_type, _ in event_bus.published)

    assert controller._coerce_start_index('bad', len(tracks)) is None


def test_dispatch_to_ui_thread_handles_missing_and_failing_dispatcher():
    controller, _, _, _, _, _ = _make_controller(event_bus=DummyEventBus())
    called = []
    assert controller._dispatch_to_ui_thread(lambda: called.append('run')) is False
    assert called == []

    def failing_dispatcher(callback):
        raise ValueError('dispatch fail')

    failing_controller, _, _, _, _, _ = _make_controller(event_bus=DummyEventBus(dispatcher=failing_dispatcher))
    assert failing_controller._dispatch_to_ui_thread(lambda: called.append('never')) is False
    assert called == []

    def good_dispatcher(callback):
        callback()

    good_controller, _, _, _, _, _ = _make_controller(event_bus=DummyEventBus(dispatcher=good_dispatcher))
    assert good_controller._dispatch_to_ui_thread(lambda: called.append('ok')) is True
    assert called == ['ok']


def test_shutdown_is_best_effort_and_idempotent():
    controller, _, _, engine, progress, _ = _make_controller()
    handler = DummyEventHandler()
    controller._event_handler = handler
    progress.raise_on_stop = RuntimeError('progress fail')
    engine.raise_on_stop = RuntimeError('engine fail')
    controller.state_manager.update_state = lambda state: (_ for _ in ()).throw(RuntimeError('state fail'))

    controller.shutdown()
    controller.shutdown()

    assert handler.shutdown_calls == 1
    assert progress.stopped == 1
    assert engine.stop_calls == 1




def test_track_end_repeats_only_when_current_playback_mode_requires_it():
    controller, state, queue, engine, _, _ = _make_controller()
    first = DummyTrack('song.mp3')
    second = DummyTrack('next.mp3')
    queue.playlist = [first, second]
    queue.index = 0
    queue.current_track = first
    state.current_track = first
    state._loop = True

    engine.repeat_current = False
    controller._run_track_end_transition()
    assert queue.current_track is second
    assert engine.play_calls[-1] == (second, True)

    queue.index = 0
    queue.current_track = first
    state.current_track = first
    engine.repeat_current = True
    controller._run_track_end_transition()
    assert engine.play_calls[-1] == (first, True)


def test_toggle_shuffle_updates_queue_without_interrupting_current_track():
    controller, state, queue, engine, _, event_bus = _make_controller()
    track = DummyTrack('song.mp3')
    queue.current_track = track
    state.current_track = track

    controller.toggle_shuffle()

    assert state.shuffle_enabled is True
    assert queue.shuffle == [True]
    assert queue.current_track is track
    assert engine.stop_calls == 0
    assert engine.play_calls == []
    assert any(evt == AudioEventType.SHUFFLE_CHANGED and payload.get('shuffle_enabled') is True for evt, payload in event_bus.published)

def test_set_video_controller_factory_uses_setter_and_attribute_fallback():
    controller, _, _, engine, _, _ = _make_controller()
    factory = lambda: 'video-controller'
    controller.set_video_controller_factory(factory)
    assert engine.factory is factory

    fallback_controller, _, _, _, _, _ = _make_controller(engine_controller=EngineWithoutFactorySetter())
    fallback_controller.set_video_controller_factory(factory)
    assert fallback_controller.engine_controller._video_controller_factory is factory

    failing_controller, _, _, _, _, _ = _make_controller(engine_controller=EngineWithFailingFactorySetter())
    failing_controller.set_video_controller_factory(factory)


def test_previous_switches_from_video_to_previous_audio_and_requests_video_cleanup():
    controller, state, queue, engine, _, event_bus = _make_controller()
    first = DummyTrack('song.mp3')
    second = DummyTrack('clip.mp4', MediaType.VIDEO)
    queue.playlist = [first, second]
    queue.index = 1
    queue.current_track = second
    state.current_track = second
    state._video = True

    controller.previous()

    assert queue.current_track is first
    assert state.current_track is first
    assert engine.stop_calls == 1
    assert engine.play_calls[-1] == (first, False)
    assert (AudioEventType.CANCEL_VIDEO_PLAYBACK, {}) in event_bus.published
