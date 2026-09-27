"""Owned-engine refusal, rollback and uncertainty; no OS/MediaEngine calls.

Python boundary receivers raise errors before or after dispatch without raising
through ctypes callbacks. Test storage stays allocated even at ledger refcount zero.
"""
from __future__ import annotations

import ctypes
import threading
from types import SimpleNamespace

import pytest

from tests.test_engine_acquisition_ownership import native_shape, Ledger, seed_old_address
import src.video.component_base.com_helpers as helpers
import src.video.media_engine_core_setup as setup
from src.video.component_base.definitions import IUnknown, IMFMediaEngine
from src.video.media_engine_core_shared import MediaEngineError
from src.video.media_engine_ownership import EngineReference, EngineResources, release_engine, owned_release_pair


def acquire(ledger, interface=IUnknown):
    owner = EngineReference(interface)
    owner.receive()[0] = ledger.address
    owner.confirm(0)
    return owner


@pytest.mark.parametrize('hresult', [0x80004005, 0x80004002])
@pytest.mark.parametrize('null_output', [False, True])
def test_failed_factory_output_is_not_invented_as_ownership(native_shape, hresult, null_output):
    r = native_shape
    r.hr, r.null = hresult, null_output
    with pytest.raises((MediaEngineError, OSError, RuntimeError)):
        r.core._create_engine_on_com_thread(17)
    assert r.core._media_engine is None and r.ledger.releases == []
    pending = getattr(r.core, '_creation_resources', None)
    if null_output:
        assert pending is None and r.factory.releases == r.attributes.releases == 1
    else:
        assert pending is not None
        assert pending.engine.acquisition_state == 'UNCONFIRMED'
        assert r.native == []
        with pytest.raises(MediaEngineError):
            r.core._create_engine_on_com_thread(18)
        assert r.creates == 1


def test_success_with_null_engine_has_no_native_release(native_shape):
    r = native_shape
    r.null = True
    with pytest.raises(MediaEngineError):
        r.core._create_engine_on_com_thread(17)
    assert r.ledger.releases == [] and getattr(r.core, '_creation_resources', None) is None
    assert r.notification.releases == 1


@pytest.mark.parametrize('error_type', [OSError, ValueError, MemoryError, KeyboardInterrupt, SystemExit])
def test_interrupted_foreign_acquisition_retains_output_without_dereference(native_shape, error_type):
    r = native_shape
    error = error_type('native-shaped call interrupted after output write')
    def fail():
        raise error
    r.create_hook = fail  # Python factory boundary, NOT a ctypes callback.
    with pytest.raises(error_type) as captured:
        r.core._create_engine_on_com_thread(17)
    assert captured.value is error
    pending = r.core._creation_resources
    assert pending.creation_error is error and pending.engine.unresolved
    assert pending.engine.acquisition_state == 'RECEIVING'
    assert r.ledger.releases == [] and r.native == []
    with pytest.raises(MediaEngineError):
        r.core._create_engine_on_com_thread(18)
    assert r.core._creation_resources is pending and r.creates == 1


def test_preallocation_failure_preserves_core_and_never_calls_factory(native_shape, monkeypatch):
    r = native_shape
    error = MemoryError('preallocation')
    def fail():
        raise error
    monkeypatch.setattr(setup, 'EngineResources', fail)
    with pytest.raises(MemoryError) as captured:
        r.core._create_engine_on_com_thread(17)
    assert captured.value is error and r.creates == 0
    assert r.core._media_engine is None and not getattr(r.core, '_creation_resources', None)


