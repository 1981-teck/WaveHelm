from __future__ import annotations

import logging
import sqlite3
from typing import Optional

from src.model.component_database.db_core import DbCore
from src.utils.exceptions import DatabaseError as AppDatabaseError

logger = logging.getLogger(__name__)

SETTINGS_MANAGER_GET_EXCEPTIONS = (AppDatabaseError, KeyError, TypeError, ValueError, sqlite3.Error)
SETTINGS_MANAGER_SET_EXCEPTIONS = (AppDatabaseError, TypeError, ValueError, sqlite3.Error)


class SettingsManager:
    def __init__(self, db_core: DbCore):
        self.db_core = db_core

    def get_app_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        query = """SELECT value FROM app_settings WHERE key = ?"""
        try:
            row = self.db_core._execute_query(query, (key,), fetch_one=True)
            return row["value"] if row else default
        except SETTINGS_MANAGER_GET_EXCEPTIONS as e:
            logger.error(f"Error getting setting '{key}': {str(e)}")
            return default

    def set_app_setting(self, key: str, value: str):
        query = """
        INSERT INTO app_settings (key, value) 
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """
        try:
            self.db_core._execute_query(query, (key, value))
        except SETTINGS_MANAGER_SET_EXCEPTIONS as e:
            logger.error(f"Error setting '{key}': {str(e)}")
