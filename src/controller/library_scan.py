"""Bounded, cooperative filesystem scanning for library imports.

The scanner owns traversal limits and cancellation. Media decoding remains in
``library_media_loader`` so filesystem discovery and provider work stay
separately testable.

Edge cases handled:
- hostile or oversized path sequences are rejected before unbounded iteration;
- directory, entry, media-file, and elapsed-time budgets fail closed;
- cancellation is checked before and after every external filesystem boundary;
- linked roots and linked descendants are never traversed;
- traversal errors never become a partial-success import.

Complexity: O(I + D + E), bounded by the configured input, directory, and entry
limits. Memory is O(D + M), bounded by directory identities and media paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import logging
import math
from numbers import Real
import threading
import time
from typing import Final, NoReturn, TypeAlias

from src.model.media_file import MediaFile

_MAX_INPUT_PATHS: Final = 100_000
_MAX_DIRECTORIES: Final = 100_000
_MAX_ENTRIES: Final = 1_000_000
_MAX_MEDIA_FILES: Final = 100_000
_MAX_ELAPSED_SECONDS: Final = 3_600.0
_MAX_PATH_CHARS: Final = 32_767
_MAX_PATH_BYTES: Final = 131_072
_MAX_ERROR_TEXT: Final = 320

MediaFileIdentity: TypeAlias = tuple[int, int, int, int, int, int]


class LibraryScanLimitKind(StrEnum):
    """Identify the hard budget that terminated a scan."""

    INPUT_PATHS = "input_paths"
    DIRECTORIES = "directories"
    ENTRIES = "entries"
    MEDIA_FILES = "media_files"
    ELAPSED_TIME = "elapsed_time"


@dataclass(frozen=True, slots=True)
class LibraryScanLimits:
    """Validated resource ceilings for one library scan."""

    max_input_paths: int = _MAX_INPUT_PATHS
    max_directories: int = 50_000
    max_entries: int = 500_000
    max_media_files: int = _MAX_MEDIA_FILES
    max_elapsed_seconds: float = 300.0

    def __post_init__(self) -> None:
        _validate_count_limit("max_input_paths", self.max_input_paths, _MAX_INPUT_PATHS)
        _validate_count_limit("max_directories", self.max_directories, _MAX_DIRECTORIES)
        _validate_count_limit("max_entries", self.max_entries, _MAX_ENTRIES)
        _validate_count_limit("max_media_files", self.max_media_files, _MAX_MEDIA_FILES)
        if isinstance(self.max_elapsed_seconds, bool) or not isinstance(
            self.max_elapsed_seconds, Real
        ):
            raise TypeError("max_elapsed_seconds must be a real number")
        try:
            seconds = float(self.max_elapsed_seconds)
        except (OverflowError, TypeError, ValueError) as error:
            raise TypeError("max_elapsed_seconds must be a real number") from error
        if not math.isfinite(seconds) or not 0.001 <= seconds <= _MAX_ELAPSED_SECONDS:
            raise ValueError(
                f"max_elapsed_seconds must be between 0.001 and {_MAX_ELAPSED_SECONDS}"
            )
        object.__setattr__(self, "max_elapsed_seconds", seconds)


class LibraryScanCancellation:
    """Thread-safe one-way cancellation token for cooperative library scans."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        """Request cancellation; the token intentionally cannot be reset."""
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        """Return whether cancellation has been requested."""
        return self._event.is_set()


@dataclass(frozen=True, slots=True)
class LibraryScanReport:
    """Immutable counters captured at scan completion or termination."""

    completed: bool
    termination: str | None
    input_paths_seen: int
    directories_scanned: int
    entries_examined: int
    media_paths_discovered: int
    metadata_probes_started: int
    media_files_loaded: int
    invalid_inputs: int
    missing_paths: int
    unsupported_entries: int
    linked_entries_skipped: int
    duplicate_directories_skipped: int
    directory_errors: int
    elapsed_seconds: float


@dataclass(frozen=True, slots=True)
class LibraryPathScanResult:
    """Complete filesystem discovery result."""

    paths: tuple[str, ...]
    report: LibraryScanReport


@dataclass(frozen=True, slots=True)
class LibraryMediaScanResult:
    """Complete media loading result plus the shared scan report."""

    media_files: tuple[MediaFile, ...]
    report: LibraryScanReport


class LibraryScanError(RuntimeError):
    """Base error for incomplete or unsafe library scans."""

    def __init__(self, message: str, report: LibraryScanReport) -> None:
        self.report = report
        super().__init__(message)


class LibraryScanCancelledError(LibraryScanError):
    """Raised when a cancellation token terminates the scan."""


class LibraryScanLimitError(LibraryScanError):
    """Raised when a configured hard resource limit is reached."""

    def __init__(
        self,
        kind: LibraryScanLimitKind,
        message: str,
        report: LibraryScanReport,
    ) -> None:
        self.kind = kind
        super().__init__(message, report)


class LibraryScanTraversalError(LibraryScanError):
    """Raised when filesystem traversal cannot be completed safely."""


class LibraryScanInputError(LibraryScanError):
    """Raised when the caller-provided path sequence cannot be consumed safely."""


class LibraryScanClockError(LibraryScanError):
    """Raised when the monotonic clock violates its required contract."""


@dataclass(slots=True)
class _Counters:
    input_paths_seen: int = 0
    directories_scanned: int = 0
    entries_examined: int = 0
    media_paths_discovered: int = 0
    metadata_probes_started: int = 0
    media_files_loaded: int = 0
    invalid_inputs: int = 0
    missing_paths: int = 0
    unsupported_entries: int = 0
    linked_entries_skipped: int = 0
    duplicate_directories_skipped: int = 0
    directory_errors: int = 0


class LibraryScanSession:
    """Single-owner budget tracker shared by discovery and metadata loading."""

    def __init__(
        self,
        limits: LibraryScanLimits | None = None,
        cancellation: LibraryScanCancellation | None = None,
    ) -> None:
        if limits is not None and not isinstance(limits, LibraryScanLimits):
            raise TypeError("limits must be LibraryScanLimits")
        self.limits = limits if limits is not None else LibraryScanLimits()
        if cancellation is not None and not isinstance(cancellation, LibraryScanCancellation):
            raise TypeError("cancellation must be a LibraryScanCancellation")
        self.cancellation = cancellation
        self._counters = _Counters()
        self._media_identities: dict[str, MediaFileIdentity] = {}
        self._started_at = time.monotonic()
        self._last_observed = self._started_at

    def checkpoint(self) -> None:
        """Fail before more work when cancellation or elapsed budget applies."""
        elapsed = self._observe_elapsed()
        if self.cancellation is not None and self.cancellation.is_cancelled:
            raise LibraryScanCancelledError(
                "library scan was cancelled",
                self._report(False, "cancelled", elapsed),
            )
        if elapsed >= self.limits.max_elapsed_seconds:
            self._raise_limit(
                LibraryScanLimitKind.ELAPSED_TIME,
                "library scan exceeded the elapsed-time limit",
                elapsed,
            )

    def remaining_seconds(self, maximum: float) -> float:
        """Return a positive timeout bounded by the remaining scan budget."""
        self.checkpoint()
        remaining = self.limits.max_elapsed_seconds - self._elapsed_unchecked()
        if remaining <= 0.0:
            self._raise_limit(
                LibraryScanLimitKind.ELAPSED_TIME,
                "library scan exceeded the elapsed-time limit",
                self._elapsed_unchecked(),
            )
        return min(maximum, remaining)

    def claim_input_path(self) -> None:
        self._claim(
            "input_paths_seen",
            self.limits.max_input_paths,
            LibraryScanLimitKind.INPUT_PATHS,
        )

    def claim_directory(self) -> None:
        self._claim(
            "directories_scanned",
            self.limits.max_directories,
            LibraryScanLimitKind.DIRECTORIES,
        )

    def ensure_directory_queue_capacity(self, queued: int) -> None:
        """Fail before queued directory paths can exceed the global budget."""
        if self._counters.directories_scanned + queued > self.limits.max_directories:
            self._raise_limit(
                LibraryScanLimitKind.DIRECTORIES,
                "library scan exceeded the directories limit",
            )

    def claim_entry(self) -> None:
        self._claim("entries_examined", self.limits.max_entries, LibraryScanLimitKind.ENTRIES)

    def claim_media_path(self) -> None:
        self._claim(
            "media_paths_discovered",
            self.limits.max_media_files,
            LibraryScanLimitKind.MEDIA_FILES,
        )

    def claim_metadata_probe(self) -> None:
        self.checkpoint()
        self._counters.metadata_probes_started += 1

    def record_media_loaded(self) -> None:
        self.checkpoint()
        self._counters.media_files_loaded += 1

    def remember_media_identity(self, path: str, identity: MediaFileIdentity) -> None:
        """Bind a discovered path to one file identity for the whole scan."""
        previous = self._media_identities.get(path)
        if previous is not None and previous != identity:
            self.fail_traversal(path, "media file changed during discovery")
        self._media_identities[path] = identity

    def expected_media_identity(self, path: str) -> MediaFileIdentity | None:
        """Return the identity captured when the media path was discovered."""
        return self._media_identities.get(path)

    def record_invalid_input(self) -> None:
        self._counters.invalid_inputs += 1

    def record_missing_path(self) -> None:
        self._counters.missing_paths += 1

    def record_unsupported_entry(self) -> None:
        self._counters.unsupported_entries += 1

    def record_link_skipped(self) -> None:
        self._counters.linked_entries_skipped += 1

    def record_duplicate_directory(self) -> None:
        self._counters.duplicate_directories_skipped += 1

    def fail_traversal(self, path: str, error: Exception | str) -> NoReturn:
        """Convert one traversal failure into a bounded typed error."""
        self._counters.directory_errors += 1
        detail = bounded_scan_error_text(error)
        display_path = repr(path)[:_MAX_ERROR_TEXT]
        raise LibraryScanTraversalError(
            f"unable to scan {display_path}: {detail}",
            self._report(False, "traversal_error"),
        )

    def fail_input(self, error: Exception) -> NoReturn:
        """Convert a failing external sequence into a bounded typed error."""
        self._counters.invalid_inputs += 1
        detail = bounded_scan_error_text(error)
        raise LibraryScanInputError(
            f"unable to consume the library path sequence: {detail}",
            self._report(False, "input_error"),
        )

    def complete_report(self) -> LibraryScanReport:
        """Return a completed report only after a final cancellation/time check."""
        self.checkpoint()
        return self._report(True, None)

    def fail_limit(self, kind: LibraryScanLimitKind, message: str) -> NoReturn:
        """Terminate the scan with a typed resource-limit error."""
        self._raise_limit(kind, message)

    def _claim(
        self,
        counter_name: str,
        limit: int,
        kind: LibraryScanLimitKind,
    ) -> None:
        self.checkpoint()
        current = getattr(self._counters, counter_name)
        if current >= limit:
            self._raise_limit(kind, f"library scan exceeded the {kind.value} limit")
        setattr(self._counters, counter_name, current + 1)

    def _raise_limit(
        self,
        kind: LibraryScanLimitKind,
        message: str,
        elapsed: float | None = None,
    ) -> NoReturn:
        raise LibraryScanLimitError(
            kind,
            message,
            self._report(False, f"limit:{kind.value}", elapsed),
        )

    def _observe_elapsed(self) -> float:
        now = time.monotonic()
        if not math.isfinite(now) or now < self._last_observed:
            raise LibraryScanClockError(
                "library scan monotonic clock regressed or became non-finite",
                self._report(False, "clock_error", self._elapsed_unchecked()),
            )
        self._last_observed = now
        return max(0.0, now - self._started_at)

    def _elapsed_unchecked(self) -> float:
        return max(0.0, self._last_observed - self._started_at)

    def _report(
        self,
        completed: bool,
        termination: str | None,
        elapsed: float | None = None,
    ) -> LibraryScanReport:
        counters = self._counters
        return LibraryScanReport(
            completed=completed,
            termination=termination,
            input_paths_seen=counters.input_paths_seen,
            directories_scanned=counters.directories_scanned,
            entries_examined=counters.entries_examined,
            media_paths_discovered=counters.media_paths_discovered,
            metadata_probes_started=counters.metadata_probes_started,
            media_files_loaded=counters.media_files_loaded,
            invalid_inputs=counters.invalid_inputs,
            missing_paths=counters.missing_paths,
            unsupported_entries=counters.unsupported_entries,
            linked_entries_skipped=counters.linked_entries_skipped,
            duplicate_directories_skipped=counters.duplicate_directories_skipped,
            directory_errors=counters.directory_errors,
            elapsed_seconds=self._elapsed_unchecked() if elapsed is None else elapsed,
        )

def bounded_scan_error_text(error: Exception | str) -> str:
    if isinstance(error, str):
        return error[:_MAX_ERROR_TEXT]
    try:
        return str(error)[:_MAX_ERROR_TEXT]
    except Exception as render_error:  # explicit diagnostic-rendering boundary
        return f"{type(error).__name__} with unprintable detail ({type(render_error).__name__})"


def safe_scan_log(
    logger: logging.Logger,
    level: int,
    message: str,
    *args: object,
) -> None:
    """Keep logging-handler failures outside library scan contracts."""
    try:
        logger.log(level, message, *args)
    except Exception:  # explicit logging-handler boundary
        return


def validate_scan_path_text(path: str) -> bool:
    """Return whether a text path fits the shared catalog path ceilings."""
    if not path or "\x00" in path or len(path) > _MAX_PATH_CHARS:
        return False
    try:
        return len(path.encode("utf-8")) <= _MAX_PATH_BYTES
    except UnicodeEncodeError:
        return False


def _validate_count_limit(name: str, value: int, maximum: int) -> None:
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer")
    if not 1 <= value <= maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}")


__all__ = [
    "LibraryMediaScanResult",
    "LibraryPathScanResult",
    "LibraryScanCancellation",
    "LibraryScanCancelledError",
    "LibraryScanClockError",
    "LibraryScanError",
    "LibraryScanLimitError",
    "LibraryScanInputError",
    "LibraryScanLimitKind",
    "LibraryScanLimits",
    "LibraryScanReport",
    "LibraryScanSession",
    "LibraryScanTraversalError",
    "MediaFileIdentity",
    "bounded_scan_error_text",
    "safe_scan_log",
    "validate_scan_path_text",
]
