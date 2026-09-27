"""Cold audio-stream selection behavior extracted from playback without changing its public API.

The state lock guards only state snapshots. COM operations remain outside it and
on the adapter-owned thread. Existing compatibility exports live in playback.
"""
from __future__ import annotations

import logging
from typing import Iterable, SupportsInt, Tuple

from .media_engine_core_shared import (
    AudioStreamCore,
    CONVERSION_EXCEPTIONS, QUERY_EXCEPTIONS,
)

logger = logging.getLogger(__name__)

STREAM_QUERY_E_FAIL_MARKERS = (
    'IMFMediaEngineEx::GetNumberOfStreams failed: hr=0x80004005',
    'IMFMediaEngineEx::SetStreamSelection failed: hr=0x80004005',
    'IMFMediaEngineEx::ApplyStreamSelections failed: hr=0x80004005',
)



def _is_nonfatal_stream_query_error(exc: Exception) -> bool:
    """Return whether a COM-side stream query failure should degrade cleanly.

    Edge cases:
        1. Media Foundation can return E_FAIL while the source is still warming up even though playback later succeeds.
        2. Stream-selection priming may be unsupported for some containers and must not abort the caller's default playback path.
        3. Unexpected exceptions must remain visible so real COM regressions do not get silently masked.
    """
    message = str(exc)
    return any(marker in message for marker in STREAM_QUERY_E_FAIL_MARKERS)



def _normalize_candidate_stream_indices(candidate_stream_indices: Iterable[SupportsInt | str | bytes] | None) -> Tuple[int, ...]:
    """Return a stable, de-duplicated tuple of stream indices for audio selection work.

    Edge cases:
        1. Callers can pass None or an empty iterable and must get a deterministic empty tuple.
        2. Mixed numeric/string values must coerce to plain ints without leaking duplicates.
        3. Negative or non-numeric entries must fail early before any COM-side stream mutation.
    """
    if candidate_stream_indices is None:
        return ()

    normalized: list[int] = []
    seen: set[int] = set()
    for raw_index in tuple(candidate_stream_indices):
        try:
            stream_index = int(raw_index)
        except CONVERSION_EXCEPTIONS as exc:
            raise ValueError(f'Invalid audio stream index: {raw_index!r}') from exc
        if stream_index < 0:
            raise ValueError(f'Invalid audio stream index: {raw_index!r}')
        if stream_index in seen:
            continue
        seen.add(stream_index)
        normalized.append(stream_index)
    return tuple(normalized)



def _selected_candidate_streams_on_com_thread(self: AudioStreamCore, candidate_stream_indices: Tuple[int, ...]) -> Tuple[int, ...]:
    """Return the currently selected stream indices among a bounded candidate set.

    Edge cases:
        1. Empty candidate sets must return an empty tuple without touching Media Foundation state.
        2. Stream-selection probes can fail per-index, so errors must retain exact context for callers.
        3. Duplicate candidate indices must already be normalized so the result ordering stays stable.
    """
    if not candidate_stream_indices:
        return ()

    selected_indices: list[int] = []
    for stream_index in candidate_stream_indices:
        if self._get_stream_selection_on_com_thread(stream_index):
            selected_indices.append(stream_index)
    return tuple(selected_indices)



def get_selected_audio_streams(self: AudioStreamCore, candidate_stream_indices: Iterable[SupportsInt | str | bytes] | None) -> Tuple[int, ...]:
    adapter_obj = self._adapter_ref()
    normalized_candidates = _normalize_candidate_stream_indices(candidate_stream_indices)
    if not adapter_obj or not self._media_engine or not normalized_candidates:
        return ()

    def _get() -> Tuple[int, ...]:
        with self._state_lock:
            if self._shutdown_requested or not self._media_engine:
                return ()
        return _selected_candidate_streams_on_com_thread(self, normalized_candidates)

    try:
        selected = adapter_obj.call_on_com_thread('get_selected_audio_streams', _get)
    except QUERY_EXCEPTIONS:
        return ()
    if not isinstance(selected, tuple):
        return ()
    return tuple(int(stream_index) for stream_index in selected)



