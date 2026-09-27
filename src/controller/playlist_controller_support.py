from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence

from src.audio.audio_events import AudioEventType
from src.model.component_database.playlist_identity import (
    MAX_PLAYLISTS_PER_DATABASE,
    canonicalize_playlist_name,
)
from src.model.component_database.playlist_position import (
    MAX_PLAYLIST_ITEMS_PER_PLAYLIST,
    MAX_PLAYLIST_MEDIA_PATH_CHARS,
)
from src.utils.exceptions import DatabaseError

logger = logging.getLogger(__name__)

CACHE_EXCEPTIONS = (
    AttributeError, DatabaseError, KeyError, OSError, RuntimeError, TypeError, ValueError
)
LOCALIZATION_LOOKUP_EXCEPTIONS = (AttributeError, LookupError, RuntimeError, TypeError, ValueError)
FORMAT_EXCEPTIONS = (AttributeError, IndexError, KeyError, TypeError, ValueError)
EVENT_BUS_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)


class PlaylistCacheInvariantError(RuntimeError):
    """Raised when database rows cannot form an exact controller cache."""


def _playlist_cache_rows(rows: object) -> dict[int, dict[str, object]]:
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
        raise PlaylistCacheInvariantError('Playlist cache inventory must be a sequence')
    if len(rows) > MAX_PLAYLISTS_PER_DATABASE:
        raise PlaylistCacheInvariantError('Playlist cache inventory exceeds its hard cap')
    cache: dict[int, dict[str, object]] = {}
    identities: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise PlaylistCacheInvariantError('Playlist cache row must be a mapping')
        playlist_id = row.get('id')
        name = row.get('name')
        if isinstance(playlist_id, bool) or not isinstance(playlist_id, int):
            raise PlaylistCacheInvariantError('Playlist cache ID must be an integer')
        if playlist_id < 1 or playlist_id in cache:
            raise PlaylistCacheInvariantError('Playlist cache ID is invalid or duplicated')
        try:
            canonical_name = canonicalize_playlist_name(name)
        except (TypeError, ValueError) as error:
            raise PlaylistCacheInvariantError('Playlist cache name is invalid') from error
        if canonical_name in identities:
            raise PlaylistCacheInvariantError('Playlist cache identity is duplicated')
        identities.add(canonical_name)
        cache[playlist_id] = dict(row)
    return cache


def _playlist_item_cache_rows(
    rows: object, playlist_id: int
) -> list[tuple[int, str]]:
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
        raise PlaylistCacheInvariantError('Playlist item inventory must be a sequence')
    if len(rows) > MAX_PLAYLIST_ITEMS_PER_PLAYLIST:
        raise PlaylistCacheInvariantError('Playlist item inventory exceeds its hard cap')
    cache: list[tuple[int, str]] = []
    paths: set[str] = set()
    for expected_position, row in enumerate(rows, start=1):
        if not isinstance(row, Mapping):
            raise PlaylistCacheInvariantError('Playlist item row must be a mapping')
        position = row.get('position')
        media_path = row.get('media_path')
        row_playlist_id = row.get('playlist_id', playlist_id)
        valid_row_id = (
            not isinstance(row_playlist_id, bool)
            and isinstance(row_playlist_id, int)
            and row_playlist_id == playlist_id
        )
        if not valid_row_id:
            raise PlaylistCacheInvariantError('Playlist item belongs to another playlist')
        if isinstance(position, bool) or not isinstance(position, int):
            raise PlaylistCacheInvariantError('Playlist item position must be an integer')
        if position != expected_position:
            raise PlaylistCacheInvariantError('Playlist item positions are not contiguous 1..N')
        valid_path = (
            isinstance(media_path, str)
            and media_path == media_path.strip()
            and 0 < len(media_path) <= MAX_PLAYLIST_MEDIA_PATH_CHARS
            and '\x00' not in media_path
        )
        if not valid_path or media_path in paths:
            raise PlaylistCacheInvariantError('Playlist item path is invalid or duplicated')
        paths.add(media_path)
        cache.append((position, media_path))
    return cache


