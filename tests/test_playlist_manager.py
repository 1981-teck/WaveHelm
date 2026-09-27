from __future__ import annotations

import shutil
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

import src.model.component_database.db_core as db_core_module
from src.model.component_database.db_core import DbCore
from src.model.component_database.playlist_manager import PlaylistManager
from src.model.component_database.playlist_position import (
    PlaylistPositionInvariantError,
)
from src.utils.exceptions import DatabaseError, IntegrityError, NotFoundError


RUNTIME_ROOT = Path(__file__).resolve().parent / "_playlist_manager_runtime"


class DummyLocalization:
    def get_text(self, key: str, **kwargs):
        return key


@pytest.fixture(autouse=True)
def reset_db_core_singleton():
    instance = getattr(DbCore, '_instance', None)
    if instance is not None and getattr(instance, 'conn', None) is not None:
        instance.close()
    DbCore._instance = None
    yield
    instance = getattr(DbCore, '_instance', None)
    if instance is not None and getattr(instance, 'conn', None) is not None:
        instance.close()
    DbCore._instance = None


def _runtime_dir(name: str) -> Path:
    target = RUNTIME_ROOT / name
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    return target


def _make_manager(monkeypatch, name: str):
    runtime_dir = _runtime_dir(name)
    monkeypatch.setattr(db_core_module, 'get_user_data_dir', lambda: runtime_dir)
    core = DbCore(db_path='playlist.db', localization_manager=DummyLocalization())
    return PlaylistManager(core), core


def test_create_rename_and_duplicate_validation(monkeypatch):
    manager, core = _make_manager(monkeypatch, 'create_rename')

    alpha_id = manager.create_playlist('Alpha')
    beta_id = manager.create_playlist('Beta')
    assert alpha_id != beta_id

    manager.rename_playlist(alpha_id, 'Gamma')
    assert manager.get_playlist_by_id(alpha_id)['name'] == 'Gamma'

    with pytest.raises(IntegrityError):
        manager.rename_playlist(beta_id, 'gamma')

    with pytest.raises(ValueError):
        manager.rename_playlist(alpha_id, '   ')

    with pytest.raises(IntegrityError):
        manager.create_playlist('Gamma')

    core.close()


def test_playlist_items_add_remove_reorder_and_delete(monkeypatch):
    manager, core = _make_manager(monkeypatch, 'items_flow')
    playlist_id = manager.create_playlist('Flow')

    manager.add_playlist_item(playlist_id, 'track_1.wav')
    manager.add_playlist_item(playlist_id, 'track_2.wav')
    manager.add_playlist_item(playlist_id, 'track_0.wav', position=1)

    items = manager.get_playlist_items(playlist_id)
    assert [item['media_path'] for item in items] == ['track_0.wav', 'track_1.wav', 'track_2.wav']
    assert [item['position'] for item in items] == [1, 2, 3]

    manager.reorder_playlist_item(playlist_id, 'track_2.wav', 1)
    items = manager.get_playlist_items(playlist_id)
    assert [item['media_path'] for item in items] == ['track_2.wav', 'track_0.wav', 'track_1.wav']
    assert [item['position'] for item in items] == [1, 2, 3]

    manager.remove_playlist_item(playlist_id, 'track_0.wav')
    items = manager.get_playlist_items(playlist_id)
    assert [item['media_path'] for item in items] == ['track_2.wav', 'track_1.wav']
    assert [item['position'] for item in items] == [1, 2]

    with pytest.raises(IntegrityError):
        manager.add_playlist_item(playlist_id, 'track_1.wav')

    manager.delete_playlist(playlist_id)
    assert manager.get_playlist_by_id(playlist_id) is None

    with pytest.raises(NotFoundError):
        manager.delete_playlist(playlist_id)

    core.close()


def test_get_playlist_items_recovers_invalid_metadata_json(monkeypatch):
    manager, core = _make_manager(monkeypatch, 'invalid_metadata')
    playlist_id = manager.create_playlist('Json')

    core._execute_query(
        "INSERT INTO library_items (path, title, media_type, duration, metadata) VALUES (?, ?, ?, ?, ?)",
        ('track_bad.wav', 'Bad', 'audio', 12.0, '{broken'),
    )
    core._execute_query(
        "INSERT INTO playlist_items (playlist_id, media_path, position, added_at) VALUES (?, ?, ?, ?)",
        (playlist_id, 'track_bad.wav', 1, '2024-01-01T00:00:00'),
    )

    items = manager.get_playlist_items(playlist_id)
    assert items[0]['metadata'] == {}

    core.close()


