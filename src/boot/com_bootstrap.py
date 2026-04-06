from __future__ import annotations

"""
Legacy explicit COM bootstrap helper for Windows backends.

The main application entry point no longer calls this module directly. Current
startup centralizes the process-wide COM policy in
``src.boot.runtime_bootstrap.configure_comtypes_policy()`` and keeps explicit
``CoInitializeEx`` calls inside dedicated COM threads.

Keep ``init_com()`` only for specialized scenarios that explicitly need a main
thread COM apartment bootstrap.
"""

import ctypes
import logging

from src.boot.com_policy import COINIT_APARTMENTTHREADED, RPC_E_CHANGED_MODE

logger = logging.getLogger(__name__)

COM_BOOTSTRAP_EXCEPTIONS = (AttributeError, OSError, RuntimeError, TypeError, ValueError)


def init_com() -> None:
    """
    Explicitly initialize COM on the current thread in STA.

    This helper is intentionally separate from the default app bootstrap:
    the main WaveHelm startup configures comtypes to use MTA by default,
    while the dedicated video COM thread performs an explicit STA init.
    Call this only when a specialized entry point needs a real STA current
    thread bootstrap.
    """
    try:
        ole32 = ctypes.windll.ole32
        CoInitializeEx = ole32.CoInitializeEx
        CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        CoInitializeEx.restype = ctypes.c_long

        hr = CoInitializeEx(None, COINIT_APARTMENTTHREADED)

        if hr == 0:  # S_OK
            logger.info("[COM] CoInitializeEx(STA) successful.")
        elif hr == 1:  # S_FALSE
            logger.info("[COM] CoInitializeEx(STA) already initialized on this thread.")
        elif hr == RPC_E_CHANGED_MODE:
            logger.warning(
                "[COM] CoInitializeEx(STA) failed: thread mode was already set "
                "(likely MTA by another component)."
            )
        else:
            logger.error(
                f"[COM] CoInitializeEx(STA) failed with unexpected HRESULT: 0x{hr:08X}"
            )
    except COM_BOOTSTRAP_EXCEPTIONS as e:  # pragma: no cover - defensive logging
        logger.error(f"[COM] Failed to explicitly initialize COM: {e}", exc_info=True)
