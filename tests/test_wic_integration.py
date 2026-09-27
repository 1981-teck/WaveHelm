"""Exercise real product entry points, with only native/wx endpoints replaced."""
from __future__ import annotations

import ctypes as C
import threading
from types import SimpleNamespace

import pytest

from src.video import media_engine_core_setup as setup
from src.video import media_engine_core_vtable as vt
from src.video import imf_media_engine_adapter_events as events
from src.video.imf_media_engine_adapter import IMFMediaEngineAdapter
from src.video.media_engine_core_shared import MediaEngineError
from src.video.media_engine_core_shutdown import _detach_shutdown_resources
from src.video.seek_receipt import SeekPhase
from src.video.wic_pipeline import FramePipeline
from tests.test_engine_acquisition_ownership import native_shape  # pytest fixture import
from tests.test_wic_pipeline import fixture


class Renderer:
    def __init__(self): self.opened=None; self.closed=0
    def open(self,engine): self.opened=engine.value
    def render(self,*args): return None
    def close(self): self.closed+=1


@pytest.mark.parametrize('teardown',['shutdown','recreation'])
def test_exact_setup_wic_config_commit_rebind_and_shutdown_owner(native_shape,monkeypatch,teardown) -> None:
    f=native_shape; configured=[]; renderer=Renderer()
    monkeypatch.setenv('WAVEHELM_VIDEO_BACKEND','wic')
    monkeypatch.setattr(setup,'WicRenderer',lambda **kwargs:renderer)
    # Attribute storage is a fixture boundary, never interpreted as a native vtable.
    f.attributes.view=C.c_void_p(123)
    monkeypatch.setattr(setup,'configure_frame_server',lambda a:configured.append(a.value))
    monkeypatch.setattr(setup,'_configure_wic_source_policy',lambda core,engine:None)
    def forbidden(*args): raise AssertionError('Legacy HWND attribute must not be set')
    monkeypatch.setattr(f.core,'_imfattributes_set_uint64',forbidden)
    f.adapter.submit_frame_to_com_thread=lambda *args:True
    f.adapter.frame_ready=lambda:True
    f.adapter.frame_pump_ready=lambda:True  # Explicit native endpoint fixture contract.
    f.core.ensure_engine(7)
    service=f.core._wic_pipeline
    assert isinstance(service,FramePipeline) and configured==[123]
    assert service._pump_ready is f.adapter.frame_pump_ready
    assert renderer.opened==f.ledger.address
    f.core.ensure_engine(8)
    assert f.creates==1 and service.hwnd==8
    if teardown=='shutdown':
        f.core.shutdown()
        assert f.core.get_shutdown_snapshot().execution=='RETURNED'
    else:
        setup._release_current_engine_on_com_thread(f.core,'test replacement')
    assert renderer.closed==1 and f.ledger.refs==0
    assert f.core._wic_pipeline is None


def test_staged_wic_failure_rolls_back_borrowed_and_owned_interfaces(native_shape,monkeypatch) -> None:
    f=native_shape; renderer=Renderer(); f.attributes.view=C.c_void_p(123)
    def failure(pointer): raise RuntimeError('WIC factory unavailable')
    renderer.open=failure
    monkeypatch.setenv('WAVEHELM_VIDEO_BACKEND','wic')
    monkeypatch.setattr(setup,'configure_frame_server',lambda a:None)
    monkeypatch.setattr(setup,'_configure_wic_source_policy',lambda core,engine:None)
    monkeypatch.setattr(setup,'WicRenderer',lambda **kwargs:renderer)
    with pytest.raises(RuntimeError): f.core.ensure_engine(7)
    assert f.ledger.refs==0 and renderer.closed==1
    assert f.core._wic_pipeline is None


def test_native_shutdown_failure_is_observable_and_resources_not_falsely_released(native_shape) -> None:
    f=native_shape; f.core.ensure_engine(7)
    def failure(pointer): raise MediaEngineError('Shutdown hr=0x80004005')
    f.core._shutdown_engine_ptr=failure
    f.core.shutdown()
    assert f.core.get_shutdown_snapshot().execution=='FAILED_UNCERTAIN'
    assert f.ledger.refs==2  # Native outcome not completed; retained for diagnosis.


