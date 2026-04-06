from __future__ import annotations

from src.controller import playlist_controller_tracks as tracks_mod
from src.model.media_file import MediaFile, MediaType


class DummyDB:
    def __init__(self, fail_add=False, fail_remove=False):
        self.fail_add = fail_add
        self.fail_remove = fail_remove
        self.added = []
        self.removed = []

    def add_playlist_item(self, playlist_id, path, position):
        if self.fail_add:
            raise RuntimeError('add fail')
        self.added.append((playlist_id, path, position))

    def remove_playlist_item(self, playlist_id, path):
        if self.fail_remove:
            raise RuntimeError('remove fail')
        self.removed.append((playlist_id, path))

    def get_playlist_items(self, playlist_id):
        return []


class DummyEventBus:
    def __init__(self):
        self.calls = []

    def publish(self, event_type, payload):
        self.calls.append((event_type, payload))


class DummyLibraryController:
    def __init__(self, media=None, fail_add=False):
        self.media = media
        self.fail_add = fail_add
        self.add_calls = []

    def get_media_by_path(self, path):
        return self.media

    def add_media_files(self, paths):
        self.add_calls.append(paths)
        if self.fail_add:
            raise RuntimeError('library add fail')


class DummyController:
    def __init__(self):
        self._current_playlist_id = 1
        self._playlists_cache = {1: {'name': 'One'}}
        self._playlist_items_cache = {1: []}
        self.database_manager = DummyDB()
        self.event_bus = DummyEventBus()
        self.library_controller = DummyLibraryController(media=MediaFile(path='C:/song.mp3', title='Song', media_type=MediaType.AUDIO))
        self.feedback = []
        self.updated = []
        self.synced = []
        self.handled = []

    def _ensure_cache_valid(self):
        return None

    def _notify_feedback(self, key, **kwargs):
        self.feedback.append((key, kwargs))

    def _sync_playlist_file(self, playlist_id):
        self.synced.append(playlist_id)

    def _notify_playlist_updated(self, playlist_id):
        self.updated.append(playlist_id)

    def _handle_error(self, error, key):
        self.handled.append((str(error), key))


for _name in [
    'add_files_to_current_playlist', '_ensure_media_in_library', '_add_media_to_playlist',
    'remove_from_playlist', 'remove_many_from_playlist', 'get_tracks_in_playlist',
    'get_playlist_track_count', '_build_media_from_playlist_row',
]:
    setattr(DummyController, _name, getattr(tracks_mod, _name))


def test_add_files_to_current_playlist_counts_failures_without_crashing():
    controller = DummyController()
    controller.library_controller = DummyLibraryController(media=None, fail_add=True)

    controller.add_files_to_current_playlist(['C:/missing.mp3'])

    assert controller.feedback[-1][0] == 'some_tracks_failed_to_add'


def test_add_media_to_playlist_returns_false_on_db_failure():
    controller = DummyController()
    controller.database_manager = DummyDB(fail_add=True)

    result = controller._add_media_to_playlist(1, MediaFile(path='C:/song.mp3', title='Song', media_type=MediaType.AUDIO))

    assert result is False


def test_remove_from_playlist_handles_db_failure():
    controller = DummyController()
    controller.database_manager = DummyDB(fail_remove=True)
    controller._playlist_items_cache[1] = [(1, 'C:/song.mp3')]

    result = controller.remove_from_playlist(1, 'C:/song.mp3')

    assert result is False
    assert controller.handled == [('remove fail', 'error_removing_from_playlist')]
