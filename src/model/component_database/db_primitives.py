from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os
import re
from pathlib import Path
import sqlite3
import sys
import threading
from types import TracebackType
from typing import Literal, Protocol

from src.utils.exceptions import DatabaseError, IntegrityError


TransactionMode = Literal["DEFERRED", "IMMEDIATE", "EXCLUSIVE"]
ConnectionProvider = Callable[[], sqlite3.Connection]
RollbackHandler = Callable[[sqlite3.Connection, BaseException | None], None]


class SqlExecutor(Protocol):
    """Minimal SQL execution surface shared by connection and transaction scopes."""

    def execute(
        self, query: str, params: tuple[object, ...] | None = None
    ) -> sqlite3.Cursor | DbCursor:
        """Execute one SQL statement and return its cursor capability."""


@dataclass(frozen=True, slots=True)
class DbWriteResult:
    """Capture write metadata from the exact cursor that executed the statement.

    Edge cases:
        1. A statement that matches no rows reports ``rowcount == 0``.
        2. Non-insert statements normally report ``lastrowid is None``.
        3. The result is published only after the matching commit succeeds.
    """

    rowcount: int
    lastrowid: int | None

    @classmethod
    def from_cursor(cls, query: str, cursor: sqlite3.Cursor) -> DbWriteResult:
        """Build a conservative result without leaking stale insert identifiers."""
        tokens = query.lstrip().split(None, 1)
        keyword = tokens[0].upper() if tokens else ""
        raw_lastrowid = cursor.lastrowid
        lastrowid = None
        if keyword in {"INSERT", "REPLACE"} and raw_lastrowid is not None:
            lastrowid = int(raw_lastrowid)
        return cls(rowcount=int(cursor.rowcount), lastrowid=lastrowid)


class _ScopedExecutor:
    """Thread-bound, revocable execution capability for one boundary scope."""

    __slots__ = ("_connection", "_cursors", "_owner_thread_id")

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection: sqlite3.Connection | None = connection
        self._cursors: list[DbCursor] = []
        self._owner_thread_id = threading.get_ident()

    def _connection_for_owner(self) -> sqlite3.Connection:
        if self._connection is None:
            raise DatabaseError("Database boundary capability is no longer active.")
        if threading.get_ident() != self._owner_thread_id:
            raise DatabaseError(
                "Database boundary capability cannot cross thread ownership."
            )
        return self._connection

    @property
    def in_transaction(self) -> bool:
        return self._connection_for_owner().in_transaction

    def execute(self, query: str, params: tuple[object, ...] | None = None) -> DbCursor:
        connection = self._connection_for_owner()
        raw_cursor = (
            connection.execute(query)
            if params is None
            else connection.execute(query, params)
        )
        cursor = DbCursor(raw_cursor, self)
        self._cursors.append(cursor)
        return cursor

    def _revoke(self) -> None:
        for cursor in self._cursors:
            cursor._revoke()
        self._cursors.clear()
        self._connection = None


class DbCursor:
    """Thread-bound cursor view that cannot outlive its database boundary."""

    __slots__ = ("_cursor", "_owner")

    def __init__(self, cursor: sqlite3.Cursor, owner: _ScopedExecutor) -> None:
        self._cursor: sqlite3.Cursor | None = cursor
        self._owner = owner

    def _active_cursor(self) -> sqlite3.Cursor:
        self._owner._connection_for_owner()
        if self._cursor is None:
            raise DatabaseError("Database cursor capability is no longer active.")
        return self._cursor

    @property
    def rowcount(self) -> int:
        return int(self._active_cursor().rowcount)

    @property
    def lastrowid(self) -> int | None:
        value = self._active_cursor().lastrowid
        return None if value is None else int(value)

    def fetchone(self) -> sqlite3.Row | tuple[object, ...] | None:
        return self._active_cursor().fetchone()

    def fetchall(self) -> list[sqlite3.Row] | list[tuple[object, ...]]:
        return self._active_cursor().fetchall()

    def _revoke(self) -> None:
        self._cursor = None


class DbConnectionLease(_ScopedExecutor):
    """Revocable shared-connection capability for reads or schema ownership."""

    def commit(self) -> None:
        self._connection_for_owner().commit()

    def rollback(self) -> None:
        self._connection_for_owner().rollback()


