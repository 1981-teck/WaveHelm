from __future__ import annotations

import sys
from types import SimpleNamespace

from src.boot import com_policy


def test_apply_main_thread_comtypes_policy_sets_mta(monkeypatch):
    fake_comtypes = SimpleNamespace(
        COINIT_MODE=99,
        COINIT_MULTITHREADED=123,
    )
    monkeypatch.setitem(sys.modules, "comtypes", fake_comtypes)

    requested_mode = com_policy.apply_main_thread_comtypes_policy()

    assert requested_mode == 123
    assert fake_comtypes.COINIT_MODE == 123


def test_get_runtime_policy_summary_describes_split_policy():
    assert com_policy.get_runtime_policy_summary() == {
        "main_thread_comtypes_policy": "MTA",
        "video_thread_com_policy": "STA",
    }
