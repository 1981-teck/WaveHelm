from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Iterable, Sequence, TypeVar

from src.model.media_file import MediaFile, MediaType


_ExceptionTypes = tuple[type[BaseException], ...]
_T = TypeVar('_T')


def canonical_media_path(path: str, *, exceptions: _ExceptionTypes) -> str:
    value = str(path or '').strip()
    if not value:
        return ''
    if value.startswith(('http://', 'https://', 'rtsp://', 'rtmp://')):
        return value
    try:
        return os.path.normcase(os.path.abspath(os.path.normpath(value)))
    except exceptions:
        return value


def selected_items(list_ctrl: object, items: Sequence[_T]) -> list[_T]:
    first_selected = getattr(list_ctrl, 'GetFirstSelected', None)
    next_selected = getattr(list_ctrl, 'GetNextSelected', None)
    if not callable(first_selected) or not callable(next_selected):
        return []
    selected: list[_T] = []
    index = first_selected()
    while index != -1:
        if 0 <= index < len(items):
            selected.append(items[index])
        index = next_selected(index)
    return selected


def selected_media_paths(list_ctrl: object, media_items: Sequence[MediaFile]) -> list[str]:
    return [media.path for media in selected_items(list_ctrl, media_items) if media.path]


def library_search_blob(media: MediaFile) -> str:
    metadata = dict(getattr(media, 'metadata', {}) or {})
    parts = (
        getattr(media, 'title', '') or '',
        metadata.get('artist', '') or '',
        metadata.get('album', '') or '',
        getattr(media, 'path', '') or '',
    )
    return ' '.join(str(part).lower() for part in parts if part)


def filter_library_media(
    media_items: Iterable[MediaFile],
    *,
    query: str,
    selected_filter: MediaType,
) -> list[MediaFile]:
    normalized_query = str(query or '').strip().lower()
    filtered: list[MediaFile] = []
    for media in media_items:
        if selected_filter is not MediaType.ALL and getattr(media, 'media_type', MediaType.UNKNOWN) is not selected_filter:
            continue
        if normalized_query and normalized_query not in library_search_blob(media):
            continue
        filtered.append(media)
    return filtered


def library_sort_key(media: MediaFile, sort_name: str) -> object:
    metadata = dict(getattr(media, 'metadata', {}) or {})
    title = str(getattr(media, 'title', '') or '').lower()
    if sort_name == 'artist':
        return (str(metadata.get('artist', '')).lower(), title)
    if sort_name == 'album':
        return (str(metadata.get('album', '')).lower(), title)
    if sort_name == 'duration':
        return (float(getattr(media, 'duration', 0.0) or 0.0), title)
    return title


def sort_library_media(media_items: Iterable[MediaFile], *, sort_name: str) -> list[MediaFile]:
    return sorted(media_items, key=lambda media: library_sort_key(media, sort_name))


def library_row_values(
    media: MediaFile,
    *,
    media_type_text: Callable[[MediaType], str],
    duration_text: Callable[[float], str],
) -> list[str]:
    metadata = dict(getattr(media, 'metadata', {}) or {})
    media_type = getattr(media, 'media_type', MediaType.UNKNOWN)
    return [
        getattr(media, 'title', '') or '',
        str(metadata.get('artist', '') or ''),
        str(metadata.get('album', '') or ''),
        duration_text(float(getattr(media, 'duration', 0.0) or 0.0)),
        media_type_text(media_type),
        getattr(media, 'path', '') or '',
    ]


def playlist_id(playlist: dict[str, object], *, exceptions: _ExceptionTypes) -> int | None:
    try:
        return int(playlist.get('id'))  # type: ignore[arg-type]
    except exceptions:
        return None


def playlist_name(playlist: dict[str, object]) -> str:
    value = playlist.get('name')
    return str(value).strip() if value else ''


def playlist_track_row_values(media: MediaFile, *, duration_text: Callable[[float], str]) -> list[str]:
    metadata = dict(getattr(media, 'metadata', {}) or {})
    artist = str(getattr(media, 'artist', None) or metadata.get('artist', '') or '')
    path = getattr(media, 'path', '') or ''
    return [
        getattr(media, 'title', '') or Path(path).stem,
        artist,
        duration_text(float(getattr(media, 'duration', 0.0) or 0.0)),
        path,
    ]


def select_file_paths(
    wx_module: object,
    parent: object,
    *,
    message: str,
    cancelled_results: set[int],
    unavailable: Callable[[], None],
) -> list[str]:
    dialog_cls = getattr(wx_module, 'FileDialog', None)
    if dialog_cls is None:
        unavailable()
        return []
    dialog = dialog_cls(parent, message=message)
    try:
        if dialog.ShowModal() in cancelled_results:
            return []
        getter = getattr(dialog, 'GetPaths', None)
        if callable(getter):
            return [str(path) for path in getter() if path]
        single_getter = getattr(dialog, 'GetPath', None)
        if callable(single_getter):
            value = single_getter()
            return [str(value)] if value else []
        return []
    finally:
        destroy = getattr(dialog, 'Destroy', None)
        if callable(destroy):
            destroy()


def select_folder_path(
    wx_module: object,
    parent: object,
    *,
    message: str,
    cancelled_results: set[int],
    unavailable: Callable[[], None],
    allow_paths_fallback: bool,
) -> str:
    dialog_cls = getattr(wx_module, 'DirDialog', None)
    if dialog_cls is None:
        unavailable()
        return ''
    dialog = dialog_cls(parent, message=message)
    try:
        if dialog.ShowModal() in cancelled_results:
            return ''
        getter = getattr(dialog, 'GetPath', None)
        if callable(getter):
            value = getter()
            return str(value or '').strip()
        if allow_paths_fallback:
            getters = getattr(dialog, 'GetPaths', None)
            if callable(getters):
                values = [str(path).strip() for path in getters() if str(path).strip()]
                return values[0] if values else ''
        return ''
    finally:
        destroy = getattr(dialog, 'Destroy', None)
        if callable(destroy):
            destroy()


def collect_media_file_paths(folder_path: str, *, is_media_file: Callable[[str], bool]) -> list[str]:
    folder = Path(str(folder_path or '').strip())
    if not folder.is_dir():
        return []
    media_paths: list[str] = []
    for candidate in sorted(folder.rglob('*'), key=lambda item: str(item).lower()):
        if not candidate.is_file():
            continue
        resolved = str(candidate)
        if is_media_file(resolved):
            media_paths.append(resolved)
    return media_paths
