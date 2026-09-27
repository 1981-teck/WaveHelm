from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import threading

import pytest

from src.model import playlist_legacy_store as store
from src.model.playlist import (
    LegacyPlaylistContractError,
    LegacyPlaylistPathError,
    LegacyPlaylistReadError,
    LegacyPlaylistWriteError,
    Playlist,
)
from src.model.playlist_legacy_contract import MAX_PLAYLIST_BYTES
from src.model.song import Song
from src.utils.durable_io import DurabilityStatus


def _playlist(identifier: str = "legacy") -> Playlist:
    timestamp = datetime(2026, 1, 1)
    song = Song(
        id="song",
        title="Title",
        artist="Artist",
        album="Album",
        duration=timedelta(seconds=5),
        file_path="/music/song.mp3",
        tags=["tag"],
    )
    return Playlist(
        id=identifier,
        name="Legacy",
        songs=[song],
        creation_date=timestamp,
        last_modified=timestamp,
    )


def test_untrusted_identifier_never_escapes_assigned_directory(tmp_path: Path) -> None:
    assigned = tmp_path / "assigned"
    playlist = _playlist("../../escaped")

    result = playlist.save_to_file(assigned)

    assert result.path.parent == assigned.resolve()
    assert result.path.exists()
    assert not (tmp_path / "escaped.json").exists()
    assert list(assigned.glob("*.json")) == [result.path]


def test_linked_storage_root_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable")

    with pytest.raises(LegacyPlaylistPathError, match="link"):
        _playlist().save_to_file(linked)
    assert not list(target.iterdir())


def test_linked_ancestor_is_rejected_before_child_creation(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable")

    child = linked / "new-child"
    with pytest.raises(LegacyPlaylistPathError, match="linked path component"):
        _playlist().save_to_file(child)
    assert not (target / "new-child").exists()


def test_existing_linked_or_hard_linked_target_is_rejected(tmp_path: Path) -> None:
    playlist = _playlist()
    filename = playlist.storage_filename(playlist.id)
    outside = tmp_path / "outside.json"
    outside.write_text("outside", encoding="utf-8")
    target = tmp_path / filename

    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("file symlink creation is unavailable")
    with pytest.raises(LegacyPlaylistPathError):
        playlist.save_to_file(tmp_path)
    assert outside.read_text(encoding="utf-8") == "outside"

    target.unlink()
    try:
        os.link(outside, target)
    except OSError:
        pytest.skip("hard-link creation is unavailable")
    with pytest.raises(LegacyPlaylistPathError, match="hard-linked"):
        playlist.save_to_file(tmp_path)
    assert outside.read_text(encoding="utf-8") == "outside"


def test_load_rejects_linked_hard_linked_and_oversized_files(tmp_path: Path) -> None:
    playlist = _playlist()
    result = playlist.save_to_file(tmp_path)
    linked = tmp_path / "linked.json"
    try:
        linked.symlink_to(result.path)
    except OSError:
        pytest.skip("file symlink creation is unavailable")
    with pytest.raises((LegacyPlaylistPathError, LegacyPlaylistReadError)):
        Playlist.load_from_file(linked)

    linked.unlink()
    hard = tmp_path / "hard.json"
    try:
        os.link(result.path, hard)
    except OSError:
        pytest.skip("hard-link creation is unavailable")
    with pytest.raises(LegacyPlaylistPathError, match="hard-linked"):
        Playlist.load_from_file(hard)

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"x" * (MAX_PLAYLIST_BYTES + 1))
    with pytest.raises(LegacyPlaylistReadError, match="byte limit"):
        Playlist.load_from_file(oversized)


def test_load_rejects_duplicate_keys_nonfinite_numbers_and_unknown_fields(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        '{"id":"one","id":"two","name":"N","songs":[],"creation_date":'
        '"2026-01-01T00:00:00","last_modified":"2026-01-01T00:00:00",'
        '"description":null,"cover_art":null}',
        encoding="utf-8",
    )
    with pytest.raises(LegacyPlaylistContractError, match="Duplicate JSON key"):
        Playlist.load_from_file(duplicate)

    nonfinite = _playlist().to_dict()
    songs = list(nonfinite["songs"])  # type: ignore[arg-type]
    songs[0] = dict(songs[0])
    songs[0]["duration"] = float("inf")
    nonfinite["songs"] = songs
    path = tmp_path / "nonfinite.json"
    path.write_text(json.dumps(nonfinite), encoding="utf-8")
    with pytest.raises(LegacyPlaylistContractError, match="Non-standard|finite"):
        Playlist.load_from_file(path)

    unknown = _playlist().to_dict()
    unknown["unknown"] = "value"
    path.write_text(json.dumps(unknown), encoding="utf-8")
    with pytest.raises(LegacyPlaylistContractError, match="unexpected"):
        Playlist.load_from_file(path)


