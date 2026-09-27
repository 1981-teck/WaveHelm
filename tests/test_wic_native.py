"""Executable ABI doubles: real ctypes callbacks, not a simulated Windows run."""
from __future__ import annotations

import ctypes as C
import threading
from types import SimpleNamespace

import pytest

from src.video import wic_native as n
from src.video.wic_renderer import WicRenderer


class Interface:
    """Keep the vtable and callback objects alive until every borrowed call ends."""
    def __init__(self, size: int = 45) -> None:
        self.table = (n.P * size)()
        self.instance = (n.P * 1)(C.cast(self.table, n.P).value)
        self.ptr = C.cast(self.instance, n.P)
        self.callbacks: list[object] = []
        self.calls: list[int] = []
        self.add(2, n.U32, (), lambda *_: 0)

    def add(self, index: int, result: type, args: tuple[type, ...], callback) -> None:
        def invoke(this, *values):
            assert int(this) == self.ptr.value
            self.calls.append(index)
            return callback(this, *values)
        fn = getattr(C, 'WINFUNCTYPE', C.CFUNCTYPE)(result, n.P, *args)(invoke)
        self.callbacks.append(fn)
        self.table[index] = C.cast(fn, n.P).value


def write32(pointer, value: int) -> None:
    C.cast(pointer, C.POINTER(n.U32)).contents.value = value


class NativeFixture:
    def __init__(self) -> None:
        self.engine, self.factory, self.bitmap = Interface(), Interface(), Interface()
        self.width, self.height = 64, 36
        self.hr = 0
        self.copy_hr = 0
        self.tick_hr = 0
        self.pts = 100
        self.engine.add(44, n.I32, (C.POINTER(C.c_int64),), self.tick)
        self.engine.add(43, n.I32, (n.P, C.POINTER(n.NormalRect), C.POINTER(n.Rect), n.P), self.transfer)
        self.engine.add(41, n.I32, (C.POINTER(n.U32), C.POINTER(n.U32)), self.aspect)
        self.bitmap.add(7, n.I32, (n.P, n.U32, n.U32, n.P), self.copy)
        self.bitmap.add(3, n.I32, (C.POINTER(n.U32), C.POINTER(n.U32)), self.size)
        self.bitmap.add(4, n.I32, (C.POINTER(n.GUID),), self.format)
        self.factory.add(17, n.I32, (n.U32, n.U32, C.POINTER(n.GUID), n.U32, C.POINTER(n.P)), self.create)
        self.api = n.WicApi.__new__(n.WicApi)
        def create_factory(clsid, outer, context, iid, output):
            assert bytes(C.cast(clsid, C.POINTER(n.GUID)).contents) == bytes(n.CLSID_WIC)
            C.cast(output, C.POINTER(n.P)).contents.value = self.factory.ptr.value
            return 0
        self.api.create = create_factory

    def tick(self, this, pts) -> int:
        pts.contents.value = self.pts
        return self.tick_hr

    def transfer(self, this, bitmap, src, dest, border) -> int:
        assert bitmap == self.bitmap.ptr.value
        assert src.contents.right == src.contents.bottom == 1
        return self.hr

    def aspect(self, this, w, h) -> int:
        w.contents.value, h.contents.value = 16, 9
        return 0

    def copy(self, this, rect, stride, count, output) -> int:
        assert stride == self.width * 4 and count == self.width * self.height * 4
        C.memset(output, 123, count)
        return self.copy_hr

    def size(self, this, w, h) -> int:
        w.contents.value, h.contents.value = self.width, self.height
        return 0

    def format(self, this, fmt) -> int:
        C.memmove(fmt, C.byref(n.BGRA), 16)
        return 0

    def create(self, this, w, h, fmt, cache, output) -> int:
        self.width, self.height = w, h
        assert cache == 2 and bytes(fmt.contents) == bytes(n.BGRA)
        output.contents.value = self.bitmap.ptr.value
        return 0

    def renderer(self) -> WicRenderer:
        renderer = WicRenderer(self.api)
        renderer.open(self.engine.ptr)
        return renderer


def test_layout_and_sdk_identifiers() -> None:
    n.validate_layout()
    assert str(n.VISUAL_KEY).lower().strip('{}') == '6debd26f-6ab9-4d7e-b0ee-c61a73ffad15'
    assert C.sizeof(n.BitmapInfo) == 40 and C.sizeof(n.Rect) == 16


@pytest.mark.parametrize('width,height', [(0,10), (10,0), (-1,10), (3841,1), (1,2161), (True,5), (1.5,2)])
def test_output_budget_rejects_invalid_dimensions(width, height) -> None:
    with pytest.raises(ValueError):
        n.checked_size(width, height)


@pytest.mark.parametrize('width,height', [(1,1), (640,360), (1920,1080), (3840,2160)])
def test_output_budget_exact(width: int, height: int) -> None:
    assert n.checked_size(width,height) == width*height*4


