from __future__ import annotations

from src.controller import playlist_controller_playlists as playlists_mod


class DummyDB:
    def __init__(self, fail_create=False, fail_rename=False, fail_delete=False):
        self.fail_create = fail_create
        self.fail_rename = fail_rename
        self.fail_delete = fail_delete
        self.created = []

    def create_playlist(self, name, description=''):
        if self.fail_create:
            raise RuntimeError('create fail')
        self.created.append((name, description))
        return 7

    def update_playlist_name(self, playlist_id, new_name):
        if self.fail_rename:
            raise RuntimeError('rename fail')

    def delete_playlist(self, playlist_id):
        if self.fail_delete:
            raise RuntimeError('delete fail')


class DummyEventBus:
    def __init__(self):
        self.calls = []

    def publish(self, event_type, payload):
        self.calls.append((event_type, payload))


class DummyController:
    def __init__(self):
        self.database_manager = DummyDB()
        self.event_bus = DummyEventBus()
        self._playlists_cache = {1: {'name': 'Old', 'description': ''}}
        self._playlist_items_cache = {1: []}
        self._current_playlist_id = None
        self._currently_playing = None
        self.feedback = []
        self.updated = []
        self.synced = []
        self.deleted = []
        self.handled = []
        self.valid_names = set(['Old', 'New'])
        self.duplicate_name = None

    def _validate_playlist_name(self, name):
        return bool(name)

    def _playlist_name_exists(self, name, exclude_id=None):
        return name == self.duplicate_name

    def _notify_feedback(self, key, **kwargs):
        self.feedback.append((key, kwargs))

    def _notify_playlist_updated(self, playlist_id):
        self.updated.append(playlist_id)

    def _sync_playlist_file(self, playlist_id, previous_name=None):
        self.synced.append((playlist_id, previous_name))

    def _delete_playlist_storage(self, playlist_id, playlist_name):
        self.deleted.append((playlist_id, playlist_name))

    def _ensure_cache_valid(self):
        return None

    def _handle_error(self, error, key, **kwargs):
        self.handled.append((str(error), key, kwargs))


for _name in [
    'create_playlist', 'rename_playlist', 'delete_playlist', 'get_all_playlists',
    'get_playlist_by_id', 'search_playlists', 'set_current_playlist',
    'set_currently_playing', 'get_currently_playing',
]:
    setattr(DummyController, _name, getattr(playlists_mod, _name))
DummyController.current_playlist_id = playlists_mod.current_playlist_id


def test_create_playlist_handles_database_failure():
    controller = DummyController()
    controller.database_manager = DummyDB(fail_create=True)

    result = controller.create_playlist('Demo')

    assert result is None
    assert controller.handled == [('create fail', 'error_creating_playlist', {'name': 'Demo'})]


def test_rename_playlist_handles_database_failure():
    controller = DummyController()
    controller.database_manager = DummyDB(fail_rename=True)

    controller.rename_playlist(1, 'Renamed')

    assert controller.handled == [('rename fail', 'error_renaming_playlist', {'name': 'Renamed'})]


def test_delete_playlist_handles_database_failure():
    controller = DummyController()
    controller.database_manager = DummyDB(fail_delete=True)

    controller.delete_playlist(1)

    assert controller.handled == [('delete fail', 'error_deleting_playlist', {'name': 'Old'})]
