from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import logging
from pathlib import Path
from types import TracebackType
import sqlite3
import sys
import threading

from src.model.component_database.core_schema import (
    CORE_SCHEMA_VERSION,
    ensure_core_schema,
)
from src.model.component_database.db_primitives import (
    DbWriteResult,
    SerializedConnectionGate,
    SharedConnectionBoundary,
    SharedTransactionBoundary,
    TransactionMode,
    cleanup_durable_connection,
    resolve_database_path,
)
from src.model.localization_manager import LocalizationManager
from src.utils.exceptions import DatabaseError, IntegrityError
from src.utils.helpers import get_user_data_dir


logger = logging.getLogger(__name__)

DB_CONFIG_EXCEPTIONS = (sqlite3.Error,)
DB_QUERY_ARGUMENT_EXCEPTIONS = (TypeError, ValueError)


class DbCore:
    """Own the process-wide WaveHelm SQLite connection and typed primitives.

    Edge cases:
        1. Reinitialization with a different path or localization boundary fails.
        2. A failed first initialization leaves the singleton reusable for retry.
        3. Shared-connection operations are serialized without mutex-held I/O.
    """

    _instance: DbCore | None = None
    _lock = threading.Lock()

    def __new__(cls, *args: object, **kwargs: object) -> DbCore:
        del args, kwargs
        with cls._lock:
            if cls._instance is None:
                instance = super().__new__(cls)
                instance._initialization_gate = SerializedConnectionGate()
                instance._initialized = False
                cls._instance = instance
        instance = cls._instance
        if instance is None:
            raise RuntimeError("DbCore singleton initialization failed")
        return instance

    def __init__(
        self,
        db_path: str | None = None,
        localization_manager: LocalizationManager | None = None,
    ) -> None:
        requested_path = self._resolve_database_path(db_path)
        with self._initialization_gate:
            if self._initialized:
                self._validate_reinitialization(requested_path, localization_manager)
                return
            self._initialize(requested_path, localization_manager)

    @staticmethod
    def _resolve_database_path(db_path: str | None) -> Path:
        return resolve_database_path(Path(get_user_data_dir()), db_path)

    def _validate_reinitialization(
        self,
        requested_path: Path,
        localization_manager: LocalizationManager | None,
    ) -> None:
        if requested_path != Path(self.db_path):
            raise DatabaseError(
                "DbCore is already initialized with a different database path.",
                details=f"active={self.db_path}; requested={requested_path}",
            )
        if (
            localization_manager is not None
            and localization_manager is not self.localization_manager
        ):
            raise DatabaseError(
                "DbCore is already initialized with a different localization manager."
            )

    def _initialize(
        self,
        requested_path: Path,
        localization_manager: LocalizationManager | None,
    ) -> None:
        self.localization_manager = (
            localization_manager
            if localization_manager is not None
            else LocalizationManager()
        )
        self._db_lock = SerializedConnectionGate()
        self.db_path = str(requested_path)
        self.conn: sqlite3.Connection | None = None
        initialized = False
        try:
            logger.info("DatabaseManager initializing with db_path: %s", self.db_path)
            self.initialize_database()
            initialized = True
        finally:
            if not initialized:
                self._discard_failed_initialization()
        self._initialized = True

    def _discard_failed_initialization(self) -> None:
        connection = getattr(self, "conn", None)
        self.conn = None
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error as error:
                logger.error(
                    "Failed to close SQLite connection after initialization error: %s",
                    error,
                    exc_info=True,
                )

    def _get_localized_text(self, key: str, **kwargs: object) -> str:
        return self.localization_manager.get_text(key, **kwargs)

    def connect(self) -> None:
        with self._db_lock:
            if self.conn is not None:
                logger.debug("Database is already connected.")
                return
            try:
                self.conn = self._open_connection(synchronous="NORMAL")
            except sqlite3.Error as error:
                message = f"Database connection error: {error}"
                logger.critical("[DbCore] %s", message, exc_info=True)
                raise DatabaseError(message) from error
            logger.info("Connected to database: %s", self.db_path)

    @staticmethod
    def _apply_connection_pragmas(
        connection: sqlite3.Connection, *, synchronous: str
    ) -> None:
        if synchronous not in {"NORMAL", "FULL"}:
            raise ValueError(f"Unsupported SQLite synchronous mode: {synchronous}")
        cursor = connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute(f"PRAGMA synchronous={synchronous};")
        cursor.execute("PRAGMA busy_timeout=5000;")
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.execute("PRAGMA temp_store=MEMORY;")
        try:
            cursor.execute("PRAGMA mmap_size=268435456;")
        except DB_CONFIG_EXCEPTIONS as error:
            logger.debug("SQLite mmap_size PRAGMA skipped: %s", error, exc_info=True)

    def _open_connection(self, *, synchronous: str) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, check_same_thread=False, timeout=5.0)
        try:
            connection.row_factory = sqlite3.Row
            self._apply_connection_pragmas(connection, synchronous=synchronous)
            return connection
        except (sqlite3.Error, TypeError, ValueError):
            try:
                connection.close()
            except sqlite3.Error as close_error:
                logger.error(
                    "SQLite connection close failed after configuration error: %s",
                    close_error,
                    exc_info=True,
                )
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
        """Yield an isolated WAL/FULL connection without global serialization.

        Edge cases:
            1. An uncommitted transaction is rolled back before close.
            2. Cleanup failures are surfaced when no body exception is active.
            3. Cleanup failures never mask an exception raised by the caller body.
        """
        try:
            connection = self._open_connection(synchronous="FULL")
        except sqlite3.Error as error:
            raise DatabaseError(
                f"Unable to open durable SQLite connection: {error}"
            ) from error
        try:
            self._verify_durable_connection(connection)
            yield connection
        finally:
            cleanup_errors = cleanup_durable_connection(connection)
            if cleanup_errors:
                message = "; ".join(cleanup_errors)
                if sys.exc_info()[0] is None:
                    raise DatabaseError(message)
                logger.error("Durable SQLite cleanup failed: %s", message)

    def shared_connection(self) -> SharedConnectionBoundary:
        """Return the serialized idle-connection boundary."""
        return SharedConnectionBoundary(
            self._db_lock, self._connection, self._rollback_shared_connection)

    def shared_transaction(self, mode: TransactionMode = "IMMEDIATE") -> SharedTransactionBoundary:
        """Return a boundary-owned transaction on the shared connection."""
        return SharedTransactionBoundary(
            self._db_lock, self._connection, self._rollback_shared_connection, mode)

    @staticmethod
    def _verify_durable_connection(connection: sqlite3.Connection) -> None:
        try:
            journal_row = connection.execute("PRAGMA journal_mode;").fetchone()
            sync_row = connection.execute("PRAGMA synchronous;").fetchone()
            journal_mode = str(journal_row[0]).lower()
            synchronous = int(sync_row[0])
        except (sqlite3.Error, IndexError, TypeError, ValueError) as error:
            raise DatabaseError(
                f"Durable SQLite connection verification failed: {error}"
            ) from error
        if journal_mode != "wal" or synchronous != 2:
            raise DatabaseError(
                "Durable SQLite connection could not enable WAL/FULL mode."
            )

    def close(self) -> None:
        with self._db_lock:
            connection = self.conn
            active_transaction = getattr(connection, "in_transaction", False)
            if active_transaction is True:
                raise DatabaseError("Cannot close an active shared database transaction.")
            self.conn = None
        if connection is None:
            logger.debug("Database not connected, close call ignored.")
            return
        try:
            connection.close()
        except sqlite3.Error as error:
            message = f"Error closing database connection: {error}"
            logger.error("[DbCore] %s", message, exc_info=True)
            raise DatabaseError(message) from error
        logger.info("Database connection closed.")

    def _connection(self) -> sqlite3.Connection:
        if self.conn is None:
            self.connect()
        if self.conn is None:
            raise DatabaseError("Database not connected.")
        return self.conn

    def _execute_read(
        self,
        query: str,
        params: tuple[object, ...] | None,
        *,
        fetch_one: bool,
    ) -> sqlite3.Row | list[sqlite3.Row] | None:
        with self._db_lock:
            connection = self._connection()
            if getattr(connection, "in_transaction", False) is True:
                raise DatabaseError("Standalone database read cannot join an active transaction.")
            try:
                cursor = connection.cursor()
                cursor.execute(query, params) if params is not None else cursor.execute(query)
                return cursor.fetchone() if fetch_one else cursor.fetchall()
            except sqlite3.IntegrityError as error:
                raise IntegrityError(
                    "Database integrity constraint failed.", details=str(error)
                ) from error
            except sqlite3.Error as error:
                raise DatabaseError(
                    "Database read failed.", details=str(error)
                ) from error

    def _execute_write(
        self,
        query: str,
        params: tuple[object, ...] | None = None,
    ) -> DbWriteResult:
        """Execute, commit, and return metadata from the same SQLite cursor.

        Edge cases:
            1. A zero-row update/delete remains a successful write with rowcount 0.
            2. Integrity failures roll back and map to the application error type.
            3. A failed commit never returns write metadata to the caller.
        """
        with self._db_lock:
            connection = self._connection()
            if getattr(connection, "in_transaction", False) is True:
                raise DatabaseError("Standalone database write cannot join an active transaction.")
            try:
                cursor = connection.cursor()
                cursor.execute(query, params) if params is not None else cursor.execute(query)
                result = DbWriteResult.from_cursor(query, cursor)
                connection.commit()
            except sqlite3.IntegrityError as error:
                self._rollback_shared_connection(connection, error)
                raise IntegrityError(
                    "Database integrity constraint failed.", details=str(error)
                ) from error
            except sqlite3.Error as error:
                self._rollback_shared_connection(connection, error)
                raise DatabaseError(
                    "Database write failed.", details=str(error)
                ) from error
            except DB_QUERY_ARGUMENT_EXCEPTIONS as error:
                self._rollback_shared_connection(connection, error)
                raise
        return result

    def _rollback_shared_connection(
        self,
        connection: sqlite3.Connection,
        original_error: BaseException | None,
    ) -> None:
        try:
            connection.rollback()
        except sqlite3.Error as rollback_error:
            if self.conn is connection:
                self.conn = None
            try:
                connection.close()
            except sqlite3.Error as close_error:
                logger.error(
                    "SQLite rollback and close failed: rollback=%s; close=%s",
                    rollback_error,
                    close_error,
                    exc_info=True,
                )
            message = f"Database rollback failed: {rollback_error}"
            if original_error is not None:
                message = f"{message}; original error: {original_error}"
            raise DatabaseError(message) from rollback_error

    def _execute_query(
        self,
        query: str,
        params: tuple[object, ...] | None = None,
        fetch_one: bool = False,
        fetch_all: bool = False,
    ) -> sqlite3.Row | list[sqlite3.Row] | None:
        if fetch_one and fetch_all:
            raise ValueError("fetch_one and fetch_all are mutually exclusive")
        if fetch_one or fetch_all:
            return self._execute_read(query, params, fetch_one=fetch_one)
        self._execute_write(query, params)
        return None

    def initialize_database(self) -> None:
        """Create, adopt, or migrate the core schema atomically.

        Edge cases:
            1. Partial legacy schemas fail without creating missing tables.
            2. Future schema versions fail before any database mutation.
            3. DDL and the version marker roll back together on migration failure.
        """
        self.connect()
        with self.shared_transaction(mode="IMMEDIATE") as transaction:
            version = ensure_core_schema(transaction)
        if version != CORE_SCHEMA_VERSION:
            raise DatabaseError("Core database schema did not reach the required version.")
        logger.info("Database core schema initialized at version %s.", version)

    def __enter__(self) -> DbCore:
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()