def get_number_of_streams(self: AudioStreamCore) -> int:
    """Return the Media Foundation stream count for the currently loaded source.

    Edge cases:
        1. Legacy or partially initialized adapters can lack a live engine and must degrade to 0 without raising.
        2. COM-side stream queries can fail transiently right after source load and must not abort controller-level fallback planning.
        3. Returned values must normalize to a bounded non-negative int so runtime candidate bootstrapping stays deterministic.
    """
    adapter_obj = self._adapter_ref()
    if not adapter_obj or not self._media_engine:
        return 0

    def _get() -> int:
        with self._state_lock:
            if self._shutdown_requested or not self._media_engine:
                return 0
        try:
            return self._get_number_of_streams_on_com_thread()
        except RuntimeError as exc:
            if not _is_nonfatal_stream_query_error(exc):
                raise
            logger.debug('[MediaEngineCore] Non-fatal GetNumberOfStreams failure during source warm-up.', exc_info=True)
            return 0

    try:
        stream_count = adapter_obj.call_on_com_thread('get_number_of_streams', _get)
    except QUERY_EXCEPTIONS:
        return 0
    try:
        return max(0, int(stream_count or 0))
    except CONVERSION_EXCEPTIONS:
        return 0



def _apply_audio_stream_selection_on_com_thread(
    self: AudioStreamCore,
    target_stream_index: int,
    candidate_stream_indices: Tuple[int, ...],
) -> int:
    """Apply one deterministic audio-stream selection across the provided candidate set.

    Edge cases:
        1. The target stream can be missing from the candidate list and must still be selected explicitly.
        2. Previously selected fallback streams must be deselected first to avoid ambiguous multi-audio state.
        3. ApplyStreamSelections can fail after per-stream changes, so callers need the exact failing context.
    """
    ordered_candidates = candidate_stream_indices
    if target_stream_index not in ordered_candidates:
        ordered_candidates = ordered_candidates + (target_stream_index,)

    for stream_index in ordered_candidates:
        self._set_stream_selection_on_com_thread(stream_index, stream_index == target_stream_index)
    self._apply_stream_selections_on_com_thread()
    return int(target_stream_index)



def select_audio_stream(self: AudioStreamCore, stream_index: int, candidate_stream_indices: Iterable[SupportsInt | str | bytes] | None = None) -> bool:
    adapter_obj = self._adapter_ref()
    target_stream_index = int(stream_index)
    normalized_candidates = _normalize_candidate_stream_indices(candidate_stream_indices)
    if not adapter_obj or not self._media_engine:
        return False

    def _select() -> bool:
        with self._state_lock:
            if self._shutdown_requested or not self._media_engine:
                return False
        _apply_audio_stream_selection_on_com_thread(self, target_stream_index, normalized_candidates)
        return True

    try:
        return bool(adapter_obj.call_on_com_thread('select_audio_stream', _select))
    except QUERY_EXCEPTIONS:
        logger.debug('[MediaEngineCore] select_audio_stream(%s) failed.', target_stream_index, exc_info=True)
        return False



def prime_audio_stream_candidates(self: AudioStreamCore, candidate_stream_indices: Iterable[SupportsInt | str | bytes] | None) -> int | None:
    adapter_obj = self._adapter_ref()
    normalized_candidates = _normalize_candidate_stream_indices(candidate_stream_indices)
    if not adapter_obj or not self._media_engine or not normalized_candidates:
        return None

    def _prime() -> int | None:
        with self._state_lock:
            if self._shutdown_requested or not self._media_engine:
                return None
        try:
            return _apply_audio_stream_selection_on_com_thread(self, normalized_candidates[0], normalized_candidates)
        except RuntimeError as exc:
            if not _is_nonfatal_stream_query_error(exc):
                raise
            logger.debug('[MediaEngineCore] Non-fatal audio-stream priming failure; keeping default engine selection.', exc_info=True)
            return None

    try:
        selected = adapter_obj.call_on_com_thread('prime_audio_stream_candidates', _prime)
    except QUERY_EXCEPTIONS:
        logger.debug('[MediaEngineCore] prime_audio_stream_candidates() failed.', exc_info=True)
        return None
    if selected is None:
        return None
    try:
        return int(selected)
    except CONVERSION_EXCEPTIONS:
        return None

