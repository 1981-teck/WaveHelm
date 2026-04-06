from __future__ import annotations
import logging
from typing import List, Dict, Any, Optional

from src.model.localization_manager import LocalizationManager
from src.model.component_database import (
    DbCore,
    LibraryManager,
    PlaylistManager,
    HistoryManager,
    FavoritesManager,
    EqPresetManager,
    SettingsManager,
)

logger = logging.getLogger(__name__)


class DatabaseManager:
    """
    Facade per la persistenza (SQLite) e manager specializzati runtime.
    """

    def __init__(self, localization_manager: LocalizationManager):
        self.localization_manager = localization_manager

        self.db_core = DbCore(localization_manager=self.localization_manager)
        self.library = LibraryManager(db_core=self.db_core)
        self.playlists = PlaylistManager(db_core=self.db_core)
        self.history = HistoryManager(db_core=self.db_core)
        self.favorites = FavoritesManager(db_core=self.db_core)
        self.eq_presets = EqPresetManager(db_core=self.db_core)
        self.settings = SettingsManager(db_core=self.db_core)

    # --- Library Methods ---
    def add_library_item(
        self,
        path: str,
        title: str,
        media_type: str,
        duration: float,
        metadata: Optional[Dict[str, Any]] = None,
        additional_data: Optional[Dict[str, Any]] = None,
    ):
        return self.library.add_library_item(
            path,
            title,
            media_type,
            duration,
            metadata,
            additional_data,
        )

    def remove_library_item(self, path: str):
        return self.library.remove_library_item(path)

    def get_all_library_items(self) -> List[Dict[str, Any]]:
        return self.library.get_all_library_items()

    def get_library_item(self, path: str) -> Optional[Dict[str, Any]]:
        return self.library.get_library_item(path)

    def update_library_item(self, path: str, updates: Dict[str, Any]):
        return self.library.update_library_item(path, updates)

    # --- Playlist Methods ---
    def create_playlist(
        self,
        name: str,
        description: Optional[str] = None,
        cover_art: Optional[str] = None,
    ):
        return self.playlists.create_playlist(name, description, cover_art)

    def delete_playlist(self, playlist_id: int):
        return self.playlists.delete_playlist(playlist_id)

    def update_playlist_name(self, playlist_id: int, new_name: str):
        return self.playlists.rename_playlist(playlist_id, new_name)

    def rename_playlist(self, playlist_id: int, new_name: str):
        return self.update_playlist_name(playlist_id, new_name)

    def get_all_playlists(self) -> List[Dict[str, Any]]:
        return self.playlists.get_all_playlists()

    def add_playlist_item(
        self, playlist_id: int, media_path: str, position: Optional[int] = None
    ):
        return self.playlists.add_playlist_item(playlist_id, media_path, position)

    def add_item_to_playlist(self, playlist_id: int, path: str):
        return self.add_playlist_item(playlist_id, path)

    def remove_playlist_item(self, playlist_id: int, media_path: str):
        return self.playlists.remove_playlist_item(playlist_id, media_path)

    def remove_item_from_playlist(self, playlist_id: int, path: str):
        return self.remove_playlist_item(playlist_id, path)

    def get_playlist_items(self, playlist_id: int) -> List[Dict[str, Any]]:
        return self.playlists.get_playlist_items(playlist_id)

    # --- History Methods ---
    def add_history_entry(
        self,
        path: str,
        title: str,
        media_type: str,
        duration: float,
        metadata: Optional[Dict[str, Any]] = None,
        additional_data: Optional[Dict[str, Any]] = None,
    ):
        return self.history.add_history_entry(
            path,
            title,
            media_type,
            duration,
            metadata,
            additional_data,
        )

    def get_history(self, limit: int = 200) -> List[Dict[str, Any]]:
        return self.history.get_history(limit=limit)

    def clear_history(self):
        return self.history.clear_history()

    # --- Favorites Methods ---
    def add_favorite(
        self,
        path: str,
        title: str,
        media_type: str,
        duration: float,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        return self.favorites.add_favorite(path, title, media_type, duration, metadata)

    def remove_favorite(self, path: str):
        return self.favorites.remove_favorite(path)

    def get_favorites(self) -> List[Dict[str, Any]]:
        return self.favorites.get_favorites()

    def get_all_favorite_items(self) -> List[Dict[str, Any]]:
        """Compat: usato da FavoritesView.

        Ritorna la lista dei preferiti come lista di dict.
        Alias di get_favorites().
        """
        return self.get_favorites()

    def is_favorite(self, path: str) -> bool:
        return self.favorites.is_favorite(path)

    # --- EQ Preset Methods ---
    def add_custom_eq_preset(self, name: str, settings: Dict[str, Any]):
        return self.eq_presets.add_custom_eq_preset(name, settings)

    def remove_custom_eq_preset(self, preset_id: int):
        return self.eq_presets.delete_custom_eq_preset(preset_id)

    def delete_custom_eq_preset(self, preset_id_or_name):
        """Compat: supporta cancellazione per id o per nome."""
        if isinstance(preset_id_or_name, str):
            for preset in self.get_custom_eq_presets():
                if str(preset.get("name")) == preset_id_or_name:
                    return self.eq_presets.delete_custom_eq_preset(int(preset["id"]))
            return None
        return self.eq_presets.delete_custom_eq_preset(int(preset_id_or_name))

    def get_custom_eq_presets(self) -> List[Dict[str, Any]]:
        return self.eq_presets.get_custom_eq_presets()

    # --- App Settings Methods (DB-backed) ---
    def get_app_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        return self.settings.get_app_setting(key, default)

    def set_app_setting(self, key: str, value: str):
        return self.settings.set_app_setting(key, value)