def _safe_publish_event(
    self, event_type: AudioEventType, payload: dict[str, object], *, log_context: str
) -> bool:
    publish = getattr(self.event_bus, 'publish', None)
    if not callable(publish):
        logger.error('[PlaylistController] Event bus publish unavailable for %s', log_context)
        return False
    try:
        publish(event_type, payload)
        return True
    except EVENT_BUS_EXCEPTIONS:
        logger.error('[PlaylistController] Failed publishing %s', log_context, exc_info=True)
        return False



def _initialize_controller(self) -> None:
    """Initialize the controller by loading a validated database snapshot."""
    try:
        self._refresh_cache()
        logger.info('PlaylistController initialized successfully.')
    except CACHE_EXCEPTIONS as error:
        self._handle_error(error, 'error_initializing_playlist_controller')



def _refresh_cache(self) -> None:
    """Load one exact cache snapshot without sorting or repairing database rows.

    Edge cases:
        1. Duplicate playlist IDs cannot silently overwrite a previous cache entry.
        2. Missing, sparse, reordered, or non-integer positions fail closed.
        3. Partial validation never replaces the previously committed cache snapshot.
    """
    try:
        playlist_cache = _playlist_cache_rows(
            self.database_manager.get_all_playlists()
        )
        item_cache: dict[int, list[tuple[int, str]]] = {}
        for playlist_id in playlist_cache:
            rows = self.database_manager.get_playlist_items(playlist_id)
            item_cache[playlist_id] = _playlist_item_cache_rows(rows, playlist_id)
        self._playlists_cache = playlist_cache
        self._playlist_items_cache = item_cache
        self._cache_valid = True
        self._sync_all_playlist_files()
        logger.debug('Cache refreshed: %s playlists loaded.', len(playlist_cache))
    except CACHE_EXCEPTIONS as error:
        self._cache_valid = False
        logger.error('Failed to refresh cache: %s', error)
        raise



def _invalidate_cache(self) -> None:
    """Invalidate the cache so the next access performs a full reload."""
    self._cache_valid = False



def _ensure_cache_valid(self) -> None:
    """Ensure that the controller cache has a validated snapshot."""
    if not self._cache_valid:
        self._refresh_cache()



def _get_localized_text(self, key: str, **kwargs: object) -> str:
    """Return localized text with deterministic fallback behavior."""
    manager = getattr(self, 'localization_manager', None)
    get_text = getattr(manager, 'get_text', None)
    message: object = key

    if callable(get_text):
        try:
            message = get_text(key, default=key)
        except TypeError:
            try:
                message = get_text(key)
            except LOCALIZATION_LOOKUP_EXCEPTIONS:
                logger.debug(
                    "[PlaylistController] Localization lookup failed for key '%s'",
                    key,
                    exc_info=True,
                )
                message = key
        except LOCALIZATION_LOOKUP_EXCEPTIONS:
            logger.debug(
                "[PlaylistController] Localization lookup failed for key '%s'",
                key,
                exc_info=True,
            )
            message = key

    if kwargs:
        try:
            return str(message).format(**kwargs)
        except FORMAT_EXCEPTIONS:
            logger.debug(
                "[PlaylistController] Localization formatting failed for key '%s' with kwargs=%s",
                key,
                kwargs,
                exc_info=True,
            )
    return str(message)



def _notify_feedback(
    self, key: str, color: str = 'green', **kwargs: object
) -> None:
    """Publish a localized feedback message."""
    message = self._get_localized_text(key, **kwargs)
    self._safe_publish_event(
        AudioEventType.FEEDBACK_MESSAGE,
        {'message': message, 'color': color},
        log_context=f"feedback '{key}'",
    )



