"""Typed WIC/GDI boundary; all sizes are Windows ABI sizes, also in tests.

Native calls require Windows. Injected functions are restricted to tests. Engine
pointers are borrowed, bitmap/factory pointers are exclusively owned. No driver,
registry, DXGI manager, DLL refcount manipulation or legacy rendering fallback.
"""
from __future__ import annotations

import ctypes as C
import os
import ntpath
from urllib.parse import urlsplit, unquote
from typing import Callable

from .component_base.definitions_abi import GUID

I32, U32, U16, U8 = C.c_int32, C.c_uint32, C.c_uint16, C.c_uint8
P = C.c_void_p
NativeCall = Callable[..., int]


class WicError(RuntimeError):
    """A checked native failure; never a signal to select another backend."""


class Rect(C.Structure):
    _fields_ = [('left', I32), ('top', I32), ('right', I32), ('bottom', I32)]


class NormalRect(C.Structure):
    _fields_ = [('left', C.c_float), ('top', C.c_float),
                ('right', C.c_float), ('bottom', C.c_float)]


class BitmapInfo(C.Structure):
    _fields_ = [('size', U32), ('width', I32), ('height', I32),
                ('planes', U16), ('bits', U16), ('compression', U32),
                ('image_bytes', U32), ('xppm', I32), ('yppm', I32),
                ('used', U32), ('important', U32)]


CLSID_WIC = GUID.from_string('{CACAF262-9370-4615-A13B-9F5539DA4C0A}')
IID_WIC = GUID.from_string('{EC5EC8A9-C395-4314-9C77-54D7A935FF70}')
BGRA = GUID.from_string('{6FDDC324-4E03-4BFE-B185-3D77768DC90F}')
FORMAT = GUID.from_string('{5066893C-8CF9-42BC-8B8A-472212E52726}')
HWND_KEY = GUID.from_string('{D988879B-67C9-4D92-BAA7-6EADD446039D}')
VISUAL_KEY = GUID.from_string('{6DEBD26F-6AB9-4D7E-B0EE-C61A73FFAD15}')
DXGI_KEY = GUID.from_string('{065702DA-1094-486D-8617-EE7CC4EE4648}')
MF_E_ATTRIBUTENOTFOUND = 0xC00D36E6
MAX_WIDTH, MAX_HEIGHT = 3840, 2160
MAX_PIXELS = MAX_WIDTH * MAX_HEIGHT


def validate_layout() -> None:
    """Reject wrong scalar/struct layout before a pointer reaches Windows.

    Edges: LP64 host longs; mispacked bitmap header; non-pointer-width HWND.
    """
    sizes = (C.sizeof(I32), C.sizeof(U32), C.sizeof(GUID), C.sizeof(Rect),
             C.sizeof(NormalRect), C.sizeof(BitmapInfo), BitmapInfo.bits.offset)
    if sizes != (4, 4, 16, 16, 16, 40, 14) or C.sizeof(P) not in (4, 8):
        raise WicError('WIC/GDI ABI layout mismatch')


def check_hr(value: int, operation: str) -> None:
    """Require S_OK where the documented contract requires a completed operation."""
    if value & 0xFFFFFFFF:
        raise WicError(f'{operation} failed HRESULT=0x{value & 0xFFFFFFFF:08X}')


def source_identity(value: str) -> str:
    """Compare file URLs and Windows paths without decoding literal percent signs.

    Edges: local file URL; UNC path; spaces/escaped percent signs. Non-file URL
    identities remain exact; they are not rewritten as local filesystem paths.
    """
    if not value or len(value) > 32768 or '\x00' in value:
        raise WicError('Invalid native source identity')
    parts = urlsplit(value)
    if parts.scheme.lower() == 'file':
        value = unquote(parts.path)
        if parts.netloc and parts.netloc.lower() != 'localhost':
            value = '//' + parts.netloc + value
        elif len(value) > 2 and value[0] == '/' and value[2] == ':':
            value = value[1:]
    elif parts.scheme and len(parts.scheme) > 1:
        return value
    return ntpath.normcase(ntpath.normpath(value))


