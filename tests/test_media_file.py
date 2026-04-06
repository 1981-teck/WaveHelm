from __future__ import annotations

import logging
import shutil
from pathlib import Path

import src.model.media_file as media_file_module
from src.model.media_file import MediaFile, MediaType


RUNTIME_ROOT = Path(__file__).resolve().parent / '_media_file_runtime'


def _runtime_dir(name: str) -> Path:
    target = RUNTIME_ROOT / name
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    return target


def test_post_init_clamps_negative_duration_and_resets_bad_metadata():
    runtime = _runtime_dir('post_init')
    media_path = runtime / 'track.mp3'
    media_path.write_text('x', encoding='utf-8')

    media = MediaFile(path=str(media_path), duration=-5.0, metadata=['bad'])

    assert media.duration == 0.0
    assert media.metadata == {}
    assert media.title == 'Sconosciuto'
    assert media.media_type == MediaType.AUDIO


def test_get_formatted_duration_returns_default_on_invalid_value():
    media = MediaFile(path='https://example.com/stream', media_type=MediaType.STREAM)
    media.duration = 'bad'

    assert media.get_formatted_duration() == '00:00'


def test_from_dict_handles_unknown_type_and_invalid_input():
    media = MediaFile.from_dict({'path': 'https://example.com/video', 'media_type': 'weird'})

    assert media is not None
    assert media.media_type == MediaType.STREAM
    assert MediaFile.from_dict([]) is None


def test_detect_media_type_logs_invalid_metadata_enum(caplog):
    caplog.set_level(logging.DEBUG, logger=media_file_module.logger.name)

    media = MediaFile(path='mystery.bin', metadata={'media_type': 'broken'})

    assert media.media_type == MediaType.UNKNOWN
    assert 'Invalid media_type metadata' in caplog.text
