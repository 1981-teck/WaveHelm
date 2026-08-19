from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
import src.controller.library_controller as library_module
from src.audio.audio_events import AudioEventType
from src.controller.library_controller import LibraryController, _canon_path_win
from src.model.media_file import MediaFile, MediaType
from src.utils import ffprobe_service
from src.utils.media_metadata import AudioTagMetadata


RUNTIME_ROOT = Path(__file__).resolve().parent / "_library_controller_runtime"


def _runtime_dir(name: str) -> Path:
    target = RUNTIME_ROOT / name
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    return target


class DummyEventBus:
    def __init__(self, fail_publish=False):
        self.fail_publish = fail_publish
        self.calls = []

    def publish(self, event_type, payload):
        if self.fail_publish:
            raise ValueError('publish fail')
        self.calls.append((event_type, payload))


class DummyDatabaseManager:
    def __init__(self):
        self.library_items = []
        self.removed = []
        self.favorites = set()
        self.favorite_items = []
        self.fail_on = {}

    def add_library_item(self, **kwargs):
        error = self.fail_on.get('add_library_item')
        if error:
            raise error
        self.library_items.append(kwargs)

    def remove_library_item(self, path):
        error = self.fail_on.get('remove_library_item')
        if error:
            raise error
        self.removed.append(path)

    def is_favorite(self, path):
        error = self.fail_on.get('is_favorite')
        if error:
            raise error
        return path in self.favorites

    def add_favorite(self, **kwargs):
        error = self.fail_on.get('add_favorite')
        if error:
            raise error
        self.favorite_items.append(kwargs)
        self.favorites.add(kwargs['path'])


def _make_controller(monkeypatch, name: str, *, event_bus=None, database_manager=None):
    runtime_dir = _runtime_dir(name)
    monkeypatch.setattr(library_module, 'get_app_data_path', lambda *parts, create=True: runtime_dir.joinpath(*parts) if parts else runtime_dir)
    event_bus = event_bus or DummyEventBus()
    database_manager = database_manager or DummyDatabaseManager()
    controller = LibraryController(event_bus=event_bus, database_manager=database_manager)
    return controller, event_bus, database_manager, runtime_dir


def test_canon_path_win_falls_back_on_bad_input():
    class BadPath:
        def __fspath__(self):
            raise TypeError('bad path')

    assert isinstance(_canon_path_win('demo.mp3'), str)
    bad = BadPath()
    assert _canon_path_win(bad) is bad


def test_add_media_files_from_objects_saves_syncs_and_deduplicates(monkeypatch):
    controller, event_bus, database_manager, runtime_dir = _make_controller(monkeypatch, 'add_objects')
    media = MediaFile(path=str(runtime_dir / 'song.wav'), title='Song', media_type=MediaType.AUDIO, duration=12.0, metadata={'artist': 'A'})
    duplicate = MediaFile(path=str(runtime_dir / 'song.wav'), title='Song', media_type=MediaType.AUDIO, duration=12.0, metadata={})

    controller.add_media_files_from_objects([media, duplicate])

    assert len(controller.get_all_media()) == 1
    assert database_manager.library_items[-1]['title'] == 'Song'
    assert controller._library_path.is_file()
    assert json.loads(controller._library_path.read_text(encoding='utf-8')) == [str(runtime_dir / 'song.wav')]
    assert [call[0] for call in event_bus.calls] == [AudioEventType.LIBRARY_UPDATED, AudioEventType.FEEDBACK_MESSAGE]


def test_load_persistent_library_restores_entries_and_handles_invalid_json(monkeypatch):
    runtime_dir = _runtime_dir('load_persistent')
    audio_file = runtime_dir / 'track.wav'
    audio_file.write_text('x', encoding='utf-8')
    library_file = runtime_dir / 'library.json'
    library_file.write_text(json.dumps([str(audio_file)]), encoding='utf-8')

    monkeypatch.setattr(library_module, 'get_app_data_path', lambda *parts, create=True: runtime_dir.joinpath(*parts) if parts else runtime_dir)
    monkeypatch.setattr(library_module, 'is_audio_file', lambda path: str(path).endswith('.wav'))
    monkeypatch.setattr(library_module, 'is_video_file', lambda path: False)
    monkeypatch.setattr(
        library_module,
        'read_audio_basic_metadata',
        lambda path: AudioTagMetadata(title=None, artist=None, album=None, duration=0.0),
    )

    controller = LibraryController(DummyEventBus(), DummyDatabaseManager())
    assert [media.path for media in controller.get_all_media()] == [str(audio_file)]

    broken_dir = _runtime_dir('load_invalid')
    (broken_dir / 'library.json').write_text('{broken', encoding='utf-8')
    monkeypatch.setattr(library_module, 'get_app_data_path', lambda *parts, create=True: broken_dir.joinpath(*parts) if parts else broken_dir)
    broken = LibraryController(DummyEventBus(), DummyDatabaseManager())
    assert broken.get_all_media() == []


def test_add_to_favorites_handles_duplicate_success_and_failure(monkeypatch):
    controller, event_bus, database_manager, runtime_dir = _make_controller(monkeypatch, 'favorites')
    media = MediaFile(path=str(runtime_dir / 'fav.wav'), title='Fav', media_type=MediaType.AUDIO, duration=5.0, metadata={})

    assert controller.add_to_favorites(media) is True
    assert database_manager.favorite_items[-1]['path'] == media.path
    assert [call[0] for call in event_bus.calls[-2:]] == [AudioEventType.FAVORITE_CHANGED, AudioEventType.FEEDBACK_MESSAGE]

    assert controller.add_to_favorites(media) is False
    assert event_bus.calls[-1][1]['color'] == 'orange'

    database_manager.fail_on['add_favorite'] = RuntimeError('db fail')
    other = MediaFile(path=str(runtime_dir / 'other.wav'), title='Other', media_type=MediaType.AUDIO, duration=3.0, metadata={})
    assert controller.add_to_favorites(other) is False
    assert event_bus.calls[-1][1]['color'] == 'red'


