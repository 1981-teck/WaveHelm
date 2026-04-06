# -*- coding: utf-8 -*-
"""video_controller.py

Gestisce l'adapter IMFMediaEngine:
- collega l'HWND fornito dalla UI al video adapter (Media Foundation)
- carica e riproduce il video
- gestisce loop e fine riproduzione
"""

from __future__ import annotations

import copy
import logging
import threading
from typing import Optional, Any

from src.video.imf_media_engine_adapter import IMFMediaEngineAdapter
from .video_controller_core import attach_video_controller_core_behavior as _attach_video_controller_core_behavior
from .video_controller_events import attach_video_controller_event_behavior as _attach_video_controller_event_behavior
from .video_controller_playback import attach_video_controller_playback_behavior as _attach_video_controller_playback_behavior
from .video_controller_state import VideoState

_VIDEO_CONTROLLER_ATTACHERS = (
    _attach_video_controller_core_behavior,
    _attach_video_controller_playback_behavior,
    _attach_video_controller_event_behavior,
)

logger = logging.getLogger(__name__)


class VideoController:
    """Controller responsabile della gestione del backend video MF."""

    def __init__(self, event_bus: object):
        self._logger = logger
        self._event_bus = event_bus
        self._close_lock = threading.RLock()
        self._closing: bool = False
        self._shutting_down: bool = False
        self._adapter: Optional[IMFMediaEngineAdapter] = None
        self._loop_enabled: bool = False
        self._current_hwnd: Optional[int] = None
        self._current_path: Optional[str] = None
        self._current_duration_hint: float = 0.0
        self._last_surface_signature: Optional[tuple[int, int, int]] = None
        self._last_error_info: Optional[dict[str, Any]] = None
        self._current_media_metadata: dict[str, Any] = {}
        self._current_audio_track_candidates: tuple[int, ...] = ()
        self._current_audio_track_descriptors: tuple[dict[str, Any], ...] = ()
        self._current_audio_stream_index: int | None = None
        self._current_text_track_descriptors: tuple[dict[str, Any], ...] = ()
        self._current_text_track_id: int | None = None
        self._state: VideoState = VideoState.IDLE
        self._volume: float = 1.0
        self._setup_event_subscriptions()

    @property
    def state(self) -> VideoState:
        return self._state

    @property
    def is_playing(self) -> bool:
        return self._state == VideoState.PLAYING

    @property
    def is_paused(self) -> bool:
        return self._state == VideoState.PAUSED

    @property
    def is_ready(self) -> bool:
        return self._adapter is not None and self._state == VideoState.READY

    @property
    def current_hwnd(self) -> Optional[int]:
        return self._current_hwnd

    @property
    def current_path(self) -> Optional[str]:
        return self._current_path

    @property
    def loop_enabled(self) -> bool:
        return self._loop_enabled

    @property
    def volume(self) -> float:
        return self._volume

    def set_current_media_metadata(self, metadata: Any) -> dict[str, Any]:
        """Store normalized media metadata for the active video session.

        Edge cases handled deterministically:
        1. Callers can provide None or non-dicts and must clear stale state instead of crashing.
        2. Nested metadata structures may be mutated elsewhere, so the controller stores a defensive deep copy for UI consumers.
        3. Audio-track payloads can be malformed, so normalization must skip invalid entries while keeping a stable bounded order.
        """
        if not isinstance(metadata, dict):
            self._current_media_metadata = {}
            self._current_audio_track_descriptors = ()
            self._current_audio_stream_index = None
            return {}

        normalized_metadata = copy.deepcopy(metadata)
        descriptors = self._normalize_audio_track_descriptors(normalized_metadata.get('audio_tracks'))
        self._current_audio_track_descriptors = descriptors
        self._current_media_metadata = self._build_audio_track_metadata_snapshot(
            normalized_metadata,
            descriptors,
        )
        return copy.deepcopy(self._current_media_metadata)

    def get_current_media_metadata(self) -> dict[str, Any]:
        """Return a defensive copy of the normalized current media metadata."""
        return copy.deepcopy(getattr(self, '_current_media_metadata', {}))

    def set_audio_track_descriptors(self, descriptors: Any) -> tuple[dict[str, Any], ...]:
        """Store normalized audio-track descriptors for later UI consumption."""
        normalized_descriptors = self._normalize_audio_track_descriptors(descriptors)
        self._current_audio_track_descriptors = normalized_descriptors
        metadata = dict(getattr(self, '_current_media_metadata', {}) or {})
        self._current_media_metadata = self._build_audio_track_metadata_snapshot(
            metadata,
            normalized_descriptors,
        )
        return tuple(copy.deepcopy(item) for item in normalized_descriptors)

    def get_audio_track_descriptors(self) -> tuple[dict[str, Any], ...]:
        """Return normalized audio-track descriptors for the current session."""
        current_descriptors = getattr(self, '_current_audio_track_descriptors', ())
        if current_descriptors:
            return tuple(copy.deepcopy(item) for item in current_descriptors)
        metadata = getattr(self, '_current_media_metadata', None)
        if isinstance(metadata, dict):
            normalized = self._normalize_audio_track_descriptors(metadata.get('audio_tracks'))
            self._current_audio_track_descriptors = normalized
            return tuple(copy.deepcopy(item) for item in normalized)
        return ()

    def _build_audio_track_metadata_snapshot(
        self,
        metadata: dict[str, Any],
        descriptors: tuple[dict[str, Any], ...],
    ) -> dict[str, Any]:
        """Build a metadata snapshot with deterministic audio-track selection state.

        Edge cases handled deterministically:
        1. A previous session can leave a stale selected stream that is no longer present in the new descriptors.
        2. Metadata dictionaries may already contain audio-track keys and must be rewritten without mutating caller-owned objects.
        3. UI consumers still need a stable highlighted option even before the backend reports a runtime-selected stream.
        """
        metadata_snapshot = dict(metadata or {})
        descriptor_items = tuple(copy.deepcopy(item) for item in descriptors)
        valid_stream_indices = {int(item['stream_index']) for item in descriptor_items}

        current_stream_index = getattr(self, '_current_audio_stream_index', None)
        try:
            normalized_current_stream = int(current_stream_index) if current_stream_index is not None else None
        except (TypeError, ValueError):
            normalized_current_stream = None
        if normalized_current_stream not in valid_stream_indices:
            normalized_current_stream = None
            self._current_audio_stream_index = None

        fallback_selected_stream = None
        if normalized_current_stream is None:
            for item in descriptor_items:
                if item.get('is_default'):
                    fallback_selected_stream = int(item['stream_index'])
                    break
        if fallback_selected_stream is None and descriptor_items:
            fallback_selected_stream = int(descriptor_items[0]['stream_index'])

        selected_stream = normalized_current_stream if normalized_current_stream is not None else fallback_selected_stream
        metadata_snapshot['audio_tracks'] = [
            {
                **copy.deepcopy(item),
                'selected': int(item['stream_index']) == selected_stream,
            }
            for item in descriptor_items
        ]
        return metadata_snapshot

    def get_audio_track_options(self) -> tuple[dict[str, Any], ...]:
        """Expose audio-track options with stable selection state for the UI layer.

        Edge cases handled deterministically:
        1. The adapter can be unavailable, so UI options fall back to controller-cached selection state.
        2. Track descriptors may omit labels or language tags, so each option gets a stable non-empty label.
        3. Candidate filtering must preserve descriptor order while still surfacing a selected fallback track when metadata is partial.
        """
        descriptors = self.get_audio_track_descriptors()
        if not descriptors:
            return ()

        candidate_streams = {
            int(value)
            for value in getattr(self, '_current_audio_track_candidates', ())
            if isinstance(value, int) or str(value).lstrip('-').isdigit()
        }
        descriptor_streams = [
            int(descriptor.get('stream_index'))
            for descriptor in descriptors
            if isinstance(descriptor, dict) and str(descriptor.get('stream_index', '')).lstrip('-').isdigit()
        ]
        if not candidate_streams:
            candidate_streams = set(descriptor_streams)

        selected_streams_getter = getattr(self, 'get_selected_audio_streams', None)
        selected_streams: set[int] = set()
        if callable(selected_streams_getter):
            try:
                selected_streams = {int(value) for value in selected_streams_getter() if int(value) in candidate_streams}
            except (TypeError, ValueError):
                selected_streams = set()

        current_stream_index = getattr(self, '_current_audio_stream_index', None)
        try:
            if current_stream_index is not None and int(current_stream_index) in candidate_streams:
                selected_streams.add(int(current_stream_index))
        except (TypeError, ValueError):
            pass

        if not selected_streams:
            for descriptor in descriptors:
                try:
                    stream_index = int(descriptor.get('stream_index'))
                except (AttributeError, TypeError, ValueError):
                    continue
                if stream_index not in candidate_streams:
                    continue
                if descriptor.get('is_default'):
                    selected_streams = {stream_index}
                    break
        if not selected_streams and candidate_streams:
            for stream_index in descriptor_streams:
                if stream_index in candidate_streams:
                    selected_streams = {stream_index}
                    break

        options: list[dict[str, Any]] = []
        for ordinal, descriptor in enumerate(descriptors, start=1):
            try:
                stream_index = int(descriptor.get('stream_index'))
            except (AttributeError, TypeError, ValueError):
                continue
            if stream_index < 0 or stream_index not in candidate_streams:
                continue
            option = copy.deepcopy(descriptor)
            label = str(option.get('label') or '').strip()
            if not label:
                language = str(option.get('language') or 'Unknown').strip() or 'Unknown'
                codec_name = str(option.get('codec_name') or 'audio').strip() or 'audio'
                label = f'Track {ordinal} — {language} — {codec_name}'
            option['label'] = label
            option['selected'] = stream_index in selected_streams
            options.append(option)

        if options:
            self._current_media_metadata = self._build_audio_track_metadata_snapshot(
                getattr(self, '_current_media_metadata', {}),
                tuple(copy.deepcopy(item) for item in options),
            )
        return tuple(options)

    @staticmethod
    def _normalize_audio_track_descriptors(descriptors: Any) -> tuple[dict[str, Any], ...]:
        """Normalize audio-track descriptors so UI consumers receive stable dictionaries."""
        try:
            raw_descriptors = tuple(descriptors or ())
        except TypeError:
            return ()

        normalized_descriptors: list[dict[str, Any]] = []
        seen_stream_indices: set[int] = set()
        for raw_descriptor in raw_descriptors:
            if not isinstance(raw_descriptor, dict):
                continue
            try:
                stream_index = int(raw_descriptor.get('stream_index'))
            except (TypeError, ValueError):
                continue
            if stream_index < 0 or stream_index in seen_stream_indices:
                continue
            seen_stream_indices.add(stream_index)
            normalized_descriptors.append({
                'stream_index': stream_index,
                'track_index': int(raw_descriptor.get('track_index', len(normalized_descriptors))),
                'language': str(raw_descriptor.get('language') or '').strip(),
                'title': str(raw_descriptor.get('title') or '').strip(),
                'codec_name': str(raw_descriptor.get('codec_name') or '').strip(),
                'codec_long_name': str(raw_descriptor.get('codec_long_name') or '').strip(),
                'channels': raw_descriptor.get('channels'),
                'channel_layout': str(raw_descriptor.get('channel_layout') or '').strip(),
                'is_default': bool(raw_descriptor.get('is_default')),
                'is_forced': bool(raw_descriptor.get('is_forced')),
                'label': str(raw_descriptor.get('label') or '').strip(),
            })
        return tuple(normalized_descriptors)



    def set_text_track_descriptors(self, descriptors: Any) -> tuple[dict[str, Any], ...]:
        """Store normalized subtitle/text-track descriptors for later UI consumption.

        Edge cases handled deterministically:
        1. Callers can provide None or malformed descriptor payloads and must clear stale subtitle state cleanly.
        2. Duplicate or invalid track ids must be filtered before any UI or adapter selection logic uses them.
        3. A previously selected track can disappear when loading a new file, so stale selection state must be dropped.
        """
        normalized_descriptors = self._normalize_text_track_descriptors(descriptors)
        valid_track_ids = {int(item['track_id']) for item in normalized_descriptors}
        current_track_id = getattr(self, '_current_text_track_id', None)
        try:
            normalized_current_track_id = int(current_track_id) if current_track_id is not None else None
        except (TypeError, ValueError):
            normalized_current_track_id = None
        if normalized_current_track_id not in valid_track_ids:
            normalized_current_track_id = None

        descriptor_active_ids = self._resolve_text_track_active_ids_from_descriptors(normalized_descriptors)
        if normalized_current_track_id is None:
            normalized_current_track_id = descriptor_active_ids[0] if descriptor_active_ids else None
        self._current_text_track_id = normalized_current_track_id

        synced_descriptors = self._sync_text_track_selection_state(
            normalized_descriptors,
            descriptor_active_ids if descriptor_active_ids else None,
        )
        self._current_text_track_descriptors = synced_descriptors
        return tuple(copy.deepcopy(item) for item in synced_descriptors)

    def get_text_track_descriptors(self) -> tuple[dict[str, Any], ...]:
        """Return normalized subtitle/text-track descriptors for the current video session.

        Edge cases handled deterministically:
        1. The runtime adapter can be unavailable, so cached descriptors must still be returned safely.
        2. Adapter probes can fail transiently and must not clear previously known descriptors silently.
        3. Active-track state can change at runtime, so descriptor selection flags must be reconciled before returning.
        """
        cached_descriptors = getattr(self, '_current_text_track_descriptors', ())
        if cached_descriptors:
            return tuple(copy.deepcopy(item) for item in self._sync_text_track_selection_state(cached_descriptors))

        adapter = getattr(self, '_adapter', None)
        getter = getattr(adapter, 'get_text_track_descriptors', None) if adapter is not None else None
        if callable(getter):
            try:
                normalized_descriptors = self._normalize_text_track_descriptors(getter())
            except (AttributeError, RuntimeError, TypeError, ValueError, OSError):
                normalized_descriptors = ()
            if normalized_descriptors:
                self._current_text_track_descriptors = normalized_descriptors
                return tuple(copy.deepcopy(item) for item in self._sync_text_track_selection_state(normalized_descriptors))
        return ()

    def get_active_text_track_ids(self) -> tuple[int, ...]:
        """Return active subtitle/text-track ids from the runtime adapter or cached controller state.

        Edge cases handled deterministically:
        1. Missing or legacy adapters must degrade to cached state without raising.
        2. Adapter probes can return mixed numeric values that still need stable int normalization.
        3. Duplicate active ids must be collapsed so UI selection state remains bounded and deterministic.
        """
        adapter = getattr(self, '_adapter', None)
        getter = getattr(adapter, 'get_active_text_track_ids', None) if adapter is not None else None
        normalized_active_ids: list[int] = []
        if callable(getter):
            try:
                raw_active_ids = tuple(getter() or ())
            except (AttributeError, RuntimeError, TypeError, ValueError, OSError):
                raw_active_ids = ()
            seen_track_ids: set[int] = set()
            for raw_track_id in raw_active_ids:
                try:
                    track_id = int(raw_track_id)
                except (TypeError, ValueError):
                    continue
                if track_id < 0 or track_id in seen_track_ids:
                    continue
                seen_track_ids.add(track_id)
                normalized_active_ids.append(track_id)

        descriptor_items = tuple(getattr(self, '_current_text_track_descriptors', ()) or ())
        valid_track_ids = {int(item['track_id']) for item in descriptor_items}
        if normalized_active_ids:
            self._current_text_track_id = normalized_active_ids[0]
        else:
            descriptor_active_ids = self._resolve_text_track_active_ids_from_descriptors(descriptor_items)
            if descriptor_active_ids:
                normalized_active_ids = list(descriptor_active_ids)
                self._current_text_track_id = descriptor_active_ids[0]
            else:
                current_track_id = getattr(self, '_current_text_track_id', None)
                try:
                    cached_track_id = int(current_track_id) if current_track_id is not None else None
                except (TypeError, ValueError):
                    cached_track_id = None
                if cached_track_id in valid_track_ids:
                    normalized_active_ids = [cached_track_id]
                else:
                    self._current_text_track_id = None

        if descriptor_items:
            self._current_text_track_descriptors = self._sync_text_track_selection_state(
                descriptor_items,
                tuple(normalized_active_ids),
            )
        return tuple(normalized_active_ids)

    def get_text_track_options(self) -> tuple[dict[str, Any], ...]:
        """Expose normalized subtitle/text-track options with stable labels and selection state for the UI.

        Edge cases handled deterministically:
        1. Tracks can lack both language and label metadata, so each option needs a stable non-empty fallback label.
        2. Runtime selection can be unavailable, so cached state must still surface one deterministic active track when present.
        3. Descriptor payloads may omit kind labels, so UI labels need sensible fallbacks without mutating adapter-owned objects.
        """
        descriptors = self.get_text_track_descriptors()
        if not descriptors:
            return ()

        active_track_ids = set(self.get_active_text_track_ids())
        options: list[dict[str, Any]] = []
        for ordinal, descriptor in enumerate(descriptors, start=1):
            try:
                track_id = int(descriptor.get('track_id'))
            except (AttributeError, TypeError, ValueError):
                continue
            option = copy.deepcopy(descriptor)
            label = str(option.get('label') or '').strip()
            if not label:
                kind_label = str(option.get('kind_label') or '').strip() or 'Subtitle'
                language = str(option.get('language') or '').strip()
                if language:
                    label = f'{kind_label} — {language}'
                else:
                    label = f'{kind_label} {ordinal}'
            option['label'] = label
            option['selected'] = track_id in active_track_ids
            options.append(option)
        return tuple(options)

    def select_text_track(self, track_id: int) -> bool:
        """Select one subtitle/text track through the controller boundary.

        Edge cases handled deterministically:
        1. Invalid track ids must be rejected before the adapter boundary.
        2. Legacy adapters can miss the selection API and must return False instead of raising.
        3. Successful selections must update cached controller state so UI refreshes stay coherent.
        """
        adapter = getattr(self, '_adapter', None)
        selector = getattr(adapter, 'select_text_track', None) if adapter is not None else None
        if not callable(selector):
            return False

        try:
            target_track_id = int(track_id)
        except (TypeError, ValueError):
            return False
        if target_track_id < 0:
            return False

        try:
            ok = bool(selector(target_track_id))
        except (AttributeError, RuntimeError, TypeError, ValueError, OSError):
            return False

        if ok:
            self._current_text_track_id = target_track_id
            if getattr(self, '_current_text_track_descriptors', ()):
                self._current_text_track_descriptors = self._sync_text_track_selection_state(
                    self._current_text_track_descriptors,
                    (target_track_id,),
                )
        return ok

    def disable_text_tracks(self) -> bool:
        """Disable all subtitle/text tracks through the controller boundary.

        Edge cases handled deterministically:
        1. Legacy adapters can miss the disable API and must degrade to False instead of raising.
        2. Runtime disable failures must preserve existing state instead of claiming subtitles are off.
        3. Successful disable operations must clear cached selected-track state for future UI refreshes.
        """
        adapter = getattr(self, '_adapter', None)
        disabler = getattr(adapter, 'disable_text_tracks', None) if adapter is not None else None
        if not callable(disabler):
            return False

        try:
            ok = bool(disabler())
        except (AttributeError, RuntimeError, TypeError, ValueError, OSError):
            return False

        if ok:
            self._current_text_track_id = None
            if getattr(self, '_current_text_track_descriptors', ()):
                self._current_text_track_descriptors = self._sync_text_track_selection_state(
                    self._current_text_track_descriptors,
                    (),
                )
        return ok

    def _sync_text_track_selection_state(
        self,
        descriptors: tuple[dict[str, Any], ...],
        active_track_ids: tuple[int, ...] | None = None,
    ) -> tuple[dict[str, Any], ...]:
        """Return subtitle descriptors with deterministic selection flags.

        Edge cases handled deterministically:
        1. The adapter may not report active tracks yet, so cached selection state must still be reflected.
        2. Descriptor payloads can be malformed and must be skipped without breaking the whole list.
        3. Multiple active ids should remain supported for backend compatibility even if the UI highlights one primary entry.
        """
        normalized_descriptors = self._normalize_text_track_descriptors(descriptors)
        if active_track_ids is None:
            descriptor_active_ids = self._resolve_text_track_active_ids_from_descriptors(normalized_descriptors)
            if descriptor_active_ids:
                active_track_ids = descriptor_active_ids
            else:
                current_track_id = getattr(self, '_current_text_track_id', None)
                try:
                    active_track_ids = (int(current_track_id),) if current_track_id is not None else ()
                except (TypeError, ValueError):
                    active_track_ids = ()

        normalized_active_ids: set[int] = set()
        for raw_track_id in tuple(active_track_ids or ()):
            try:
                track_id = int(raw_track_id)
            except (TypeError, ValueError):
                continue
            if track_id >= 0:
                normalized_active_ids.add(track_id)

        synced_descriptors: list[dict[str, Any]] = []
        for descriptor in normalized_descriptors:
            track_id = int(descriptor['track_id'])
            synced_descriptor = copy.deepcopy(descriptor)
            synced_descriptor['is_active'] = track_id in normalized_active_ids
            synced_descriptors.append(synced_descriptor)
        return tuple(synced_descriptors)

    @staticmethod
    def _resolve_text_track_active_ids_from_descriptors(
        descriptors: Any,
    ) -> tuple[int, ...]:
        """Return active subtitle track ids already embedded in descriptor payloads.

        Edge cases handled deterministically:
        1. Callers can pass malformed descriptor iterables and still receive an empty tuple.
        2. Duplicate or invalid active track ids must be collapsed to keep selection state bounded.
        3. Descriptor-provided active flags should be preserved when runtime polling is temporarily unavailable.
        """
        normalized_descriptors = VideoController._normalize_text_track_descriptors(descriptors)
        active_track_ids: list[int] = []
        seen_track_ids: set[int] = set()
        for descriptor in normalized_descriptors:
            if not descriptor.get('is_active'):
                continue
            track_id = int(descriptor['track_id'])
            if track_id in seen_track_ids:
                continue
            seen_track_ids.add(track_id)
            active_track_ids.append(track_id)
        return tuple(active_track_ids)

    @staticmethod
    def _normalize_text_track_descriptors(descriptors: Any) -> tuple[dict[str, Any], ...]:
        """Normalize subtitle/text-track descriptors so UI consumers receive stable dictionaries.

        Edge cases handled deterministically:
        1. Callers can provide None or malformed iterables and must get an empty tuple instead of a runtime error.
        2. Duplicate or invalid track ids must be filtered to keep the option list bounded and stable.
        3. Missing label/language metadata must still yield stable string fields for UI rendering.
        """
        try:
            raw_descriptors = tuple(descriptors or ())
        except TypeError:
            return ()

        normalized_descriptors: list[dict[str, Any]] = []
        seen_track_ids: set[int] = set()
        for raw_descriptor in raw_descriptors:
            if not isinstance(raw_descriptor, dict):
                continue
            try:
                track_id = int(raw_descriptor.get('track_id'))
            except (TypeError, ValueError):
                continue
            if track_id < 0 or track_id in seen_track_ids:
                continue
            seen_track_ids.add(track_id)
            normalized_descriptors.append({
                'track_id': track_id,
                'kind': int(raw_descriptor.get('kind', 0) or 0),
                'kind_label': str(raw_descriptor.get('kind_label') or '').strip(),
                'language': str(raw_descriptor.get('language') or '').strip(),
                'label': str(raw_descriptor.get('label') or '').strip(),
                'raw_label': str(raw_descriptor.get('raw_label') or '').strip(),
                'is_active': bool(raw_descriptor.get('is_active')),
                'is_in_band': bool(raw_descriptor.get('is_in_band')),
            })
        return tuple(normalized_descriptors)

def attach_video_controller_behavior(controller_cls: type["VideoController"]) -> None:
    """Attach video controller behavior from one central coordinator.

    Edge cases handled:
    - repeated imports or reloads can silently rebind controller methods;
    - a partial split import can leave the controller only partially patched;
    - reordered installers can break core, playback, or event wiring.
    """
    if getattr(controller_cls, "_video_controller_behavior_attached", False):
        return

    for installer in _VIDEO_CONTROLLER_ATTACHERS:
        installer(controller_cls)

    setattr(controller_cls, "_video_controller_behavior_attached", True)


attach_video_controller_behavior(VideoController)