@pytest.mark.parametrize('hr',[-2147467259,0x80004005,0x80070005])
def test_exact_vtable_shutdown_does_not_swallow_hresult(hr) -> None:
    core=SimpleNamespace(_call_engine_ptr_method=lambda *args:hr)
    with pytest.raises(MediaEngineError): vt._shutdown_engine_ptr(core,C.c_void_p(123))


def test_rebind_replacement_requests_real_shutdown(native_shape) -> None:
    f=native_shape; f.core.ensure_engine(7)
    setup._release_current_engine_on_com_thread(f.core, "test rebind")
    assert ('shutdown',True) in f.native
    assert f.ledger.refs==0


@pytest.mark.parametrize('event',[events.MF_MEDIA_ENGINE_EVENT_ENDED,events.MF_MEDIA_ENGINE_EVENT_PLAYING,
                                 events.MF_MEDIA_ENGINE_EVENT_ERROR,events.MF_MEDIA_ENGINE_EVENT_CANPLAY])
def test_stale_queued_events_are_not_published_to_application(event) -> None:
    published=[]
    adapter=SimpleNamespace(_lock=threading.Lock(),_source_epoch=2,_closed=False,
                            _shutdown_requested=False,_publish=lambda *args,**kw:published.append(args))
    events._dispatch_event(adapter,event,0,0,1)
    assert not published


def adapter_shape(service=None):
    adapter=IMFMediaEngineAdapter.__new__(IMFMediaEngineAdapter)
    adapter._lock=threading.Lock(); adapter._closed=False; adapter._shutdown_requested=False
    adapter._source='old.mp4'; adapter._source_epoch=2; adapter._ready_epoch=2
    adapter._pending_source_epoch=None; adapter._hwnd=7
    adapter._core=SimpleNamespace(_wic_pipeline=service,get_seek_receipt=lambda:None)
    return adapter


def test_frame_submission_uses_revision_not_seek_source_failure() -> None:
    service,renderer,queue,_=fixture(); adapter=adapter_shape(service)
    manager=SimpleNamespace(submit_to_com_thread=queue)
    adapter._com_thread_manager=manager
    service.submit=adapter.submit_frame_to_com_thread
    assert service.request(2,2)
    adapter._source='new.mp4'; service.invalidate()
    queue.run()
    assert not renderer.calls and service.error() is None
    assert service.request(2,2); queue.run(); assert service.consume() is not None


@pytest.mark.parametrize('change',['closed','core','manager'])
def test_queued_frame_losing_owner_is_discarded(change) -> None:
    service,renderer,queue,_=fixture(); adapter=adapter_shape(service)
    adapter._com_thread_manager=SimpleNamespace(submit_to_com_thread=queue)
    service.submit=adapter.submit_frame_to_com_thread
    assert service.request(2,2)
    if change=='closed': adapter._closed=True
    elif change=='core': adapter._core=object()
    else: adapter._com_thread_manager=object()
    queue.run()
    assert not renderer.calls and service.error() is None


@pytest.mark.parametrize('phase',list(SeekPhase))
def test_frame_readiness_waits_for_correlated_seek_completion(phase) -> None:
    adapter=adapter_shape()
    adapter._core.get_seek_receipt=lambda:SimpleNamespace(phase=phase)
    assert adapter.frame_ready() is (phase in (SeekPhase.NATIVE_COMPLETED,SeekPhase.REJECTED,SeekPhase.CANCELLED))


def test_frame_window_identity_is_not_a_new_service_registry() -> None:
    service,_,_,_=fixture(); adapter=adapter_shape(service)
    assert adapter.get_frame_pipeline(7) is service
    assert adapter.get_frame_pipeline(9) is None
    service.rebind(9)
    assert adapter.get_frame_pipeline(7) is None
