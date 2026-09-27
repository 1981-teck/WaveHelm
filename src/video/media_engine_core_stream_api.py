"""Existing extended audio-stream COM helpers, re-exported by core setup.

Cohesive extraction preserves the previous helper contracts and dispatch behavior.
Absent interface, shutdown and failing HRESULT paths remain explicit exceptions.
"""
from __future__ import annotations
from ctypes import byref, wintypes
from .component_base.com_helpers import _check_hr
from .media_engine_core_shared import MediaEngineError
from .media_engine_ownership import release_engine

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
        resources = getattr(self, "_engine_references", None)
        generation = getattr(self, "_engine_generation", 0)

    if cached_engine_ex is not None:
        return cached_engine_ex
    if not engine_ptr:
        raise MediaEngineError('MediaEngine non inizializzato: IMFMediaEngineEx non disponibile.')

    media_engine_ex = (self._query_media_engine_ex_on_com_thread(engine_ptr)
                       if resources is None else
                       self._query_media_engine_ex_on_com_thread(engine_ptr, owner=resources.extension))
    if media_engine_ex is None:
        raise MediaEngineError('IMFMediaEngineEx non disponibile sul backend corrente.')

    with self._state_lock:
        stale = (self._shutdown_requested or self._media_engine is not engine_ptr
                 or getattr(self, '_engine_generation', 0) != generation
                 or getattr(self, '_engine_references', None) is not resources)
        if not stale:
            self._media_engine_ex = media_engine_ex
    if stale:
        if resources is not None:
            release_engine(resources.extension, 'stale media engine ex')
        raise MediaEngineError('Engine changed during extended-interface acquisition')
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
