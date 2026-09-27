from __future__ import annotations

import json
import logging
from datetime import datetime
from collections.abc import Mapping
from typing import List

from src.model.component_database.db_core import DbCore
from src.utils.exceptions import DatabaseError, IntegrityError, NotFoundError

logger = logging.getLogger(__name__)


class EqPresetManager:
    def __init__(self, db_core: DbCore):
        self.db_core = db_core

    def add_custom_eq_preset(
        self, name: str, settings: Mapping[str, object]
    ) -> None:
        """Insert exactly one custom EQ preset.

        Edge cases:
            1. A preset with the same unique name already exists.
            2. Settings serialization or the database write fails.
            3. The insert reports an unexpected zero or multi-row result.
        """
        query = (
            "INSERT INTO custom_eq_presets (name, settings, created_at) "
            "VALUES (?, ?, ?)"
        )
        timestamp = datetime.now().isoformat()
        try:
            result = self.db_core._execute_write(
                query, (name, json.dumps(settings), timestamp)
            )
        except IntegrityError as error:
            raise IntegrityError(
                "EQ preset insert violates a database integrity constraint.",
                details=str(error),
            ) from error
        except DatabaseError as error:
            raise DatabaseError(
                f"Error adding EQ preset: {name}", details=str(error)
            ) from error
        if result.rowcount != 1:
            raise DatabaseError(
                "EQ preset insert affected an unexpected number of rows.",
                details=f"expected=1; actual={result.rowcount}; name={name}",
            )
        logger.info("EQ preset added: %s", name)

    def get_custom_eq_presets(self) -> List[dict[str, object]]:
        query = (
            "SELECT id, name, settings, created_at "
            "FROM custom_eq_presets ORDER BY name"
        )
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
        self, preset_id: int, name: str, settings: Mapping[str, object]
    ) -> None:
        """Update exactly one custom EQ preset by primary key.

        Edge cases:
            1. The requested preset ID does not exist.
            2. The replacement name conflicts with another preset.
            3. Schema drift permits an impossible multi-row update.
        """
        query = (
            "UPDATE custom_eq_presets "
            "SET name = ?, settings = ?, created_at = ? WHERE id = ?"
        )
        timestamp = datetime.now().isoformat()
        try:
            result = self.db_core._execute_write(
                query, (name, json.dumps(settings), timestamp, preset_id)
            )
        except IntegrityError as error:
            raise IntegrityError(
                "EQ preset update violates a database integrity constraint.",
                details=str(error),
            ) from error
        except DatabaseError as error:
            raise DatabaseError(
                f"Error updating EQ preset: {name}", details=str(error)
            ) from error
        if result.rowcount == 0:
            raise NotFoundError(f"EQ preset not found: {preset_id}")
        if result.rowcount != 1:
            raise DatabaseError(
                "EQ preset update affected an unexpected number of rows.",
                details=f"expected=1; actual={result.rowcount}; id={preset_id}",
            )
        logger.info("EQ preset updated: %s (%s)", name, preset_id)

    def delete_custom_eq_preset(self, preset_id: int) -> None:
        """Delete exactly one custom EQ preset by primary key.

        Edge cases:
            1. The requested preset ID does not exist.
            2. A concurrent delete removes the row before this statement runs.
            3. Schema drift permits an impossible multi-row delete.
        """
        query = "DELETE FROM custom_eq_presets WHERE id = ?"
        try:
            result = self.db_core._execute_write(query, (preset_id,))
        except IntegrityError as error:
            raise IntegrityError(
                "EQ preset delete violates a database integrity constraint.",
                details=str(error),
            ) from error
        except DatabaseError as error:
            raise DatabaseError(
                f"Error deleting EQ preset: {preset_id}", details=str(error)
            ) from error
        if result.rowcount == 0:
            raise NotFoundError(f"EQ preset not found: {preset_id}")
        if result.rowcount != 1:
            raise DatabaseError(
                "EQ preset delete affected an unexpected number of rows.",
                details=f"expected=1; actual={result.rowcount}; id={preset_id}",
            )
        logger.info("EQ preset deleted: %s", preset_id)
