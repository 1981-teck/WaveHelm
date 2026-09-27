"""Protect the native-stream import cleanup without creating native devices.

Edge cases: deferred annotations still resolve; private playback reexports survive;
error/release providers retain identity; NULL strings never invoke a COM release.
The absence checks are implementation-detail guards. Other checks protect contracts.
"""
from __future__ import annotations

import ctypes
import inspect
from types import ModuleType
from typing import get_type_hints

import pytest

from src.video import media_engine_core_audio_streams as audio
from src.video import media_engine_core_playback as playback
from src.video import media_engine_core_shared as shared
from src.video import media_engine_core_timed_text as timed
from src.video import media_engine_timed_text_backend as backend
from src.video import mf_base
from src.video.component_base import com_helpers, definitions
from src.video.media_engine_core import MediaEngineCore

AUDIO_EXPORTS = (
    '_apply_audio_stream_selection_on_com_thread', '_is_nonfatal_stream_query_error',
    '_normalize_candidate_stream_indices', '_selected_candidate_streams_on_com_thread',
    'get_number_of_streams', 'get_selected_audio_streams',
    'prime_audio_stream_candidates', 'select_audio_stream',
)
TEXT_EXPORTS = (
    '_decode_timed_text_wstr', '_enumerate_timed_text_track_list_on_com_thread',
    '_get_active_text_track_ids_on_com_thread', '_get_text_track_descriptors_on_com_thread',
    '_read_timed_text_track_descriptor_on_com_thread', '_select_timed_text_track_on_com_thread',
    'disable_text_tracks', 'get_active_text_track_ids', 'get_text_track_descriptors',
    'select_text_track',
)


@pytest.mark.parametrize(('module', 'name'), [
    (audio, 'MediaEngineError'), (audio, 'CoreState'),
    (audio, 'CORE_PLAYBACK_EXCEPTIONS'), (timed, 'CONVERSION_EXCEPTIONS'),
])
def test_only_reviewed_unused_bindings_are_absent(module: ModuleType, name: str) -> None:
    """Assert the specific cleanup, not removal of the providers themselves."""
    assert name not in vars(module)


@pytest.mark.parametrize(('module', 'name'), [
    (audio, 'AudioStreamCore'), (audio, 'CONVERSION_EXCEPTIONS'),
    (audio, 'QUERY_EXCEPTIONS'), (timed, 'MediaEngineError'),
    (timed, 'CoreState'), (timed, 'CORE_PLAYBACK_EXCEPTIONS'),
    (timed, 'QUERY_EXCEPTIONS'),
])
def test_required_shared_types_and_error_policies_retain_identity(module: ModuleType, name: str) -> None:
    """Do not substitute or widen a canonical type or exception tuple."""
    assert getattr(module, name) is getattr(shared, name)


@pytest.mark.parametrize(('module', 'count', 'self_type'), [
    (audio, 8, shared.AudioStreamCore), (timed, 15, shared.CoreState),
])
def test_all_stream_function_annotations_resolve(module: ModuleType, count: int, self_type: type) -> None:
    """Resolve every module-owned function, including deferred self annotations."""
    functions = [value for value in vars(module).values()
                 if inspect.isfunction(value) and value.__module__ == module.__name__]
    assert len(functions) == count
    for function in functions:
        hints = get_type_hints(function)
        assert 'return' in hints
        if 'self' in inspect.signature(function).parameters:
            assert hints['self'] is self_type


@pytest.mark.parametrize(('module', 'names'), [(audio, AUDIO_EXPORTS), (timed, TEXT_EXPORTS)])
def test_playback_reexports_still_reference_original_functions(module: ModuleType, names: tuple[str, ...]) -> None:
    """Keep both public controls and documented private compatibility exports."""
    for name in names:
        assert getattr(playback, name) is getattr(module, name)


@pytest.mark.parametrize(('module', 'name'), [
    (audio, 'get_number_of_streams'), (audio, 'get_selected_audio_streams'),
    (audio, 'prime_audio_stream_candidates'), (audio, 'select_audio_stream'),
    (timed, 'disable_text_tracks'), (timed, 'get_active_text_track_ids'),
    (timed, 'get_text_track_descriptors'), (timed, 'select_text_track'),
])
def test_media_engine_owner_keeps_stream_method_attachments(module: ModuleType, name: str) -> None:
    """Check the assembled owner rather than a synthetic substitute class."""
    assert getattr(MediaEngineCore, name) is getattr(module, name)


def test_native_memory_providers_and_pointer_types_are_unchanged() -> None:
    """Keep task-memory release and native pointer providers, without a DLL call."""
    assert timed.safe_release is backend.release_owned
    assert timed.TrackPointer is ctypes.POINTER(definitions.IMFTimedTextTrack)
    assert timed.TrackListPointer is ctypes.POINTER(definitions.IMFTimedTextTrackList)
    assert callable(timed._CoTaskMemFree)
    assert issubclass(timed.TimedTextMemoryError, shared.MediaEngineError)


@pytest.mark.parametrize('null_owner', [None, ctypes.c_wchar_p()])
def test_null_owned_string_needs_no_release(null_owner: ctypes.c_wchar_p | None) -> None:
    """The real decoder must return before the native deallocator for NULL."""
    assert timed._decode_timed_text_wstr(null_owner) == ''


@pytest.mark.parametrize('invalid_owner', ['not-owned', ctypes.c_void_p()])
def test_untyped_string_owner_is_rejected(invalid_owner: object) -> None:
    """Invalid ownership must not become successful string decoding."""
    with pytest.raises(TypeError, match='owned LPWSTR'):
        timed._decode_timed_text_wstr(invalid_owner)


def test_com_facade_required_aliases_remain_available() -> None:
    """Do not remove public compatibility providers based only on local loads."""
    assert com_helpers.GUID is definitions.GUID is mf_base.GUID
    assert com_helpers.HRESULT is definitions.HRESULT is mf_base.HRESULT