class DbTransaction(_ScopedExecutor):
    """Revocable SQL executor whose transaction lifecycle remains boundary-owned.

    Edge cases:
        1. Transaction-control SQL is rejected before reaching SQLite.
        2. Cross-thread use is rejected before touching the connection.
        3. Cursors and the executor are revoked when the boundary exits.
    """

    _FORBIDDEN_KEYWORDS = frozenset(
        {"ATTACH", "BEGIN", "COMMIT", "DETACH", "END", "RELEASE", "ROLLBACK", "SAVEPOINT"}
    )

    def execute(self, query: str, params: tuple[object, ...] | None = None) -> DbCursor:
        keyword = _leading_sql_keyword(query)
        if keyword in self._FORBIDDEN_KEYWORDS:
            raise DatabaseError(
                f"Transaction-control statement is boundary-owned: {keyword}."
            )
        return super().execute(query, params)


def _leading_sql_keyword(query: str) -> str:
    if not isinstance(query, str):
        raise TypeError("SQL query must be text")
    remainder = query
    while True:
        remainder = remainder.lstrip("\ufeff \t\r\n\f")
        if remainder.startswith(";"):
            remainder = remainder[1:]
            continue
        if remainder.startswith("--"):
            newline = remainder.find("\n")
            if newline < 0:
                return ""
            remainder = remainder[newline + 1 :]
            continue
        if remainder.startswith("/*"):
            terminator = remainder.find("*/", 2)
            if terminator < 0:
                return ""
            remainder = remainder[terminator + 2 :]
            continue
        break
    match = re.match(r"[A-Za-z]+", remainder)
    return match.group(0).upper() if match is not None else ""


