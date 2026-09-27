"""Identity-stability tests for bounded library scanning."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.controller import library_media_loader, library_scan_traversal
from src.controller.library_media_loader import (
    scan_library_media_files,
    scan_library_media_paths,
)
from src.controller.library_scan import LibraryScanTraversalError
from src.utils.media_metadata import AudioMetadataError, AudioTagMetadata


def _metadata() -> AudioTagMetadata:
    return AudioTagMetadata(title="Track", artist="Artist", album="Album", duration=1.0)


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory replacement semantics required")
def test_scan_rejects_directory_replaced_after_scandir_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "library"
    moved = tmp_path / "library-original"
    root.mkdir()
    (root / "one.mp3").write_bytes(b"one")
    original_scandir = library_scan_traversal.os.scandir
    replaced = False

    def replacing_scandir(path: str) -> os.ScandirIterator[str]:
        nonlocal replaced
        iterator = original_scandir(path)
        if not replaced and os.fspath(path) == os.fspath(root):
            replaced = True
            root.rename(moved)
            root.mkdir()
        return iterator

    monkeypatch.setattr(library_scan_traversal.os, "scandir", replacing_scandir)

    with pytest.raises(LibraryScanTraversalError, match="changed during open"):
        scan_library_media_files([str(root)])


@pytest.mark.skipif(os.name == "nt", reason="POSIX atomic replacement semantics required")
def test_scan_rejects_media_replaced_during_metadata_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media_path = tmp_path / "track.mp3"
    replacement = tmp_path / "replacement.mp3"
    media_path.write_bytes(b"original")
    replacement.write_bytes(b"replacement-content")

    def replacing_metadata(path: str) -> AudioTagMetadata:
        os.replace(replacement, path)
        return _metadata()

    monkeypatch.setattr(library_media_loader, "read_audio_basic_metadata", replacing_metadata)

    with pytest.raises(LibraryScanTraversalError, match="changed during metadata loading"):
        scan_library_media_files([str(media_path)])


@pytest.mark.skipif(os.name == "nt", reason="POSIX atomic replacement semantics required")
def test_scan_binds_media_identity_from_discovery_to_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media_path = tmp_path / "track.mp3"
    replacement = tmp_path / "replacement.mp3"
    media_path.write_bytes(b"original")
    replacement.write_bytes(b"replacement-content")
    real_discover = library_media_loader.discover_supported_paths

    def replacing_after_discovery(*args: object, **kwargs: object) -> tuple[str, ...]:
        paths = real_discover(*args, **kwargs)  # type: ignore[arg-type]
        os.replace(replacement, media_path)
        return paths

    monkeypatch.setattr(
        library_media_loader, "discover_supported_paths", replacing_after_discovery
    )
    monkeypatch.setattr(
        library_media_loader, "read_audio_basic_metadata", lambda _path: _metadata()
    )

    with pytest.raises(LibraryScanTraversalError, match="changed during metadata loading"):
        scan_library_media_files([str(media_path)])


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink replacement semantics required")
def test_scan_rejects_media_changed_to_symlink_during_metadata_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media_path = tmp_path / "track.mp3"
    external = tmp_path / "external.mp3"
    media_path.write_bytes(b"original")
    external.write_bytes(b"external")

    def replacing_metadata(path: str) -> AudioTagMetadata:
        Path(path).unlink()
        Path(path).symlink_to(external)
        return _metadata()

    monkeypatch.setattr(library_media_loader, "read_audio_basic_metadata", replacing_metadata)

    with pytest.raises(LibraryScanTraversalError, match="linked media files"):
        scan_library_media_files([str(media_path)])


def test_scan_accepts_stable_media_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media_path = tmp_path / "track.mp3"
    media_path.write_bytes(b"stable")
    monkeypatch.setattr(library_media_loader, "read_audio_basic_metadata", lambda _path: _metadata())

    result = scan_library_media_files([str(media_path)])

    assert [item.path for item in result.media_files] == [str(media_path)]
    assert result.report.completed is True
    assert result.report.media_files_loaded == 1


def test_logging_handler_failure_does_not_change_scan_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media_path = tmp_path / "track.mp3"
    media_path.write_bytes(b"stable")

    def failing_metadata(_path: str) -> AudioTagMetadata:
        raise AudioMetadataError("unreadable tags")

    def failing_log(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("logging handler failed")

    monkeypatch.setattr(library_media_loader, "read_audio_basic_metadata", failing_metadata)
    monkeypatch.setattr(library_media_loader.logger, "log", failing_log)

    result = scan_library_media_files([str(media_path)])

    assert len(result.media_files) == 1
    assert result.media_files[0].duration == 0.0
    assert result.report.completed is True


def test_unprintable_path_boundary_error_is_contained() -> None:
    class UnprintablePathError(RuntimeError):
        def __str__(self) -> str:
            raise RuntimeError("render failed")

    class BrokenPath(os.PathLike[str]):
        def __fspath__(self) -> str:
            raise UnprintablePathError()

    result = scan_library_media_paths([BrokenPath()])

    assert result.paths == ()
    assert result.report.completed is True
    assert result.report.invalid_inputs == 1


def test_zero_inode_with_nonzero_device_falls_back_to_path_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "library"
    child = root / "child"
    child.mkdir(parents=True)
    first = root / "one.mp3"
    second = child / "two.mp3"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    real_stat = library_scan_traversal.os.stat
    real_scandir = library_scan_traversal.os.scandir

    class ZeroInodeStat:
        def __init__(self, metadata: os.stat_result) -> None:
            self.st_mode = metadata.st_mode
            self.st_dev = 7
            self.st_ino = 0
            self.st_size = metadata.st_size
            self.st_mtime_ns = metadata.st_mtime_ns
            self.st_ctime_ns = metadata.st_ctime_ns

    def zero_inode_stat(path: object, *, follow_symlinks: bool) -> ZeroInodeStat:
        return ZeroInodeStat(real_stat(path, follow_symlinks=follow_symlinks))

    class ZeroInodeEntry:
        def __init__(self, entry: os.DirEntry[str]) -> None:
            self._entry = entry
            self.path = entry.path

        def is_symlink(self) -> bool:
            return self._entry.is_symlink()

        def stat(self, *, follow_symlinks: bool) -> ZeroInodeStat:
            return ZeroInodeStat(self._entry.stat(follow_symlinks=follow_symlinks))

    class ZeroInodeScandir:
        def __init__(self, path: object) -> None:
            self._entries = real_scandir(path)

        def __enter__(self) -> "ZeroInodeScandir":
            self._entries.__enter__()
            return self

        def __exit__(self, *args: object) -> object:
            return self._entries.__exit__(*args)

        def __iter__(self) -> "ZeroInodeScandir":
            return self

        def __next__(self) -> ZeroInodeEntry:
            return ZeroInodeEntry(next(self._entries))

    monkeypatch.setattr(library_scan_traversal.os, "stat", zero_inode_stat)
    monkeypatch.setattr(library_scan_traversal.os, "scandir", ZeroInodeScandir)

    result = scan_library_media_paths([root])

    assert set(result.paths) == {str(first), str(second)}
    assert result.report.directories_scanned == 2
    assert result.report.duplicate_directories_skipped == 0


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory timestamp semantics required")
def test_scan_rejects_directory_mutated_during_iteration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    (root / "one.mp3").write_bytes(b"one")
    original_scandir = library_scan_traversal.os.scandir

    class MutatingIterator:
        def __init__(self, path: str) -> None:
            self._entries = original_scandir(path)
            self._mutated = False

        def __enter__(self) -> "MutatingIterator":
            self._entries.__enter__()
            return self

        def __exit__(self, *args: object) -> object:
            return self._entries.__exit__(*args)

        def __iter__(self) -> "MutatingIterator":
            return self

        def __next__(self) -> os.DirEntry[str]:
            entry = next(self._entries)
            if not self._mutated:
                self._mutated = True
                (root / "added.txt").write_bytes(b"changed")
            return entry

    monkeypatch.setattr(
        library_scan_traversal.os,
        "scandir",
        lambda path: MutatingIterator(os.fspath(path)),
    )

    with pytest.raises(LibraryScanTraversalError, match="changed during traversal"):
        scan_library_media_files([str(root)])
