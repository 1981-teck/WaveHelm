"""Explicit UTF-8 artifact contracts, not native Windows ABI qualification.

Edge cases: Unicode paths and metadata, invalid UTF-8/JSON, layout drift, native
verification failures, byte budgets and raw timeout logs. ABI reference success
fixtures are synthesized from local ctypes declarations, never an SDK oracle.
"""
from __future__ import annotations

import ast
import ctypes
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

import pytest

from tests import test_timed_text_native as native_checks
from tests.test_windows_abi_sdk_contract import SDK_SLOTS
from tools import verify_windows_abi as verifier

TEXTS = ("caf\u00e9 \u20ac", "\u97f3\u697d \U0001f3b5", "e\u0301 \u041c\u0443\u0437\u044b\u043a\u0430")
IDS = ("accent-euro", "cjk-emoji", "combining-cyrillic")


def _reference() -> dict[str, object]:
    """Create an explicitly synthetic local contract fixture, not an SDK result."""
    return {"schema": verifier.SCHEMA, "pointer_size": ctypes.sizeof(ctypes.c_void_p),
            "types": verifier.ctypes_layout()}


@pytest.mark.parametrize("filename,count", (
    ("test_windows_abi_contract.py", 5), ("test_windows_abi_sdk_contract.py", 1),
    ("test_timed_text_native.py", 5),
))
def test_abi_fixture_text_calls_select_strict_utf8(filename: str, count: int) -> None:
    """Check the exact full Path-call roster, including earlier explicit calls."""
    tree = ast.parse(Path(__file__).with_name(filename).read_bytes())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr in ("read_text", "write_text")]
    assert len(calls) == count
    for call in calls:
        encodings = [k.value for k in call.keywords if k.arg == "encoding"]
        assert len(encodings) == 1, f"Missing encoding at line {call.lineno}"
        assert isinstance(encodings[0], ast.Constant) and encodings[0].value == "utf-8"
        assert not any(k.arg == "errors" for k in call.keywords)


def test_python_abi_cli_text_decoders_select_strict_utf8() -> None:
    tree = ast.parse(Path(__file__).with_name("test_windows_abi_contract.py").read_bytes())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name)
             and n.func.value.id == "subprocess" and n.func.attr == "run"]
    assert len(calls) == 2
    for call in calls:
        options = {k.arg: k.value for k in call.keywords}
        assert isinstance(options.get("encoding"), ast.Constant)
        assert options["encoding"].value == "utf-8" and "errors" not in options
        assert isinstance(options["timeout"], ast.Constant) and options["timeout"].value == 30
        assert isinstance(options["check"], ast.Constant) and options["check"].value is False


@pytest.mark.parametrize("text", TEXTS, ids=IDS)
def test_synthetic_reference_unicode_path_preserves_seal(tmp_path: Path, text: str) -> None:
    directory = tmp_path / text
    directory.mkdir()
    path = directory / "reference.json"
    reference = _reference()
    raw = json.dumps(reference, ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw)
    observed = verifier._read_reference(path)
    assert observed == reference and path.read_bytes() == raw
    assert verifier.compare_layout(observed, verifier.ctypes_layout()) == []
    assert verifier.seal_file(path, path.name).sha256 == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("text", TEXTS, ids=IDS)
def test_unicode_extra_type_is_not_silently_accepted(tmp_path: Path, text: str) -> None:
    reference = _reference()
    types = reference["types"]
    assert isinstance(types, dict)
    types[text] = {"size": 4, "align": 4, "offsets": {"value": 0}}
    path = tmp_path / "drift.json"
    raw = json.dumps(reference, ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw)
    loaded = verifier._read_reference(path)
    assert loaded == reference
    assert "reference:type_set" in verifier.compare_layout(loaded, verifier.ctypes_layout())
    assert path.read_bytes() == raw


@pytest.mark.parametrize("raw", (
    b'{"\xc3\xa9":1,"\xc3\xa9":2}', b'{"value":NaN}', b'{"value":Infinity}',
    b'{} {}', b'[]', b'{"value":"\xff"}',
), ids=("duplicate-unicode", "nan", "infinity", "trailing", "array-root", "corrupt-utf8"))
def test_invalid_reference_bytes_remain_rejected_and_unchanged(tmp_path: Path, raw: bytes) -> None:
    path = tmp_path / "invalid.json"
    path.write_bytes(raw)
    with pytest.raises(verifier.AbiVerificationError):
        verifier._read_reference(path)
    assert path.read_bytes() == raw


