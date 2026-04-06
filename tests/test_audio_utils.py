from __future__ import annotations

import pytest

from src.audio.audio_utils import AudioUtils
from src.audio import audio_utils as audio_utils_mod


def test_get_raw_audio_data_wraps_typed_read_failure(monkeypatch):
    monkeypatch.setattr(audio_utils_mod.os.path, 'exists', lambda path: True)
    monkeypatch.setattr(
        AudioUtils,
        '_get_localized_text',
        staticmethod(lambda key, **kwargs: 'load error: {path} :: {error}'),
    )

    def fail_read(path, dtype='float32'):
        raise OSError('disk fail')

    monkeypatch.setattr(audio_utils_mod.sf, 'read', fail_read)

    with pytest.raises(ValueError, match='load error: demo.wav :: disk fail'):
        AudioUtils.get_raw_audio_data('demo.wav')
