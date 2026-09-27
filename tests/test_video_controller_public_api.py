from __future__ import annotations

from copy import deepcopy

from src.controller.video_controller import VideoController
from src.controller.video_controller_state import VideoState


class DummyBus:
    def __init__(self):
        self.subscriptions = []

    def subscribe(self, event_type, callback):
        self.subscriptions.append((event_type, callback))

    def publish(self, event_type, payload, require_ui_thread=False):
        return None


class DummyTextAdapter:
    def __init__(self):
        self.descriptors = ()
        self.active_ids = ()
        self.selected = []
        self.disable_calls = 0
        self.raise_on = None

    def get_text_track_descriptors(self):
        if self.raise_on == 'descriptors':
            raise RuntimeError('descriptor probe failed')
        return self.descriptors

    def get_active_text_track_ids(self):
        if self.raise_on == 'active':
            raise RuntimeError('active probe failed')
        return self.active_ids

    def select_text_track(self, track_id):
        if self.raise_on == 'select':
            raise RuntimeError('select failed')
        self.selected.append(track_id)
        return True

    def disable_text_tracks(self):
        if self.raise_on == 'disable':
            raise RuntimeError('disable failed')
        self.disable_calls += 1
        return True


def _controller() -> VideoController:
    return VideoController(DummyBus())


def test_video_controller_initial_state_and_properties_use_real_class():
    controller = _controller()

    assert type(controller).__module__ == 'src.controller.video_controller'
    assert controller.state is VideoState.IDLE
    assert controller.is_playing is False
    assert controller.is_paused is False
    assert controller.is_ready is False
    assert controller.current_hwnd is None
    assert controller.current_path is None
    assert controller.loop_enabled is False
    assert controller.volume == 1.0
    assert len(controller._event_bus.subscriptions) == 3


def test_audio_metadata_normalization_is_defensive_and_selects_default():
    controller = _controller()
    source = {
        'title': 'Movie',
        'audio_tracks': [
            {'stream_index': '2', 'language': 'it', 'codec_name': 'aac', 'is_default': True},
            {'stream_index': 4, 'language': 'en', 'codec_name': 'ac3'},
            {'stream_index': 4, 'language': 'duplicate'},
            {'stream_index': -1},
            {'stream_index': 'bad'},
            'invalid',
        ],
    }

    snapshot = controller.set_current_media_metadata(source)
    source['audio_tracks'][0]['language'] = 'mutated'

    assert snapshot['title'] == 'Movie'
    assert [item['stream_index'] for item in snapshot['audio_tracks']] == [2, 4]
    assert snapshot['audio_tracks'][0]['selected'] is True
    assert snapshot['audio_tracks'][0]['language'] == 'it'
    assert controller.get_current_media_metadata() == snapshot


def test_non_dict_metadata_clears_stale_audio_state():
    controller = _controller()
    controller._current_media_metadata = {'stale': True}
    controller._current_audio_track_descriptors = ({'stream_index': 1},)
    controller._current_audio_stream_index = 1

    assert controller.set_current_media_metadata(None) == {}
    assert controller.get_current_media_metadata() == {}
    assert controller.get_audio_track_descriptors() == ()
    assert controller._current_audio_stream_index is None


def test_audio_track_options_filter_candidates_and_generate_stable_labels():
    controller = _controller()
    controller.set_audio_track_descriptors([
        {'stream_index': 1, 'language': 'it', 'codec_name': 'aac', 'is_default': True},
        {'stream_index': 3, 'language': '', 'codec_name': ''},
        {'stream_index': 5, 'label': 'Commentary'},
    ])
    controller._current_audio_track_candidates = (3, 5)
    controller._current_audio_stream_index = 5
    controller.get_selected_audio_streams = lambda: (5,)

    options = controller.get_audio_track_options()

    assert [item['stream_index'] for item in options] == [3, 5]
    assert options[0]['label'] == 'Track 2 — Unknown — audio'
    assert options[0]['selected'] is False
    assert options[1]['label'] == 'Commentary'
    assert options[1]['selected'] is True


def test_audio_descriptor_normalizer_rejects_malformed_and_duplicate_entries():
    assert VideoController._normalize_audio_track_descriptors(None) == ()
    assert VideoController._normalize_audio_track_descriptors(123) == ()

    normalized = VideoController._normalize_audio_track_descriptors([
        {'stream_index': '7', 'track_index': '2', 'language': ' it '},
        {'stream_index': 7},
        {'stream_index': -1},
        {'stream_index': 'bad'},
        None,
    ])

    assert normalized == ({
        'stream_index': 7,
        'track_index': 2,
        'language': 'it',
        'title': '',
        'codec_name': '',
        'codec_long_name': '',
        'channels': None,
        'channel_layout': '',
        'is_default': False,
        'is_forced': False,
        'label': '',
    },)


