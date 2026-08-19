# -*- coding: utf-8 -*-
"""mf_base.py - Modulo di compatibilità (ponte).

Questo modulo riespone simboli dai moduli in `component_base/` per mantenere la
retrocompatibilità con codice esistente che importa da `src.video.mf_base`.

Requisiti:
- Niente wildcard imports (evita collisioni invisibili e rende tracciabile l'export)
- Presenza di __all__ (controlla cosa viene esportato)
- Nessuna logica duplicata (es. _hr_ok viene re-esposto da definitions.py)

Nuovo codice dovrebbe importare direttamente da:
- src.video.component_base.definitions
- src.video.component_base.com_helpers
- src.video.component_base.mf_helpers
- src.video.component_base.utils

Nota: questo modulo non deve introdurre side-effect applicativi (niente MFStartup/COM init su import).
"""

from __future__ import annotations

import logging
from types import ModuleType
from typing import Iterable, List

logger = logging.getLogger(__name__)

# Compatibility modules can legitimately expose a name that disappears at
# resolution time (for example through a dynamic module attribute). Only that
# absence is skipped; unexpected runtime failures must remain visible.
MF_BASE_EXPORT_EXCEPTIONS = (AttributeError,)


def _public_names_from_module(mod: ModuleType) -> List[str]:
    """Ritorna i nomi pubblici di un modulo.

    Se il modulo definisce __all__, usa quello.
    Altrimenti esporta i nomi che non iniziano con '_' (convenzione Python).
    """
    names = getattr(mod, "__all__", None)
    if isinstance(names, (list, tuple)) and all(isinstance(x, str) for x in names):
        return list(names)
    return [n for n in dir(mod) if not n.startswith("_")]


def _export(mod: ModuleType, names: Iterable[str], *, source_label: str) -> None:
    """Esporta in globals() i simboli indicati.

    Mantiene la semantica storica dei vecchi star-import:
    l'ordine di import decide chi "vince" in caso di collisione.
    In caso di collisione con oggetti diversi, viene loggato un warning.
    """
    g = globals()
    for name in names:
        if name == "logger":
            # Keep the bridge logger stable and avoid noisy collisions from submodules.
            continue
        try:
            value = getattr(mod, name)
        except MF_BASE_EXPORT_EXCEPTIONS:
            continue

        if name in g and g[name] is not value:
            logger.warning(
                "[mf_base] Collisione export '%s': sovrascrivo (%s) con (%s)",
                name,
                type(g[name]).__name__,
                source_label,
            )
        g[name] = value


# Import espliciti dei moduli sorgente
from .component_base import definitions as _definitions
from .component_base import com_helpers as _com_helpers
from .component_base import mf_helpers as _mf_helpers
from .component_base import utils as _utils

# Re-export controllato (ordine storico: definitions, com_helpers, mf_helpers, utils)
_export(_definitions, _public_names_from_module(_definitions), source_label="definitions")
_export(_com_helpers, _public_names_from_module(_com_helpers), source_label="com_helpers")
_export(_mf_helpers, _public_names_from_module(_mf_helpers), source_label="mf_helpers")
_export(_utils, _public_names_from_module(_utils), source_label="utils")

# Re-export esplicito di helper legacy "underscore" usati da import diretti.
# (Non entra in __all__ a meno che tu non lo aggiunga esplicitamente.)
_hr_ok = _definitions._hr_ok  # type: ignore[attr-defined]

# __all__ limita i wildcard import da mf_base (from mf_base import *).
__all__ = sorted(
    set(
        _public_names_from_module(_definitions)
        + _public_names_from_module(_com_helpers)
        + _public_names_from_module(_mf_helpers)
        + _public_names_from_module(_utils)
    )
)

logger.debug("[mf_base] Modulo di compatibilità caricato. Exports=%d", len(__all__))