def checked_size(width: int, height: int) -> int:
    """Reject zero, overflow and unsupported output; never downscale silently."""
    if type(width) is not int or type(height) is not int:
        raise ValueError('Viewport dimensions must be integers')
    if not 0 < width <= MAX_WIDTH or not 0 < height <= MAX_HEIGHT:
        raise ValueError('WIC viewport exceeds the explicit 3840x2160 output budget')
    return width * height * 4


def method(pointer: P, slot: int, result: type, arguments: tuple[type, ...]) -> NativeCall:
    """Bind a borrowed, live COM vtable once, including the native This argument."""
    if not pointer.value or slot < 0:
        raise WicError('Null native interface or invalid vtable slot')
    table = C.cast(pointer, C.POINTER(C.POINTER(P))).contents
    address = table[slot]
    if not address:
        raise WicError('Null COM method address')
    prototype = getattr(C, 'WINFUNCTYPE', C.CFUNCTYPE)(result, P, *arguments)
    return prototype(address)


def release(pointer: P) -> None:
    """Release exactly the reference owned by the caller; do not infer graph cleanup."""
    if pointer.value:
        function = method(pointer, 2, U32, ())
        value = pointer.value
        count = int(function(P(value)))
        pointer.value = None
        if not 0 <= count <= 0xFFFFFFFF:
            raise WicError('Invalid IUnknown.Release result')


def configure_frame_server(attributes: P) -> None:
    """Set/read back BGRA and prove rendering/DXGI keys absent in fresh attributes.

    Edges: setter fails; format readback drifts; a conflicting attribute survives.
    """
    setter = method(attributes, 21, I32, (C.POINTER(GUID), U32))
    getter = method(attributes, 7, I32, (C.POINTER(GUID), C.POINTER(U32)))
    get_type = method(attributes, 4, I32, (C.POINTER(GUID), C.POINTER(U32)))
    check_hr(setter(attributes, C.byref(FORMAT), 87), 'Attributes.SetUINT32')
    actual = U32()
    check_hr(getter(attributes, C.byref(FORMAT), C.byref(actual)), 'Attributes.GetUINT32')
    if actual.value != 87:
        raise WicError('Video output format readback mismatch')
    for key in (HWND_KEY, VISUAL_KEY, DXGI_KEY):
        hr = int(get_type(attributes, C.byref(key), C.byref(actual))) & 0xFFFFFFFF
        if hr != MF_E_ATTRIBUTENOTFOUND:
            raise WicError(f'Conflicting/unreadable MediaEngine attribute: 0x{hr:08X}')