@pytest.mark.parametrize('when', ['before_dispatch', 'after_dispatch'])
def test_failed_owned_release_is_retained_and_never_automatically_replayed(native_shape, monkeypatch, when):
    r = native_shape
    r.ledger.qi_hr = 0x80004002
    r.core._create_engine_on_com_thread(17)
    resources = r.core._engine_references
    original_cast = helpers.ctypes.cast
    error = ValueError('controlled release failure')
    calls = []
    def fail_release(this):
        calls.append('Release dispatched')
        raise error
    def cast(value, kind):
        if kind is ctypes.POINTER(IUnknown):
            if when == 'before_dispatch':
                raise error
            return SimpleNamespace(contents=SimpleNamespace(lpVtbl=SimpleNamespace(
                contents=SimpleNamespace(Release=fail_release))))
        if isinstance(value, SimpleNamespace):
            return ctypes.c_void_p(r.ledger.address)
        return original_cast(value, kind)
    monkeypatch.setattr(helpers.ctypes, 'cast', cast)
    r.core.shutdown()
    receipt = resources.engine.release_snapshot
    assert receipt.error is error
    assert receipt.state.value == ('not_dispatched' if when == 'before_dispatch' else 'uncertain')
    snapshot = r.core.get_shutdown_snapshot()
    assert snapshot.execution == 'FAILED_UNCERTAIN'
    assert snapshot.error.__cause__ is error
    assert r.core._shutdown_job._resources is not None
    r.core.shutdown()
    r.core._shutdown_job.run()
    assert calls == ([] if when == 'before_dispatch' else ['Release dispatched'])
    assert r.ledger.releases == []


def test_failed_rebind_retains_owner_and_blocks_recreation(native_shape, monkeypatch):
    r = native_shape
    r.ledger.qi_hr = 0x80004002
    r.core._create_engine_on_com_thread(17)
    resources = r.core._engine_references
    original_cast = helpers.ctypes.cast
    error = ValueError('release lookup failed')
    def cast(value, kind):
        if kind is ctypes.POINTER(IUnknown):
            raise error
        return original_cast(value, kind)
    monkeypatch.setattr(helpers.ctypes, 'cast', cast)
    with pytest.raises(MediaEngineError):
        r.core._release_current_engine_on_com_thread('test failure')
    assert r.core._retired_engine_resources is resources
    assert resources.cleanup_error.__cause__ is error
    with pytest.raises(MediaEngineError):
        r.core._create_engine_on_com_thread(18)
    assert r.creates == 1 and r.ledger.releases == []


@pytest.mark.parametrize('field', ['_media_engine', '_media_engine_ex'])
def test_changed_borrowed_address_is_rejected_before_detachment(native_shape, field):
    r = native_shape
    r.core._create_engine_on_com_thread(17)
    old = getattr(r.core, field)
    other = Ledger()  # Owned valid storage; never an arbitrary numerical address.
    setattr(r.core, field, other.pointer)
    with pytest.raises(MediaEngineError):
        r.core.shutdown()
    assert not r.core._shutdown_requested and r.native == []
    assert getattr(r.core, '_engine_references', None) is not None
    assert r.ledger.releases == [] and other.releases == []
    setattr(r.core, field, old)
    r.core.shutdown()
    assert r.ledger.refs == 0


@pytest.mark.parametrize('transition', ['generation', 'shutdown'])
def test_late_query_invalidated_before_publication_releases_only_new_reference(native_shape, transition):
    r = native_shape
    r.ledger.qi_hr = 0x80004002
    r.core._create_engine_on_com_thread(17)
    r.ledger.qi_hr = 0
    def change():
        if transition == 'generation':
            r.core._engine_generation += 1
        else:
            r.core._shutdown_requested = True
    r.ledger.qi_hook = change
    with pytest.raises(MediaEngineError):
        r.core._ensure_media_engine_ex_on_com_thread()
    assert r.ledger.refs == 1 and r.core._media_engine_ex is None
    assert r.ledger.releases == [(1, 1)]
    assert r.core._engine_references.engine.unresolved
    r.core._release_current_engine_on_com_thread('explicit owned cleanup')
    assert r.ledger.refs == 0


@pytest.mark.parametrize('hresult', [0, 0x80004002])
def test_output_slot_is_single_acquisition_unless_no_interface(hresult):
    ledger = Ledger()
    owner = EngineReference(IUnknown)
    output = owner.receive()
    if hresult == 0:
        output[0] = ledger.address
    owner.confirm(hresult)
    if hresult == 0:
        with pytest.raises(MediaEngineError):
            owner.receive()
        with pytest.raises(MediaEngineError):
            owner.confirm(0)
        release_engine(owner, 'known test reference')
        release_engine(owner, 'sequential duplicate')
        assert ledger.releases == [(1, 0)]
    else:
        assert not owner.unresolved
        owner.receive()[0] = ledger.address
        owner.confirm(0)
        release_engine(owner, 'later successful query')
        assert ledger.releases == [(1, 0)]


