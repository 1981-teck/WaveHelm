"""L2 contract tests for bounded and cancellable library scanning."""

from __future__ import annotations

import os
import shutil
from collections.abc import Sequence
from pathlib import Path

import pytest

import src.controller.library_media_loader as loader_module
import src.controller.library_scan as scan_module
import src.controller.library_scan_traversal as traversal_module
from src.controller.library_media_loader import (
    scan_library_media_files,
    scan_library_media_paths,
)
from src.controller.library_scan import (
    LibraryScanCancellation,
    LibraryScanCancelledError,
    LibraryScanClockError,
    LibraryScanInputError,
    LibraryScanLimitError,
    LibraryScanLimitKind,
    LibraryScanLimits,
    LibraryScanTraversalError,
)
from src.utils import ffprobe_service
from src.utils.media_metadata import AudioTagMetadata

RUNTIME_ROOT = Path(__file__).resolve().parent / "_library_scan_runtime"


def _runtime_dir(name: str) -> Path:
    target = RUNTIME_ROOT / name
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    return target


def _patch_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loader_module, "is_audio_file", lambda path: str(path).endswith(".mp3"))
    monkeypatch.setattr(loader_module, "is_video_file", lambda path: False)
    monkeypatch.setattr(
        loader_module,
        "read_audio_basic_metadata",
        lambda path: AudioTagMetadata(duration=1.0),
    )


@pytest.mark.parametrize(
    ("kwargs", "error_type"),
    [
        ({"max_input_paths": True}, TypeError),
        ({"max_directories": 0}, ValueError),
        ({"max_entries": 1_000_001}, ValueError),
        ({"max_media_files": "2"}, TypeError),
        ({"max_elapsed_seconds": "1"}, TypeError),
        ({"max_elapsed_seconds": float("nan")}, ValueError),
        ({"max_elapsed_seconds": float("inf")}, ValueError),
        ({"max_elapsed_seconds": 0.0}, ValueError),
        ({"max_elapsed_seconds": 3_601.0}, ValueError),
    ],
)
def test_scan_limits_reject_ambiguous_or_unbounded_values(
    kwargs: dict[str, object],
    error_type: type[Exception],
) -> None:
    with pytest.raises(error_type):
        LibraryScanLimits(**kwargs)  # type: ignore[arg-type]


def test_scan_session_rejects_invalid_contract_objects() -> None:
    with pytest.raises(TypeError, match="limits"):
        scan_module.LibraryScanSession(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="cancellation"):
        scan_module.LibraryScanSession(cancellation=object())  # type: ignore[arg-type]


def test_scalar_plural_input_preserves_empty_complete_result() -> None:
    result = scan_library_media_paths("track.mp3")
    assert result.paths == ()
    assert result.report.completed is True
    assert result.report.invalid_inputs == 1


def test_input_sequence_limit_fails_before_iteration(tmp_path: Path) -> None:
    paths = [tmp_path / "one.mp3", tmp_path / "two.mp3"]
    with pytest.raises(LibraryScanLimitError) as captured:
        scan_library_media_paths(paths, limits=LibraryScanLimits(max_input_paths=1))
    assert captured.value.kind is LibraryScanLimitKind.INPUT_PATHS
    assert captured.value.report.input_paths_seen == 0


def test_failing_sequence_iteration_becomes_typed_input_error() -> None:
    class FailingSequence(Sequence[object]):
        def __len__(self) -> int:
            return 1

        def __getitem__(self, index: int) -> object:
            raise RuntimeError(f"sequence failed at {index}")

    with pytest.raises(LibraryScanInputError, match="sequence failed") as captured:
        scan_library_media_paths(FailingSequence())
    assert captured.value.report.completed is False
    assert captured.value.report.invalid_inputs == 1


def test_directory_entry_and_media_limits_fail_without_partial_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_dir = _runtime_dir("limits")
    child = runtime_dir / "child"
    child.mkdir()
    for name in ("a.mp3", "b.mp3"):
        (runtime_dir / name).write_text("x", encoding="utf-8")
    _patch_audio(monkeypatch)

    with pytest.raises(LibraryScanLimitError) as directories:
        scan_library_media_paths(
            [runtime_dir],
            limits=LibraryScanLimits(max_directories=1),
        )
    assert directories.value.kind is LibraryScanLimitKind.DIRECTORIES
    assert directories.value.report.directories_scanned == 1

    with pytest.raises(LibraryScanLimitError) as entries:
        scan_library_media_paths(
            [runtime_dir],
            limits=LibraryScanLimits(max_entries=1),
        )
    assert entries.value.kind is LibraryScanLimitKind.ENTRIES
    assert entries.value.report.entries_examined == 1

    with pytest.raises(LibraryScanLimitError) as media:
        scan_library_media_paths(
            [runtime_dir],
            limits=LibraryScanLimits(max_media_files=1),
        )
    assert media.value.kind is LibraryScanLimitKind.MEDIA_FILES
    assert media.value.report.media_paths_discovered == 1


