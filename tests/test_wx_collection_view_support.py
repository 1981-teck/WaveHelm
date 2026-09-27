from __future__ import annotations

import os
from types import SimpleNamespace

from src.model.media_file import MediaFile, MediaType
from src.ui_wx.collection_view_support import (
    canonical_media_path,
    collect_media_file_paths,
    filter_library_media,
    library_row_values,
    playlist_id,
    playlist_name,
    playlist_track_row_values,
    select_file_paths,
    select_folder_path,
    selected_items,
    selected_media_paths,
    sort_library_media,
)
from tests.wx_fakes import FakeDirDialog, FakeFileDialog, FakeListCtrl


TEST_EXCEPTIONS = (AttributeError, OSError, RuntimeError, TypeError, ValueError)


class DialogWx:
    FileDialog = FakeFileDialog
    DirDialog = FakeDirDialog


def test_collection_support_canonicalizes_local_paths_and_preserves_urls():
    local = canonical_media_path('/tmp/../tmp/song.mp3', exceptions=TEST_EXCEPTIONS)
    assert local == os.path.normcase(os.path.abspath(os.path.normpath('/tmp/song.mp3')))
    assert canonical_media_path('https://example.test/song.mp3', exceptions=TEST_EXCEPTIONS) == 'https://example.test/song.mp3'
    assert canonical_media_path('', exceptions=TEST_EXCEPTIONS) == ''


def test_collection_support_filters_sorts_and_formats_library_rows():
    alpha = MediaFile(
        '/tmp/a.mp3', title='Alpha', media_type=MediaType.AUDIO, duration=65.0,
        metadata={'artist': 'Zulu', 'album': 'One'},
    )
    bravo = MediaFile(
        '/tmp/b.mp4', title='Bravo', media_type=MediaType.VIDEO, duration=5.0,
        metadata={'artist': 'Able', 'album': 'Two'},
    )

    assert filter_library_media([alpha, bravo], query='able', selected_filter=MediaType.ALL) == [bravo]
    assert filter_library_media([alpha, bravo], query='', selected_filter=MediaType.AUDIO) == [alpha]
    assert sort_library_media([alpha, bravo], sort_name='artist') == [bravo, alpha]
    assert sort_library_media([alpha, bravo], sort_name='duration') == [bravo, alpha]

    row = library_row_values(
        bravo,
        media_type_text=lambda media_type: media_type.value,
        duration_text=lambda seconds: f'{int(seconds)}s',
    )
    assert row == ['Bravo', 'Able', 'Two', '5s', MediaType.VIDEO.value, bravo.path]


def test_collection_support_selects_items_and_media_paths():
    table = FakeListCtrl()
    media = [
        MediaFile('/tmp/a.mp3', title='A', media_type=MediaType.AUDIO),
        MediaFile('/tmp/b.mp3', title='B', media_type=MediaType.AUDIO),
        MediaFile('/tmp/c.mp3', title='C', media_type=MediaType.AUDIO),
    ]
    table.Select(0, True)
    table.Select(2, True)

    assert selected_items(table, media) == [media[0], media[2]]
    assert selected_media_paths(table, media) == [media[0].path, media[2].path]


def test_collection_support_playlist_identity_and_track_rows():
    assert playlist_id({'id': '7'}, exceptions=TEST_EXCEPTIONS) == 7
    assert playlist_id({'id': None}, exceptions=TEST_EXCEPTIONS) is None
    assert playlist_name({'name': '  Focus  '}) == 'Focus'

    media = MediaFile('/tmp/fallback-title.mp3', media_type=MediaType.AUDIO, duration=61.0, metadata={'artist': 'Artist'})
    row = playlist_track_row_values(media, duration_text=lambda seconds: f'{int(seconds)}s')
    assert row == ['fallback-title', 'Artist', '61s', media.path]


def test_collection_support_file_and_folder_dialogs_preserve_contracts():
    FakeFileDialog.next_result = 1
    FakeFileDialog.next_paths = ['/tmp/a.mp3', '/tmp/b.mp3']
    FakeDirDialog.next_result = 1
    FakeDirDialog.next_paths = ['/tmp/folder']
    unavailable_calls: list[str] = []

    assert select_file_paths(
        DialogWx,
        object(),
        message='files',
        cancelled_results={0, -1},
        unavailable=lambda: unavailable_calls.append('file'),
    ) == ['/tmp/a.mp3', '/tmp/b.mp3']
    assert select_folder_path(
        DialogWx,
        object(),
        message='folder',
        cancelled_results={0, -1},
        unavailable=lambda: unavailable_calls.append('folder'),
        allow_paths_fallback=False,
    ) == '/tmp/folder'
    assert unavailable_calls == []


def test_collection_support_dialog_unavailable_and_cancel_are_bounded():
    unavailable_calls: list[str] = []
    no_dialogs = SimpleNamespace()
    assert select_file_paths(
        no_dialogs,
        object(),
        message='files',
        cancelled_results={0, -1},
        unavailable=lambda: unavailable_calls.append('file'),
    ) == []
    assert select_folder_path(
        no_dialogs,
        object(),
        message='folder',
        cancelled_results={0, -1},
        unavailable=lambda: unavailable_calls.append('folder'),
        allow_paths_fallback=True,
    ) == ''
    assert unavailable_calls == ['file', 'folder']

    FakeFileDialog.next_result = 0
    FakeFileDialog.next_paths = ['/tmp/ignored.mp3']
    assert select_file_paths(
        DialogWx,
        object(),
        message='files',
        cancelled_results={0, -1},
        unavailable=lambda: None,
    ) == []


def test_collection_support_collects_supported_paths_recursively(tmp_path):
    root = tmp_path / 'media'
    nested = root / 'nested'
    nested.mkdir(parents=True)
    audio = root / 'a.mp3'
    video = nested / 'b.mp4'
    ignored = root / 'notes.txt'
    audio.write_bytes(b'')
    video.write_bytes(b'')
    ignored.write_text('ignore', encoding='utf-8')

    paths = collect_media_file_paths(
        str(root),
        is_media_file=lambda path: path.endswith(('.mp3', '.mp4')),
    )
    assert paths == sorted([str(audio), str(video)], key=str.lower)
