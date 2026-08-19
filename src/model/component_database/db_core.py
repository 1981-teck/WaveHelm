from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import sqlite3
import logging
import threading
from typing import List, Optional, Tuple, Union, Any, Dict

from src.model.localization_manager import LocalizationManager
from src.utils.exceptions import DatabaseError
from src.utils.helpers import get_user_data_dir

logger = logging.getLogger(__name__)

DB_CONFIG_EXCEPTIONS = (sqlite3.Error,)
DB_QUERY_ARGUMENT_EXCEPTIONS = (TypeError, ValueError)
DB_LAST_CHANGES_EXCEPTIONS = (AttributeError, IndexError, KeyError, sqlite3.Error, TypeError, ValueError)


class DbCore:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(
        self,
        db_path: Optional[str] = None,
        localization_manager: Optional[LocalizationManager] = None,
    ):
        if hasattr(self, "_initialized") and self._initialized:
            return

        self.localization_manager = (
            localization_manager if localization_manager else LocalizationManager()
        )
        self._db_lock = threading.RLock()
        app_data_dir = get_user_data_dir()
        if db_path:
            self.db_path = str(app_data_dir / db_path)
        else:
            self.db_path = str(app_data_dir / "wavehelm.db")

        logger.info(f"DatabaseManager initializing with db_path: {self.db_path}")
        self.conn: Optional[sqlite3.Connection] = None
        self.initialize_database()
        self._initialized = True

    def _get_localized_text(self, key: str, **kwargs) -> str:
        return self.localization_manager.get_text(key, **kwargs)

    def connect(self):
        with self._db_lock:
            if self.conn:
                logger.debug("Database is already connected.")
                return

            try:
                self.conn = self._open_connection(synchronous="NORMAL")
                logger.info(f"Connected to database: {self.db_path}")
            except sqlite3.Error as e:
                error_msg = f"Database connection error: {e}"
                logger.critical(f"[DbCore] {error_msg}", exc_info=True)
                raise DatabaseError(error_msg) from e

    @staticmethod
    def _apply_connection_pragmas(
        connection: sqlite3.Connection, *, synchronous: str
    ) -> None:
        if synchronous not in {"NORMAL", "FULL"}:
            raise ValueError(f"Unsupported SQLite synchronous mode: {synchronous}")
        cur = connection.cursor()
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute(f"PRAGMA synchronous={synchronous};")
        cur.execute("PRAGMA busy_timeout=5000;")
        cur.execute("PRAGMA foreign_keys=ON;")
        cur.execute("PRAGMA temp_store=MEMORY;")
        try:
            cur.execute("PRAGMA mmap_size=268435456;")
        except DB_CONFIG_EXCEPTIONS as error:
            logger.debug("SQLite mmap_size PRAGMA skipped: %s", error, exc_info=True)

    def _open_connection(self, *, synchronous: str) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.db_path, check_same_thread=False, timeout=5.0
        )
        try:
            connection.row_factory = sqlite3.Row
            self._apply_connection_pragmas(connection, synchronous=synchronous)
            return connection
        except (sqlite3.Error, TypeError, ValueError):
            connection.close()
            raise

    def _configure_connection(self) -> None:
        if not self.conn:
            return
        try:
            self._apply_connection_pragmas(self.conn, synchronous="NORMAL")
        except DB_CONFIG_EXCEPTIONS as error:
            logger.warning("SQLite PRAGMA configuration skipped: %s", error)

    @contextmanager
    def durable_write_connection(self) -> Iterator[sqlite3.Connection]:
        """Yield an isolated WAL connection with FULL commit durability.

        The shared application connection remains on NORMAL so bulk/non-critical
        writes do not pay an fsync on every commit. Playlist canonical mutations
        use this isolated connection because the matching mirror intent must
        survive an OS crash or power loss once commit returns.
        """
        with self._db_lock:
            try:
                connection = self._open_connection(synchronous="FULL")
            except sqlite3.Error as error:
                raise DatabaseError(
                    f"Unable to open durable SQLite connection: {error}"
                ) from error
            try:
                journal_mode = str(
                    connection.execute("PRAGMA journal_mode;").fetchone()[0]
                ).lower()
                synchronous = int(
                    connection.execute("PRAGMA synchronous;").fetchone()[0]
                )
                if journal_mode != "wal" or synchronous != 2:
                    raise DatabaseError(
                        "Durable SQLite connection could not enable WAL/FULL mode."
                    )
                yield connection
            finally:
                if connection.in_transaction:
                    try:
                        connection.rollback()
                    except sqlite3.Error as error:
                        logger.error(
                            "Durable SQLite rollback failed: %s", error, exc_info=True
                        )
                try:
                    connection.close()
                except sqlite3.Error as error:
                    logger.error(
                        "Durable SQLite connection close failed: %s",
                        error,
                        exc_info=True,
                    )

    def close(self):
        with self._db_lock:
            if self.conn:
                try:
                    self.conn.close()
                    self.conn = None
                    logger.info("Database connection closed.")
                except sqlite3.Error as e:
                    error_msg = f"Error closing database connection: {e}"
                    logger.error(f"[DbCore] {error_msg}", exc_info=True)
                    raise DatabaseError(error_msg) from e
            else:
                logger.debug("Database not connected, close call ignored.")

    def _execute_query(
        self,
        query: str,
        params: Optional[Tuple] = None,
        fetch_one: bool = False,
        fetch_all: bool = False,
    ) -> Union[sqlite3.Row, List[sqlite3.Row], None]:
        if not self.conn:
            self.connect()

        with self._db_lock:
            if not self.conn:
                raise DatabaseError("Database not connected.")

            try:
                cursor = self.conn.cursor()
                if params is not None:
                    cursor.execute(query, params)
                else:
                    cursor.execute(query)
                self.conn.commit()

                if fetch_one:
                    return cursor.fetchone()
                elif fetch_all:
                    return cursor.fetchall()
                return None
            except sqlite3.IntegrityError as e:
                self.conn.rollback()
                raise e
            except sqlite3.Error as e:
                self.conn.rollback()
                raise e
            except DB_QUERY_ARGUMENT_EXCEPTIONS as e:
                self.conn.rollback()
                raise e

    def _last_changes(self) -> int:
        if not self.conn:
            return 0
        try:
            return int(self.conn.execute("SELECT changes()").fetchone()[0])
        except DB_LAST_CHANGES_EXCEPTIONS:
            return int(getattr(self.conn, "total_changes", 0))

    def initialize_database(self):
        queries = [
            """CREATE TABLE IF NOT EXISTS library_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                media_type TEXT NOT NULL,
                duration REAL NOT NULL,
                metadata TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE IF NOT EXISTS playlists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                cover_art TEXT,
                creation_date TEXT NOT NULL,
                last_modified TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS playlist_items (
                playlist_id INTEGER NOT NULL,
                media_path TEXT NOT NULL,
                position INTEGER NOT NULL,
                added_at TEXT NOT NULL,
                PRIMARY KEY (playlist_id, media_path),
                FOREIGN KEY (playlist_id) REFERENCES playlists (id) ON DELETE CASCADE
            )""",
            """CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                path TEXT NOT NULL,
                media_type TEXT NOT NULL,
                duration REAL NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                metadata TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS favorites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                media_type TEXT NOT NULL,
                duration REAL NOT NULL,
                metadata TEXT,
                added_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE IF NOT EXISTS custom_eq_presets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                settings TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE IF NOT EXISTS custom_ambient_presets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                settings TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE IF NOT EXISTS user_profile (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_name TEXT NOT NULL,
                avatar_path TEXT,
                stats_data TEXT,
                eq_settings_data TEXT,
                effects_settings_data TEXT,
                ambient_settings_data TEXT,
                created_at TEXT NOT NULL,
                last_updated TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )""",
        ]
        if not self.conn:
            self.connect()
        for query in queries:
            self._execute_query(query)
        logger.info("Database tables initialized.")

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