def test_remove_media_search_and_create_media_from_path(monkeypatch):
    controller, event_bus, database_manager, runtime_dir = _make_controller(monkeypatch, 'remove_search')
    audio_file = runtime_dir / 'track.mp3'
    audio_file.write_text('x', encoding='utf-8')
    video_file = runtime_dir / 'clip.mp4'
    video_file.write_text('x', encoding='utf-8')

    monkeypatch.setattr(library_module, 'is_audio_file', lambda path: str(path).endswith('.mp3'))
    monkeypatch.setattr(library_module, 'is_video_file', lambda path: str(path).endswith('.mp4'))

    def fake_read_audio_basic_metadata(path: str) -> AudioTagMetadata:
        if str(path).endswith('.mp3'):
            return AudioTagMetadata(title='Real Title', artist='Artist', album='Album', duration=91.0)
        raise RuntimeError('metadata fail')

    monkeypatch.setattr(library_module, 'read_audio_basic_metadata', fake_read_audio_basic_metadata)

    audio_media = controller._create_media_from_path(str(audio_file))
    video_media = controller._create_media_from_path(str(video_file))

    assert audio_media.title == 'Real Title'
    assert audio_media.metadata == {'artist': 'Artist', 'album': 'Album'}
    assert round(audio_media.duration, 1) == 91.0
    assert video_media.title == 'clip.mp4'
    assert video_media.media_type == MediaType.VIDEO

    controller.add_media_files_from_objects([audio_media, video_media], emit_event=False, emit_feedback=False)
    assert [m.title for m in controller.search_media('artist')] == ['Real Title']
    assert controller.get_media_by_path(str(audio_file)).title == 'Real Title'

    assert controller.remove_media(str(audio_file)) is True
    assert database_manager.removed == [str(audio_file)]
    assert controller.get_media_by_path(str(audio_file)) is None
    assert controller.remove_media(str(audio_file)) is False


def test_create_media_from_path_populates_video_audio_metadata_and_candidates(monkeypatch):
    controller, _, _, runtime_dir = _make_controller(monkeypatch, "video_audio_metadata")
    video_file = runtime_dir / "movie.mkv"
    video_file.write_text("x", encoding="utf-8")
    tracks = [
        {
            "stream_index": 4,
            "track_index": 0,
            "language": "eng",
            "title": "Atmos",
            "codec_name": "truehd",
            "codec_long_name": "TrueHD",
            "channels": 8,
            "channel_layout": "7.1",
            "is_default": True,
            "is_forced": False,
            "label": "Track 1 — eng — Atmos — TRUEHD — 7.1",
        },
        {
            "stream_index": 1,
            "track_index": 1,
            "language": "ita",
            "title": "Dub",
            "codec_name": "ac3",
            "codec_long_name": "AC-3",
            "channels": 6,
            "channel_layout": "5.1",
            "is_default": False,
            "is_forced": False,
            "label": "Track 2 — ita — Dub — AC3 — 5.1",
        },
    ]
    monkeypatch.setattr(library_module, "is_audio_file", lambda path: False)
    monkeypatch.setattr(library_module, "is_video_file", lambda path: str(path).endswith(".mkv"))
    fake_cv2 = SimpleNamespace(
        CAP_PROP_FPS=1,
        CAP_PROP_FRAME_COUNT=2,
        VideoCapture=lambda path: SimpleNamespace(
            isOpened=lambda: True,
            get=lambda prop: float("inf") if prop == 1 else 600.0,
            release=lambda: None,
        ),
    )
    monkeypatch.setattr(library_module, "cv2", fake_cv2)
    monkeypatch.setattr(ffprobe_service, "probe_duration", lambda path: 123.4)
    monkeypatch.setattr(ffprobe_service, "probe_audio_tracks", lambda path: tracks)

    video_media = controller._create_media_from_path(str(video_file))

    assert video_media.media_type == MediaType.VIDEO
    assert round(video_media.duration, 1) == 123.4
    assert [track["stream_index"] for track in video_media.metadata["audio_tracks"]] == [4, 1]
    assert video_media.metadata["audio_track_candidates"] == [1, 4]


def test_read_library_video_metadata_uses_shared_probe_and_fails_closed(monkeypatch):
    tracks = [
        {
            "stream_index": 2,
            "track_index": 0,
            "language": "ita",
            "title": "Main",
            "codec_name": "eac3",
            "codec_long_name": "E-AC-3",
            "channels": 6,
            "channel_layout": "5.1",
            "is_default": True,
            "is_forced": False,
            "label": "Track 1 — ita — Main — EAC3 — 5.1",
        }
    ]
    monkeypatch.setattr(ffprobe_service, "probe_audio_tracks", lambda path: tracks)
    metadata = library_module._read_library_video_metadata("movie.mkv")
    assert metadata["audio_tracks"] == tracks
    assert metadata["audio_track_candidates"] == [2]

    def raise_timeout(path: str):
        raise ffprobe_service.FfprobeTimeoutError("timeout")

    monkeypatch.setattr(ffprobe_service, "probe_audio_tracks", raise_timeout)
    assert library_module._read_library_video_metadata("movie.mkv") == {
        "audio_tracks": [],
        "audio_track_candidates": [],
    }

