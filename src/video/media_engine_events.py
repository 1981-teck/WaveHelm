# -*- coding: utf-8 -*-
"""src.video.media_engine_events

Compatibility shim.

In WaveHelm, the canonical implementation of the MediaEngine notify COM callback
lives under:

    src.video.component_adapter.media_engine_events

Some modules (e.g. src.video.media_engine_core) import it from:

    src.video.media_engine_events

To avoid import-time failures and keep backward compatibility, this module re-exports
the required symbol(s).
"""

from __future__ import annotations

from .component_adapter.media_engine_events import _MediaEngineNotifyCOM

__all__ = ["_MediaEngineNotifyCOM"]
