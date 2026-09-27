from __future__ import annotations
import ctypes
import logging
import sys
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
    _IsWindow,
)
from src.video.component_base.iid_registry import IID_IMFMediaEngineEx
from src.video.component_base.definitions_runtime import _bind_function, _load_windll
from src.video.component_base.mf_helpers import MFCreateAttributes, MFCreateMediaEngine
from src.video.component_base.utils import is_success, safe_release

from .media_engine_core_shared import MediaEngineError
from .media_engine_ownership import EngineResources, EngineReference, owned_release_pair
from .wic_native import configure_frame_server
from .wic_renderer import WicRenderer
from .wic_pipeline import FramePipeline, wic_selected
from .media_engine_events import _MediaEngineNotifyCOM
from .media_engine_seek_events import create_bound_notify

from .media_engine_core_stream_api import (
    _ensure_media_engine_ex_on_com_thread,
    _call_media_engine_ex_method_on_com_thread,
    _normalize_stream_index,
    _get_number_of_streams_on_com_thread,
    _get_stream_selection_on_com_thread,
    _set_stream_selection_on_com_thread,
    _apply_stream_selections_on_com_thread,
)

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

def _configure_notify(self, attr_ptr, resources: EngineResources | None = None) -> tuple[object, object]:
    """Configure the native attribute/notify boundary; errors abort engine creation."""
    adapter_obj = self._adapter_ref()
    if adapter_obj is None:
        raise MediaEngineError("Adapter non più disponibile")

    notify_handler = create_bound_notify(self, adapter_obj, _MediaEngineNotifyCOM)
    if not notify_handler:
        raise MediaEngineError("Creazione _MediaEngineNotifyCOM fallita")

    if resources is not None:
        resources.notify_handler = notify_handler
    try:
        notify_iunknown = notify_handler.QueryInterface(CT_IUnknown)
        if resources is not None:
            resources.notify_iunknown = notify_iunknown
    except CORE_SETUP_EXCEPTIONS as exc:
        logger.error("[MediaEngineCore] QI a IUnknown fallito per notify handler: %s", exc, exc_info=True)
        raise

    if notify_iunknown is not None:
        self._imfattributes_set_unknown(attr_ptr, MF_MEDIA_ENGINE_CALLBACK, notify_iunknown)

    return notify_handler, notify_iunknown

def _create_engine_on_com_thread(self, hwnd: int) -> None:
    resources = EngineResources()
    with self._state_lock:
        if self._shutdown_requested:
            raise MediaEngineError('Shutdown requested: cannot create engine')
        if getattr(self, '_creation_resources', None) is not None or getattr(self, '_retired_engine_resources', None) is not None:
            raise MediaEngineError('Prior engine creation/cleanup is unresolved')
        if self._media_engine:
            return
        self._creation_resources = resources
    committed = False
    try:
        logger.info('[MediaEngineCore] Creating owned engine on COM thread.')
        self._validate_hwnd_for_rendering(hwnd)
        _build_engine_resources(self, resources, hwnd)
        engine = resources.engine.borrow()
        extension = resources.extension.borrow()
        with self._state_lock:
            if self._shutdown_requested or self._creation_resources is not resources:
                raise MediaEngineError('Engine creation invalidated before publication')
            self._attributes, self._factory = resources.attributes, resources.factory
            self._notify_handler, self._notify_iunknown = resources.notify_handler, resources.notify_iunknown
            self._engine_references = resources
            self._wic_pipeline = resources.frame_pipeline
            self._media_engine, self._media_engine_ex = engine, extension
            self._playback_hwnd = int(hwnd)
            self._engine_generation += 1
            self._creation_resources = None
            committed = True
        logger.info('[MediaEngineCore] Owned engine created successfully.')
    except CORE_SETUP_EXCEPTIONS:
        logger.exception('[MediaEngineCore] Engine creation failed.')
        raise
    finally:
        if not committed:
            resources.creation_error = sys.exception()
            resources.rollback_creation(self)

def _configure_wic_source_policy(self, engine) -> None:
    """WIC-only AUTO preload/no-autoplay; setter failure/readback drift/HWND entry fail closed."""
    _check_hr(int(self._call_engine_ptr_method(engine, 'SetAutoPlay', wintypes.BOOL(0))), 'IMFMediaEngine::SetAutoPlay(FALSE)')
    _check_hr(int(self._call_engine_ptr_method(engine, 'SetPreload', c_uint32(4))), 'IMFMediaEngine::SetPreload(AUTOMATIC)')
    preload = int(self._call_engine_ptr_method(engine, 'GetPreload'))
    autoplay = bool(self._call_engine_ptr_method(engine, 'GetAutoPlay'))
    if preload != 4 or autoplay:
        raise MediaEngineError(f'WIC source policy readback mismatch: preload={preload} autoplay={autoplay}')
    logger.info('[WIC] SOURCE_POLICY preload=4 autoplay=false')