def test_playlist_mutations_run_on_full_synchronous_connections(monkeypatch):
    manager, core = _make_manager(monkeypatch, 'durable_mutations')
    observed_modes: list[int] = []
    real_durable_connection = core.durable_write_connection

    @contextmanager
    def tracked_durable_connection():
        with real_durable_connection() as connection:
            observed_modes.append(
                int(connection.execute("PRAGMA synchronous").fetchone()[0])
            )
            yield connection

    monkeypatch.setattr(core, 'durable_write_connection', tracked_durable_connection)

    playlist_id = manager.create_playlist('Durable')
    manager.add_playlist_item(playlist_id, 'track.wav')
    manager.reorder_playlist_item(playlist_id, 'track.wav', 1)
    manager.remove_playlist_item(playlist_id, 'track.wav')
    manager.rename_playlist(playlist_id, 'Durable Renamed')
    manager.delete_playlist(playlist_id)

    assert observed_modes == [2, 2, 2, 2, 2, 2]
    assert core.conn is not None
    assert int(core.conn.execute("PRAGMA synchronous").fetchone()[0]) == 1



def test_unicode_create_rename_lookup_share_one_identity(monkeypatch):
    manager, core = _make_manager(monkeypatch, 'unicode_identity')
    street_id = manager.create_playlist('Straße')
    accent_id = manager.create_playlist('é')

    assert manager.get_playlist_by_name('STRASSE')['id'] == street_id
    assert manager.get_playlist_by_name('e\u0301')['id'] == accent_id
    with pytest.raises(IntegrityError):
        manager.create_playlist('STRASSE')
    with pytest.raises(IntegrityError):
        manager.rename_playlist(accent_id, 'ＳＴＲＡＳＳＥ')

    manager.rename_playlist(street_id, 'STRASSE')
    renamed = manager.get_playlist_by_name('Straße')
    assert renamed is not None
    assert renamed['name'] == 'STRASSE'
    assert core.conn is not None
    row = core.conn.execute(
        'SELECT name, canonical_name FROM playlists WHERE id = ?', (street_id,)
    ).fetchone()
    assert tuple(row) == ('STRASSE', 'strasse')
    core.close()


@pytest.mark.parametrize('value', ['', '   ', 'x' * 101, 'bad\nname', None, 5])
def test_playlist_identity_inputs_fail_before_database_mutation(monkeypatch, value):
    manager, core = _make_manager(monkeypatch, f'invalid_identity_{type(value).__name__}')
    expected = TypeError if value is None or isinstance(value, int) else ValueError

    with pytest.raises(expected):
        manager.create_playlist(value)

    assert manager.get_all_playlists() == []
    core.close()


def test_concurrent_equivalent_creates_have_one_winner(monkeypatch):
    manager, core = _make_manager(monkeypatch, 'concurrent_equivalent_create')
    barrier = threading.Barrier(2)
    created: list[int] = []
    errors: list[Exception] = []

    def create(name: str) -> None:
        try:
            barrier.wait(timeout=5.0)
            created.append(manager.create_playlist(name))
        except Exception as error:
            errors.append(error)

    threads = [
        threading.Thread(target=create, args=('Straße',)),
        threading.Thread(target=create, args=('STRASSE',)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10.0)

    assert all(not thread.is_alive() for thread in threads)
    assert len(created) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], IntegrityError)
    assert len(manager.get_all_playlists()) == 1
    core.close()


def test_concurrent_equivalent_renames_are_database_enforced(monkeypatch):
    manager, core = _make_manager(monkeypatch, 'concurrent_equivalent_rename')
    first_id = manager.create_playlist('First')
    second_id = manager.create_playlist('Second')
    barrier = threading.Barrier(2)
    completed: list[int] = []
    errors: list[Exception] = []

    def rename(playlist_id: int, name: str) -> None:
        try:
            barrier.wait(timeout=5.0)
            manager.rename_playlist(playlist_id, name)
            completed.append(playlist_id)
        except Exception as error:
            errors.append(error)

    threads = [
        threading.Thread(target=rename, args=(first_id, 'é')),
        threading.Thread(target=rename, args=(second_id, 'e\u0301')),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10.0)

    assert all(not thread.is_alive() for thread in threads)
    assert len(completed) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], IntegrityError)
    assert manager.get_playlist_by_name('é')['id'] == completed[0]
    core.close()


@pytest.mark.parametrize(
    ('position', 'expected_error'),
    [
        (0, ValueError),
        (-1, ValueError),
        (4, ValueError),
        (True, TypeError),
        ('2', TypeError),
        (2.0, TypeError),
    ],
)
def test_add_position_rejects_non_exact_values_without_mutation(
    monkeypatch, position, expected_error
):
    manager, core = _make_manager(monkeypatch, f'invalid_add_{position!r}')
    playlist_id = manager.create_playlist('Exact Insert')
    manager.add_playlist_item(playlist_id, 'first.wav')
    manager.add_playlist_item(playlist_id, 'second.wav')

    with pytest.raises(expected_error):
        manager.add_playlist_item(playlist_id, 'candidate.wav', position=position)

    items = manager.get_playlist_items(playlist_id)
    assert [(item['media_path'], item['position']) for item in items] == [
        ('first.wav', 1),
        ('second.wav', 2),
    ]
    core.close()


