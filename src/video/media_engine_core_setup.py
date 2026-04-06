from __future__ import annotations

import ctypes
import logging
from ctypes import byref, c_uint32, c_uint64, c_void_p, wintypes, cast

from src.video.component_base.com_helpers import _check_hr, _hr_to_hex
from src.video.component_base.definitions import (
    CT_IUnknown,
    IMFAttributes,
    IMFMediaEngine,
    IMFMediaEngineClassFactory,
    IUnknown,
    MF_MEDIA_ENGINE_CALLBACK,
    MF_MEDIA_ENGINE_PLAYBACK_HWND,
    MF_MEDIA_ENGINE_VIDEO_OUTPUT_FORMAT,
    _IsWindow,
)
from src.video.component_base.iid_registry import IID_IMFMediaEngineEx
from src.video.component_base.definitions_runtime import _bind_function, _load_windll
from src.video.component_base.mf_helpers import MFCreateAttributes, MFCreateMediaEngine
from src.video.component_base.utils import is_success, safe_release

from .media_engine_core_shared import MediaEngineError
from .media_engine_events import _MediaEngineNotifyCOM

logger = logging.getLogger(__name__)

CORE_SETUP_EXCEPTIONS = (AttributeError, OSError, ReferenceError, RuntimeError, TypeError, ValueError, ctypes.ArgumentError)
VTABLE_LOOKUP_EXCEPTIONS = (AttributeError, OSError, RuntimeError, TypeError, ValueError, ctypes.ArgumentError)

# Win32 DLL loading is kept fail-fast on real Windows runtimes, while degraded
# imports outside Windows remain collectable for tests/audit tools.
_user32 = _load_windll("user32")
_kernel32 = _load_windll("kernel32")

_GetWindowThreadProcessId = _bind_function(
    _user32,
    "user32",
    "GetWindowThreadProcessId",
    argtypes=[wintypes.HWND, ctypes.POINTER(wintypes.DWORD)],
    restype=wintypes.DWORD,
)

_GetCurrentProcessId = _bind_function(
    _kernel32,
    "kernel32",
    "GetCurrentProcessId",
    argtypes=[],
    restype=wintypes.DWORD,
)

_IsWindowVisible = _bind_function(
    _user32,
    "user32",
    "IsWindowVisible",
    argtypes=[wintypes.HWND],
    restype=wintypes.BOOL,
)

_GetClientRect = _bind_function(
    _user32,
    "user32",
    "GetClientRect",
    argtypes=[wintypes.HWND, ctypes.POINTER(wintypes.RECT)],
    restype=wintypes.BOOL,
)


def _validate_hwnd_for_rendering(self, hwnd: int) -> None:
    if not hwnd or int(hwnd) <= 0:
        raise MediaEngineError(f"HWND non valido: {hwnd}")

    h = wintypes.HWND(int(hwnd))
    if not _IsWindow(h):
        raise MediaEngineError(f"HWND non è una window valida (IsWindow=0): {hwnd}")

    pid = wintypes.DWORD(0)
    _GetWindowThreadProcessId(h, byref(pid))
    cur_pid = int(_GetCurrentProcessId())
    if int(pid.value) not in (0, cur_pid):
        raise MediaEngineError(f"HWND appartiene a un altro processo (pid={pid.value}, cur={cur_pid}): {hwnd}")

    rc = wintypes.RECT()
    if _GetClientRect(h, byref(rc)):
        w = int(rc.right - rc.left)
        hgt = int(rc.bottom - rc.top)
        if w <= 0 or hgt <= 0:
            logger.warning(
                "[MediaEngineCore] HWND valido ma area client zero (%dx%d). Video potrebbe non renderizzare finché la UI non viene ridimensionata. hwnd=%s",
                w,
                hgt,
                hwnd,
            )

    if not bool(_IsWindowVisible(h)):
        logger.debug("[MediaEngineCore] HWND non visibile al momento della creazione (best effort). hwnd=%s", hwnd)


