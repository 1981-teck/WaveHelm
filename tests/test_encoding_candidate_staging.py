"""UTF-8 staging fixtures; synthetic PASS receipts are not release qualification.

Edge cases: non-ASCII data/paths, malformed UTF-8, altered sealed artifacts and
nonzero child exits. Only local disposable files/processes and test Git repos run.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import sys

import pytest

from tests import test_candidate_staging as fixtures
from tools import local_preflight as gate
from tools import stage_candidate as stage
from tools import staging_integrity as integrity
from tools.staging_integrity import StagingError

UNICODE_TEXT = ("caf\u00e9 \u20ac", "\u97f3\u697d \U0001f3b5", "e\u0301 \u041c\u0443\u0437\u044b\u043a\u0430")
UNICODE_IDS = ("accent-euro", "cjk-supplementary", "combining-cyrillic")


def test_candidate_fixture_text_calls_select_utf8() -> None:
    """Protect the thirteen reviewed sites without removing their tested operations."""
    path = Path(__file__).with_name("test_candidate_staging.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute)
             and n.func.attr in ("read_text", "write_text")]
    assert len(calls) == 13
    for call in calls:
        encodings = [k.value for k in call.keywords if k.arg == "encoding"]
        assert len(encodings) == 1, f"line {call.lineno}: missing explicit encoding"
        assert isinstance(encodings[0], ast.Constant) and encodings[0].value == "utf-8"


@pytest.mark.parametrize("text", UNICODE_TEXT, ids=UNICODE_IDS)
def test_unicode_fixture_names_and_sealed_bytes_survive_copy(tmp_path: Path, text: str) -> None:
    """Seals describe encoded byte counts, not the number of Unicode characters."""
    root = tmp_path / "input"
    root.mkdir()
    name = f"{text}.txt"
    payload = text + "\n"
    path = root / name
    assert path.write_text(payload, encoding="utf-8", newline="\n") == len(payload)
    expected_bytes = path.read_bytes()
    assert expected_bytes == payload.encode("utf-8")
    before = integrity.inventory(root)
    assert before[0].size == len(expected_bytes)
    assert before[0].sha256 == hashlib.sha256(expected_bytes).hexdigest()
    target = tmp_path / "copied"
    integrity.copy_sealed(root, target, before)
    assert integrity.inventory(target) == before == integrity.inventory(root)
    assert (target / name).read_bytes() == expected_bytes
    assert (target / name).read_text(encoding="utf-8") == payload


@pytest.mark.parametrize("text", UNICODE_TEXT, ids=UNICODE_IDS)
def test_unescaped_unicode_json_fixture_uses_strict_record_reader(tmp_path: Path, text: str) -> None:
    """Real record decoding must preserve Unicode, including combining characters."""
    path = tmp_path / "state.json"
    value = {"path": f"C:/users/{text}/source", "qualification": "UNIT_FIXTURE_ONLY"}
    serialized = json.dumps(value, ensure_ascii=False)
    assert path.write_text(serialized, encoding="utf-8") == len(serialized)
    assert text.encode("utf-8") in path.read_bytes()
    assert integrity.read_record(path) == value
    assert json.loads(path.read_text(encoding="utf-8")) == value


@pytest.mark.parametrize("text", UNICODE_TEXT, ids=UNICODE_IDS)
def test_unicode_local_staging_preserves_caller_and_no_overwrite(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    """Synthetic preflight authorizes a unit state transition, never a real release."""
    source = tmp_path / "input"
    source.mkdir()
    assert (source / "title.txt").write_text(text, encoding="utf-8") == len(text)
    before = integrity.inventory(source)
    monkeypatch.setattr(stage, "preflight", fixtures.synthetic_receipt)
    session = tmp_path / f"session-{text}"
    prepared = stage.prepare(source, session)
    assert prepared["state"] == "PREPARED_LOCAL"
    assert prepared["release_readiness"] == "NOT_VERIFIED"
    state = (session / "state.json").read_bytes()
    with pytest.raises(FileExistsError):
        stage.prepare(source, session)
    assert (session / "state.json").read_bytes() == state
    first = stage.commit(session)
    assert stage.commit(session)["local_commit"] == first["local_commit"]
    assert first["state"] == "COMMITTED_LOCAL" and first["release_readiness"] == "NOT_VERIFIED"
    assert (session / "source/title.txt").read_text(encoding="utf-8") == text
    assert integrity.inventory(source) == before and not (source / ".git").exists()


@pytest.mark.parametrize("relative", ("source/title.txt", "evidence/preflight.json",
                                      "evidence/full-tests.log", "dist/fixture.whl"))
def test_unicode_tamper_keeps_commit_rejection(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: str) -> None:
    """Text portability never permits mutated source, receipt, log or artifact bytes."""
    source = tmp_path / "input"
    source.mkdir()
    assert (source / "title.txt").write_text("caf\u00e9", encoding="utf-8") == 4
    before = integrity.inventory(source)
    monkeypatch.setattr(stage, "preflight", fixtures.synthetic_receipt)
    session = tmp_path / "session"
    assert stage.prepare(source, session)["state"] == "PREPARED_LOCAL"
    path = session / relative
    altered = path.read_bytes() + "\n\u6539\U0001f3b5\n".encode("utf-8")
    assert path.write_bytes(altered) == len(altered)
    with pytest.raises(StagingError):
        stage.commit(session)
    assert stage.git(session / "source", "rev-parse", "--verify", "--quiet", "HEAD",
                     allowed=(0, 1)) == ""
    assert integrity.inventory(source) == before


@pytest.mark.parametrize("text", UNICODE_TEXT, ids=UNICODE_IDS)
def test_real_utf8_child_output_keeps_nonzero_status(tmp_path: Path, text: str) -> None:
    """Emit known UTF-8 bytes, not an assumption about arbitrary native command output."""
    payload = text.encode("utf-8")
    code = f"import sys;sys.stdout.buffer.write({payload!r});sys.exit(7)"
    result = gate.run_check("unicode", (sys.executable, "-I", "-B", "-c", code),
                            tmp_path, tmp_path)
    assert result.exit_code == 7
    path = tmp_path / "unicode.log"
    assert path.read_bytes() == payload and path.read_text(encoding="utf-8") == text
    assert result.log == integrity.seal_file(path, path.name)
    assert result.log.size == len(payload)
    assert result.log.sha256 == hashlib.sha256(payload).hexdigest()


def test_invalid_utf8_output_and_record_remain_rejected(tmp_path: Path) -> None:
    """Raw capture preserves corrupt bytes; strict consumers cannot replace them."""
    payload = b'{"value":"\xff"}'
    code = f"import sys;sys.stdout.buffer.write({payload!r})"
    result = gate.run_check("corrupt", (sys.executable, "-I", "-B", "-c", code),
                            tmp_path, tmp_path)
    path = tmp_path / "corrupt.log"
    assert result.exit_code == 0 and path.read_bytes() == payload
    with pytest.raises(UnicodeDecodeError):
        path.read_text(encoding="utf-8")
    with pytest.raises(StagingError, match="invalid record"):
        integrity.read_record(path)
    assert path.read_bytes() == payload