def _build_engine_resources(self, resources: EngineResources, hwnd: int) -> None:
    """Capture each acquisition before the next fallible step; no native calls under locks."""
    resources.attributes = MFCreateAttributes(10)
    if not resources.attributes:
        raise MediaEngineError('MFCreateAttributes returned no owner')
    attr_ptr = resources.attributes.as_interface(IMFAttributes)
    if not attr_ptr:
        raise MediaEngineError('Missing IMFAttributes view')
    use_wic = wic_selected()
    if use_wic:
        configure_frame_server(cast(attr_ptr, c_void_p))
    else:
        self._imfattributes_set_uint64(attr_ptr, MF_MEDIA_ENGINE_PLAYBACK_HWND, c_uint64(int(hwnd)))
    _configure_notify(self, attr_ptr, resources)
    resources.factory = MFCreateMediaEngine()
    if not resources.factory:
        raise MediaEngineError('MFCreateMediaEngine returned no factory owner')
    factory_ptr = resources.factory.as_interface(IMFMediaEngineClassFactory)
    if not factory_ptr:
        raise MediaEngineError('Missing IMFMediaEngineClassFactory view')
    output = cast(resources.engine.receive(), ctypes.POINTER(ctypes.POINTER(IMFMediaEngine)))
    hr = int(factory_ptr.contents.lpVtbl.contents.CreateInstance(factory_ptr, c_uint32(0), attr_ptr, output))
    resources.engine.confirm(hr)
    _check_hr(hr, 'IMFMediaEngineClassFactory::CreateInstance')
    if use_wic:
        _configure_wic_source_policy(self, resources.engine.borrow())
    if not resources.engine:
        raise MediaEngineError('CreateInstance returned a null engine')
    self._query_media_engine_ex_on_com_thread(resources.engine.borrow(), owner=resources.extension)
    if use_wic:
        adapter = self._adapter_ref()
        resources.wic_renderer = WicRenderer(identity=lambda: self._active_source)
        resources.wic_renderer.open(cast(resources.engine.borrow(), c_void_p))
        resources.frame_pipeline = FramePipeline(resources.wic_renderer,
            adapter.submit_frame_to_com_thread, adapter.frame_ready, int(hwnd), pump_ready=adapter.frame_pump_ready)
        logger.info('[WIC] BACKEND_READY format=87 no_hwnd_attribute=true')

def _release_current_engine_on_com_thread(self, reason: str) -> None:
    with self._state_lock:
        owned_release_pair(self)  # Reject provenance mismatch before detaching anything.
        resources = getattr(self, '_engine_references', None)
        if getattr(self, '_retired_engine_resources', None) is not None:
            raise MediaEngineError('Prior rebind cleanup remains unresolved')
        self._engine_references = None
        self._wic_pipeline = None
        self._retired_engine_resources = resources
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
    if resources is not None:
        try:
            resources.cleanup_once(self, stop=True)
        finally:
            if resources.reclaimed:
                with self._state_lock:
                    self._retired_engine_resources = None
        return
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
    pipeline = getattr(self, '_wic_pipeline', None)
    if pipeline is not None:
        self._validate_hwnd_for_rendering(hwnd)
        pipeline.rebind(hwnd)
        with self._state_lock:
            self._playback_hwnd = hwnd
        return True

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

def _query_media_engine_ex_on_com_thread(self, engine_ptr, *, owner: EngineReference | None = None):
    if not engine_ptr:
        return None

    try:
        iunknown = cast(engine_ptr, ctypes.POINTER(IUnknown))
        query_interface = iunknown.contents.lpVtbl.contents.QueryInterface
        out_ptr = c_void_p()
        output = byref(out_ptr) if owner is None else owner.receive()
        hr = int(
            query_interface(
                cast(iunknown, c_void_p),
                byref(IID_IMFMediaEngineEx),
                output,
            )
        )
        if owner is not None:
            owner.confirm(hr)
            out_ptr = owner.ptr or c_void_p()
        if not is_success(hr) or not out_ptr.value:
            logger.debug("[MediaEngineCore] IMFMediaEngineEx non disponibile (hr=%s).", _hr_to_hex(hr))
            return None

        logger.debug("[MediaEngineCore] IMFMediaEngineEx acquisito con successo.")
        return cast(out_ptr, ctypes.POINTER(IUnknown))
    except CORE_SETUP_EXCEPTIONS:
        logger.debug("[MediaEngineCore] QueryInterface(IMFMediaEngineEx) failed.", exc_info=True)
        if owner is not None and owner.unresolved:
            raise
        return None

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
