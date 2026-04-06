from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from src.audio.audio_events import AudioEventBus
    from src.controller.library_controller import LibraryController
    from src.model.database_manager import DatabaseManager
    from src.model.localization_manager import LocalizationManager

from src.utils.helpers import get_app_data_path
from src.controller.playlist_controller_playlists import (
    attach_playlist_controller_playlists_behavior as _attach_playlist_controller_playlists_behavior,
)
from src.controller.playlist_controller_storage import (
    attach_playlist_controller_storage_behavior as _attach_playlist_controller_storage_behavior,
)
from src.controller.playlist_controller_support import (
    attach_playlist_controller_support_behavior as _attach_playlist_controller_support_behavior,
)
from src.controller.playlist_controller_tracks import (
    attach_playlist_controller_tracks_behavior as _attach_playlist_controller_tracks_behavior,
)

_PLAYLIST_CONTROLLER_ATTACHERS: tuple[tuple[str, Any], ...] = (
    ('support', _attach_playlist_controller_support_behavior),
    ('storage', _attach_playlist_controller_storage_behavior),
    ('playlists', _attach_playlist_controller_playlists_behavior),
    ('tracks', _attach_playlist_controller_tracks_behavior),
)

logger = logging.getLogger(__name__)


class PlaylistController:
    """Controller per la gestione delle playlist con logica robusta e cache locale."""

    def __init__(
        self,
        database_manager: DatabaseManager,
        localization_manager: LocalizationManager,
        event_bus: AudioEventBus,
        library_controller: LibraryController,
    ):
        self.database_manager = database_manager
        self.localization_manager = localization_manager
        self.event_bus = event_bus
        self.library_controller = library_controller

        self._playlists_cache: Dict[int, Dict[str, Any]] = {}
        self._playlist_items_cache: Dict[int, List[Tuple[int, str]]] = {}
        self._current_playlist_id: Optional[int] = None
        self._currently_playing: Optional[int] = None
        self._playlists_storage_dir: Path = get_app_data_path("playlists")
        self._cache_valid = False

        self._initialize_controller()


def attach_playlist_controller_behavior(cls) -> None:
    """Attach playlist behavior from the central coordinator module.

    Edge cases:
        1. The target is not a class and a split installer mutates an unexpected object.
        2. A split installer is missing or non-callable and leaves the controller partially wired.
        3. Re-import or reload repeats the wiring path and needlessly rebinds controller methods.
    """
    if not isinstance(cls, type):
        raise TypeError('Playlist controller behavior can only be attached to classes')
    if getattr(cls, '_playlist_controller_behavior_attached', False):
        return

    for group_name, attacher in _PLAYLIST_CONTROLLER_ATTACHERS:
        if not callable(attacher):
            raise TypeError(
                f'Invalid playlist controller attacher: {group_name}'
            )
        attacher(cls)

    setattr(cls, '_playlist_controller_behavior_attached', True)


attach_playlist_controller_behavior(PlaylistController)