def _create_engine_on_com_thread(self, hwnd: int) -> None:
    with self._state_lock:
        if self._shutdown_requested:
            raise MediaEngineError("Shutdown già richiesto: impossibile creare l'engine.")
        if self._media_engine:
            return

    logger.info("[MediaEngineCore] Inizio creazione engine su thread COM...")
    self._validate_hwnd_for_rendering(hwnd)

    try:
        attributes = MFCreateAttributes(10)
        if not attributes:
            raise MediaEngineError("MFCreateAttributes ha restituito None")

        attr_ptr = attributes.as_interface(IMFAttributes)
        if not attr_ptr:
            raise MediaEngineError("Impossibile ottenere IMFAttributes dall'oggetto attributes")

        self._imfattributes_set_uint64(attr_ptr, MF_MEDIA_ENGINE_PLAYBACK_HWND, c_uint64(int(hwnd)))

        adapter_obj = self._adapter_ref()
        if adapter_obj is None:
            raise MediaEngineError("Adapter non più disponibile")

        notify_handler = _MediaEngineNotifyCOM(adapter_obj)
        if not notify_handler:
            raise MediaEngineError("Creazione _MediaEngineNotifyCOM fallita")

        try:
            notify_iunknown = notify_handler.QueryInterface(CT_IUnknown)
        except CORE_SETUP_EXCEPTIONS as exc:
            logger.error("[MediaEngineCore] QI a IUnknown fallito per notify handler: %s", exc, exc_info=True)
            raise

        if notify_iunknown is not None:
            self._imfattributes_set_unknown(attr_ptr, MF_MEDIA_ENGINE_CALLBACK, notify_iunknown)

        try:
            self._imfattributes_set_uint32(
                attr_ptr,
                MF_MEDIA_ENGINE_VIDEO_OUTPUT_FORMAT,
                c_uint32(int(0x00000015)),
            )
        except CORE_SETUP_EXCEPTIONS:
            logger.debug("[MediaEngineCore] Output format non impostato (best effort).", exc_info=True)

        factory = MFCreateMediaEngine()
        if not factory:
            raise MediaEngineError("MFCreateMediaEngineClassFactory ha restituito None")

        factory_ptr = factory.as_interface(IMFMediaEngineClassFactory)
        if not factory_ptr:
            raise MediaEngineError("Impossibile ottenere IMFMediaEngineClassFactory dalla factory")

        vtbl_factory = factory_ptr.contents.lpVtbl.contents
        p_engine = ctypes.POINTER(IMFMediaEngine)()
        hr = int(vtbl_factory.CreateInstance(factory_ptr, c_uint32(0), attr_ptr, byref(p_engine)))
        _check_hr(hr, "IMFMediaEngineClassFactory::CreateInstance")

        if not p_engine:
            raise MediaEngineError("CreateInstance ha restituito un puntatore engine nullo")

        media_engine_ex = self._query_media_engine_ex_on_com_thread(p_engine)

        with self._state_lock:
            self._attributes = attributes
            self._factory = factory
            self._media_engine_ex = media_engine_ex
            self._notify_handler = notify_handler
            self._notify_iunknown = notify_iunknown
            self._media_engine = p_engine
            self._playback_hwnd = int(hwnd)
            self._engine_generation += 1
        logger.info("[MediaEngineCore] MediaEngine creato con successo.")

    except CORE_SETUP_EXCEPTIONS as exc:
        logger.error("[MediaEngineCore] Creazione engine fallita: %s", exc, exc_info=True)
        self.shutdown()
        raise


def _release_current_engine_on_com_thread(self, reason: str) -> None:
    with self._state_lock:
        media_engine = self._media_engine
        factory = self._factory
        media_engine_ex = self._media_engine_ex
        attributes = self._attributes
        notify_iunknown = self._notify_iunknown
        notify_handler = self._notify_handler

        self._media_engine = None
        self._factory = None
        self._media_engine_ex = None
        self._attributes = None
        self._notify_iunknown = None
        self._notify_handler = None
        self._vtable_call_cache.clear()
        self._source = None
        self._active_source = None
        self._playback_hwnd = None

    logger.info("[MediaEngineCore] Rilascio engine corrente (reason=%s).", reason)
    try:
        safe_release(media_engine_ex, "media engine ex")
        safe_release(media_engine, "media engine")
        safe_release(factory, "factory")
        safe_release(attributes, "attributes")
    finally:
        _ = notify_handler
        self._release_notify_iunknown(notify_iunknown)


def _try_rebind_video_window_on_com_thread(self, hwnd: int) -> bool:
    hwnd = int(hwnd)
    with self._state_lock:
        if self._shutdown_requested or not self._media_engine:
            return False

    try:
        self._call_vtable_method("SetVideoWindow", c_void_p(hwnd))
        with self._state_lock:
            self._playback_hwnd = hwnd
        logger.info("[MediaEngineCore] Rebind HWND riuscito via SetVideoWindow (HWND=%s).", hwnd)
        return True
    except CORE_SETUP_EXCEPTIONS:
        logger.debug(
            "[MediaEngineCore] SetVideoWindow non disponibile o fallito; ricreazione engine necessaria.",
            exc_info=True,
        )
        return False


