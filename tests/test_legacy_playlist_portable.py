"""Exercise portable storage on every host without changing public error contracts.

Edge cases: missing leaf, disappearance on open or commit, denied inspection,
non-regular targets and hard links. These tests do not qualify Windows durability.
"""
from __future__ import annotations

from pathlib import Path
import os
from typing import IO

import pytest

from src.model import playlist_legacy_store as store


@pytest.fixture(autouse=True)
def portable_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "_supports_descriptor_boundary", lambda: False)


def test_portable_missing_leaf_is_a_read_error(tmp_path: Path) -> None:
    with pytest.raises(store.LegacyPlaylistReadError, match="does not exist"):
        store.load_playlist_payload(tmp_path / "absent.json")


def test_portable_disappearance_on_open_is_a_read_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "vanishing.json"
    target.write_bytes(b"{}")
    original = Path.open

    def failed_open(path: Path, *args: object, **kwargs: object) -> IO[bytes]:
        if path == target:
            raise FileNotFoundError("vanished between validation and open")
        return original(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "open", failed_open)
    with pytest.raises(store.LegacyPlaylistReadError, match="does not exist"):
        store.load_playlist_payload(target)


def test_portable_missing_committed_target_is_a_write_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A deliberately broken writer models disappearance before post-write inspection.
    monkeypatch.setattr(store, "write_bytes_atomic_durable", lambda *_args: True)
    with pytest.raises(store.LegacyPlaylistWriteError):
        store.save_playlist_payload(tmp_path, "lost", b"{}")


def test_portable_directory_is_still_a_path_error(tmp_path: Path) -> None:
    directory = tmp_path / "not-a-file.json"
    directory.mkdir()
    with pytest.raises(store.LegacyPlaylistPathError, match="regular file"):
        store.load_playlist_payload(directory)


def test_portable_denied_inspection_preserves_the_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "denied.json"
    target.write_bytes(b"{}")
    original = Path.lstat

    def denied(path: Path) -> os.stat_result:
        if path == target:
            raise PermissionError("inspection denied")
        return original(path)

    with monkeypatch.context() as context:
        context.setattr(Path, "lstat", denied)
        with pytest.raises(store.LegacyPlaylistPathError) as caught:
            store.load_playlist_payload(target)
        assert isinstance(caught.value.__cause__, PermissionError)


def test_portable_regular_file_roundtrip(tmp_path: Path) -> None:
    saved = store.save_playlist_payload(tmp_path, "roundtrip", b'{"id":"roundtrip"}')
    assert store.load_playlist_payload(saved.path) == b'{"id":"roundtrip"}'


def test_portable_hard_link_is_still_rejected(tmp_path: Path) -> None:
    target = tmp_path / "original.json"
    linked = tmp_path / "alias.json"
    target.write_bytes(b"{}")
    os.link(target, linked)
    with pytest.raises(store.LegacyPlaylistPathError, match="hard-linked"):
        store.load_playlist_payload(linked)
