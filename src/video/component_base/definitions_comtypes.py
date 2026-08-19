from __future__ import annotations

import ctypes
from ctypes import wintypes

COMTYPES_LOAD_EXCEPTIONS = (Exception,)


def load_comtypes_symbols(iid_imf_media_engine_notify_str: str):
    """Carica simboli comtypes opzionali usati per il callback IMFMediaEngineNotify."""
    comtypes_module = None
    comtypes_available = False

    try:
        import comtypes  # noqa: F401
        from comtypes import COMObject, IUnknown as CT_IUnknown  # type: ignore
        from comtypes import GUID as CT_GUID  # type: ignore
        from comtypes import COMMETHOD, HRESULT as CT_HRESULT  # type: ignore

        comtypes_module = comtypes
        comtypes_available = True

        class IMFMediaEngineNotify(CT_IUnknown):
            _iid_ = CT_GUID(iid_imf_media_engine_notify_str)
            _methods_ = [
                COMMETHOD(
                    [],
                    CT_HRESULT,
                    "EventNotify",
                    (["in"], wintypes.DWORD, "meEvent"),
                    (["in"], ctypes.c_size_t, "param1"),
                    (["in"], wintypes.DWORD, "param2"),
                ),
            ]

        class CT_IMFMediaEngineClassFactory(CT_IUnknown):
            _iid_ = CT_GUID("{4D645ACE-26AA-4688-9BE1-DF3516990B93}")

        return (
            comtypes_module,
            comtypes_available,
            COMObject,
            CT_IUnknown,
            CT_GUID,
            COMMETHOD,
            CT_HRESULT,
            IMFMediaEngineNotify,
            CT_IMFMediaEngineClassFactory,
        )

    except COMTYPES_LOAD_EXCEPTIONS:
        class _ComtypesStub:  # pragma: no cover
            """Stub che fallisce in modo esplicito se comtypes non è disponibile."""

            def __init__(self, *args, **kwargs):
                raise RuntimeError("comtypes non disponibile: impossibile usare questa classe/interfaccia")

        class IMFMediaEngineNotify(_ComtypesStub):  # type: ignore
            pass

        class CT_GUID(_ComtypesStub):  # type: ignore
            pass

        return (
            None,
            False,
            _ComtypesStub,
            _ComtypesStub,
            CT_GUID,
            _ComtypesStub,
            _ComtypesStub,
            IMFMediaEngineNotify,
            _ComtypesStub,
        )