def _query_media_engine_ex_on_com_thread(self, engine_ptr):
    if not engine_ptr:
        return None

    try:
        iunknown = cast(engine_ptr, ctypes.POINTER(IUnknown))
        query_interface = iunknown.contents.lpVtbl.contents.QueryInterface
        out_ptr = c_void_p()
        hr = int(
            query_interface(
                cast(iunknown, c_void_p),
                byref(IID_IMFMediaEngineEx),
                byref(out_ptr),
            )
        )
        if not is_success(hr) or not out_ptr.value:
            logger.debug("[MediaEngineCore] IMFMediaEngineEx non disponibile (hr=%s).", _hr_to_hex(hr))
            return None

        logger.debug("[MediaEngineCore] IMFMediaEngineEx acquisito con successo.")
        return cast(out_ptr, ctypes.POINTER(IUnknown))
    except CORE_SETUP_EXCEPTIONS:
        logger.debug("[MediaEngineCore] QueryInterface(IMFMediaEngineEx) failed.", exc_info=True)
        return None


def _ensure_media_engine_ex_on_com_thread(self):
    """Return a cached IMFMediaEngineEx pointer or acquire it deterministically.

    Edge cases:
        1. The engine can exist while the extended interface has not been queried yet.
        2. QueryInterface can fail transiently, so the helper must leave the cached state consistent.
        3. Shutdown or teardown can race with callers, so a missing engine must fail fast with a stable error.
    """
    with self._state_lock:
        if self._shutdown_requested:
            raise MediaEngineError('Shutdown già richiesto: impossibile usare IMFMediaEngineEx.')
        engine_ptr = self._media_engine
        cached_engine_ex = self._media_engine_ex

    if cached_engine_ex is not None:
        return cached_engine_ex
    if not engine_ptr:
        raise MediaEngineError('MediaEngine non inizializzato: IMFMediaEngineEx non disponibile.')

    media_engine_ex = self._query_media_engine_ex_on_com_thread(engine_ptr)
    if media_engine_ex is None:
        raise MediaEngineError('IMFMediaEngineEx non disponibile sul backend corrente.')

    with self._state_lock:
        if self._shutdown_requested:
            raise MediaEngineError('Shutdown già richiesto: impossibile usare IMFMediaEngineEx.')
        self._media_engine_ex = media_engine_ex
    return media_engine_ex


def _call_media_engine_ex_method_on_com_thread(self, method_name: str, *args):
    """Invoke an IMFMediaEngineEx method through the canonical vtable specs.

    Edge cases:
        1. The extended interface can be absent on older runtimes and must fail with a precise error.
        2. Method signatures are bound by the shared vtable map, so unsupported names must not silently fall back.
        3. COM-thread callers can race with shutdown, so interface acquisition must be revalidated before dispatch.
    """
    media_engine_ex = self._ensure_media_engine_ex_on_com_thread()
    return self._call_engine_ptr_method(media_engine_ex, method_name, *args)


def _normalize_stream_index(self, stream_index: int) -> wintypes.DWORD:
    """Normalize stream indices before touching Media Foundation stream-selection APIs.

    Edge cases:
        1. UI or probe layers can pass strings or floats, so the helper coerces to int deterministically.
        2. Negative indices must be rejected early to avoid undefined COM calls.
        3. Extremely large values must remain bounded by DWORD conversion rules without wraparound surprises.
    """
    try:
        normalized = int(stream_index)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'Indice stream non valido: {stream_index!r}') from exc
    if normalized < 0 or normalized > 0xFFFFFFFF:
        raise ValueError(f'Indice stream non valido: {stream_index!r}')
    return wintypes.DWORD(normalized)


def _get_number_of_streams_on_com_thread(self) -> int:
    """Read the Media Foundation stream count from IMFMediaEngineEx.

    Edge cases:
        1. Backends without IMFMediaEngineEx must fail explicitly instead of pretending there are zero streams.
        2. HRESULT failures must surface with context so higher layers can choose fallback strategies.
        3. The returned DWORD must be normalized to a plain Python int for deterministic downstream logic.
    """
    stream_count = wintypes.DWORD(0)
    hr = int(self._call_media_engine_ex_method_on_com_thread('GetNumberOfStreams', byref(stream_count)))
    _check_hr(hr, 'IMFMediaEngineEx::GetNumberOfStreams')
    return max(0, int(stream_count.value))