class SerializedConnectionGate:
    """Serialize shared-connection use without mutex-held SQLite I/O.

    The internal condition mutex protects only bounded owner/depth state. Logical
    ownership remains active while the caller performs I/O, so one shared SQLite
    connection is never used concurrently by multiple threads.

    Edge cases:
        1. Spurious wakeups cannot admit two owner threads.
        2. Exceptions always release the logical ownership token.
        3. Same-thread reentry remains supported for legacy connection helpers.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._owner_thread_id: int | None = None
        self._depth = 0

    def __enter__(self) -> SerializedConnectionGate:
        thread_id = threading.get_ident()
        with self._condition:
            while self._owner_thread_id not in (None, thread_id):
                self._condition.wait()
            self._owner_thread_id = thread_id
            self._depth += 1
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        thread_id = threading.get_ident()
        with self._condition:
            if self._owner_thread_id != thread_id or self._depth < 1:
                raise RuntimeError("database connection gate ownership was lost")
            self._depth -= 1
            if self._depth == 0:
                self._owner_thread_id = None
                self._condition.notify_all()


class _SharedBoundary:
    """Common ownership state for public shared-connection boundaries."""

    def __init__(
        self,
        gate: SerializedConnectionGate,
        connection_provider: ConnectionProvider,
        rollback_handler: RollbackHandler,
    ) -> None:
        self._gate = gate
        self._connection_provider = connection_provider
        self._rollback_handler = rollback_handler
        self._connection: sqlite3.Connection | None = None
        self._owner_thread_id: int | None = None

    def _acquire_idle_connection(self) -> sqlite3.Connection:
        if self._connection is not None:
            raise DatabaseError("Shared database boundary is already active.")
        self._gate.__enter__()
        self._owner_thread_id = threading.get_ident()
        acquired = False
        try:
            connection = self._connection_provider()
            if connection.in_transaction:
                self._rollback_handler(connection, None)
                raise DatabaseError(
                    "Shared database connection has an unowned active transaction."
                )
            self._connection = connection
            acquired = True
            return connection
        except sqlite3.IntegrityError as error:
            _raise_mapped_sqlite(error, "Shared database boundary initialization")
        except sqlite3.Error as error:
            _raise_mapped_sqlite(error, "Shared database boundary initialization")
        finally:
            if not acquired:
                try:
                    self._gate.__exit__(*sys.exc_info())
                finally:
                    self._owner_thread_id = None

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("shared database boundary was not entered")
        if threading.get_ident() != self._owner_thread_id:
            raise DatabaseError("Shared database boundary cannot exit on another thread.")
        return self._connection

    def _release(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._gate.__exit__(exc_type, exc_value, traceback)
        self._connection = None
        self._owner_thread_id = None


class SharedConnectionBoundary(_SharedBoundary):
    """Yield a revocable connection lease under serialized ownership."""

    def __init__(
        self,
        gate: SerializedConnectionGate,
        connection_provider: ConnectionProvider,
        rollback_handler: RollbackHandler,
    ) -> None:
        super().__init__(gate, connection_provider, rollback_handler)
        self._lease: DbConnectionLease | None = None

    def __enter__(self) -> DbConnectionLease:
        connection = self._acquire_idle_connection()
        self._lease = DbConnectionLease(connection)
        return self._lease

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        connection = self._require_connection()
        leaked_transaction = False
        try:
            leaked_transaction = connection.in_transaction
            if leaked_transaction:
                self._rollback_handler(connection, exc_value)
        except sqlite3.Error as error:
            self._rollback_handler(connection, error)
            _raise_mapped_sqlite(error, "Shared database connection cleanup")
        finally:
            if self._lease is not None:
                self._lease._revoke()
                self._lease = None
            self._release(exc_type, exc_value, traceback)
        if leaked_transaction and exc_type is None:
            raise DatabaseError(
                "Shared database connection scope leaked an active transaction."
            )
        if isinstance(exc_value, sqlite3.Error):
            _raise_mapped_sqlite(exc_value, "Shared database operation")
        return False


class SharedTransactionBoundary(_SharedBoundary):
    """Own begin, commit, rollback, and error mapping for one shared transaction."""

    def __init__(
        self,
        gate: SerializedConnectionGate,
        connection_provider: ConnectionProvider,
        rollback_handler: RollbackHandler,
        mode: TransactionMode,
    ) -> None:
        super().__init__(gate, connection_provider, rollback_handler)
        if mode not in {"DEFERRED", "IMMEDIATE", "EXCLUSIVE"}:
            raise ValueError("unsupported shared transaction mode")
        self._mode = mode
        self._transaction: DbTransaction | None = None

    def __enter__(self) -> DbTransaction:
        connection = self._acquire_idle_connection()
        entered = False
        try:
            connection.execute(f"BEGIN {self._mode}")
            self._transaction = DbTransaction(connection)
            entered = True
            return self._transaction
        except sqlite3.IntegrityError as error:
            _raise_mapped_sqlite(error, "Shared database transaction begin")
        except sqlite3.Error as error:
            _raise_mapped_sqlite(error, "Shared database transaction begin")
        finally:
            if not entered:
                exc_type, exc_value, traceback = sys.exc_info()
                try:
                    if connection.in_transaction:
                        self._rollback_handler(connection, exc_value)
                finally:
                    self._release(exc_type, exc_value, traceback)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        connection = self._require_connection()
        try:
            if exc_type is None:
                if not connection.in_transaction:
                    raise DatabaseError(
                        "Shared database transaction ended outside its owner boundary."
                    )
                connection.commit()
                if connection.in_transaction:
                    self._rollback_handler(connection, None)
                    raise DatabaseError(
                        "Shared database transaction remained active after commit."
                    )
            else:
                self._rollback_handler(connection, exc_value)
        except sqlite3.IntegrityError as error:
            self._rollback_handler(connection, error)
            _raise_mapped_sqlite(error, "Shared database transaction completion")
        except sqlite3.Error as error:
            self._rollback_handler(connection, error)
            _raise_mapped_sqlite(error, "Shared database transaction completion")
        finally:
            if self._transaction is not None:
                self._transaction._revoke()
                self._transaction = None
            self._release(exc_type, exc_value, traceback)
        if isinstance(exc_value, sqlite3.Error):
            _raise_mapped_sqlite(exc_value, "Shared database transaction body")
        return False


def _raise_mapped_sqlite(error: sqlite3.Error, action: str) -> None:
    details = str(error)
    if isinstance(error, sqlite3.IntegrityError):
        raise IntegrityError(
            f"{action} violated a database integrity constraint.", details=details
        ) from error
    raise DatabaseError(f"{action} failed.", details=details) from error


def resolve_database_path(app_data_dir: Path, db_path: str | None) -> Path:
    """Return a lexical, platform-normalized database path."""
    if db_path is None:
        raw_path = app_data_dir / "wavehelm.db"
    else:
        if not isinstance(db_path, str):
            raise TypeError("db_path must be a string or None")
        if not db_path or "\x00" in db_path:
            raise ValueError("db_path must be a non-empty path without NUL bytes")
        raw_path = app_data_dir / Path(db_path)
    return Path(os.path.normcase(os.path.abspath(raw_path)))


def cleanup_durable_connection(
    connection: sqlite3.Connection,
) -> tuple[str, ...]:
    """Rollback an open transaction and close, returning every cleanup error."""
    errors: list[str] = []
    try:
        if connection.in_transaction:
            connection.rollback()
    except sqlite3.Error as error:
        errors.append(f"Durable SQLite rollback failed: {error}")
    try:
        connection.close()
    except sqlite3.Error as error:
        errors.append(f"Durable SQLite close failed: {error}")
    return tuple(errors)
