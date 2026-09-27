"""08R source-bound acquisition tests, NOT a Windows COM/allocator qualification.

The actual core/setup/query/teardown run against a recording factory boundary and
ctypes storage owned by the test. Storage remains allocated; reference lifetimes
are independent ledger entries. No media, OS allocation or log address is used.
"""
from __future__ import annotations

import ctypes
import gc
import threading
from types import SimpleNamespace

import pytest

import src.video.media_engine_core_setup as setup
import src.video.media_engine_core_playback as playback
import src.video.component_base.utils as utils
from src.video.component_base.definitions import IUnknown, IUnknownVtbl, IMFMediaEngine
from src.video.media_engine_core import MediaEngineCore
from src.video.media_engine_core_shared import MediaEngineError


class Ledger:
    def __init__(self):
        self.refs = 1
        self.life = 1
        self.releases = []
        self.queries = []
        self.qi_hr = 0
        self.qi_hook = None
        self.release_hook = None
        types = dict(IUnknownVtbl._fields_)
        self.callbacks = (types['QueryInterface'](self.query),
                          types['AddRef'](self.add_ref), types['Release'](self.release))
        self.table = IUnknownVtbl(*self.callbacks)
        self.storage = IUnknown(ctypes.pointer(self.table))
        self.pointer = ctypes.pointer(self.storage)
        self.address = ctypes.cast(self.pointer, ctypes.c_void_p).value
        self.events = []

    def query(self, this, iid, output):
        self.queries.append(self.life)
        if self.qi_hr == 0:
            self.refs += 1
            output[0] = self.address
        if self.qi_hook is not None:
            self.qi_hook()  # Test hooks must never raise through ctypes.
        return self.qi_hr

    def add_ref(self, this):
        self.refs += 1
        return self.refs

    def release(self, this):
        self.refs -= 1
        self.releases.append((self.life, self.refs))
        if self.release_hook is not None:
            self.release_hook()
        return max(0, self.refs)

    def new_life(self):
        assert self.refs == 0
        self.life += 1
        self.refs = 1


class ShellOwner:
    """A recording factory/attribute/notify boundary, not a native object."""
    def __init__(self, view=None):
        self.view = view
        self.releases = 0

    def as_interface(self, interface):
        return self.view

    def Release(self):
        self.releases += 1
        return 0


class Adapter:
    def call_on_com_thread(self, name, callback):
        return callback()


@pytest.fixture
def native_shape(monkeypatch):
    """Legacy/HWND fixture; edges: Windows WIC default, explicit WIC override, env restoration."""
    monkeypatch.setenv('WAVEHELM_VIDEO_BACKEND', 'legacy_hwnd')
    ledger = Ledger()
    adapter = Adapter()
    core = MediaEngineCore(adapter)
    attributes = ShellOwner(object())
    notification = ShellOwner()
    notify = SimpleNamespace(QueryInterface=lambda _kind: notification)
    state = SimpleNamespace(ledger=ledger, core=core, adapter=adapter,
                            attributes=attributes, notification=notification,
                            notify=notify, hr=0, null=False, create_hook=None,
                            creates=0, native=[], setattr_error=None)
    def create(factory, flags, attrs, output):
        state.creates += 1
        if not state.null:
            ctypes.cast(output, ctypes.POINTER(ctypes.POINTER(IMFMediaEngine)))[0] = (
                ctypes.cast(ledger.pointer, ctypes.POINTER(IMFMediaEngine)))
        if state.create_hook is not None:
            state.create_hook()
        return state.hr
    factory = ShellOwner(SimpleNamespace(contents=SimpleNamespace(
        lpVtbl=SimpleNamespace(contents=SimpleNamespace(CreateInstance=create)))))
    state.factory = factory
    monkeypatch.setattr(setup, 'MFCreateAttributes', lambda count: attributes)
    monkeypatch.setattr(setup, 'MFCreateMediaEngine', lambda: factory)
    monkeypatch.setattr(setup, 'create_bound_notify', lambda *args: notify)
    monkeypatch.setattr(core, '_validate_hwnd_for_rendering', lambda hwnd: None)
    def set_attribute(*args):
        if state.setattr_error is not None:
            raise state.setattr_error
    monkeypatch.setattr(core, '_imfattributes_set_uint64', set_attribute)
    monkeypatch.setattr(core, '_imfattributes_set_uint32', lambda *args: None)
    monkeypatch.setattr(core, '_imfattributes_set_unknown', lambda *args: None)
    monkeypatch.setattr(core, '_stop_engine_ptr_playback',
                        lambda ptr: state.native.append(('stop', bool(ptr))))
    monkeypatch.setattr(core, '_shutdown_engine_ptr',
                        lambda ptr: state.native.append(('shutdown', bool(ptr))))
    monkeypatch.setattr(utils, '_released_ptrs', {})
    monkeypatch.setattr(utils, '_last_purge_monotonic', 0.0)
    assert utils._RELEASED_PTR_TTL_SEC == 60.0
    yield state
    # Never invoke a native destructor after test-owned storage has gone away.
    core._shutdown_requested = True


def test_hwnd_engine_creation_does_not_attach_extra_dxgi_unknown(native_shape):
    """08AC regression: HWND rendering keeps native device ownership inside MediaEngine.

    Edge cases:
        1. The notify callback remains the single SetUnknown attribute during creation.
        2. Reintroducing an app-owned DXGI manager adds a second SetUnknown and fails this test.
        3. The engine is shut down explicitly so acquisition ownership remains balanced.
    """
    r = native_shape
    unknown_calls: list[tuple[object, ...]] = []
    r.core._imfattributes_set_unknown = lambda *args: unknown_calls.append(args)

    r.core._create_engine_on_com_thread(17)

    assert len(unknown_calls) == 1
    r.core.shutdown()
    assert r.ledger.refs == 0