def _get_stream_selection_on_com_thread(self, stream_index: int) -> bool:
    """Return whether a specific Media Foundation stream is currently selected.

    Edge cases:
        1. Invalid indices must be rejected before they hit the COM boundary.
        2. Stream-selection queries can fail independently from engine creation and must keep a precise error context.
        3. BOOL results must be normalized to a Python bool so UI and retry logic remain deterministic.
    """
    normalized_index = self._normalize_stream_index(stream_index)
    selected = wintypes.BOOL(0)
    hr = int(self._call_media_engine_ex_method_on_com_thread('GetStreamSelection', normalized_index, byref(selected)))
    _check_hr(hr, 'IMFMediaEngineEx::GetStreamSelection')
    return bool(selected.value)


def _set_stream_selection_on_com_thread(self, stream_index: int, selected: bool) -> None:
    """Select or deselect a Media Foundation stream on the COM thread.

    Edge cases:
        1. Invalid indices must be rejected before the COM call to avoid undefined backend behavior.
        2. BOOL coercion must be explicit so callers cannot leak arbitrary integers across the boundary.
        3. HRESULT failures must remain actionable for higher-level retry logic and UI feedback.
    """
    normalized_index = self._normalize_stream_index(stream_index)
    hr = int(
        self._call_media_engine_ex_method_on_com_thread(
            'SetStreamSelection',
            normalized_index,
            wintypes.BOOL(1 if selected else 0),
        )
    )
    _check_hr(hr, 'IMFMediaEngineEx::SetStreamSelection')


def _apply_stream_selections_on_com_thread(self) -> None:
    """Commit pending Media Foundation stream-selection changes.

    Edge cases:
        1. Some runtimes accept individual selection changes but fail when applying them as a batch.
        2. The helper must preserve the exact HRESULT context for deterministic fallback diagnostics.
        3. Callers may invoke apply repeatedly, so the method stays idempotent on successful backends.
    """
    hr = int(self._call_media_engine_ex_method_on_com_thread('ApplyStreamSelections'))
    _check_hr(hr, 'IMFMediaEngineEx::ApplyStreamSelections')


def ensure_engine(self, hwnd: int) -> None:
    hwnd = int(hwnd)

    with self._state_lock:
        if self._shutdown_requested:
            raise MediaEngineError("Shutdown già richiesto: impossibile ensure_engine().")
        if self._media_engine:
            if self._playback_hwnd == hwnd:
                return
            needs_rebind_or_recreate = True
        else:
            needs_rebind_or_recreate = False

    adapter_obj = self._adapter_ref()
    if not adapter_obj:
        return

    if not needs_rebind_or_recreate:
        adapter_obj.call_on_com_thread("create_engine", lambda: self._create_engine_on_com_thread(hwnd))
        return

    def _rebind_or_recreate() -> None:
        if self._try_rebind_video_window_on_com_thread(hwnd):
            return
        self._release_current_engine_on_com_thread("hwnd_changed")
        self._create_engine_on_com_thread(hwnd)

    adapter_obj.call_on_com_thread("rebind_or_recreate_engine", _rebind_or_recreate)


def _imfattributes_set_uint64(self, attrs_ptr, key_guid, value) -> None:
    guid_ptr = byref(key_guid) if hasattr(key_guid, "Data1") else byref(key_guid)
    c_val = value if isinstance(value, c_uint64) else c_uint64(int(value))
    try:
        set_uint64 = attrs_ptr.contents.lpVtbl.contents.SetUINT64
    except VTABLE_LOOKUP_EXCEPTIONS as exc:
        logger.exception("[MediaEngineCore] IMFAttributes VTable non disponibile per SetUINT64")
        raise RuntimeError("IMFAttributes VTable not available for SetUINT64") from exc

    hr = set_uint64(attrs_ptr, guid_ptr, c_val)
    hr_u32 = int(hr) & 0xFFFFFFFF
    if hr_u32 != 0:
        logger.error(
            "[MediaEngineCore] IMFAttributes::SetUINT64 fallita hr=0x%08X (guid=%s, value=%s)",
            hr_u32,
            repr(key_guid),
            int(c_val.value),
        )
        raise OSError(f"IMFAttributes::SetUINT64 failed hr=0x{hr_u32:08X}")