def test_atomic_replace_failure_preserves_previous_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    playlist = _playlist()
    first = playlist.save_to_file(tmp_path)
    original = first.path.read_bytes()
    playlist.name = "Updated"

    if store._supports_descriptor_boundary():
        monkeypatch.setattr(store.os, "replace", lambda *_args, **_kwargs: _raise_os_error())
    else:
        monkeypatch.setattr(store, "write_bytes_atomic_durable", lambda *_args: _raise_os_error())

    with pytest.raises(LegacyPlaylistWriteError):
        playlist.save_to_file(tmp_path)
    assert first.path.read_bytes() == original
    assert not list(tmp_path.glob("*.tmp"))


def test_directory_sync_degradation_is_reported_without_false_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    playlist = _playlist()
    if store._supports_descriptor_boundary():
        monkeypatch.setattr(store, "_sync_directory_descriptor", lambda _fd: _raise_os_error())
    else:
        original_writer = store.write_bytes_atomic_durable

        def degraded_writer(path: Path, payload: bytes) -> bool:
            assert original_writer(path, payload) in {True, False}
            return False

        monkeypatch.setattr(store, "write_bytes_atomic_durable", degraded_writer)

    result = playlist.save_to_file(tmp_path)
    assert result.durability is DurabilityStatus.COMMITTED_WITHOUT_DIRECTORY_SYNC
    assert Playlist.load_from_file(result.path).to_dict() == playlist.to_dict()


def test_invalid_pathlike_failure_is_converted_at_the_boundary() -> None:
    class BrokenPath:
        def __fspath__(self) -> str:
            raise RuntimeError("do not escape")

    with pytest.raises(LegacyPlaylistPathError, match="path-like"):
        _playlist().save_to_file(BrokenPath())  # type: ignore[arg-type]


