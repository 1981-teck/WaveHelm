# -*- coding: utf-8 -*-
"""src.video.component_base

Base layer (ctypes/COM/MF) per l'integrazione Media Foundation.

Contiene moduli a basso livello:
- definitions.py  (GUID, costanti, binding Win32/COM, vtable specs)
- com_helpers.py  (ComPtr, _check_hr, util HRESULT)
- mf_helpers.py   (MFStartup/MFShutdown, MFCreateAttributes, factory MediaEngine)
- utils.py        (safe_release, is_success)

Questo package NON deve avere side-effect su import (niente MFStartup/COM init).
"""

__all__: list[str] = ["com_helpers", "definitions", "mf_helpers", "utils"]

# Added for Video ABI kit (DXGI/D3D11 struct layouts)
from .win_types import *  # noqa: F401,F403
from .dxgi_structs import *  # noqa: F401,F403
from .d3d11_structs import *  # noqa: F401,F403
from .iid_registry import *  # noqa: F401,F403
