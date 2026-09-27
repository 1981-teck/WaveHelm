"""Explicit UTF-8 for diagnostic/trace artifacts without native qualification.

Edge cases: Unicode labels and paths, malformed bytes, native-operation errors,
ring overflow, file/byte budgets and no-overwrite. Native operations are injected
failures only; trace serialization and filesystem observations execute for real.
"""
from __future__ import annotations

import ast
import json
import logging
from pathlib import Path
from uuid import UUID

import pytest

from tools import probe_timed_text_native as probe
from src.ui_wx import progress_trace as trace_module
from src.ui_wx.playback_presentation import DisplayReading
from src.ui_wx.progress_trace import CAPACITY, FIELDS, MAX_FILES, ProgressTrace

TEXTS = ("caf\u00e9 \u20ac", "\u97f3\u697d \U0001f3b5", "e\u0301 \u041c\u0443\u0437\u044b\u043a\u0430")
IDS = ("accent-euro", "cjk-emoji", "combining-cyrillic")


@pytest.mark.parametrize("filename", ("test_native_probe_diagnostics.py", "test_progress_trace.py"))
def test_selected_artifact_calls_have_explicit_strict_utf8(filename: str) -> None:
    """Guard exactly three existing Path calls per module without importing tests."""
    source = Path(__file__).with_name(filename).read_text(encoding="utf-8")
    calls = [node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute)
             and node.func.attr in ("read_text", "write_text")]
    assert len(calls) == 3
    for call in calls:
        encodings = [key.value for key in call.keywords if key.arg == "encoding"]
        assert len(encodings) == 1, f"Missing encoding at line {call.lineno}"
        assert isinstance(encodings[0], ast.Constant) and encodings[0].value == "utf-8"
        assert not any(key.arg == "errors" for key in call.keywords)


@pytest.mark.parametrize("text", TEXTS, ids=IDS)
def test_probe_json_unicode_roundtrip_and_temporary_cleanup(tmp_path: Path, text: str) -> None:
    """Use the actual UTF-8 writer; Unicode values stay exact after JSON escaping."""
    directory = tmp_path / text
    directory.mkdir()
    target = directory / "diagnostics.json"
    record = {"message": text, "failure_count": 1, "status": "FAIL"}
    probe._write(target, record)
    raw = target.read_bytes()
    assert raw.isascii() and raw.endswith(b"\n")
    assert json.loads(target.read_text(encoding="utf-8")) == record
    assert not target.with_suffix(".json.tmp").exists()
    assert sorted(item.name for item in directory.iterdir()) == ["diagnostics.json"]


@pytest.mark.parametrize("text", TEXTS, ids=IDS)
@pytest.mark.parametrize("kind", ("logged-error", "raised-error"))
def test_unicode_probe_failure_stays_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                           text: str, kind: str) -> None:
    """An injected failure must retain Unicode diagnostics, exit 1 and cleanup."""
    output = tmp_path / text
    def operation(path: Path, steps: list[str]) -> None:
        assert path == output
        steps.append("test-owned failed operation; not native qualification")
        if kind == "raised-error":
            raise RuntimeError(text)
        logging.getLogger("src.video.encoding_test").error("%s", text)
    handlers = list(logging.getLogger().handlers)
    monkeypatch.setattr(probe, "run_probe", operation)
    monkeypatch.setattr(probe.sys, "platform", "win32")
    monkeypatch.setattr(probe.sys, "argv", ["probe", "--output", str(output)])
    assert probe.main() == 1
    report = json.loads((output / "probe.json").read_text(encoding="utf-8"))
    facts = json.loads((output / "diagnostics.json").read_text(encoding="utf-8"))
    assert report["status"] == "FAIL" and report["media_file_loaded"] is False
    assert report["release_readiness"] == "NOT_VERIFIED"
    assert facts["failure_count"] == (1 if kind == "logged-error" else 0)
    observed = facts["records"][0]["message"] if kind == "logged-error" else report["error"]
    assert text in observed
    assert (output / "traceback.txt").read_text(encoding="utf-8")
    assert logging.getLogger().handlers == handlers


@pytest.mark.parametrize("raw", (b"\xff", b'{"message":"\xc3("}'))
def test_corrupt_utf8_artifact_is_not_replaced(tmp_path: Path, raw: bytes) -> None:
    """A corrupt diagnostic artifact remains raw evidence and fails strict reading."""
    path = tmp_path / "diagnostics.json"
    assert path.write_bytes(raw) == len(raw)
    with pytest.raises(UnicodeDecodeError):
        path.read_text(encoding="utf-8")
    assert path.read_bytes() == raw


@pytest.mark.parametrize("value", (float("nan"), float("inf")))
def test_probe_writer_rejects_nonfinite_json_without_replacing(tmp_path: Path, value: float) -> None:
    """Rejected serialization leaves a pre-existing artifact unchanged."""
    path = tmp_path / "probe.json"
    original = b'{"status":"FAIL"}'
    assert path.write_bytes(original) == len(original)
    with pytest.raises(ValueError):
        probe._write(path, {"time": value})
    assert path.read_bytes() == original and not path.with_suffix(".json.tmp").exists()