def _imfattributes_set_uint32(self, attrs_ptr, key_guid, value) -> None:
    guid_ptr = byref(key_guid) if hasattr(key_guid, "Data1") else byref(key_guid)
    c_val = value if isinstance(value, c_uint32) else c_uint32(int(value))
    try:
        set_uint32 = attrs_ptr.contents.lpVtbl.contents.SetUINT32
    except VTABLE_LOOKUP_EXCEPTIONS as exc:
        logger.exception("[MediaEngineCore] IMFAttributes VTable non disponibile per SetUINT32")
        raise RuntimeError("IMFAttributes VTable not available for SetUINT32") from exc

    hr = set_uint32(attrs_ptr, guid_ptr, c_val)
    hr_u32 = int(hr) & 0xFFFFFFFF
    if hr_u32 != 0:
        logger.error(
            "[MediaEngineCore] IMFAttributes::SetUINT32 fallita hr=0x%08X (guid=%s, value=%s)",
            hr_u32,
            repr(key_guid),
            int(c_val.value),
        )
        raise OSError(f"IMFAttributes::SetUINT32 failed hr=0x{hr_u32:08X}")


def _imfattributes_set_unknown(self, attrs_ptr, key_guid, unk_obj) -> None:
    guid_ptr = byref(key_guid) if hasattr(key_guid, "Data1") else byref(key_guid)
    unk_ptr = unk_obj
    try:
        set_unknown = attrs_ptr.contents.lpVtbl.contents.SetUnknown
    except VTABLE_LOOKUP_EXCEPTIONS as exc:
        logger.exception("[MediaEngineCore] IMFAttributes VTable non disponibile per SetUnknown")
        raise RuntimeError("IMFAttributes VTable not available for SetUnknown") from exc

    hr = set_unknown(attrs_ptr, guid_ptr, unk_ptr)
    hr_u32 = int(hr) & 0xFFFFFFFF
    if hr_u32 != 0:
        logger.error(
            "[MediaEngineCore] IMFAttributes::SetUnknown fallita hr=0x%08X (guid=%s)",
            hr_u32,
            repr(key_guid),
        )
        raise OSError(f"IMFAttributes::SetUnknown failed hr=0x{hr_u32:08X}")


_MEDIA_ENGINE_CORE_SETUP_METHODS = (
    ("_validate_hwnd_for_rendering", _validate_hwnd_for_rendering),
    ("_create_engine_on_com_thread", _create_engine_on_com_thread),
    ("_release_current_engine_on_com_thread", _release_current_engine_on_com_thread),
    ("_try_rebind_video_window_on_com_thread", _try_rebind_video_window_on_com_thread),
    ("_query_media_engine_ex_on_com_thread", _query_media_engine_ex_on_com_thread),
    ("_ensure_media_engine_ex_on_com_thread", _ensure_media_engine_ex_on_com_thread),
    ("_call_media_engine_ex_method_on_com_thread", _call_media_engine_ex_method_on_com_thread),
    ("_normalize_stream_index", _normalize_stream_index),
    ("_get_number_of_streams_on_com_thread", _get_number_of_streams_on_com_thread),
    ("_get_stream_selection_on_com_thread", _get_stream_selection_on_com_thread),
    ("_set_stream_selection_on_com_thread", _set_stream_selection_on_com_thread),
    ("_apply_stream_selections_on_com_thread", _apply_stream_selections_on_com_thread),
    ("ensure_engine", ensure_engine),
    ("_imfattributes_set_uint64", _imfattributes_set_uint64),
    ("_imfattributes_set_uint32", _imfattributes_set_uint32),
    ("_imfattributes_set_unknown", _imfattributes_set_unknown),
)


def install_media_engine_core_setup_behavior(cls) -> None:
    """Install MediaEngineCore setup behavior on the central class.

    Edge cases:
        - Re-running the installer during reloads can silently override patched methods.
        - Partial split imports can expose only a subset of setup helpers on the class.
        - Divergent leaf-level entrypoints can drift from the coordinator contract over time.
    """
    if getattr(cls, "_media_engine_core_setup_behavior_attached", False):
        return

    for name, method in _MEDIA_ENGINE_CORE_SETUP_METHODS:
        setattr(cls, name, method)

    setattr(cls, "_media_engine_core_setup_behavior_attached", True)


def attach_media_engine_core_setup_behavior(cls) -> None:
    """Compatibility shim for historical attach_* imports.

    Prefer install_media_engine_core_setup_behavior() from the central coordinator path.
    """
    install_media_engine_core_setup_behavior(cls)
