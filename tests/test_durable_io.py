from __future__ import annotations

import os
from pathlib import Path

import pytest

import src.utils.durable_io as durable_io


def test_durable_replace_dispatches_to_os_replace_on_posix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    calls: list[tuple[object, object]] = []

    def fake_replace(raw_source: object, raw_target: object) -> None:
        calls.append((raw_source, raw_target))

    monkeypatch.setattr(durable_io.os, "name", "posix")
    monkeypatch.setattr(durable_io.os, "replace", fake_replace)
    durable_io.durable_replace(source, target)

    assert calls == [(source, target)]


def test_durable_replace_dispatches_to_windows_write_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    calls: list[tuple[Path, Path]] = []

    def fake_windows_replace(raw_source: Path, raw_target: Path) -> None:
        calls.append((raw_source, raw_target))

    monkeypatch.setattr(durable_io.os, "name", "nt")
    monkeypatch.setattr(durable_io, "_replace_windows_write_through", fake_windows_replace)
    durable_io.durable_replace(source, target)

    assert calls == [(source, target)]


def test_sync_parent_directory_calls_fsync_on_posix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, int]] = []

    monkeypatch.setattr(durable_io.os, "name", "posix")
    monkeypatch.setattr(durable_io.os, "open", lambda path, flags: 77)
    monkeypatch.setattr(durable_io.os, "fsync", lambda fd: calls.append(("fsync", fd)))
    monkeypatch.setattr(durable_io.os, "close", lambda fd: calls.append(("close", fd)))

    durable_io.sync_parent_directory(tmp_path)

    assert calls == [("fsync", 77), ("close", 77)]


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows replacement")
def test_windows_native_durable_replace_replaces_existing_file(tmp_path: Path) -> None:
    source = tmp_path / "source.tmp"
    target = tmp_path / "target.json"
    source.write_bytes(b"new")
    target.write_bytes(b"old")

    durable_io.durable_replace(source, target)

    assert target.read_bytes() == b"new"
    assert not source.exists()


def test_write_bytes_atomic_durable_preserves_old_target_on_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "state.json"
    target.write_bytes(b"old")

    def fail_replace(_source: Path, _target: Path) -> None:
        raise PermissionError("replace blocked")

    monkeypatch.setattr(durable_io, "durable_replace", fail_replace)

    with pytest.raises(PermissionError, match="replace blocked"):
        durable_io.write_bytes_atomic_durable(target, b"new")

    assert target.read_bytes() == b"old"
    assert list(tmp_path.glob(".state.json.*.tmp")) == []


def test_write_bytes_atomic_durable_reports_post_commit_sync_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "state.json"
    target.write_bytes(b"old")

    def fail_sync(_parent: Path) -> None:
        raise OSError("sync failed")

    monkeypatch.setattr(durable_io, "sync_parent_directory", fail_sync)

    assert durable_io.write_bytes_atomic_durable(target, b"new") is False
    assert target.read_bytes() == b"new"
    assert list(tmp_path.glob(".state.json.*.tmp")) == []
