"""UTF-8 test artifacts and bounded subprocess decoding for lock tooling.

Edge cases: Unicode names/comments/markers, corrupt bytes, duplicate JSON keys,
lock/root tampering and existing outputs. Synthetic graphs are NOT installations.
Real child processes emit explicitly encoded bytes; no network or native simulation.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from tests.test_dependency_lock import _report
from tools.dependency_lock import LockError, read_report, render_lock, verify_lock
from tools.staging_integrity import copy_sealed, inventory, write_record
from tools.windows_lock_matrix import MatrixError, prepare_output
from tools.windows_lock_support import evidence_archive


MODULES = ("test_windows_lock_matrix.py", "test_dependency_lock.py")
TEXTS = ("caf\u00e9 \u20ac", "\u97f3\u697d \U0001f3b5", "e\u0301 \u041c\u0443\u0437\u044b\u043a\u0430")
IDS = ("accent-euro", "cjk-emoji", "combining-cyrillic")


def _calls(filename: str) -> list[ast.Call]:
    """Inspect source bytes without importing or executing the test module."""
    source = Path(__file__).with_name(filename).read_text(encoding="utf-8")
    return [node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Call)]


def _assert_utf8(call: ast.Call, *, process: bool = False) -> None:
    """Text paths use UTF-8; controlled process decoders state strictness explicitly."""
    values = [keyword.value for keyword in call.keywords if keyword.arg == "encoding"]
    assert len(values) == 1, f"Missing encoding at line {call.lineno}"
    assert isinstance(values[0], ast.Constant) and values[0].value == "utf-8"
    errors = [keyword.value for keyword in call.keywords if keyword.arg == "errors"]
    if process:
        assert len(errors) == 1, f"Missing strict errors policy at line {call.lineno}"
        assert isinstance(errors[0], ast.Constant) and errors[0].value == "strict"
    else:
        assert not errors


@pytest.mark.parametrize("filename", MODULES)
def test_reviewed_path_calls_select_utf8(filename: str) -> None:
    """Both modules have six total text calls, including existing explicit calls."""
    calls = [call for call in _calls(filename) if isinstance(call.func, ast.Attribute)
             and call.func.attr in ("read_text", "write_text")]
    assert len(calls) == 6
    for call in calls:
        _assert_utf8(call)


@pytest.mark.parametrize("filename,count", [(MODULES[0], 2), (MODULES[1], 1)])
def test_reviewed_process_decoders_select_utf8(filename: str, count: int) -> None:
    """Controlled Python JSON/refusal output must not consult the parent locale."""
    calls = [call for call in _calls(filename) if isinstance(call.func, ast.Attribute)
             and isinstance(call.func.value, ast.Name)
             and call.func.value.id == "subprocess" and call.func.attr == "run"]
    assert len(calls) == count
    for call in calls:
        _assert_utf8(call, process=True)


def _graph_files(tmp_path: Path, text: str) -> tuple[Path, Path, Path]:
    """Serialize the existing two-package synthetic graph, never a live resolution."""
    root = tmp_path / text
    root.mkdir()
    requirements, report, lock = (root / name for name in (
        "requirements.txt", "report.json", "requirements.lock"))
    value = f"# {text}\nalpha==1.0\n"
    assert requirements.write_text(value, encoding="utf-8", newline="\n") == len(value)
    data = _report()
    data["fixture_note"] = text
    serialized = json.dumps(data, ensure_ascii=False)
    assert report.write_text(serialized, encoding="utf-8", newline="\n") == len(serialized)
    raw = render_lock(read_report(report), requirements).encode("utf-8")
    assert lock.write_bytes(raw) == len(raw)
    return requirements, report, lock


@pytest.mark.parametrize("text", TEXTS, ids=IDS)
def test_unicode_graph_artifacts_remain_exact(tmp_path: Path, text: str) -> None:
    """Paths, comments and unescaped JSON retain bytes and graph qualification limits."""
    roots, report, lock = _graph_files(tmp_path, text)
    assert roots.read_text(encoding="utf-8") == f"# {text}\nalpha==1.0\n"
    assert text.encode("utf-8") in report.read_bytes()
    assert read_report(report)["fixture_note"] == text
    assert verify_lock(lock, report, roots) == hashlib.sha256(lock.read_bytes()).hexdigest()
    assert "NOT an installed-graph audit" in lock.read_text(encoding="utf-8")
    assert "Human review: REQUIRED" in lock.read_text(encoding="utf-8")


@pytest.mark.parametrize("text", TEXTS, ids=IDS)
def test_unicode_marker_uses_report_not_host(tmp_path: Path, text: str) -> None:
    """Synthetic marker matching must remain bound to the stated environment."""
    report = _report()
    report["environment"]["platform_release"] = text
    roots = tmp_path / "requirements.txt"
    value = f'alpha==1.0; platform_release == "{text}"\n'
    assert roots.write_text(value, encoding="utf-8", newline="\n") == len(value)
    assert "alpha==1.0" in render_lock(report, roots)
    report["environment"]["platform_release"] = "different-release"
    with pytest.raises(LockError, match="empty requirements"):
        render_lock(report, roots)


@pytest.mark.parametrize("text", TEXTS, ids=IDS)
def test_duplicate_unicode_json_keys_are_rejected(tmp_path: Path, text: str) -> None:
    """Duplicate-key rejection is not bypassed by non-ASCII names or values."""
    key = json.dumps(text, ensure_ascii=False)
    serialized = "{" + key + ":1," + key + ":2}"
    path = tmp_path / "report.json"
    assert path.write_text(serialized, encoding="utf-8", newline="\n") == len(serialized)
    with pytest.raises(LockError, match="duplicate JSON key") as caught:
        read_report(path)
    assert text in str(caught.value)


@pytest.mark.parametrize("raw", [b"\xff", b'{"note":"\xc3("}'], ids=("invalid", "broken-sequence"))
def test_invalid_utf8_report_is_not_replaced(tmp_path: Path, raw: bytes) -> None:
    """The report reader preserves the actual Unicode decoding failure as cause."""
    path = tmp_path / "report.json"
    assert path.write_bytes(raw) == len(raw)
    with pytest.raises(LockError, match="cannot parse report") as caught:
        read_report(path)
    assert isinstance(caught.value.__cause__, UnicodeDecodeError)
    assert path.read_bytes() == raw


def test_invalid_utf8_roots_do_not_become_valid_pins(tmp_path: Path) -> None:
    """Corrupt root bytes propagate their decoding error without a fallback graph."""
    path = tmp_path / "requirements.txt"
    raw = b"# \xff\nalpha==1.0\n"
    assert path.write_bytes(raw) == len(raw)
    with pytest.raises(UnicodeDecodeError):
        render_lock(_report(), path)
    assert path.read_bytes() == raw


@pytest.mark.parametrize("text", TEXTS, ids=IDS)
def test_unicode_lock_tamper_and_root_drift_still_fail(tmp_path: Path, text: str) -> None:
    """Readable Unicode additions remain byte tampering, not a canonical lock."""
    roots, report, lock = _graph_files(tmp_path, text)
    original = lock.read_bytes()
    altered = original.decode("utf-8") + f"# {text}\n"
    assert lock.write_text(altered, encoding="utf-8", newline="\n") == len(altered)
    with pytest.raises(LockError, match="bytes differ"):
        verify_lock(lock, report, roots)
    assert lock.write_bytes(original) == len(original)
    assert roots.write_text("alpha==2.0\n", encoding="utf-8", newline="\n") == 11
    with pytest.raises(LockError):
        verify_lock(lock, report, roots)


@pytest.mark.parametrize("text", TEXTS, ids=IDS)
def test_matrix_unicode_snapshots_archive_without_environment(tmp_path: Path, text: str) -> None:
    """Exact source seals, archive isolation and no-overwrite work with Unicode data."""
    source = tmp_path / ("source-" + text)
    source.mkdir()
    payload = text.encode("utf-8")
    assert (source / "requirements.txt").write_bytes(payload) == len(payload)
    before = inventory(source)
    output = prepare_output(source, tmp_path / ("output-" + text))
    copy_sealed(source, output / "source-snapshot", before)
    assert (output / "environments/private.txt").write_bytes(b"excluded") == 8
    write_record(output / "evidence/note.json", {"fixture": text, "status": "NOT_RUN"})
    assert (output / "locks/requirements.lock").write_bytes(payload) == len(payload)
    archive = evidence_archive(output)
    saved = archive.read_bytes()
    with zipfile.ZipFile(archive) as data:
        assert data.read("locks/requirements.lock") == payload
        assert json.loads(data.read("evidence/note.json"))["fixture"] == text
        assert not any(name.startswith(("environments/", "source-snapshot/")) for name in data.namelist())
    with pytest.raises(MatrixError, match="new directory"):
        prepare_output(source, output)
    with pytest.raises(FileExistsError):
        evidence_archive(output)
    assert inventory(source) == before and archive.read_bytes() == saved


@pytest.mark.parametrize("text", TEXTS, ids=IDS)
def test_controlled_utf8_process_retains_both_streams_and_exit(text: str) -> None:
    """Explicit byte emission avoids assuming a native program's output encoding."""
    raw = text.encode("utf-8")
    code = f"import sys;sys.stdout.buffer.write({raw!r});sys.stderr.buffer.write({raw!r});sys.exit(7)"
    result = subprocess.run((sys.executable, "-I", "-B", "-c", code), text=True,
                            capture_output=True, encoding="utf-8", errors="strict",
                            timeout=10, check=False)
    assert result.returncode == 7 and result.stdout == text and result.stderr == text


@pytest.mark.parametrize("stream", ("stdout", "stderr"))
def test_controlled_process_rejects_corrupt_utf8(stream: str) -> None:
    """Strict decoding rejects malformed captured bytes on every supported host."""
    code = f"import sys;sys.{stream}.buffer.write(bytes([255]))"
    result = subprocess.run((sys.executable, "-I", "-B", "-c", code),
                            capture_output=True, text=False, timeout=10, check=False)
    raw = getattr(result, stream)
    assert raw == bytes([255])
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8", errors="strict")
