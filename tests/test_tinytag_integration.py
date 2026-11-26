from __future__ import annotations

import wave
from pathlib import Path

from src.utils.media_metadata import (
    read_audio_basic_metadata,
    read_audio_rich_metadata,
)


def test_tinytag_reads_real_wav_through_both_wavehelm_paths(tmp_path: Path) -> None:
    """Keep actual TinyTag parsing covered when its pinned release changes."""
    audio_path = tmp_path / "one-second.wav"
    with wave.open(str(audio_path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(8000)
        writer.writeframes(bytes(8000 * 2))

    basic = read_audio_basic_metadata(str(audio_path))
    rich = read_audio_rich_metadata(str(audio_path))

    assert 0.99 <= basic.duration <= 1.01
    assert 0.99 <= rich.duration <= 1.01
    assert rich.cover_image is None
