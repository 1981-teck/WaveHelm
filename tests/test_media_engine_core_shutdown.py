from __future__ import annotations
from src.video.seek_receipt import SeekSlot

import logging
import threading

from src.video.media_engine_core import MediaEngineCore
import src.video.media_engine_core as media_engine_core_module
import src.video.media_engine_core_shutdown as media_engine_core_shutdown


class DummyAdapter:
    def __init__(self, *, sync_exc=None, async_exc=None):
        self.sync_exc = sync_exc
        self.async_exc = async_exc
        self.calls = []

    def call_on_com_thread(self, name, callback):
        self.calls.append(('call', name))
        if self.sync_exc is not None:
            raise self.sync_exc
        return callback()

    def post_to_com_thread(self, name, callback):
        self.calls.append(('post', name))
        if self.async_exc is not None:
            raise self.async_exc
        return callback()


class DummyCore:
    def __init__(self, adapter=None):
        self._state_lock = threading.RLock()
        self._seek_slot = SeekSlot()
        self._shutdown_requested = False
        self._media_engine = object()
        self._factory = object()
        self._media_engine_ex = object()
        self._attributes = object()
        self._notify_iunknown = object()
        self._notify_handler = object()
        self._vtable_call_cache = {'x': 1}
        self._adapter = adapter
        self.com_thread_shutdowns = 0
        self.local_shutdowns = 0

    def _adapter_ref(self):
        return self._adapter

    def _shutdown_and_release_on_com_thread(self, *args):
        self.com_thread_shutdowns += 1

    def _shutdown_and_release_local(self, *args):
        self.local_shutdowns += 1


def test_shutdown_prefers_sync_com_thread_release():
    adapter = DummyAdapter()
    core = DummyCore(adapter)

    media_engine_core_shutdown.shutdown(core)

    assert adapter.calls == [('call', 'shutdown')]
    assert core.com_thread_shutdowns == 1
    assert core.local_shutdowns == 0


def test_shutdown_never_reposts_or_runs_local_after_sync_failure():
    """08Q replaces the unsafe historical async/local fallback requirement."""
    for async_error in (None, RuntimeError("async fail")):
        sync_error = RuntimeError("sync fail")
        adapter = DummyAdapter(sync_exc=sync_error, async_exc=async_error)
        core = DummyCore(adapter)
        media_engine_core_shutdown.shutdown(core)
        media_engine_core_shutdown.shutdown(core)
        assert adapter.calls == [('call', 'shutdown')]
        assert core.com_thread_shutdowns == core.local_shutdowns == 0
        snapshot = media_engine_core_shutdown.get_shutdown_snapshot(core)
        assert snapshot.admission == "UNCONFIRMED"
        assert snapshot.execution == "PREPARED"
        assert snapshot.transport_error is sync_error
        assert core._shutdown_job._resources is not None


def test_media_engine_core_del_ignores_expected_shutdown_errors(caplog):
    caplog.set_level(logging.DEBUG, logger=media_engine_core_module.logger.name)

    class Dummy:
        _shutdown_requested = False

        def shutdown(self):
            raise ValueError('boom')

    MediaEngineCore.__del__(Dummy())
    assert '__del__ shutdown failed' in caplog.text


def test_shutdown_detaches_once_and_clears_cache_outside_core_lock():
    from tests.test_video_clock_observation import CheckedLock
    adapter=DummyAdapter();core=DummyCore(adapter)
    core._state_lock=CheckedLock()
    cleared=[]
    class Cache(dict):
        def clear(self):
            assert not core._state_lock.held
            cleared.append(True)
            super().clear()
    old=Cache(core._vtable_call_cache);core._vtable_call_cache=old
    media_engine_core_shutdown.shutdown(core)
    media_engine_core_shutdown.shutdown(core)
    assert core._vtable_call_cache=={} and core._vtable_call_cache is not old
    assert cleared==[True] and core.com_thread_shutdowns==1
    assert core._media_engine is core._factory is core._attributes is None