def seed_old_address(ledger):
    utils.safe_release(ledger.pointer, 'previous test lifetime')
    assert ledger.refs == 0
    ledger.new_life()


@pytest.mark.parametrize('route', ['shutdown', 'rebind', 'local_leaf'])
@pytest.mark.parametrize('external_refs', [0, 1, 3])
def test_actual_acquisition_releases_new_lifetime_after_raw_address_quarantine(native_shape, route, external_refs):
    r = native_shape
    seed_old_address(r.ledger)
    r.ledger.refs += external_refs
    before_registry = dict(utils._released_ptrs)
    r.core._create_engine_on_com_thread(17)
    assert r.ledger.refs == external_refs + 2
    if route == 'shutdown':
        r.core.shutdown()
    elif route == 'rebind':
        r.core._release_current_engine_on_com_thread('new window')
    else:
        refs = getattr(r.core, '_engine_references', None)
        base = r.core._media_engine if refs is None else refs.engine
        ex = r.core._media_engine_ex if refs is None else refs.extension
        r.core._shutdown_and_release_local(base, ex, None, None, None, None)
    assert r.ledger.refs == external_refs
    assert r.ledger.releases[-2:] == [(2, external_refs + 1), (2, external_refs)]
    assert utils._released_ptrs == before_registry  # No clear, TTL edit or marker rewrite.


@pytest.mark.parametrize('cycles', [2, 4, 8])
def test_rebind_recreate_balances_distinct_equal_address_acquisitions(native_shape, cycles):
    r = native_shape
    for cycle in range(cycles):
        r.core._create_engine_on_com_thread(17 + cycle)
        assert r.ledger.refs == 2
        r.core._release_current_engine_on_com_thread('test recreate')
        assert r.ledger.refs == 0
        if cycle != cycles - 1:
            r.ledger.new_life()
    assert len(r.ledger.releases) == 2 * cycles
    assert r.core._media_engine is None and r.core._media_engine_ex is None


@pytest.mark.parametrize('route', ['query', 'render'])
def test_late_extension_acquisition_is_owned_and_outside_core_lock(native_shape, monkeypatch, route):
    r = native_shape
    seed_old_address(r.ledger)
    r.ledger.qi_hr = 0x80004002
    r.core._create_engine_on_com_thread(17)
    assert r.core._media_engine_ex is None and r.ledger.refs == 1
    r.ledger.qi_hr = 0
    lock_observations = []
    r.ledger.qi_hook = lambda: lock_observations.append(r.core._state_lock._is_owned())
    if route == 'query':
        result = r.core._ensure_media_engine_ex_on_com_thread()
        assert result is not None
    else:
        monkeypatch.setattr(playback, '_resolve_render_container_size', lambda *args: (640, 360))
        monkeypatch.setattr(r.core, '_call_engine_ptr_method', lambda *args: 0)
        assert r.core.update_video_stream(640, 360) is True
    assert lock_observations == [False]
    assert r.ledger.refs == 2
    r.core.shutdown()
    assert r.ledger.refs == 0


@pytest.mark.parametrize('external_refs', [0, 1])
def test_managed_shutdown_still_consumes_only_one_bundle(native_shape, external_refs):
    r = native_shape
    seed_old_address(r.ledger)
    r.ledger.refs += external_refs
    r.core._create_engine_on_com_thread(17)
    def reenter():
        r.core.shutdown()
        r.core._shutdown_job.run()
    r.ledger.release_hook = reenter
    r.core.shutdown()
    r.core.shutdown()
    assert r.ledger.refs == external_refs
    assert r.native == [('stop', True), ('shutdown', True)]
    assert r.core.get_shutdown_snapshot().execution == 'RETURNED'


@pytest.mark.parametrize('error_type', [OSError, RuntimeError, ValueError, MemoryError, KeyboardInterrupt, SystemExit])
def test_error_after_base_acquisition_cleans_owned_reference_without_ttl(native_shape, monkeypatch, error_type):
    r = native_shape
    seed_old_address(r.ledger)
    error = error_type('injected before extended query')
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(r.core, '_query_media_engine_ex_on_com_thread', fail)
    with pytest.raises(error_type) as captured:
        r.core._create_engine_on_com_thread(17)
    assert captured.value is error
    assert r.ledger.refs == 0
    assert r.core._media_engine is None
    assert r.factory.releases == 1 and r.attributes.releases == 1
    assert r.notification.releases == 1


def test_clean_rollback_permits_new_creation_without_old_owner_overwrite(native_shape, monkeypatch):
    r = native_shape
    original = r.core._query_media_engine_ex_on_com_thread
    def fail(*args, **kwargs):
        raise RuntimeError('first create failed')
    monkeypatch.setattr(r.core, '_query_media_engine_ex_on_com_thread', fail)
    with pytest.raises(RuntimeError):
        r.core._create_engine_on_com_thread(17)
    assert r.ledger.refs == 0 and getattr(r.core, '_creation_resources', None) is None
    r.ledger.new_life()
    monkeypatch.setattr(r.core, '_query_media_engine_ex_on_com_thread', original)
    r.core._create_engine_on_com_thread(18)
    r.core.shutdown()
    assert r.ledger.refs == 0 and r.creates == 2


def test_no_com_release_when_acquired_owners_are_garbage_collected(native_shape):
    r = native_shape
    r.core._create_engine_on_com_thread(17)
    resources = getattr(r.core, '_engine_references', None)
    assert resources is not None
    r.core._engine_references = None
    r.core._media_engine = r.core._media_engine_ex = None
    del resources
    gc.collect()
    assert r.ledger.refs == 2 and r.ledger.releases == []
    # This documents explicit ownership loss, not a leak-free property.