@pytest.mark.parametrize('size', [(64,36), (64,48), (36,64), (128,72)])
def test_native_receives_full_viewport_for_single_letterboxing(size) -> None:
    f=NativeFixture(); captured=[]
    def transfer(this,bitmap,source,destination,border):
        d=destination.contents
        captured.append((d.left,d.top,d.right,d.bottom))
        return 0
    f.engine.add(43,n.I32,(n.P,C.POINTER(n.NormalRect),C.POINTER(n.Rect),n.P),transfer)
    renderer=f.renderer(); renderer.render(*size,1)
    assert captured==[(0,0,*size)]
    renderer.close()


def test_renderer_double_buffer_and_idempotent_owner_cleanup() -> None:
    f = NativeFixture(); r = f.renderer()
    a = r.render(64,36,1); b = r.render(64,36,1); c = r.render(64,36,1)
    assert a.pixels is not b.pixels and c.pixels is a.pixels
    assert a.pixels[0] == 123 and r.frames == 3 and r.resizes == 1
    r.close(); r.close()
    assert f.bitmap.calls.count(2) == f.factory.calls.count(2) == 1
    assert f.engine.calls.count(2) == 0  # Engine is borrowed, never released here.


def test_sfalse_does_not_transfer_or_swap_buffers() -> None:
    f = NativeFixture(); r = f.renderer(); r.render(64,36,1)
    before = len(f.engine.calls); index = r.index
    f.tick_hr = 1
    assert r.render(64,36,1) is None
    assert f.engine.calls[before:] == [44] and r.index == index
    r.close()


def test_paused_resize_transfers_current_frame() -> None:
    f = NativeFixture(); r = f.renderer(); first = r.render(64,36,1)
    f.tick_hr = 1
    second = r.render(128,72,2)
    assert second.info.width == 128 and C.sizeof(second.pixels) == 128*72*4
    assert first.info.width == 64 and C.sizeof(first.pixels) == 64*36*4
    r.close()


@pytest.mark.parametrize('stage', ['tick','transfer','copy'])
def test_native_failure_never_yields_a_frame(stage: str) -> None:
    f=NativeFixture(); r=f.renderer()
    setattr(f, {'tick':'tick_hr','transfer':'hr','copy':'copy_hr'}[stage], -2147467259)
    with pytest.raises(n.WicError):
        r.render(64,36,1)
    assert r.frames == 0
    r.close()


def test_wrong_thread_and_post_close_are_rejected() -> None:
    f=NativeFixture(); r=f.renderer(); errors=[]
    def other() -> None:
        try:
            r.close()
        except n.WicError as error:
            errors.append(error)
    t=threading.Thread(target=other); t.start(); t.join()
    assert len(errors)==1 and not r.closed
    r.close()
    with pytest.raises(n.WicError):
        r.render(64,36,1)


def test_failed_bitmap_readback_is_reclaimed() -> None:
    f=NativeFixture(); f.bitmap.add(3,n.I32,(C.POINTER(n.U32),C.POINTER(n.U32)),lambda *_: -2147467259)
    with pytest.raises(n.WicError):
        f.api.bitmap(f.factory.ptr,64,36)
    assert f.bitmap.calls.count(2)==1


@pytest.mark.parametrize('stage', ['set','get','mismatch','conflict','query_error'])
def test_attribute_failures_are_explicit(stage: str) -> None:
    a=Interface(34)
    a.add(21,n.I32,(C.POINTER(n.GUID),n.U32),lambda *_: -2147467259 if stage=='set' else 0)
    def get(this,key,value) -> int:
        value.contents.value=0 if stage=='mismatch' else 87
        return -2147467259 if stage=='get' else 0
    a.add(7,n.I32,(C.POINTER(n.GUID),C.POINTER(n.U32)),get)
    hr = 0 if stage=='conflict' else (-2147467259 if stage=='query_error' else C.c_int32(n.MF_E_ATTRIBUTENOTFOUND).value)
    a.add(4,n.I32,(C.POINTER(n.GUID),C.POINTER(n.U32)),lambda *_:hr)
    with pytest.raises(n.WicError):
        n.configure_frame_server(a.ptr)


def test_attribute_configuration_proves_absence_of_three_keys() -> None:
    a=Interface(34); keys=[]
    a.add(21,n.I32,(C.POINTER(n.GUID),n.U32),lambda *_:0)
    def get(this,key,value) -> int:
        value.contents.value=87
        return 0
    def get_type(this,key,value) -> int:
        keys.append(bytes(key.contents))
        return C.c_int32(n.MF_E_ATTRIBUTENOTFOUND).value
    a.add(7,n.I32,(C.POINTER(n.GUID),C.POINTER(n.U32)),get)
    a.add(4,n.I32,(C.POINTER(n.GUID),C.POINTER(n.U32)),get_type)
    n.configure_frame_server(a.ptr)
    assert keys == [bytes(n.HWND_KEY),bytes(n.VISUAL_KEY),bytes(n.DXGI_KEY)]


