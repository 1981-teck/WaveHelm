from __future__ import annotations

"""
Shared COM policy definitions for WaveHelm startup.

Current runtime model:
- main thread: configure comtypes to default to MTA for any implicit COM init
- dedicated video thread: explicit STA via CoInitializeEx inside the COM thread manager
"""

COINIT_MULTITHREADED = 0x0
COINIT_APARTMENTTHREADED = 0x2
RPC_E_CHANGED_MODE = 0x80010106

MAIN_THREAD_COM_POLICY_NAME = "MTA"
VIDEO_THREAD_COM_POLICY_NAME = "STA"


def apply_main_thread_comtypes_policy() -> int:
    import comtypes

    requested_mode = int(getattr(comtypes, "COINIT_MULTITHREADED", COINIT_MULTITHREADED))
    comtypes.COINIT_MODE = requested_mode
    return requested_mode


def get_runtime_policy_summary() -> dict[str, str]:
    return {
        "main_thread_comtypes_policy": MAIN_THREAD_COM_POLICY_NAME,
        "video_thread_com_policy": VIDEO_THREAD_COM_POLICY_NAME,
    }