def test_exact_limits_complete_with_bounded_report(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_dir = _runtime_dir("exact_limits")
    media_path = runtime_dir / "one.mp3"
    ignored_path = runtime_dir / "notes.txt"
    media_path.write_text("x", encoding="utf-8")
    ignored_path.write_text("x", encoding="utf-8")
    _patch_audio(monkeypatch)

    result = scan_library_media_files(
        [runtime_dir],
        limits=LibraryScanLimits(
            max_input_paths=1,
            max_directories=1,
            max_entries=2,
            max_media_files=1,
            max_elapsed_seconds=30.0,
        ),
    )

    assert [media.path for media in result.media_files] == [str(media_path)]
    assert result.report.completed is True
    assert result.report.input_paths_seen == 1
    assert result.report.directories_scanned == 1
    assert result.report.entries_examined == 2
    assert result.report.media_paths_discovered == 1
    assert result.report.metadata_probes_started == 1
    assert result.report.media_files_loaded == 1
    assert result.report.unsupported_entries == 1


def test_cancellation_before_and_during_discovery_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_dir = _runtime_dir("cancel_discovery")
    media_path = runtime_dir / "one.mp3"
    media_path.write_text("x", encoding="utf-8")

    before = LibraryScanCancellation()
    before.cancel()
    with pytest.raises(LibraryScanCancelledError) as pre_cancelled:
        scan_library_media_paths([runtime_dir], cancellation=before)
    assert pre_cancelled.value.report.input_paths_seen == 0

    during = LibraryScanCancellation()

    def cancel_when_classified(path: str) -> bool:
        during.cancel()
        return path.endswith(".mp3")

    monkeypatch.setattr(loader_module, "is_audio_file", cancel_when_classified)
    monkeypatch.setattr(loader_module, "is_video_file", lambda path: False)
    with pytest.raises(LibraryScanCancelledError) as mid_cancelled:
        scan_library_media_paths([runtime_dir], cancellation=during)
    assert mid_cancelled.value.report.media_paths_discovered == 0


def test_cancellation_during_metadata_discards_the_partial_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_dir = _runtime_dir("cancel_metadata")
    media_path = runtime_dir / "one.mp3"
    media_path.write_text("x", encoding="utf-8")
    token = LibraryScanCancellation()
    monkeypatch.setattr(loader_module, "is_audio_file", lambda path: True)
    monkeypatch.setattr(loader_module, "is_video_file", lambda path: False)

    def cancel_metadata(path: str) -> AudioTagMetadata:
        token.cancel()
        return AudioTagMetadata(duration=1.0)

    monkeypatch.setattr(loader_module, "read_audio_basic_metadata", cancel_metadata)

    with pytest.raises(LibraryScanCancelledError) as captured:
        scan_library_media_files([media_path], cancellation=token)
    assert captured.value.report.metadata_probes_started == 1
    assert captured.value.report.media_files_loaded == 0


def test_elapsed_limit_and_clock_regression_are_typed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    media_path = tmp_path / "one.mp3"
    media_path.write_text("x", encoding="utf-8")

    class AdvancingClock:
        def __init__(self) -> None:
            self.value = -0.2

        def __call__(self) -> float:
            self.value += 0.2
            return self.value

    monkeypatch.setattr(scan_module.time, "monotonic", AdvancingClock())
    with pytest.raises(LibraryScanLimitError) as elapsed:
        scan_library_media_paths(
            [media_path],
            limits=LibraryScanLimits(max_elapsed_seconds=0.5),
        )
    assert elapsed.value.kind is LibraryScanLimitKind.ELAPSED_TIME

    values = iter((10.0, 9.0))
    monkeypatch.setattr(scan_module.time, "monotonic", lambda: next(values))
    with pytest.raises(LibraryScanClockError):
        scan_library_media_paths([media_path])


def test_ffprobe_uses_remaining_scan_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_dir = _runtime_dir("ffprobe_deadline")
    media_path = runtime_dir / "clip.mkv"
    media_path.write_text("x", encoding="utf-8")
    observed: list[float] = []

    class SlowClock:
        def __init__(self) -> None:
            self.value = -0.005

        def __call__(self) -> float:
            self.value += 0.005
            return self.value

    monkeypatch.setattr(scan_module.time, "monotonic", SlowClock())
    monkeypatch.setattr(loader_module, "is_audio_file", lambda path: False)
    monkeypatch.setattr(loader_module, "is_video_file", lambda path: True)
    monkeypatch.setattr(loader_module, "cv2", None)

    def duration(path: str, *, timeout_seconds: float) -> float:
        observed.append(timeout_seconds)
        return 1.0

    def tracks(path: str, *, timeout_seconds: float) -> list[ffprobe_service.AudioTrackMetadata]:
        observed.append(timeout_seconds)
        return []

    monkeypatch.setattr(ffprobe_service, "probe_duration", duration)
    monkeypatch.setattr(ffprobe_service, "probe_audio_tracks", tracks)

    result = scan_library_media_files(
        [media_path],
        limits=LibraryScanLimits(max_elapsed_seconds=0.5),
    )
    assert len(result.media_files) == 1
    assert len(observed) == 2
    assert all(0.0 < timeout <= 0.5 for timeout in observed)


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_linked_roots_are_rejected_and_linked_descendants_are_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_dir = _runtime_dir("links")
    real_root = runtime_dir / "real"
    real_root.mkdir()
    media_path = real_root / "one.mp3"
    media_path.write_text("x", encoding="utf-8")
    root_link = runtime_dir / "root-link"
    root_link.symlink_to(real_root, target_is_directory=True)
    linked_file = real_root / "linked.mp3"
    linked_file.symlink_to(media_path)
    _patch_audio(monkeypatch)

    with pytest.raises(LibraryScanTraversalError, match="linked scan roots"):
        scan_library_media_paths([root_link])

    result = scan_library_media_paths([real_root])
    assert result.paths == (str(media_path),)
    assert result.report.linked_entries_skipped == 1


def test_descendant_scan_error_aborts_instead_of_returning_partial_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_dir = _runtime_dir("descendant_error")
    (runtime_dir / "one.mp3").write_text("x", encoding="utf-8")
    blocked = runtime_dir / "blocked"
    blocked.mkdir()
    _patch_audio(monkeypatch)
    original_scandir = traversal_module.os.scandir

    def failing_scandir(path: object):
        if str(path) == str(blocked):
            raise PermissionError("blocked descendant")
        return original_scandir(path)

    monkeypatch.setattr(traversal_module.os, "scandir", failing_scandir)
    with pytest.raises(LibraryScanTraversalError, match="blocked descendant") as captured:
        scan_library_media_paths([runtime_dir])
    assert captured.value.report.completed is False
    assert captured.value.report.directory_errors == 1


def test_path_text_limits_are_checked_before_filesystem_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def forbidden_stat(path: str, *, follow_symlinks: bool):
        calls.append(path)
        raise AssertionError("filesystem must not be touched")

    monkeypatch.setattr(traversal_module.os, "stat", forbidden_stat)
    result = scan_library_media_paths(["x" * 32_768])
    assert result.paths == ()
    assert result.report.invalid_inputs == 1
    assert calls == []


def test_pre_cancelled_token_wins_before_external_sequence_length() -> None:
    class ExplodingLength(Sequence[object]):
        length_called = False

        def __len__(self) -> int:
            self.length_called = True
            raise RuntimeError("length must not run")

        def __getitem__(self, index: int) -> object:
            raise IndexError(index)

    values = ExplodingLength()
    token = LibraryScanCancellation()
    token.cancel()
    with pytest.raises(LibraryScanCancelledError):
        scan_library_media_paths(values, cancellation=token)
    assert values.length_called is False

    with pytest.raises(LibraryScanCancelledError):
        scan_library_media_paths("scalar.mp3", cancellation=token)


def test_unprintable_traversal_errors_remain_typed_and_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_dir = _runtime_dir("unprintable_error")

    class UnprintableError(OSError):
        def __str__(self) -> str:
            raise RuntimeError("render failed")

    def failing_scandir(path: object):
        raise UnprintableError()

    monkeypatch.setattr(traversal_module.os, "scandir", failing_scandir)
    with pytest.raises(LibraryScanTraversalError, match="unprintable detail") as captured:
        scan_library_media_paths([runtime_dir])
    assert captured.value.report.directory_errors == 1
