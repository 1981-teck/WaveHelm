from __future__ import annotations

import sys
from types import SimpleNamespace

import numpy as np

import src.audio.analysis as analysis_mod


def test_load_audio_file_falls_back_to_basic_loader_when_soundfile_fails(monkeypatch):
    analyzer = analysis_mod.AudioAnalyzer(sample_rate=44100)
    fallback_audio = np.array([[1.0, 0.0], [0.5, 0.5]], dtype=np.float32)

    monkeypatch.setitem(
        sys.modules,
        'soundfile',
        SimpleNamespace(read=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('sf fail'))),
    )
    monkeypatch.setattr(
        analysis_mod.AudioAnalyzer,
        '_load_wav_basic',
        lambda self, path: (fallback_audio, 44100),
    )

    loaded = analyzer.load_audio_file('demo.wav')

    assert loaded is not None
    assert np.allclose(loaded, np.array([0.5, 0.5], dtype=np.float32))


def test_load_audio_file_returns_none_when_all_loaders_fail(monkeypatch):
    analyzer = analysis_mod.AudioAnalyzer(sample_rate=44100)

    monkeypatch.setitem(
        sys.modules,
        'soundfile',
        SimpleNamespace(read=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('sf fail'))),
    )
    monkeypatch.setattr(
        analysis_mod.AudioAnalyzer,
        '_load_wav_basic',
        lambda self, path: (_ for _ in ()).throw(ValueError('wav fail')),
    )

    assert analyzer.load_audio_file('broken.wav') is None
