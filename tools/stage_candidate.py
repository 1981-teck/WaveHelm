"""Transactional LOCAL snapshot staging; never updates or publishes a user branch.

Prepare tests a sealed copy before its first commit. Commit rechecks the receipt,
logs, artifacts and source bytes. Cancel retains diagnostics without deleting
caller data. This tool does not implement the still-open Publish/remote Verify.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Iterator

sys.dont_write_bytecode = True
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.local_preflight import preflight, validate_local_receipt
from tools.staging_integrity import (StagingError, copy_sealed, inventory, inventory_digest,
                                     read_record, regular_path, seal_file, trusted_directory, write_record)

STATES = frozenset({"PREPARING", "FAILED", "PREPARED_LOCAL", "COMMITTING_LOCAL",
                    "COMMITTED_LOCAL", "CANCELLED"})
MAX_AGE_SECONDS = 24 * 60 * 60


@contextmanager
def operation_lease(session: Path) -> Iterator[None]:
    """Nonwaiting OS file lease for one cold CLI operation; auto-released on exit.

No in-process application mutex spans I/O. The persistent empty lease file is
not a stale-owner marker and must never be unlinked while a command is active.
"""
    path = session / "operation.lock"
    if path.exists() or path.is_symlink():
        regular_path(path)
    with path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise StagingError("another process owns this session") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def git(root: Path, *args: str, allowed: tuple[int, ...] = (0,)) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
               GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")
    result = subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(root), *args],
                            capture_output=True, env=env, text=True, encoding="utf-8",
                            errors="strict", timeout=90, check=False)
    if result.returncode not in allowed:
        raise StagingError(f"git {' '.join(args[:2])} failed ({result.returncode}): {result.stderr}")
    return result.stdout.strip()


def _save(session: Path, record: dict[str, object], state: str) -> dict[str, object]:
    if state not in STATES:
        raise StagingError("unknown session state")
    record.update(state=state, updated_unix=time.time(), release_readiness="NOT_VERIFIED")
    write_record(session / "state.json", record)
    return record


def _load(session: Path) -> dict[str, object]:
    trusted_directory(session)
    raw = read_record(session / "state.json")
    if raw.get("schema") != "wavehelm-local-staging-v1" or raw.get("state") not in STATES:
        raise StagingError("invalid local staging state")
    if raw.get("session") != str(session.absolute()):
        raise StagingError("session moved or state belongs to a different path")
    if raw.get("release_readiness") != "NOT_VERIFIED":
        raise StagingError("a local receipt cannot assert release readiness")
    return raw


def prepare(source: Path, session: Path) -> dict[str, object]:
    """Refuse existing/overlapping sessions; failures retain explicit diagnostics."""
    source = trusted_directory(source)
    if ".." in session.parts:
        raise StagingError("parent traversal is not an accepted session spelling")
    session = session.absolute()
    trusted_directory(session.parent)
    if session.is_relative_to(source) or source.is_relative_to(session):
        raise StagingError("source and session must be disjoint")
    session.mkdir(exist_ok=False)
    record: dict[str, object] = {"schema": "wavehelm-local-staging-v1", "session": str(session),
                                "source": str(source), "created_unix": time.time()}
    with operation_lease(session):
        _save(session, record, "PREPARING")
        try:
            seals = inventory(source)
            digest = inventory_digest(seals)
            record["source_digest"] = digest
            stage = session / "source"
            copy_sealed(source, stage, seals)
            git(stage, "init", "-q")
            git(stage, "config", "core.autocrlf", "false")
            git(stage, "config", "core.hooksPath", str(session / "no-hooks"))
            git(stage, "config", "commit.gpgSign", "false")
            git(stage, "config", "user.name", "WaveHelm local snapshot")
            git(stage, "config", "user.email", "local-snapshot@invalid")
            git(stage, "add", "--all")
            result = preflight(stage, session / "evidence", digest)
            if inventory(source) != seals:
                raise StagingError("caller source changed during preparation")
            record["receipt_sha256"] = seal_file(session / "evidence/preflight.json", "preflight.json").sha256
            record["prepared_unix"] = time.time()
            state = "PREPARED_LOCAL" if result["verdict"] == "PASS" else "FAILED"
            return _save(session, record, state)
        except (StagingError, OSError, UnicodeError, subprocess.SubprocessError) as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
            _save(session, record, "FAILED")
            raise


def _validate(session: Path, record: dict[str, object]) -> str:
    digest = record.get("source_digest")
    timestamp = record.get("prepared_unix")
    if not isinstance(digest, str) or len(digest) != 64 or type(timestamp) not in {int, float}:
        raise StagingError("invalid preparation identity")
    if not 0 <= time.time() - timestamp <= MAX_AGE_SECONDS:
        raise StagingError("preparation expired or timestamp is in the future")
    if inventory_digest(inventory(session / "source")) != digest:
        raise StagingError("staged source changed after preparation")
    if seal_file(session / "evidence/preflight.json", "preflight.json").sha256 != record.get("receipt_sha256"):
        raise StagingError("preflight receipt changed")
    validate_local_receipt(session, digest)
    return digest


def commit(session: Path) -> dict[str, object]:
    """Create/recover one local snapshot commit, never a canonical branch update."""
    session = trusted_directory(session)
    with operation_lease(session):
        record = _load(session)
        if record["state"] not in {"PREPARED_LOCAL", "COMMITTING_LOCAL", "COMMITTED_LOCAL"}:
            raise StagingError("only a successful local preparation can be committed")
        digest = _validate(session, record)
        stage = session / "source"
        if git(stage, "remote"):
            raise StagingError("staging repository must not have a remote")
        message = f"WaveHelm LOCAL snapshot {digest}; not release qualification"
        head = git(stage, "rev-parse", "--verify", "--quiet", "HEAD", allowed=(0, 1))
        if not head:
            _save(session, record, "COMMITTING_LOCAL")
            git(stage, "add", "--all")
            git(stage, "commit", "-q", "-m", message)
        if git(stage, "rev-list", "--count", "HEAD") != "1":
            raise StagingError("a local snapshot must contain exactly one root commit")
        if git(stage, "log", "-1", "--format=%s") != message:
            raise StagingError("unexpected commit in local staging repository")
        if git(stage, "status", "--porcelain", "--untracked-files=all", "--ignored"):
            raise StagingError("staging repository is not clean")
        if inventory_digest(inventory(stage)) != digest:
            raise StagingError("source changed during commit")
        record["local_commit"] = git(stage, "rev-parse", "HEAD")
        record["local_tree"] = git(stage, "rev-parse", "HEAD^{tree}")
        return _save(session, record, "COMMITTED_LOCAL")


def cancel(session: Path) -> dict[str, object]:
    """Cancel without deleting any source, build artifact, log or existing commit."""
    session = trusted_directory(session)
    with operation_lease(session):
        record = _load(session)
        return _save(session, record, "CANCELLED")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("Prepare", "Commit", "Cancel", "Status"))
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "Prepare":
            if args.source is None:
                raise StagingError("Prepare requires --source")
            record = prepare(args.source, args.session)
        elif args.command == "Commit":
            record = commit(args.session)
        elif args.command == "Cancel":
            record = cancel(args.session)
        else:
            record = _load(args.session)
    except (StagingError, OSError, UnicodeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"state": "ERROR", "error": f"{type(exc).__name__}: {exc}"}))
        return 1
    print(json.dumps(record, indent=2))
    return 0 if record["state"] not in {"FAILED", "PREPARING", "COMMITTING_LOCAL"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
