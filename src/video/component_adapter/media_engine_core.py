# -*- coding: utf-8 -*-
"""component_adapter/media_engine_core.py

Bridge module.

Il progetto ha storicamente importato MediaEngineCore da:
    src.video.component_adapter.media_engine_core

Per evitare divergenze (metodi mancanti come ensure_engine/load_source/shutdown),
questo modulo re-esporta l'implementazione canonica in:
    src.video.media_engine_core

Nota: non aggiungere logica qui; tenere una sola sorgente di verità.
"""

from __future__ import annotations

from ..media_engine_core import MediaEngineCore, MediaEngineError

__all__ = [
    "MediaEngineCore",
    "MediaEngineError",
]
