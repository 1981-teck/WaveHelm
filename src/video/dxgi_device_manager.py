# -*- coding: utf-8 -*-
from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import POINTER, byref, c_uint
from typing import Any, Optional

from .component_base.com_helpers import _check_hr
from .component_base.definitions import HRESULT, IUnknown, IMFDXGIDeviceManager, _mfplat
from .component_base.utils import safe_release

logger = logging.getLogger(__name__)

DXGI_PTR_COERCE_EXCEPTIONS = (TypeError, ValueError, ctypes.ArgumentError)
DXGI_IUNKNOWN_EXCEPTIONS = (
    AttributeError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
    ctypes.ArgumentError,
)
DXGI_RELEASE_EXCEPTIONS = (
    AttributeError,
    ImportError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)

_lock = threading.RLock()


def _resolve_mfcreate_dxgi_device_manager():
    fn = getattr(_mfplat, "MFCreateDXGIDeviceManager", None)
    if fn is None:
        return None
    fn.argtypes = [ctypes.POINTER(c_uint), ctypes.POINTER(ctypes.POINTER(IMFDXGIDeviceManager))]
    fn.restype = HRESULT
    return fn


_MFCreateDXGIDeviceManager = _resolve_mfcreate_dxgi_device_manager()


def _to_nonzero_int_ptr(value: Any, name: str) -> int:
    if isinstance(value, int):
        ptr = int(value)
    elif isinstance(value, ctypes.c_void_p):
        ptr = int(value.value or 0)
    else:
        try:
            ptr = int(ctypes.cast(value, ctypes.c_void_p).value or 0)
        except DXGI_PTR_COERCE_EXCEPTIONS as exc:
            raise ValueError(f"{name} must be a valid non-zero pointer (int/c_void_p/ctypes ptr): {exc}") from exc

    if ptr == 0:
        raise ValueError(f"{name} must be a valid non-zero pointer")
    return ptr


def _addref_iunknown(iunknown_ptr: POINTER(IUnknown)) -> int:
    try:
        if not iunknown_ptr or not iunknown_ptr.contents or not iunknown_ptr.contents.lpVtbl:
            return 0
        this = ctypes.cast(iunknown_ptr, ctypes.c_void_p)
        rc = iunknown_ptr.contents.lpVtbl.contents.AddRef(this)
        return int(rc) if rc is not None else 0
    except DXGI_IUNKNOWN_EXCEPTIONS:
        return 0


def _release_iunknown(iunknown_ptr: POINTER(IUnknown)) -> int:
    try:
        if not iunknown_ptr or not iunknown_ptr.contents or not iunknown_ptr.contents.lpVtbl:
            return 0
        this = ctypes.cast(iunknown_ptr, ctypes.c_void_p)
        rc = iunknown_ptr.contents.lpVtbl.contents.Release(this)
        return int(rc) if rc is not None else 0
    except DXGI_IUNKNOWN_EXCEPTIONS:
        return 0


class DXGIDeviceManager:
    def __init__(self, d3d_device_ptr: int) -> None:
        d3d_device_ptr = _to_nonzero_int_ptr(d3d_device_ptr, "d3d_device_ptr")

        if _MFCreateDXGIDeviceManager is None:
            raise RuntimeError(
                "MFCreateDXGIDeviceManager non disponibile in mfplat.dll in questo ambiente"
            )

        self._manager: Optional[POINTER(IMFDXGIDeviceManager)] = None
        self._reset_token: Optional[int] = None

        reset_token = c_uint(0)
        manager = POINTER(IMFDXGIDeviceManager)()

        hr = _MFCreateDXGIDeviceManager(byref(reset_token), byref(manager))
        _check_hr(hr, "MFCreateDXGIDeviceManager")

        if not manager:
            raise RuntimeError("MFCreateDXGIDeviceManager ha restituito un puntatore manager NULL")

        with _lock:
            self._manager = manager
            self._reset_token = int(reset_token.value)

        logger.info(
            "[DXGIDeviceManager] Created (reset_token=%s, ptr=0x%016X)",
            int(reset_token.value),
            int(ctypes.cast(manager, ctypes.c_void_p).value or 0),
        )

        self.reset_device(d3d_device_ptr)

    @property
    def manager(self) -> POINTER(IMFDXGIDeviceManager):
        with _lock:
            mgr = self._manager
        if not mgr:
            raise RuntimeError("DXGIDeviceManager è stato rilasciato")
        return mgr

    @property
    def reset_token(self) -> int:
        with _lock:
            token = self._reset_token
        if token is None:
            raise RuntimeError("DXGIDeviceManager non ha reset_token (rilasciato o non inizializzato)")
        return int(token)

    def reset_device(self, d3d_device_ptr: int) -> None:
        d3d_device_ptr = _to_nonzero_int_ptr(d3d_device_ptr, "d3d_device_ptr")

        mgr = self.manager
        vtbl = mgr.contents.lpVtbl.contents
        device_iunknown = ctypes.cast(ctypes.c_void_p(d3d_device_ptr), POINTER(IUnknown))
        rc_add = _addref_iunknown(device_iunknown)

        try:
            hr = vtbl.ResetDevice(
                ctypes.cast(mgr, ctypes.c_void_p),
                device_iunknown,
                c_uint(self.reset_token),
            )
            _check_hr(hr, "IMFDXGIDeviceManager::ResetDevice")

            logger.info(
                "[DXGIDeviceManager] ResetDevice ok (device=0x%016X, addref_rc=%s)",
                int(d3d_device_ptr),
                rc_add,
            )
        finally:
            _release_iunknown(device_iunknown)

    def as_iunknown(self) -> POINTER(IUnknown):
        return ctypes.cast(self.manager, POINTER(IUnknown))

    def release(self) -> None:
        with _lock:
            mgr = self._manager
            self._manager = None
            self._reset_token = None

        if mgr:
            try:
                safe_release(mgr, name="IMFDXGIDeviceManager")
            except DXGI_RELEASE_EXCEPTIONS as exc:
                logger.debug("[DXGIDeviceManager] safe_release raised: %r", exc, exc_info=True)

    def __del__(self) -> None:
        try:
            self.release()
        except DXGI_RELEASE_EXCEPTIONS as error:
            logger.debug("[DXGIDeviceManager] __del__ release failed: %r", error, exc_info=True)


__all__ = ["DXGIDeviceManager"]