class WicApi:
    """WIC factory/bitmap acquisition, native thread ownership enforced by renderer."""

    def __init__(self) -> None:
        validate_layout()
        if os.name != 'nt':
            raise OSError('WIC rendering requires Windows')
        self.dll = C.WinDLL('ole32', use_last_error=True)
        self.create = self.dll.CoCreateInstance
        self.create.argtypes = [C.POINTER(GUID), P, U32, C.POINTER(GUID), C.POINTER(P)]
        self.create.restype = I32
        self.aut = C.WinDLL('oleaut32', use_last_error=True)
        self.free_bstr = self.aut.SysFreeString
        self.free_bstr.argtypes, self.free_bstr.restype = [P], None
        self.bstr_len = self.aut.SysStringLen
        self.bstr_len.argtypes, self.bstr_len.restype = [P], U32
        self.uncertain_output: tuple[str, int, int] | None = None

    def factory(self) -> P:
        """Acquire one in-process WIC factory, retaining no implicit GC owner."""
        output = P()
        hr = int(self.create(C.byref(CLSID_WIC), None, 1, C.byref(IID_WIC), C.byref(output)))
        self.check_output(hr, output, 'CoCreateInstance(WIC)')
        if not output.value:
            raise WicError('WIC factory returned S_OK with null output')
        return output

    def bitmap(self, factory: P, width: int, height: int) -> P:
        """Create and verify the exact size/format; reclaim on verification failure."""
        checked_size(width, height)
        create = method(factory, 17, I32, (U32, U32, C.POINTER(GUID), U32, C.POINTER(P)))
        output = P()
        hr = int(create(factory, width, height, C.byref(BGRA), 2, C.byref(output)))
        self.check_output(hr, output, 'WIC.CreateBitmap')
        if not output.value:
            raise WicError('WIC bitmap returned S_OK with null output')
        verified = False
        try:
            self.readback(output, width, height)
            verified = True
            return output
        finally:
            if not verified:
                release(output)

    def check_output(self, hr: int, output: P, operation: str) -> None:
        """Do not call a non-null failed output; retain uncertainty for the owner."""
        if hr & 0xFFFFFFFF and output.value:
            self.uncertain_output = (operation, hr & 0xFFFFFFFF, int(output.value))
        check_hr(hr, operation)

    def verify_resolved(self) -> None:
        """Unexpected failed outputs prevent a successful cleanup receipt."""
        if getattr(self, 'uncertain_output', None) is not None:
            raise WicError('Native acquisition returned an unconfirmed non-null output')

    def current_source(self, engine: P, query: NativeCall) -> str:
        """Read a bounded owned BSTR on revision changes, not in each frame."""
        output = P()
        hr = int(query(engine, C.byref(output)))
        self.check_output(hr, output, 'GetCurrentSource')
        if not output.value:
            raise WicError('GetCurrentSource returned null')
        try:
            length = int(self.bstr_len(output))
            if not 0 < length <= 32768:
                raise WicError('GetCurrentSource returned an invalid BSTR length')
            return C.wstring_at(output, length)
        finally:
            self.free_bstr(output)

    def readback(self, bitmap: P, width: int, height: int) -> None:
        """Read documented metadata, without copying or hashing per-frame pixels."""
        w, h = U32(), U32()
        fmt = GUID.from_string('{00000000-0000-0000-0000-000000000000}')
        get_size = method(bitmap, 3, I32, (C.POINTER(U32), C.POINTER(U32)))
        get_format = method(bitmap, 4, I32, (C.POINTER(GUID),))
        check_hr(get_size(bitmap, C.byref(w), C.byref(h)), 'WIC.GetSize')
        check_hr(get_format(bitmap, C.byref(fmt)), 'WIC.GetPixelFormat')
        if (w.value, h.value) != (width, height) or bytes(fmt) != bytes(BGRA):
            raise WicError('WIC size/pixel format readback mismatch')


class GdiPainter:
    """UI-thread-only presentation to a wx-owned PaintDC; no retained HDC."""

    def __init__(self) -> None:
        validate_layout()
        if os.name != 'nt':
            raise OSError('GDI presentation requires Windows')
        self.dll = C.WinDLL('gdi32', use_last_error=True)
        self.stretch = self.dll.StretchDIBits
        self.stretch.argtypes = [P, I32, I32, I32, I32, I32, I32, I32, I32,
                                 P, C.POINTER(BitmapInfo), U32, U32]
        self.stretch.restype = I32

    def paint(self, hdc: int, pixels: C.Array, info: BitmapInfo) -> None:
        """Paint one borrowed immutable buffer. Errors do not authorize fallback."""
        if not hdc:
            raise WicError('wx PaintDC has no native HDC')
        width, height = int(info.width), -int(info.height)
        needed = checked_size(width, height)
        if C.sizeof(pixels) != needed:
            raise WicError('GDI buffer extent mismatch')
        result = int(self.stretch(P(hdc), 0, 0, width, height, 0, 0, width, height,
                                  pixels, C.byref(info), 0, 0x00CC0020))
        if result <= 0:
            raise WicError(f'StretchDIBits failed result={result}')