def test_same_instance_concurrent_saves_are_complete_and_parseable(tmp_path: Path) -> None:
    playlist = _playlist()
    errors: list[Exception] = []
    paths: list[Path] = []
    lock = threading.Lock()

    def worker() -> None:
        try:
            result = playlist.save_to_file(tmp_path)
            with lock:
                paths.append(result.path)
        except Exception as error:  # explicit thread worker boundary
            with lock:
                errors.append(error)

    threads = [threading.Thread(target=worker) for _ in range(24)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    assert all(not thread.is_alive() for thread in threads)
    assert len(paths) == 24
    assert len(set(paths)) == 1
    assert Playlist.load_from_file(paths[0]).to_dict() == playlist.to_dict()


def test_read_stability_failure_is_not_returned_as_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    result = _playlist().save_to_file(tmp_path)

    def fail_stability(_before: os.stat_result, _after: os.stat_result) -> None:
        raise LegacyPlaylistReadError("playlist file changed while it was being read")

    monkeypatch.setattr(store, "_verify_stable_file", fail_stability)
    with pytest.raises(LegacyPlaylistReadError, match="changed"):
        Playlist.load_from_file(result.path)


def _raise_os_error() -> None:
    raise OSError("injected filesystem failure")


def test_legacy_raw_filename_remains_loadable_and_resaves_without_overwrite(tmp_path: Path) -> None:
    playlist = _playlist("legacy-id")
    legacy_path = tmp_path / "legacy-id.json"
    legacy_bytes = json.dumps(playlist.to_dict()).encode("utf-8")
    legacy_path.write_bytes(legacy_bytes)

    loaded = Playlist.load_from_file(legacy_path)
    result = loaded.save_to_file(tmp_path)

    assert result.path != legacy_path
    assert legacy_path.read_bytes() == legacy_bytes
    assert Playlist.load_from_file(result.path).to_dict() == loaded.to_dict()


def test_invalid_identifier_is_rejected_before_directory_creation(tmp_path: Path) -> None:
    directory = tmp_path / "must-not-exist"
    with pytest.raises(LegacyPlaylistContractError):
        store.save_playlist_payload(directory, "\x00", b"{}")
    assert not directory.exists()


def test_storage_rejects_invalid_payload_contract_before_io(tmp_path: Path) -> None:
    with pytest.raises(LegacyPlaylistWriteError, match="bytes"):
        store.save_playlist_payload(tmp_path / "one", "id", "{}")  # type: ignore[arg-type]
    assert not (tmp_path / "one").exists()

    with pytest.raises(LegacyPlaylistWriteError, match="byte limit"):
        store.save_playlist_payload(tmp_path / "two", "id", b"x" * (MAX_PLAYLIST_BYTES + 1))
    assert not (tmp_path / "two").exists()


def test_portable_fallback_round_trip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(store, "_supports_descriptor_boundary", lambda: False)
    playlist = _playlist("portable")

    result = playlist.save_to_file(tmp_path / "nested" / "playlists")
    restored = Playlist.load_from_file(result.path)

    assert result.path.is_file()
    assert restored.to_dict() == playlist.to_dict()


def test_non_directory_root_and_missing_file_fail_with_typed_errors(tmp_path: Path) -> None:
    root_file = tmp_path / "root-file"
    root_file.write_text("not a directory", encoding="utf-8")
    with pytest.raises(LegacyPlaylistPathError):
        _playlist().save_to_file(root_file)

    with pytest.raises(LegacyPlaylistReadError, match="does not exist"):
        Playlist.load_from_file(tmp_path / "missing.json")


def test_temporary_filename_allocation_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    if not store._supports_descriptor_boundary():
        pytest.skip("descriptor-bound temporary allocation is unavailable")
    filename = _playlist().storage_filename("legacy")
    candidate = f".{filename}.{'0' * 24}.tmp"
    (tmp_path / candidate).write_bytes(b"occupied")
    monkeypatch.setattr(store.secrets, "token_hex", lambda _size: "0" * 24)
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(LegacyPlaylistWriteError, match="bounded temporary"):
            store._create_temporary_file(descriptor, filename)
    finally:
        os.close(descriptor)


def test_caller_sequences_that_raise_are_converted_to_contract_errors() -> None:
    from collections.abc import Sequence

    class BrokenSongs(Sequence[Song]):
        def __len__(self) -> int:
            return 1

        def __getitem__(self, _index: int) -> Song:
            raise RuntimeError("song provider failed")

    timestamp = datetime(2026, 1, 1)
    with pytest.raises(LegacyPlaylistContractError, match="enumerate playlist songs"):
        Playlist(
            id="broken",
            name="Broken",
            songs=BrokenSongs(),  # type: ignore[arg-type]
            creation_date=timestamp,
            last_modified=timestamp,
        )

    class BrokenTags(Sequence[str]):
        def __len__(self) -> int:
            return 1

        def __getitem__(self, _index: int) -> str:
            raise RuntimeError("tag provider failed")

    song = _playlist().songs[0]
    song.tags = BrokenTags()  # type: ignore[assignment]
    with pytest.raises(LegacyPlaylistContractError, match="enumerate song tags"):
        Playlist(
            id="broken-tags",
            name="Broken tags",
            songs=[song],
            creation_date=timestamp,
            last_modified=timestamp,
        )


def test_exact_runtime_types_and_timezone_mode_are_enforced() -> None:
    class SongSubclass(Song):
        pass

    source = _playlist().songs[0]
    subclass = SongSubclass(**source.__dict__)
    timestamp = datetime(2026, 1, 1)
    with pytest.raises(LegacyPlaylistContractError, match="Song instances"):
        Playlist(
            id="subclass",
            name="Subclass",
            songs=[subclass],
            creation_date=timestamp,
            last_modified=timestamp,
        )

    with pytest.raises(LegacyPlaylistContractError, match="timezone mode"):
        Playlist(
            id="timezone",
            name="Timezone",
            creation_date=timestamp,
            last_modified=datetime(2026, 1, 1, tzinfo=datetime.now().astimezone().tzinfo),
        )


def test_directory_replacement_before_commit_fails_without_escape(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    if not store._supports_descriptor_boundary():
        pytest.skip("descriptor-bound directory identity is unavailable")
    root = tmp_path / "root"
    root.mkdir()
    displaced = tmp_path / "displaced"
    original_verify = store._verify_directory_path_identity
    calls = 0

    def replace_before_commit(path: Path, identity: tuple[int, int]) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            path.rename(displaced)
            path.mkdir()
        original_verify(path, identity)

    monkeypatch.setattr(store, "_verify_directory_path_identity", replace_before_commit)
    with pytest.raises(LegacyPlaylistPathError, match="identity changed"):
        _playlist().save_to_file(root)
    assert not list(root.glob("*.json"))
    assert not list(displaced.glob("*.json"))
    assert not list(displaced.glob("*.tmp"))


def test_real_in_place_change_during_read_is_detected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    if not store._supports_descriptor_boundary():
        pytest.skip("descriptor-bound read stability is unavailable")
    result = _playlist().save_to_file(tmp_path)
    original_read = store.os.read
    changed = False

    def mutate_after_read(descriptor: int, amount: int) -> bytes:
        nonlocal changed
        chunk = original_read(descriptor, amount)
        if not changed:
            changed = True
            with result.path.open("ab") as handle:
                handle.write(b" ")
                handle.flush()
                os.fsync(handle.fileno())
        return chunk

    monkeypatch.setattr(store.os, "read", mutate_after_read)
    with pytest.raises(LegacyPlaylistReadError, match="changed"):
        Playlist.load_from_file(result.path)
