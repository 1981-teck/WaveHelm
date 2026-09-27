"""Cross-platform identity regressions using documented Windows DirEntry metadata.

Only the unit fixture clears cached file identifiers. Real os.stat, real paths and
existing identity comparisons remain active. Native Windows execution is separate.
Edge cases: nested directories, stable media, denied/disappearing entries, changed
media and linked entries. No partial paths may escape a failed scan.
"""
from __future__ import annotations

from collections.abc import Iterator
import os
from pathlib import Path

import pytest

from src.controller import library_media_loader as loader
from src.controller import library_scan_traversal as traversal
from src.controller.library_scan import LibraryScanTraversalError
from src.utils.media_metadata import AudioTagMetadata


def _zero_cached_identifiers(info: os.stat_result) -> os.stat_result:
    values = list(info)
    values[1:4] = [0, 0, 0]
    return os.stat_result(values, {name: getattr(info, name) for name in
                                  ("st_atime_ns", "st_mtime_ns", "st_ctime_ns")})


class _CachedEntry:
    """Windows-style cached stat fixture, not an OS/ABI replacement."""

    def __init__(self, entry: os.DirEntry[str]) -> None:
        self._entry = entry
        self.path = entry.path

    def is_symlink(self) -> bool:
        return self._entry.is_symlink()

    def stat(self, *, follow_symlinks: bool) -> os.stat_result:
        return _zero_cached_identifiers(self._entry.stat(follow_symlinks=follow_symlinks))


class _CachedEntries:
    def __init__(self, entries: os.ScandirIterator[str]) -> None:
        self._entries = entries

    def __enter__(self) -> _CachedEntries:
        return self

    def __exit__(self, *_args: object) -> None:
        self._entries.close()

    def __iter__(self) -> Iterator[_CachedEntry]:
        for entry in self._entries:
            yield _CachedEntry(entry)


def _cached_scandir(monkeypatch: pytest.MonkeyPatch) -> None:
    original = traversal.os.scandir
    monkeypatch.setattr(traversal.os, "scandir", lambda p: _CachedEntries(original(p)))


def _tags(_path: str) -> AudioTagMetadata:
    return AudioTagMetadata(title="Track", artist="Artist", album="Album", duration=1.0)


def test_nested_directories_do_not_mix_cached_and_fresh_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = tmp_path / "nested"
    child.mkdir()
    track = child / "track.mp3"
    track.write_bytes(b"unit fixture")
    _cached_scandir(monkeypatch)
    result = loader.scan_library_media_paths([str(tmp_path)])
    assert result.paths == (str(track),)
    assert result.report.completed is True
    assert result.report.directories_scanned == 2


def test_stable_media_uses_one_identity_source_through_metadata_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    track = tmp_path / "track.mp3"
    track.write_bytes(b"unit fixture")
    _cached_scandir(monkeypatch)
    monkeypatch.setattr(loader, "read_audio_basic_metadata", _tags)
    result = loader.scan_library_media_files([str(tmp_path)])
    assert [item.path for item in result.media_files] == [str(track)]
    assert result.report.media_files_loaded == 1
    assert result.report.completed is True


@pytest.mark.parametrize("error_type", [PermissionError, FileNotFoundError])
def test_fresh_descendant_stat_failure_aborts_the_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error_type: type[OSError],
) -> None:
    track = tmp_path / "track.mp3"
    track.write_bytes(b"unit fixture")
    original = traversal.os.stat
    _cached_scandir(monkeypatch)

    def failed_stat(path: object, *args: object, **kwargs: object) -> os.stat_result:
        if str(path) == str(track) and kwargs.get("follow_symlinks") is False:
            raise error_type("entry no longer readable")
        return original(path, *args, **kwargs)  # type: ignore[arg-type]

    with monkeypatch.context() as context:
        context.setattr(traversal.os, "stat", failed_stat)
        with pytest.raises(LibraryScanTraversalError, match="entry no longer readable"):
            loader.scan_library_media_paths([str(tmp_path)])


def test_media_modification_is_still_rejected_with_uncached_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    track = tmp_path / "track.mp3"
    track.write_bytes(b"old")
    _cached_scandir(monkeypatch)

    def changed(path: str) -> AudioTagMetadata:
        Path(path).write_bytes(b"changed and larger")
        return _tags(path)

    monkeypatch.setattr(loader, "read_audio_basic_metadata", changed)
    with pytest.raises(LibraryScanTraversalError, match="changed during metadata loading"):
        loader.scan_library_media_files([str(tmp_path)])


def test_fresh_stat_keeps_linked_descendants_excluded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "track.mp3").write_bytes(b"unit fixture")
    _cached_scandir(monkeypatch)
    monkeypatch.setattr(_CachedEntry, "is_symlink", lambda _entry: True)
    result = loader.scan_library_media_paths([str(tmp_path)])
    assert result.paths == ()
    assert result.report.completed is True
    assert result.report.linked_entries_skipped == 1


def test_real_nested_directory_metadata_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = tmp_path / "nested"
    child.mkdir()
    first, second = tmp_path / "one.mp3", child / "two.mp3"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    monkeypatch.setattr(loader, "read_audio_basic_metadata", _tags)
    result = loader.scan_library_media_files([str(tmp_path)])
    assert {item.path for item in result.media_files} == {str(first), str(second)}
    assert result.report.completed is True
    assert result.report.media_files_loaded == 2