def test_no_native_call_and_no_lock_retention_during_reentrant_owned_release():
    ledger = Ledger()
    owner = acquire(ledger)
    states = []
    def reentry():
        states.append(owner._lock._is_owned())
        owner.release()
    ledger.release_hook = reentry
    release_engine(owner, 'reentry')
    assert states == [False] and ledger.releases == [(1, 0)]


def test_real_two_thread_overlap_does_not_release_one_owner_twice():
    ledger = Ledger()
    owner = acquire(ledger)
    entered, resume = threading.Event(), threading.Event()
    observations = []
    def on_release():
        entered.set()
        observations.append(resume.wait(3))
    ledger.release_hook = on_release
    first = threading.Thread(target=owner.release, daemon=True)
    first.start()
    try:
        assert entered.wait(2)
        owner.release()
    finally:
        resume.set()
        first.join(4)
    assert not first.is_alive() and observations == [True]
    assert ledger.releases == [(1, 0)] and not owner.unresolved


def test_release_while_native_output_is_receiving_never_dereferences_it():
    ledger = Ledger()
    owner = EngineReference(IUnknown)
    owner.receive()[0] = ledger.address
    with pytest.raises(MediaEngineError):
        release_engine(owner, 'unconfirmed output')
    assert owner.unresolved and not ledger.releases
    owner.confirm(0)
    release_engine(owner, 'confirmed output')
    assert ledger.releases == [(1, 0)]


def test_shutdown_admitted_during_actual_late_query_preserves_and_releases_owners(native_shape):
    from tests.test_shutdown_job_execution import Worker, Adapter
    import queue
    r = native_shape
    seed_old_address(r.ledger)
    r.ledger.qi_hr = 0x80004002
    r.core._create_engine_on_com_thread(17)
    worker = Worker()
    adapter = Adapter(worker)
    r.core._adapter_ref = lambda: adapter
    r.ledger.qi_hr = 0
    entered, resume = threading.Event(), threading.Event()
    waits = []
    def during_query():
        entered.set()
        waits.append(resume.wait(3))
    r.ledger.qi_hook = during_query
    reply = queue.Queue()
    try:
        assert worker.submit_cleanup_to_com_thread('late-query', r.core._ensure_media_engine_ex_on_com_thread, reply)
        worker.gate.set()
        assert entered.wait(2)
        r.core.shutdown()
        before = r.core.get_shutdown_snapshot()
        assert before.admission == 'ACCEPTED' and before.wait_expired
        assert before.execution == 'PREPARED'
        assert r.ledger.releases == [(1, 0)]
    finally:
        resume.set()
        worker.finish()
    assert waits == [True] and not worker.errors
    assert isinstance(reply.get_nowait(), MediaEngineError)
    assert r.ledger.refs == 0 and r.ledger.releases[-2:] == [(2, 1), (2, 0)]
    assert r.core.get_shutdown_snapshot().execution == 'RETURNED'


@pytest.mark.parametrize('step', ['attributes', 'notify', 'factory'])
def test_failure_before_engine_dispatch_releases_recorded_nonengine_resources(native_shape, monkeypatch, step):
    r = native_shape
    error = ValueError('controlled setup stage failure')
    def fail(*args, **kwargs):
        raise error
    if step == 'attributes':
        r.setattr_error = error
    elif step == 'notify':
        monkeypatch.setattr(r.core, '_imfattributes_set_unknown', fail)
    else:
        monkeypatch.setattr(setup, 'MFCreateMediaEngine', fail)
    with pytest.raises(ValueError) as captured:
        r.core._create_engine_on_com_thread(17)
    assert captured.value is error and not r.creates
    assert r.ledger.releases == [] and r.attributes.releases == 1
    assert r.notification.releases == (0 if step == 'attributes' else 1)
    assert getattr(r.core, '_creation_resources', None) is None


def test_successful_rollback_inside_primary_exception_does_not_inherit_that_error(native_shape, monkeypatch):
    r = native_shape
    retained = []
    original = EngineResources.rollback_creation
    def record(resources, core):
        retained.append(resources)
        return original(resources, core)
    monkeypatch.setattr(EngineResources, 'rollback_creation', record)
    r.setattr_error = ValueError('primary failure')
    with pytest.raises(ValueError):
        r.core._create_engine_on_com_thread(17)
    assert retained[0].creation_error is r.setattr_error
    assert retained[0].cleanup_error is None and retained[0].reclaimed
