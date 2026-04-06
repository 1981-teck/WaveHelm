from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import src.model.media_loader as media_loader_module
from src.utils.media_metadata import AudioMetadataError, AudioTagMetadata
from src.model.media_file import MediaType
from src.model.media_loader import MediaLoader


class DummyLocalization:
    def get_text(self, key, **kwargs):
        return key


class DummyVideoCapture:
    def __init__(self, path, *, opened=True, frame_count=300.0, fps=30.0):
        self.path = path
        self._opened = opened
        self._frame_count = frame_count
        self._fps = fps
        self.released = False

    def isOpened(self):
        return self._opened

    def get(self, prop):
        if prop == media_loader_module.cv2.CAP_PROP_FRAME_COUNT:
            return self._frame_count
        if prop == media_loader_module.cv2.CAP_PROP_FPS:
            return self._fps
        return 0.0

    def release(self):
        self.released = True


TEST_RUNTIME = Path(__file__).resolve().parent / '_media_loader_runtime'


def _fresh_runtime_dir() -> Path:
    if TEST_RUNTIME.exists():
        shutil.rmtree(TEST_RUNTIME)
    TEST_RUNTIME.mkdir(parents=True, exist_ok=True)
    return TEST_RUNTIME



def test_probe_duration_ffprobe_success_and_failure(monkeypatch):
    runtime = _fresh_runtime_dir()
    loader = MediaLoader(DummyLocalization())
    media_path = runtime / 'track.mp3'
    media_path.write_text('x', encoding='utf-8')

    monkeypatch.setattr(media_loader_module, 'FFPROBE_AVAILABLE', True)

    class Result:
        def __init__(self, returncode=0, stdout=''):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = ''

    monkeypatch.setattr(
        media_loader_module.subprocess,
        'run',
        lambda *args, **kwargs: Result(stdout=json.dumps({'format': {'duration': '12.5'}})),
    )
    assert loader._probe_duration_ffprobe(str(media_path)) == 12.5

    monkeypatch.setattr(media_loader_module.subprocess, 'run', lambda *args, **kwargs: Result(returncode=1))
    assert loader._probe_duration_ffprobe(str(media_path)) == 0.0

    monkeypatch.setattr(media_loader_module.subprocess, 'run', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('boom')))
    assert loader._probe_duration_ffprobe(str(media_path)) == 0.0



def test_load_audio_uses_audio_metadata_reader_and_falls_back_to_basic_media(monkeypatch):
    runtime = _fresh_runtime_dir()
    loader = MediaLoader(DummyLocalization())
    audio_path = runtime / 'song.mp3'
    audio_path.write_text('x', encoding='utf-8')

    monkeypatch.setattr(media_loader_module, 'FFPROBE_AVAILABLE', True)
    monkeypatch.setattr(
        media_loader_module,
        'read_audio_rich_metadata',
        lambda path: AudioTagMetadata(duration=0.0, cover_image=b'img', lyrics='hello'),
    )
    monkeypatch.setattr(loader, '_probe_duration_ffprobe', lambda path: 33.0)

    media = loader.load_audio(str(audio_path))
    assert media is not None
    assert media.media_type == MediaType.AUDIO
    assert media.duration == 33.0
    assert media.metadata['cover_image'] == b'img'
    assert media.metadata['lyrics'] == 'hello'

    monkeypatch.setattr(
        media_loader_module,
        'read_audio_rich_metadata',
        lambda path: (_ for _ in ()).throw(AudioMetadataError('bad audio')),
    )
    fallback = loader.load_audio(str(audio_path))
    assert fallback is not None
    assert fallback.media_type == MediaType.AUDIO
    assert fallback.duration == 0.0



def test_load_video_uses_opencv_and_ffprobe_fallbacks(monkeypatch):
    runtime = _fresh_runtime_dir()
    loader = MediaLoader(DummyLocalization())
    video_path = runtime / 'clip.mp4'
    video_path.write_text('x', encoding='utf-8')

    monkeypatch.setattr(media_loader_module, 'FFPROBE_AVAILABLE', True)
    monkeypatch.setattr(media_loader_module, 'OPENCV_AVAILABLE', True)

    fake_cv2 = SimpleNamespace(
        CAP_PROP_FRAME_COUNT=1,
        CAP_PROP_FPS=2,
        VideoCapture=lambda path: DummyVideoCapture(path, opened=True, frame_count=600.0, fps=60.0),
    )
    monkeypatch.setattr(media_loader_module, 'cv2', fake_cv2)

    media = loader.load_video(str(video_path))
    assert media is not None
    assert media.media_type == MediaType.VIDEO
    assert media.duration == 10.0

    failing_cv2 = SimpleNamespace(
        CAP_PROP_FRAME_COUNT=1,
        CAP_PROP_FPS=2,
        VideoCapture=lambda path: (_ for _ in ()).throw(RuntimeError('opencv fail')),
    )
    monkeypatch.setattr(media_loader_module, 'cv2', failing_cv2)
    monkeypatch.setattr(loader, '_probe_duration_ffprobe', lambda path: 44.0)
    media_ffprobe = loader.load_video(str(video_path))
    assert media_ffprobe is not None
    assert media_ffprobe.duration == 44.0



def test_get_video_subtitles_reads_sidecar_file():
    runtime = _fresh_runtime_dir()
    loader = MediaLoader(DummyLocalization())
    video_path = runtime / 'movie.mp4'
    subtitle_path = runtime / 'movie.srt'
    video_path.write_text('x', encoding='utf-8')
    subtitle_path.write_text('1\n00:00:00,000 --> 00:00:01,000\nHello', encoding='utf-8')

    subtitles = loader._get_video_subtitles(str(video_path))
    assert subtitles == [{'lang': 'und', 'text': '1\n00:00:00,000 --> 00:00:01,000\nHello'}]
