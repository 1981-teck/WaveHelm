from __future__ import annotations

import numpy as np

from src.audio import effects_dsp


def test_apply_vintage_filter_returns_original_audio_on_typed_failure(monkeypatch):
    audio = np.array([[0.1], [0.2], [0.3]], dtype=np.float32)

    monkeypatch.setattr(
        effects_dsp.signal,
        'lfilter',
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError('filter fail')),
    )

    result = effects_dsp.apply_vintage_filter(
        audio_data=audio,
        sample_rate=44100,
        cutoff_freq=1200.0,
        resonance=0.5,
    )

    assert np.array_equal(result, audio)