def test_text_descriptors_normalize_active_state_and_labels():
    controller = _controller()

    descriptors = controller.set_text_track_descriptors([
        {'track_id': '4', 'kind_label': 'Subtitle', 'language': 'it', 'is_active': True},
        {'track_id': 8, 'kind_label': '', 'language': ''},
        {'track_id': 8, 'label': 'duplicate'},
        {'track_id': -1},
        {'track_id': 'bad'},
    ])

    assert [item['track_id'] for item in descriptors] == [4, 8]
    assert descriptors[0]['is_active'] is True
    assert controller._current_text_track_id == 4

    options = controller.get_text_track_options()
    assert options[0]['label'] == 'Subtitle — it'
    assert options[0]['selected'] is True
    assert options[1]['label'] == 'Subtitle 2'


def test_text_track_adapter_probe_normalizes_duplicates_and_updates_cached_state():
    controller = _controller()
    adapter = DummyTextAdapter()
    adapter.descriptors = (
        {'track_id': 2, 'label': 'English'},
        {'track_id': '6', 'language': 'it'},
    )
    adapter.active_ids = ('6', 6, -1, 'bad')
    controller._adapter = adapter

    descriptors = controller.get_text_track_descriptors()
    active_ids = controller.get_active_text_track_ids()

    assert [item['track_id'] for item in descriptors] == [2, 6]
    assert active_ids == (6,)
    assert controller._current_text_track_id == 6
    assert controller._current_text_track_descriptors[1]['is_active'] is True


def test_text_track_probe_failure_preserves_cached_descriptor_state():
    controller = _controller()
    cached = controller.set_text_track_descriptors([{'track_id': 3, 'label': 'Cached', 'is_active': True}])
    adapter = DummyTextAdapter()
    adapter.raise_on = 'active'
    controller._adapter = adapter

    assert controller.get_text_track_descriptors() == cached
    assert controller.get_active_text_track_ids() == (3,)


def test_select_and_disable_text_tracks_update_cached_selection():
    controller = _controller()
    controller.set_text_track_descriptors([{'track_id': 2}, {'track_id': 9}])
    adapter = DummyTextAdapter()
    controller._adapter = adapter

    assert controller.select_text_track('9') is True
    assert adapter.selected == [9]
    assert controller._current_text_track_id == 9
    assert [item['is_active'] for item in controller._current_text_track_descriptors] == [False, True]

    assert controller.disable_text_tracks() is True
    assert adapter.disable_calls == 1
    assert controller._current_text_track_id is None
    assert [item['is_active'] for item in controller._current_text_track_descriptors] == [False, False]


def test_text_track_selection_rejects_invalid_or_adapter_failures_without_false_success():
    controller = _controller()
    assert controller.select_text_track(-1) is False
    assert controller.select_text_track('bad') is False
    assert controller.disable_text_tracks() is False

    adapter = DummyTextAdapter()
    controller._adapter = adapter
    adapter.raise_on = 'select'
    assert controller.select_text_track(2) is False
    adapter.raise_on = 'disable'
    assert controller.disable_text_tracks() is False


def test_text_descriptor_normalizer_and_active_id_resolver_are_bounded():
    normalized = VideoController._normalize_text_track_descriptors([
        {'track_id': '1', 'kind': '2', 'label': ' One ', 'is_active': True},
        {'track_id': 1, 'label': 'duplicate'},
        {'track_id': 5, 'is_active': True},
        {'track_id': -1},
        {'track_id': 'bad'},
        object(),
    ])

    assert [item['track_id'] for item in normalized] == [1, 5]
    assert normalized[0]['label'] == 'One'
    assert VideoController._resolve_text_track_active_ids_from_descriptors(normalized) == (1, 5)
    assert VideoController._normalize_text_track_descriptors(123) == ()


def test_metadata_accessors_return_defensive_copies():
    controller = _controller()
    source = [{'stream_index': 1, 'label': 'Main'}]
    stored = controller.set_audio_track_descriptors(source)
    external = controller.get_audio_track_descriptors()

    assert external == stored
    external[0]['label'] = 'mutated'
    assert controller.get_audio_track_descriptors()[0]['label'] == 'Main'
