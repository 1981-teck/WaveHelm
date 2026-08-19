from __future__ import annotations

from src.video import media_engine_core_vtable as vtable_mod
from src.video.media_engine_core_shared import MediaEngineError


class DummyCore:
    def __init__(self, fail_names=None, shutdown_hr=0):
        self.fail_names = set(fail_names or [])
        self.shutdown_hr = shutdown_hr
        self.calls = []

    def _call_engine_ptr_method(self, engine_ptr, method_name, *args):
        self.calls.append((method_name, args))
        if method_name in self.fail_names:
            raise MediaEngineError(method_name)
        if method_name == 'Shutdown':
            return self.shutdown_hr
        return 0


def test_stop_engine_ptr_playback_tolerates_pause_and_seek_failures():
    core = DummyCore({'Pause', 'SetCurrentTime'})

    vtable_mod._stop_engine_ptr_playback(core, object())

    assert [name for name, _ in core.calls] == ['Pause', 'SetCurrentTime']


def test_shutdown_engine_ptr_tolerates_shutdown_failure():
    core = DummyCore({'Shutdown'})

    vtable_mod._shutdown_engine_ptr(core, object())

    assert [name for name, _ in core.calls] == ['Shutdown']


def test_sanitize_nonneg_float_handles_invalid_values():
    assert vtable_mod._sanitize_nonneg_float('bad', default=1.5) == 1.5
    assert vtable_mod._sanitize_nonneg_float(float('inf'), default=2.0) == 2.0
    assert vtable_mod._sanitize_nonneg_float(-1.0, default=3.0) == 3.0
    assert vtable_mod._sanitize_nonneg_float(4.25, default=0.0) == 4.25