@pytest.mark.parametrize("text", TEXTS, ids=IDS)
def test_trace_unicode_roundtrip_preserves_fields_time_and_close(tmp_path: Path,
                                                               monkeypatch: pytest.MonkeyPatch,
                                                               text: str) -> None:
    """Injected labels roundtrip, with no write per record and only one close flush."""
    directory = tmp_path / text
    directory.mkdir()
    monkeypatch.setenv("WAVEHELM_PROGRESS_TRACE_DIR", str(directory))
    trace = ProgressTrace("mini")
    for now in (1.25, 2.5, 3.75):
        trace.record(None, None, DisplayReading(position=1., duration=4.), now,
                     current=False, painted=True, reason=text)
    assert list(directory.iterdir()) == []
    trace.close()
    path, = directory.glob("progress-mini-*.json")
    raw = path.read_bytes()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema"] == "wavehelm-progress-trace-v1" and data["fields"] == list(FIELDS)
    assert data["total"] == 3 and data["omitted"] == 0 and raw.isascii()
    records = [dict(zip(data["fields"], row)) for row in data["records"]]
    assert [row["time"] for row in records] == [1.25, 2.5, 3.75]
    assert all(row["reason"] == text and row["drawn_ratio"] == .25 for row in records)
    trace.close()
    trace.record(None, None, DisplayReading(), 9., current=False, painted=False, reason="late")
    assert path.read_bytes() == raw and len(list(directory.iterdir())) == 1 and trace._rows == []


def test_trace_ring_overflow_preserves_order_and_omission(tmp_path: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    """Unicode diagnostic labels must not change the fixed record-count budget."""
    monkeypatch.setenv("WAVEHELM_PROGRESS_TRACE_DIR", str(tmp_path))
    trace = ProgressTrace("overlay")
    for index in range(CAPACITY + 3):
        trace.record(None, None, DisplayReading(), float(index), current=False,
                     painted=False, reason=TEXTS[0])
    assert len(trace._rows) == CAPACITY and list(tmp_path.iterdir()) == []
    trace.close()
    path, = tmp_path.glob("progress-overlay-*.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["total"] == CAPACITY + 3 and data["omitted"] == 3
    assert len(data["records"]) == CAPACITY
    assert [row[0] for row in data["records"]] == list(map(float, range(3, CAPACITY + 3)))


@pytest.mark.parametrize("text", TEXTS, ids=IDS)
def test_trace_file_budget_preserves_utf8_fixtures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                  caplog: pytest.LogCaptureFixture, text: str) -> None:
    """Full diagnostic folders reject a new flush without changing existing bytes."""
    monkeypatch.setenv("WAVEHELM_PROGRESS_TRACE_DIR", str(tmp_path))
    raw = json.dumps({"note": text}, ensure_ascii=False).encode("utf-8")
    files = [tmp_path / f"progress-{index}.json" for index in range(MAX_FILES)]
    for path in files:
        assert path.write_bytes(raw) == len(raw)
    trace = ProgressTrace("mini")
    trace.close()
    assert len(list(tmp_path.iterdir())) == MAX_FILES
    assert all(path.read_bytes() == raw for path in files)
    assert "could not be saved" in caplog.text and trace._closed and trace._rows == []


def test_trace_byte_budget_rejects_before_file_creation(tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch,
                                                      caplog: pytest.LogCaptureFixture) -> None:
    """A controlled tiny byte budget exercises rejection, not a large allocation."""
    monkeypatch.setenv("WAVEHELM_PROGRESS_TRACE_DIR", str(tmp_path))
    monkeypatch.setattr(trace_module, "MAX_BYTES", 1)
    trace = ProgressTrace("mini")
    trace.record(None, None, DisplayReading(), 1., current=False, painted=False, reason=TEXTS[0])
    trace.close()
    assert list(tmp_path.iterdir()) == [] and trace._rows == []
    assert "byte budget exceeded" in caplog.text


def test_trace_exclusive_creation_never_overwrites(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                   caplog: pytest.LogCaptureFixture) -> None:
    """A deterministic name collision must preserve the existing UTF-8 artifact."""
    monkeypatch.setenv("WAVEHELM_PROGRESS_TRACE_DIR", str(tmp_path))
    fixed = UUID(int=0)
    monkeypatch.setattr(trace_module.uuid, "uuid4", lambda: fixed)
    path = tmp_path / f"progress-mini-{fixed.hex}.json"
    raw = TEXTS[0].encode("utf-8")
    assert path.write_bytes(raw) == len(raw)
    trace = ProgressTrace("mini")
    trace.close()
    assert path.read_bytes() == raw and len(list(tmp_path.iterdir())) == 1
    assert "could not be saved" in caplog.text and trace._rows == []