@pytest.mark.parametrize("text", TEXTS, ids=IDS)
def test_abi_failure_writer_preserves_unicode_and_nonzero_exit(tmp_path: Path,
                                                              monkeypatch: pytest.MonkeyPatch,
                                                              text: str) -> None:
    """Inject only a verification failure, never native success or an SDK reference."""
    def fail(root: Path, output: Path) -> dict[str, object]:
        raise verifier.AbiVerificationError(text)
    monkeypatch.setattr(verifier, "verify", fail)
    out = tmp_path / text
    assert verifier.main(["--root", str(tmp_path), "--output", str(out)]) == 1
    report = json.loads((out / "abi-verification.json").read_text(encoding="utf-8"))
    assert report == {"schema": verifier.REPORT_SCHEMA, "verdict": "FAIL", "findings": [text]}
    assert sorted(p.name for p in out.iterdir()) == ["abi-verification.json"]


@pytest.mark.parametrize("text", TEXTS, ids=IDS)
def test_controlled_python_streams_decode_without_losing_exit(text: str) -> None:
    stdout, stderr = (text + "\n").encode("utf-8"), ("error: " + text).encode("utf-8")
    code = f"import sys;sys.stdout.buffer.write({stdout!r});sys.stderr.buffer.write({stderr!r});sys.exit(7)"
    result = subprocess.run([sys.executable, "-I", "-B", "-c", code], capture_output=True,
                            text=True, encoding="utf-8", timeout=30, check=False)
    assert result.returncode == 7 and result.stdout == text + "\n"
    assert result.stderr == "error: " + text


def test_native_probe_timeout_retains_raw_evidence(tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """Failure-only injection checks existing raw logs despite lossy console preview."""
    stdout, stderr = TEXTS[0].encode("utf-8"), b"\xffraw-error"
    def timed_out(command: list[str], **options: object) -> subprocess.CompletedProcess[bytes]:
        assert options == {"capture_output": True, "timeout": 120, "check": False}
        raise subprocess.TimeoutExpired(command, 120, output=stdout, stderr=stderr)
    monkeypatch.setattr(native_checks.subprocess, "run", timed_out)
    out = tmp_path / "failed-probe"
    code, preview = native_checks._run_probe(out)
    raw_error = stderr + b"\nNATIVE PROBE TIMEOUT (direct child killed by subprocess.run)."
    assert code == -1 and "NATIVE PROBE TIMEOUT" in preview
    assert (out / "stdout.log").read_bytes() == stdout
    assert (out / "stderr.log").read_bytes() == raw_error
    receipt = json.loads((out / "process.json").read_text(encoding="utf-8"))
    assert receipt["exit_code"] == -1 and receipt["timeout_seconds"] == 120
    inventory = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert set(inventory) == {"stdout.log", "stderr.log", "process.json"}
    for name, record in inventory.items():
        raw = (out / name).read_bytes()
        assert record == {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    assert not (out / "probe.json").exists()


def test_reference_byte_budget_is_not_character_budget(tmp_path: Path) -> None:
    path = tmp_path / "oversize.json"
    raw = ('{"note":"' + "\u00e9" * 524288 + '"}').encode("utf-8")
    assert len(raw) > 1024 * 1024 and len(raw.decode("utf-8")) < 1024 * 1024
    path.write_bytes(raw)
    with pytest.raises(verifier.AbiVerificationError, match="exceeds"):
        verifier._read_reference(path)
    assert path.read_bytes() == raw


@pytest.mark.parametrize("text", TEXTS, ids=IDS)
def test_utf8_cpp_fixture_preserves_independent_slot_order(tmp_path: Path, text: str) -> None:
    """Source-text fixture only: no C++ compile or native SDK execution is claimed."""
    source_path = Path(__file__).resolve().parents[1] / "tools/abi_harness/reference_layout.cpp"
    original = source_path.read_bytes()
    path = tmp_path / "reference.cpp"
    path.write_bytes(("// " + text + "\n").encode("utf-8") + original)
    source = path.read_text(encoding="utf-8")
    assert source.startswith("// " + text + "\n") and source_path.read_bytes() == original
    for table, names in SDK_SLOTS.items():
        block = source.split(f"BEGIN_TYPE({table});", 1)[1].split("END_TYPE(", 1)[0]
        assert tuple(re.findall(r"(?:LAST_)?FIELD\(\w+, (\w+)\)", block)) == names
        assert all(f"CHECK_METHOD({table}, {name}," in source for name in names)
