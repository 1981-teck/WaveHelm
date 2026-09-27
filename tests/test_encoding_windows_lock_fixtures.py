"""Protect UTF-8 fixtures; synthetic graphs are not Windows installation evidence.

Edge cases: non-ASCII paths/text, combining/supplementary characters, invalid UTF-8.
Real child processes emit explicit bytes; no shell, network or package installation.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import sys
import zipfile

import pytest

from tests.test_windows_install import fixture_files
from tools.verify_windows_install import verify_installation
from tools.windows_lock_support import MatrixError, evidence_archive, require_command, run_command


REVIEWED_MODULES = (
    "test_windows_lock_resolution.py",
    "test_windows_lock_support.py",
    "test_windows_install.py",
)
UNICODE_TEXT = ("caf\u00e9 \u20ac", "\u97f3\u697d \U0001f3b5", "e\u0301 \u041c\u0443\u0437\u044b\u043a\u0430")
UNICODE_IDS = ("accent-euro", "cjk-supplementary", "combining-cyrillic")


@pytest.mark.parametrize("filename", REVIEWED_MODULES)
def test_reviewed_fixture_text_calls_select_utf8(filename: str) -> None:
    """The three reviewed modules must not revert to locale-dependent fixture I/O."""
    source = Path(__file__).with_name(filename).read_text(encoding="utf-8")
    calls = [node for node in ast.walk(ast.parse(source))
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
             and node.func.attr in ("read_text", "write_text")]
    assert calls, "The reviewed module must contain real text fixture calls"
    for call in calls:
        encodings = [item.value for item in call.keywords if item.arg == "encoding"]
        assert len(encodings) == 1, f"{filename}:{call.lineno}: encoding must be explicit"
        assert isinstance(encodings[0], ast.Constant) and encodings[0].value == "utf-8"


@pytest.mark.parametrize("text", UNICODE_TEXT, ids=UNICODE_IDS)
def test_real_command_keeps_utf8_streams_and_failure(tmp_path: Path, text: str) -> None:
    """UTF-8 bytes, distinct streams and nonzero status all survive capture."""
    payload = text.encode("utf-8")
    code = (f"import sys;sys.stdout.buffer.write({payload!r});"
            f"sys.stderr.buffer.write({payload!r});sys.exit(7)")
    result = run_command((sys.executable, "-I", "-B", "-c", code),
                         tmp_path / "unicode-command", tmp_path, timeout=10)
    assert result.exit_code == 7 and result.error is None and not result.ok
    for filename in (result.stdout, result.stderr):
        path = Path(filename)
        assert path.read_bytes() == payload
        assert path.read_text(encoding="utf-8") == text
    record = json.loads((tmp_path / "unicode-command/command.json").read_text(encoding="utf-8"))
    assert record["exit_code"] == 7 and record["argv"][-1] == code
    with pytest.raises(MatrixError):
        require_command(result)


def test_invalid_utf8_log_is_preserved_not_silently_replaced(tmp_path: Path) -> None:
    """A zero process status does not make corrupt text decodable."""
    code = "import sys;sys.stdout.buffer.write(bytes([255,254]))"
    result = run_command((sys.executable, "-I", "-B", "-c", code),
                         tmp_path / "invalid-utf8", tmp_path, timeout=10)
    require_command(result)
    assert Path(result.stdout).read_bytes() == b"\xff\xfe"
    with pytest.raises(UnicodeDecodeError):
        Path(result.stdout).read_text(encoding="utf-8")


@pytest.mark.parametrize("text", UNICODE_TEXT, ids=UNICODE_IDS)
def test_unicode_archive_names_and_hashes_are_exact(tmp_path: Path, text: str) -> None:
    """Unicode member names/data retain exact bytes; existing archives are not replaced."""
    (tmp_path / "evidence").mkdir()
    (tmp_path / "locks").mkdir()
    relative = f"evidence/{text}.txt"
    payload = text.encode("utf-8")
    assert (tmp_path / relative).write_bytes(payload) == len(payload)
    archive = evidence_archive(tmp_path)
    before = archive.read_bytes()
    with zipfile.ZipFile(archive) as data:
        assert data.read(relative) == payload
        manifest = json.loads(data.read("evidence-manifest.json"))
        assert manifest["files"] == [{"path": relative, "size": len(payload),
                                      "sha256": hashlib.sha256(payload).hexdigest()}]
    with pytest.raises(FileExistsError):
        evidence_archive(tmp_path)
    assert archive.read_bytes() == before


@pytest.mark.parametrize("text", UNICODE_TEXT, ids=UNICODE_IDS)
def test_unescaped_unicode_live_paths_keep_graph_contract(tmp_path: Path, text: str) -> None:
    """Real JSON decoding handles Unicode without promoting synthetic fixture graphs."""
    files = fixture_files(tmp_path)
    live = files[3]
    data = json.loads(live.read_text(encoding="utf-8"))
    data["prefix"] = f"C:/users/{text}/private"
    serialized = json.dumps(data, ensure_ascii=False)
    assert live.write_text(serialized, encoding="utf-8") == len(serialized)
    assert text.encode("utf-8") in live.read_bytes()
    result = verify_installation(*files, "3.12")
    assert result["packages"] == 2
    assert result["status"] == "INSTALLED_GRAPH_MATCH_NOT_AUDITED"
    assert result["vulnerability_status"] == "NOT_RUN"