@pytest.mark.parametrize(
    ('position', 'expected_error'),
    [
        (0, ValueError),
        (-1, ValueError),
        (3, ValueError),
        (True, TypeError),
        ('1', TypeError),
        (1.0, TypeError),
    ],
)
def test_reorder_position_rejects_non_exact_values_without_mutation(
    monkeypatch, position, expected_error
):
    manager, core = _make_manager(monkeypatch, f'invalid_reorder_{position!r}')
    playlist_id = manager.create_playlist('Exact Reorder')
    manager.add_playlist_item(playlist_id, 'first.wav')
    manager.add_playlist_item(playlist_id, 'second.wav')

    with pytest.raises(expected_error):
        manager.reorder_playlist_item(playlist_id, 'second.wav', position)

    items = manager.get_playlist_items(playlist_id)
    assert [(item['media_path'], item['position']) for item in items] == [
        ('first.wav', 1),
        ('second.wav', 2),
    ]
    core.close()


def test_middle_insert_and_bidirectional_reorder_preserve_one_to_n(monkeypatch):
    manager, core = _make_manager(monkeypatch, 'middle_and_reorder')
    playlist_id = manager.create_playlist('Sequence')
    for path in ('one.wav', 'two.wav', 'three.wav'):
        manager.add_playlist_item(playlist_id, path)

    manager.add_playlist_item(playlist_id, 'middle.wav', position=2)
    manager.reorder_playlist_item(playlist_id, 'three.wav', 1)
    manager.reorder_playlist_item(playlist_id, 'one.wav', 4)

    items = manager.get_playlist_items(playlist_id)
    assert [item['media_path'] for item in items] == [
        'three.wav',
        'middle.wav',
        'two.wav',
        'one.wav',
    ]
    assert [item['position'] for item in items] == [1, 2, 3, 4]
    core.close()


def test_sparse_external_state_is_rejected_before_read_or_mutation(monkeypatch):
    manager, core = _make_manager(monkeypatch, 'sparse_external_state')
    playlist_id = manager.create_playlist('Sparse')
    manager.add_playlist_item(playlist_id, 'first.wav')
    manager.add_playlist_item(playlist_id, 'second.wav')
    assert core.conn is not None
    core.conn.execute(
        'UPDATE playlist_items SET position = 20 WHERE playlist_id = ? AND position = 2',
        (playlist_id,),
    )
    core.conn.commit()

    with pytest.raises(PlaylistPositionInvariantError):
        manager.get_playlist_items(playlist_id)
    with pytest.raises(PlaylistPositionInvariantError):
        manager.add_playlist_item(playlist_id, 'third.wav')
    with pytest.raises(PlaylistPositionInvariantError):
        manager.remove_playlist_item(playlist_id, 'first.wav')

    rows = core.conn.execute(
        'SELECT media_path, position FROM playlist_items '
        'WHERE playlist_id = ? ORDER BY position',
        (playlist_id,),
    ).fetchall()
    assert [tuple(row) for row in rows] == [('first.wav', 1), ('second.wav', 20)]
    core.close()


def test_database_constraints_reject_non_positive_and_duplicate_positions(monkeypatch):
    manager, core = _make_manager(monkeypatch, 'database_position_constraints')
    playlist_id = manager.create_playlist('Constraints')
    manager.add_playlist_item(playlist_id, 'first.wav')
    timestamp = '2026-09-02T00:00:00'

    with core.durable_write_connection() as connection:
        with pytest.raises(db_core_module.sqlite3.IntegrityError):
            connection.execute(
                'INSERT INTO playlist_items '
                '(playlist_id, media_path, position, added_at) VALUES (?, ?, ?, ?)',
                (playlist_id, 'zero.wav', 0, timestamp),
            )
        connection.rollback()

    with core.durable_write_connection() as connection:
        with pytest.raises(db_core_module.sqlite3.IntegrityError):
            connection.execute(
                'INSERT INTO playlist_items '
                '(playlist_id, media_path, position, added_at) VALUES (?, ?, ?, ?)',
                (playlist_id, 'duplicate.wav', 1, timestamp),
            )
        connection.rollback()

    assert [item['position'] for item in manager.get_playlist_items(playlist_id)] == [1]
    core.close()