@pytest.mark.parametrize('result', [0,-1])
def test_failed_gdi_presentation_is_not_success(result: int) -> None:
    painter=n.GdiPainter.__new__(n.GdiPainter); painter.stretch=lambda *args: result
    with pytest.raises(n.WicError):
        painter.paint(0x100000123,(n.U8*16)(),n.BitmapInfo(40,2,-2,1,32,0,16,0,0,0,0))


def test_paused_restore_repaints_same_size_after_invalidation() -> None:
    f=NativeFixture(); r=f.renderer()
    r.render(64,36,1); f.tick_hr=1
    assert r.render(64,36,1) is None
    restored=r.render(64,36,2)
    assert restored is not None and restored.revision==2
    r.close()


@pytest.mark.parametrize('expected,actual', [
    (r'C:\Video Folder\a.mp4','file:///C:/Video%20Folder/a.mp4'),
    (r'C:\Video\a%20b.mp4','file:///C:/Video/a%2520b.mp4'),
    (r'\\SERVER\Share\a.mp4','file://server/Share/a.mp4'),
    (r'C:\VIDEO\a.mp4',r'c:\video\A.mp4'),
    ('https://example.test/Video?a=2','https://example.test/Video?a=2'),
])
def test_native_current_source_identity_comparison(expected,actual) -> None:
    assert n.source_identity(expected)==n.source_identity(actual)


@pytest.mark.parametrize('value', ['', 'a\x00b', 'a'*32769], ids=['empty','nul','over-cap'])
def test_native_source_identity_rejects_invalid_values(value) -> None:
    with pytest.raises(n.WicError): n.source_identity(value)


@pytest.mark.parametrize('actual', ['old.mp4',None])
def test_native_current_source_mismatch_never_delivers_frame(actual) -> None:
    f=NativeFixture()
    f.engine.add(7,n.I32,(C.POINTER(n.P),),lambda *_:0)
    f.api.current_source=lambda *args: actual
    r=WicRenderer(f.api,identity=lambda:'new.mp4')
    r.open(f.engine.ptr)
    with pytest.raises((n.WicError,TypeError)): r.render(64,36,1)
    assert r.frames==0
    r.close()


def test_native_identity_is_read_only_on_revision_change() -> None:
    f=NativeFixture(); calls=[]
    f.engine.add(7,n.I32,(C.POINTER(n.P),),lambda *_:0)
    def identity(*args): calls.append(1); return 'a.mp4'
    f.api.current_source=identity
    r=WicRenderer(f.api,identity=lambda:'a.mp4'); r.open(f.engine.ptr)
    r.render(64,36,1); r.render(64,36,1); r.render(64,36,2)
    assert len(calls)==2
    r.close()


@pytest.mark.parametrize('length',[0,32769,5])
def test_current_source_bstr_is_freed_on_success_or_validation_failure(length) -> None:
    f=NativeFixture(); storage=C.create_unicode_buffer('a.mp4'); freed=[]
    def query(this,output):
        C.cast(output,C.POINTER(n.P)).contents.value=C.cast(storage,n.P).value
        return 0
    f.api.bstr_len=lambda ptr:length
    f.api.free_bstr=lambda ptr:freed.append(ptr.value)
    if length==5:
        assert f.api.current_source(f.engine.ptr,query)=='a.mp4'
    else:
        with pytest.raises(n.WicError): f.api.current_source(f.engine.ptr,query)
    assert freed==[C.cast(storage,n.P).value]


def test_failed_acquisition_nonnull_output_is_not_called_or_reported_reclaimed() -> None:
    f=NativeFixture()
    def create(*args):
        C.cast(args[-1],C.POINTER(n.P)).contents.value=123456
        return -2147467259
    f.api.create=create
    r=WicRenderer(f.api)
    with pytest.raises(n.WicError): r.open(f.engine.ptr)
    assert f.api.uncertain_output==('CoCreateInstance(WIC)',0x80004005,123456)
    with pytest.raises(n.WicError): r.close()
    with pytest.raises(n.WicError,match='no second release'): r.close()
    assert 2 not in f.engine.calls  # Unconfirmed pointer and borrowed engine untouched.


@pytest.mark.parametrize('resource',['factory','bitmap'])
def test_successful_acquisition_with_null_output_fails(resource) -> None:
    f=NativeFixture()
    if resource=='factory':
        f.api.create=lambda *args:0
        with pytest.raises(n.WicError): f.api.factory()
    else:
        f.factory.add(17,n.I32,(n.U32,n.U32,C.POINTER(n.GUID),n.U32,C.POINTER(n.P)),lambda *_:0)
        with pytest.raises(n.WicError): f.api.bitmap(f.factory.ptr,64,36)