def _handle_error(
    self, error: Exception, context_key: str, **kwargs: object
) -> None:
    """Report an error through logging and the event boundary."""
    payload = dict(kwargs)
    payload.setdefault('error', str(error))
    try:
        message = self._get_localized_text(context_key, **payload)
    except (LOCALIZATION_LOOKUP_EXCEPTIONS + FORMAT_EXCEPTIONS):
        message = context_key

    logger.error('[PlaylistController] %s: %s', message, error, exc_info=True)
    self._safe_publish_event(
        AudioEventType.ERROR,
        {'message': f'{message}: {error}'},
        log_context=f"error event for '{context_key}'",
    )



def _validate_playlist_name(self, name: str) -> bool:
    """Validate playlist names with the database identity policy."""
    try:
        canonicalize_playlist_name(name)
    except (TypeError, ValueError):
        return False
    return True



def _playlist_name_exists(self, name: str, exclude_id: int | None = None) -> bool:
    """Return whether the cache contains the same canonical playlist identity."""
    try:
        canonical_name = canonicalize_playlist_name(name)
    except (TypeError, ValueError):
        return False
    for playlist_id, playlist in self._playlists_cache.items():
        if exclude_id is not None and playlist_id == exclude_id:
            continue
        stored_name = playlist.get('name', '')
        try:
            if canonicalize_playlist_name(stored_name) == canonical_name:
                return True
        except (TypeError, ValueError):
            self._cache_valid = False
            return True
    return False



def _notify_playlist_updated(self, playlist_id: int) -> None:
    """Publish a playlist-updated event from the committed cache state."""
    playlist_data: dict[str, object] = {'id': playlist_id}
    if playlist_id in self._playlists_cache:
        playlist_data['name'] = self._playlists_cache[playlist_id]['name']

    self._safe_publish_event(
        AudioEventType.PLAYLIST_UPDATED,
        playlist_data,
        log_context=f'playlist update {playlist_id}',
    )



def close(self) -> None:
    """Clear controller state and mark the cache invalid."""
    self._playlists_cache.clear()
    self._playlist_items_cache.clear()
    self._current_playlist_id = None
    self._currently_playing = None
    self._cache_valid = False
    logger.info('PlaylistController closed.')



_PLAYLIST_CONTROLLER_SUPPORT_METHODS: tuple[tuple[str, Callable[..., object]], ...] = (
    ("_safe_publish_event", _safe_publish_event),
    ("_initialize_controller", _initialize_controller),
    ("_refresh_cache", _refresh_cache),
    ("_invalidate_cache", _invalidate_cache),
    ("_ensure_cache_valid", _ensure_cache_valid),
    ("_get_localized_text", _get_localized_text),
    ("_notify_feedback", _notify_feedback),
    ("_handle_error", _handle_error),
    ("_validate_playlist_name", _validate_playlist_name),
    ("_playlist_name_exists", _playlist_name_exists),
    ("_notify_playlist_updated", _notify_playlist_updated),
    ("close", close),
)


def install_playlist_controller_support_behavior(cls) -> None:
    """Install support bindings for the playlist controller leaf module.

    Edge cases:
        1. A binding name is empty and would mutate an unexpected class attribute.
        2. A split module exports a non-callable method binding and breaks runtime wiring.
        3. Duplicate binding names silently shadow a previously attached controller method.
    """
    if not isinstance(cls, type):
        raise TypeError('Playlist controller support behavior can only be attached to classes')
    if getattr(cls, '_playlist_controller_support_behavior_attached', False):
        return

    seen_names: set[str] = set()
    for attribute_name, method in _PLAYLIST_CONTROLLER_SUPPORT_METHODS:
        if not attribute_name:
            raise TypeError('Playlist controller support binding name cannot be empty')
        if attribute_name in seen_names:
            raise TypeError(f'Duplicate playlist controller support binding: {attribute_name}')
        if not callable(method):
            raise TypeError(f'Invalid playlist controller support binding: {attribute_name}')
        setattr(cls, attribute_name, method)
        seen_names.add(attribute_name)

    setattr(cls, '_playlist_controller_support_behavior_attached', True)


def attach_playlist_controller_support_behavior(cls) -> None:
    """Compatibility shim delegating to the neutral playlist support installer."""
    install_playlist_controller_support_behavior(cls)
