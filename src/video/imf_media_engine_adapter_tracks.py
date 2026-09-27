"""Track inventory/selection behavior, re-exported by the adapter playback module.

This is a preserved extraction of the existing track boundary. The native seek
repair does not change its metadata, refcount, error or capability contracts.
"""
from __future__ import annotations
from typing import Tuple
from .mf_base import logger
CORE_EXCEPTIONS = (AttributeError, OSError, ReferenceError, RuntimeError, TypeError, ValueError)
COERCE_EXCEPTIONS = (TypeError, ValueError, OverflowError)


def get_selected_audio_streams(self, candidate_stream_indices) -> Tuple[int, ...]:
    """Return the selected audio streams among the provided candidate set.

    Edge cases:
        1. Shutdown or missing engine state must return an empty tuple without touching COM state.
        2. Core selection probes can fail on malformed candidate indices and must degrade deterministically.
        3. Legacy callers can pass mixed numeric values that still need stable int coercion on success.
    """
    if not self._is_engine_initialized():
        return ()

    with self._lock:
        if self._shutdown_requested or self._closed:
            return ()

    try:
        selected = self._core.get_selected_audio_streams(candidate_stream_indices)
    except CORE_EXCEPTIONS:
        logger.debug('[IMFAdapter] get_selected_audio_streams failed', exc_info=True)
        return ()

    if not isinstance(selected, tuple):
        return ()

    normalized_selected: list[int] = []
    for raw_index in selected:
        try:
            normalized_selected.append(int(raw_index))
        except COERCE_EXCEPTIONS:
            logger.debug('[IMFAdapter] Ignoring invalid selected audio stream index: %r', raw_index)
    return tuple(normalized_selected)


def get_number_of_streams(self) -> int:
    """Return the current Media Foundation stream count through the adapter boundary.

    Edge cases:
        1. Shutdown or missing engine state must return 0 without touching COM stream state.
        2. Core-side count queries can fail transiently after load_source and must degrade deterministically.
        3. Legacy callers can use the result to bootstrap generic fallback candidates, so normalization must clamp to a plain non-negative int.
    """
    if not self._is_engine_initialized():
        return 0

    with self._lock:
        if self._shutdown_requested or self._closed:
            return 0

    try:
        stream_count = self._core.get_number_of_streams()
    except CORE_EXCEPTIONS:
        logger.debug('[IMFAdapter] get_number_of_streams failed', exc_info=True)
        return 0

    try:
        return max(0, int(stream_count or 0))
    except COERCE_EXCEPTIONS:
        return 0


def select_audio_stream(self, stream_index: int, candidate_stream_indices=None) -> bool:
    """Select one audio stream deterministically through the adapter boundary.

    Edge cases:
        1. Selection before engine startup must fail fast instead of mutating adapter state silently.
        2. Invalid stream identifiers must be rejected before reaching the Media Foundation boundary.
        3. Core selection failures must surface as a clean False result for controller-level fallback logic.
    """
    self._require_active_engine('select_audio_stream')
    try:
        target_stream_index = int(stream_index)
    except COERCE_EXCEPTIONS as exc:
        raise ValueError(f'Invalid audio stream index: {stream_index!r}') from exc

    try:
        return bool(self._core.select_audio_stream(target_stream_index, candidate_stream_indices))
    except CORE_EXCEPTIONS:
        logger.debug('[IMFAdapter] select_audio_stream(%s) failed', target_stream_index, exc_info=True)
        return False


def prime_audio_stream_candidates(self, candidate_stream_indices) -> int | None:
    """Prime the first viable audio-stream candidate before playback starts.

    Edge cases:
        1. Empty candidate lists must return None without attempting any Media Foundation mutation.
        2. Shutdown races must fail fast so startup does not continue with partially primed stream state.
        3. Core priming can return non-int values on legacy paths and must normalize or drop them safely.
    """
    self._require_active_engine('prime_audio_stream_candidates')
    try:
        selected = self._core.prime_audio_stream_candidates(candidate_stream_indices)
    except CORE_EXCEPTIONS:
        logger.debug('[IMFAdapter] prime_audio_stream_candidates failed', exc_info=True)
        return None

    if selected is None:
        return None

    try:
        return int(selected)
    except COERCE_EXCEPTIONS:
        logger.debug('[IMFAdapter] Invalid primed audio stream index: %r', selected)
        return None


