from __future__ import annotations
import json
import logging
from typing import List, Dict, Any
from datetime import datetime

from src.model.component_database.db_core import DbCore
from src.utils.exceptions import IntegrityError, NotFoundError, DatabaseError

logger = logging.getLogger(__name__)


class EqPresetManager:
    def __init__(self, db_core: DbCore):
        self.db_core = db_core

    def add_custom_eq_preset(self, name: str, settings: Dict[str, Any]):
        query = """INSERT INTO custom_eq_presets (name, settings, created_at) VALUES (?, ?, ?)"""
        timestamp = datetime.now().isoformat()
        try:
            self.db_core._execute_query(query, (name, json.dumps(settings), timestamp))
            logger.info(f"EQ preset added: {name}")
        except IntegrityError:
            raise IntegrityError(f"EQ preset already exists: {name}")
        except DatabaseError as e:
            raise DatabaseError(f"Error adding EQ preset: {name}, error: {e}")

    def get_custom_eq_presets(self) -> List[Dict[str, Any]]:
        query = """SELECT id, name, settings, created_at FROM custom_eq_presets ORDER BY name"""
        rows = self.db_core._execute_query(query, fetch_all=True)
        presets = []
        if rows:
            for row in rows:
                preset = dict(row)
                try:
                    preset["settings"] = json.loads(preset["settings"])
                except json.JSONDecodeError:
                    logger.error("Invalid JSON in EQ preset data")
                    preset["settings"] = {}
                presets.append(preset)
        return presets

    def update_custom_eq_preset(
        self, preset_id: int, name: str, settings: Dict[str, Any]
    ):
        query = """UPDATE custom_eq_presets SET name = ?, settings = ?, created_at = ? WHERE id = ?"""
        timestamp = datetime.now().isoformat()
        try:
            self.db_core._execute_query(
                query, (name, json.dumps(settings), timestamp, preset_id)
            )
            if self.db_core._last_changes() > 0:
                logger.info(f"EQ preset updated: {name} ({preset_id})")
            else:
                raise NotFoundError(f"EQ preset not found: {preset_id}")
        except IntegrityError:
            raise IntegrityError(f"EQ preset name exists: {name}")
        except DatabaseError as e:
            raise DatabaseError(f"Error updating EQ preset: {name}, error: {e}")

    def delete_custom_eq_preset(self, preset_id: int):
        query = """DELETE FROM custom_eq_presets WHERE id = ?"""
        try:
            self.db_core._execute_query(query, (preset_id,))
            if self.db_core._last_changes() > 0:
                logger.info(f"EQ preset deleted: {preset_id}")
            else:
                raise NotFoundError(f"EQ preset not found: {preset_id}")
        except DatabaseError as e:
            raise DatabaseError(f"Error deleting EQ preset: {preset_id}, error: {e}")
