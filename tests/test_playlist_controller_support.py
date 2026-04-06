from __future__ import annotations

from src.audio.audio_events import AudioEventType
from src.controller import playlist_controller_support as support


class DummyDatabaseManager:
    def __init__(self, playlists=None, items=None, fail=False):
        self.playlists = list(playlists or [])
        self.items = dict(items or {})
        self.fail = fail
        self.calls = []

    def get_all_playlists(self):
        self.calls.append('get_all_playlists')
        if self.fail:
            raise RuntimeError('db fail')
        return list(self.playlists)

    def get_playlist_items(self, playlist_id):
        self.calls.append(('get_playlist_items', playlist_id))
        if self.fail:
            raise RuntimeError('items fail')
        return list(self.items.get(playlist_id, []))


class LegacyLocalizationManager:
    def get_text(self, key):
        mapping = {
            'playlist_created': 'Playlist {name}',
            'error_initializing_playlist_controller': 'init failed {error}',
            'error_key': 'error {error}',
        }
        return mapping.get(key, key)


class ModernLocalizationManager:
    def __init__(self, fail=False):
        self.fail = fail

    def get_text(self, key, default=None):
        if self.fail:
            raise RuntimeError('loc fail')
        mapping = {
            'feedback_key': 'ok {name}',
            'format_broken': 'broken {missing}',
            'error_key': 'error {error}',
        }
        return mapping.get(key, default if default is not None else key)


class DummyEventBus:
    def __init__(self, fail=False, no_publish=False):
        self.fail = fail
        self.no_publish = no_publish
        self.published = []
        if no_publish:
            try:
                del self.publish
            except AttributeError:
                pass

    def publish(self, event_type, payload):
        if self.fail:
            raise RuntimeError('publish fail')
        self.published.append((event_type, payload))


class DummyController:
    def __init__(self, db, loc, bus):
        self.database_manager = db
        self.localization_manager = loc
        self.event_bus = bus
        self._playlists_cache = {}
        self._playlist_items_cache = {}
        self._current_playlist_id = 9
        self._currently_playing = 8
        self._cache_valid = False
        self.synced = 0
        self.handled = []

    def _sync_all_playlist_files(self):
        self.synced += 1


support.attach_playlist_controller_support_behavior(DummyController)


def test_refresh_cache_loads_sorted_items_and_marks_valid():
    db = DummyDatabaseManager(
        playlists=[{'id': 2, 'name': 'Beta'}, {'id': 1, 'name': 'Alpha'}],
        items={
            2: [
                {'position': 3, 'media_path': 'c.mp3'},
                {'position': 1, 'media_path': 'a.mp3'},
            ],
            1: [
                {'media_path': 'x.mp3'},
            ],
        },
    )
    controller = DummyController(db, ModernLocalizationManager(), DummyEventBus())

    controller._refresh_cache()

    assert controller._cache_valid is True
    assert controller._playlists_cache[1]['name'] == 'Alpha'
    assert controller._playlist_items_cache[2] == [(1, 'a.mp3'), (3, 'c.mp3')]
    assert controller._playlist_items_cache[1] == [(0, 'x.mp3')]
    assert controller.synced == 1


def test_refresh_cache_failure_invalidates_and_initialize_routes_error():
    db = DummyDatabaseManager(fail=True)
    controller = DummyController(db, LegacyLocalizationManager(), DummyEventBus())
    calls = []
    controller._handle_error = lambda error, key, **kwargs: calls.append((type(error).__name__, key, kwargs))

    controller._initialize_controller()
    assert calls == [('RuntimeError', 'error_initializing_playlist_controller', {})]

    try:
        support._refresh_cache(controller)
    except RuntimeError:
        pass
    else:
        raise AssertionError('RuntimeError expected')

    assert controller._cache_valid is False


def test_get_localized_text_supports_legacy_modern_and_format_fallbacks():
    legacy = DummyController(DummyDatabaseManager(), LegacyLocalizationManager(), DummyEventBus())
    modern = DummyController(DummyDatabaseManager(), ModernLocalizationManager(), DummyEventBus())
    broken = DummyController(DummyDatabaseManager(), ModernLocalizationManager(fail=True), DummyEventBus())

    assert legacy._get_localized_text('playlist_created', name='Mix') == 'Playlist Mix'
    assert modern._get_localized_text('feedback_key', name='Nova') == 'ok Nova'
    assert modern._get_localized_text('format_broken', name='ignored') == 'broken {missing}'
    assert broken._get_localized_text('missing_key') == 'missing_key'


def test_notify_feedback_handle_error_and_playlist_updated_publish_safely():
    bus = DummyEventBus()
    controller = DummyController(DummyDatabaseManager(), ModernLocalizationManager(), bus)
    controller._playlists_cache = {4: {'name': 'Roadtrip'}}

    controller._notify_feedback('feedback_key', name='Wave')
    controller._handle_error(RuntimeError('boom'), 'error_key')
    controller._notify_playlist_updated(4)

    assert bus.published == [
        (AudioEventType.FEEDBACK_MESSAGE, {'message': 'ok Wave', 'color': 'green'}),
        (AudioEventType.ERROR, {'message': 'error boom: boom'}),
        (AudioEventType.PLAYLIST_UPDATED, {'id': 4, 'name': 'Roadtrip'}),
    ]

    failing = DummyController(DummyDatabaseManager(), ModernLocalizationManager(), DummyEventBus(fail=True))
    failing._notify_feedback('feedback_key', name='Wave')
    failing._handle_error(RuntimeError('boom'), 'error_key')
    failing._notify_playlist_updated(4)

    no_publish = DummyController(DummyDatabaseManager(), ModernLocalizationManager(), DummyEventBus(no_publish=True))
    no_publish._notify_feedback('feedback_key', name='Wave')


def test_validate_name_exists_and_close_reset_state():
    controller = DummyController(DummyDatabaseManager(), ModernLocalizationManager(), DummyEventBus())
    controller._playlists_cache = {
        1: {'name': 'Morning Mix'},
        2: {'name': 'Night Ride'},
    }
    controller._playlist_items_cache = {1: [(1, 'a.mp3')]}

    assert controller._validate_playlist_name('  Good Name  ') is True
    assert controller._validate_playlist_name('') is False
    assert controller._validate_playlist_name('x' * 101) is False
    assert controller._playlist_name_exists('morning mix') is True
    assert controller._playlist_name_exists('morning mix', exclude_id=1) is False
    assert controller._playlist_name_exists('unknown') is False

    controller.close()
    assert controller._playlists_cache == {}
    assert controller._playlist_items_cache == {}
    assert controller._current_playlist_id is None
    assert controller._currently_playing is None
    assert controller._cache_valid is False
