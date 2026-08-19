from __future__ import annotations

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


def test_shutdown_falls_back_to_async_then_local():
    async_adapter = DummyAdapter(sync_exc=RuntimeError('sync fail'))
    async_core = DummyCore(async_adapter)

    media_engine_core_shutdown.shutdown(async_core)

    assert async_adapter.calls == [('call', 'shutdown'), ('post', 'shutdown_async')]
    assert async_core.com_thread_shutdowns == 1
    assert async_core.local_shutdowns == 0

    local_adapter = DummyAdapter(sync_exc=RuntimeError('sync fail'), async_exc=RuntimeError('async fail'))
    local_core = DummyCore(local_adapter)

    media_engine_core_shutdown.shutdown(local_core)

    assert local_adapter.calls == [('call', 'shutdown'), ('post', 'shutdown_async')]
    assert local_core.local_shutdowns == 1


def test_media_engine_core_del_ignores_expected_shutdown_errors(caplog):
    caplog.set_level(logging.DEBUG, logger=media_engine_core_module.logger.name)

    class Dummy:
        _shutdown_requested = False

        def shutdown(self):
            raise ValueError('boom')

    MediaEngineCore.__del__(Dummy())
    assert '__del__ shutdown failed' in caplog.text
