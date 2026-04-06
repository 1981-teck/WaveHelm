from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from src.audio.audio_events import AudioEventType

logger = logging.getLogger(__name__)

CACHE_EXCEPTIONS = (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError)
LOCALIZATION_LOOKUP_EXCEPTIONS = (AttributeError, LookupError, RuntimeError, TypeError, ValueError)
FORMAT_EXCEPTIONS = (AttributeError, IndexError, KeyError, TypeError, ValueError)
EVENT_BUS_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)



def _safe_publish_event(self, event_type: AudioEventType, payload: Dict[str, Any], *, log_context: str) -> bool:
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



def _initialize_controller(self):
    """Inizializza il controller caricando i dati dal database."""
    try:
        self._refresh_cache()
        logger.info('PlaylistController initialized successfully.')
    except CACHE_EXCEPTIONS as error:
        self._handle_error(error, 'error_initializing_playlist_controller')



def _refresh_cache(self):
    """Ricarica completamente la cache dal database."""
    try:
        playlists = self.database_manager.get_all_playlists()
        self._playlists_cache = {pl['id']: pl for pl in playlists}

        self._playlist_items_cache = {}
        for playlist_id in self._playlists_cache.keys():
            items = self.database_manager.get_playlist_items(playlist_id)
            sorted_items = sorted(items, key=lambda x: x.get('position', 0))
            self._playlist_items_cache[playlist_id] = [
                (item.get('position', i), item['media_path'])
                for i, item in enumerate(sorted_items)
            ]

        self._cache_valid = True
        self._sync_all_playlist_files()
        logger.debug('Cache refreshed: %s playlists loaded.', len(self._playlists_cache))

    except CACHE_EXCEPTIONS as error:
        self._cache_valid = False
        logger.error('Failed to refresh cache: %s', error)
        raise



def _invalidate_cache(self):
    """Invalida la cache forzando il reload al prossimo accesso."""
    self._cache_valid = False



def _ensure_cache_valid(self):
    """Assicura che la cache sia valida, ricaricandola se necessario."""
    if not self._cache_valid:
        self._refresh_cache()



def _get_localized_text(self, key: str, **kwargs) -> str:
    """Ottiene testo localizzato."""
    manager = getattr(self, 'localization_manager', None)
    get_text = getattr(manager, 'get_text', None)
    message: Any = key

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



def _notify_feedback(self, key: str, color: str = 'green', **kwargs):
    """Invia messaggio di feedback."""
    message = self._get_localized_text(key, **kwargs)
    self._safe_publish_event(
        AudioEventType.FEEDBACK_MESSAGE,
        {'message': message, 'color': color},
        log_context=f"feedback '{key}'",
    )



def _handle_error(self, error: Exception, context_key: str, **kwargs):
    """Gestisce errori con logging e feedback."""
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
    """Valida il nome della playlist."""
    if not name or not name.strip():
        return False
    if len(name.strip()) > 100:
        return False
    return True



def _playlist_name_exists(self, name: str, exclude_id: Optional[int] = None) -> bool:
    """Verifica se esiste già una playlist con il nome dato (case-insensitive)."""
    name_lower = name.lower()
    for pid, playlist in self._playlists_cache.items():
        if exclude_id and pid == exclude_id:
            continue
        if playlist.get('name', '').lower() == name_lower:
            return True
    return False



def _notify_playlist_updated(self, playlist_id: int):
    """Notifica che una playlist è stata aggiornata."""
    playlist_data: Dict[str, Any] = {'id': playlist_id}
    if playlist_id in self._playlists_cache:
        playlist_data['name'] = self._playlists_cache[playlist_id]['name']

    self._safe_publish_event(
        AudioEventType.PLAYLIST_UPDATED,
        playlist_data,
        log_context=f'playlist update {playlist_id}',
    )



def close(self):
    """Chiude il controller e libera le risorse."""
    self._playlists_cache.clear()
    self._playlist_items_cache.clear()
    self._current_playlist_id = None
    self._currently_playing = None
    self._cache_valid = False
    logger.info('PlaylistController closed.')



_PLAYLIST_CONTROLLER_SUPPORT_METHODS: tuple[tuple[str, Callable[..., Any]], ...] = (
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
