from __future__ import annotations

import ctypes
from ctypes import wintypes


def _missing_comtypes_symbols():
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


def load_comtypes_symbols(iid_imf_media_engine_notify_str: str):
    """Carica simboli comtypes opzionali usati per il callback IMFMediaEngineNotify.

    Solo l'assenza effettiva del package ``comtypes`` degrada agli stub. Errori
    interni del package, API incomplete o errori nella costruzione delle
    interfacce devono propagare: trattarli come "comtypes non disponibile"
    nasconderebbe regressioni o installazioni corrotte.
    """
    try:
        import comtypes
    except ModuleNotFoundError as exc:
        if exc.name != "comtypes":
            raise
        return _missing_comtypes_symbols()

    from comtypes import COMObject, IUnknown as CT_IUnknown  # type: ignore
    from comtypes import GUID as CT_GUID  # type: ignore
    from comtypes import COMMETHOD, HRESULT as CT_HRESULT  # type: ignore

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
        comtypes,
        True,
        COMObject,
        CT_IUnknown,
        CT_GUID,
        COMMETHOD,
        CT_HRESULT,
        IMFMediaEngineNotify,
        CT_IMFMediaEngineClassFactory,
    )