def get_text_track_descriptors(self) -> Tuple[dict[str, object], ...]:
    """Return sanitized timed-text track descriptors through the adapter boundary.

    Edge cases:
        1. Shutdown or missing engine state must return an empty tuple without touching timed-text COM state.
        2. Core enumeration can yield partial or malformed descriptor dictionaries that still need stable normalization.
        3. Legacy backends can expose non-int ids or non-bool flags and must be coerced or filtered deterministically.
    """
    if not self._is_engine_initialized():
        return ()

    with self._lock:
        if self._shutdown_requested or self._closed:
            return ()

    try:
        descriptors = self._core.get_text_track_descriptors()
    except CORE_EXCEPTIONS:
        logger.debug('[IMFAdapter] get_text_track_descriptors failed', exc_info=True)
        return ()

    if not isinstance(descriptors, tuple):
        return ()

    normalized_descriptors: list[dict[str, object]] = []
    for raw_descriptor in descriptors:
        if not isinstance(raw_descriptor, dict):
            continue
        try:
            track_id = int(raw_descriptor.get('track_id', -1))
        except COERCE_EXCEPTIONS:
            logger.debug('[IMFAdapter] Ignoring invalid text track descriptor id: %r', raw_descriptor)
            continue
        if track_id < 0:
            continue

        normalized_descriptor = dict(raw_descriptor)
        normalized_descriptor['track_id'] = track_id
        normalized_descriptor['kind'] = int(raw_descriptor.get('kind', 0) or 0)
        normalized_descriptor['kind_label'] = str(raw_descriptor.get('kind_label') or '')
        normalized_descriptor['language'] = str(raw_descriptor.get('language') or '')
        normalized_descriptor['label'] = str(raw_descriptor.get('label') or '')
        normalized_descriptor['raw_label'] = str(raw_descriptor.get('raw_label') or '')
        normalized_descriptor['is_active'] = bool(raw_descriptor.get('is_active'))
        normalized_descriptor['is_in_band'] = bool(raw_descriptor.get('is_in_band'))
        normalized_descriptors.append(normalized_descriptor)
    return tuple(normalized_descriptors)


def get_active_text_track_ids(self) -> Tuple[int, ...]:
    """Return active timed-text track ids in a stable normalized tuple.

    Edge cases:
        1. Shutdown or missing engine state must degrade to an empty tuple without touching COM state.
        2. Core active-track probes can fail independently from track enumeration and must not bubble runtime errors.
        3. Legacy paths can return mixed numeric representations that still need plain-int normalization.
    """
    if not self._is_engine_initialized():
        return ()

    with self._lock:
        if self._shutdown_requested or self._closed:
            return ()

    try:
        active_track_ids = self._core.get_active_text_track_ids()
    except CORE_EXCEPTIONS:
        logger.debug('[IMFAdapter] get_active_text_track_ids failed', exc_info=True)
        return ()

    if not isinstance(active_track_ids, tuple):
        return ()

    normalized_track_ids: list[int] = []
    for raw_track_id in active_track_ids:
        try:
            track_id = int(raw_track_id)
        except COERCE_EXCEPTIONS:
            logger.debug('[IMFAdapter] Ignoring invalid active text track id: %r', raw_track_id)
            continue
        if track_id >= 0:
            normalized_track_ids.append(track_id)
    return tuple(normalized_track_ids)


def select_text_track(self, track_id: int) -> bool:
    """Select one timed-text track deterministically through the adapter boundary.

    Edge cases:
        1. Selection before engine startup must fail fast instead of mutating adapter state silently.
        2. Invalid track identifiers must be rejected before reaching the Media Foundation boundary.
        3. Core selection failures must surface as a clean False result for controller-level fallback logic.
    """
    self._require_active_engine('select_text_track')
    try:
        target_track_id = int(track_id)
    except COERCE_EXCEPTIONS as exc:
        raise ValueError(f'Invalid text track id: {track_id!r}') from exc

    try:
        return bool(self._core.select_text_track(target_track_id))
    except CORE_EXCEPTIONS:
        logger.debug('[IMFAdapter] select_text_track(%s) failed', target_track_id, exc_info=True)
        return False


def disable_text_tracks(self) -> bool:
    """Disable all timed-text tracks through the adapter boundary.

    Edge cases:
        1. Disabling before engine startup must fail fast instead of silently pretending subtitles were cleared.
        2. Shutdown races must not leave higher layers believing the timed-text state was reset when it was not.
        3. Core disable failures must degrade to False so UI/controller layers can report deterministic status.
    """
    self._require_active_engine('disable_text_tracks')
    try:
        return bool(self._core.disable_text_tracks())
    except CORE_EXCEPTIONS:
        logger.debug('[IMFAdapter] disable_text_tracks failed', exc_info=True)
        return False
