from __future__ import annotations

import ctypes
import json
from pathlib import Path
import subprocess
import sys

import pytest

from src.video.component_base import definitions as public_definitions
from src.video.component_base import definitions_abi as abi
from tools import verify_windows_abi


def _reference() -> dict[str, object]:
    return {
        "schema": verify_windows_abi.SCHEMA,
        "pointer_size": ctypes.sizeof(ctypes.c_void_p),
        "types": verify_windows_abi.ctypes_layout(),
    }


def test_public_module_reexports_abi_types() -> None:
    assert public_definitions.IMFDXGIDeviceManagerVtbl is abi.IMFDXGIDeviceManagerVtbl
    assert public_definitions.IMFTimedTextNotifyVtbl is abi.IMFTimedTextNotifyVtbl
    assert public_definitions.PROPVARIANT is abi.PROPVARIANT


def test_dxgi_lock_device_signature_includes_block_flag() -> None:
    fields = dict(abi.IMFDXGIDeviceManagerVtbl._fields_)
    lock_device = fields["LockDevice"]
    assert len(lock_device._argtypes_) == 5
    assert lock_device._argtypes_[-1] is ctypes.wintypes.BOOL


def test_timed_text_notify_vtable_is_complete_and_ordered() -> None:
    names = [name for name, *_ in abi.IMFTimedTextNotifyVtbl._fields_]
    assert names == [
        "QueryInterface", "AddRef", "Release", "TrackAdded", "TrackRemoved",
        "TrackSelected", "TrackReadyStateChanged", "Error", "Cue", "Reset",
    ]
    callbacks = dict(abi.IMFTimedTextNotifyVtbl._fields_)
    assert callbacks["TrackAdded"]._restype_ is None
    assert len(callbacks["Cue"]._argtypes_) == 4


def test_compare_layout_accepts_identical_reference() -> None:
    assert verify_windows_abi.compare_layout(_reference(), verify_windows_abi.ctypes_layout()) == []


def test_compare_layout_rejects_offset_drift() -> None:
    reference = _reference()
    types = reference["types"]
    assert isinstance(types, dict)
    guid = types["GUID"]
    assert isinstance(guid, dict)
    offsets = guid["offsets"]
    assert isinstance(offsets, dict)
    offsets["Data4"] = 9
    assert "offsets:GUID" in verify_windows_abi.compare_layout(reference, verify_windows_abi.ctypes_layout())


def test_compare_layout_rejects_missing_type() -> None:
    reference = _reference()
    types = reference["types"]
    assert isinstance(types, dict)
    del types["IMFTimedTextVtbl"]
    assert "missing:IMFTimedTextVtbl" in verify_windows_abi.compare_layout(reference, verify_windows_abi.ctypes_layout())


def test_read_reference_rejects_non_object(tmp_path: Path) -> None:
    path = tmp_path / "reference.json"
    path.write_text(json.dumps([]), encoding="utf-8")
    with pytest.raises(verify_windows_abi.AbiVerificationError, match="root must be an object"):
        verify_windows_abi._read_reference(path)


def test_compile_reference_requires_visual_studio(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    with pytest.raises(verify_windows_abi.AbiVerificationError, match=r"ProgramFiles\(x86\)"):
        verify_windows_abi.compile_reference(tmp_path, tmp_path / "out")

def test_build_script_quotes_paths_once(tmp_path: Path) -> None:
    devcmd = tmp_path / "Program Files" / "VsDevCmd.bat"
    source = tmp_path / "source dir" / "reference_layout.cpp"
    exe = tmp_path / "output dir" / "reference.exe"
    reference = tmp_path / "output dir" / "reference.json"
    script = verify_windows_abi._build_script_text(devcmd, source, exe, reference)
    assert f'call "{devcmd}" -no_logo -arch=amd64 -host_arch=amd64' in script
    assert '\\"' not in script
    assert f'"{exe}" > "{reference}"' in script


def test_direct_isolated_cli_can_import_project_tools() -> None:
    root = Path(__file__).resolve().parents[1]
    command = (
        sys.executable, "-I", "-B", str(root / "tools" / "verify_windows_abi.py"), "--help",
    )
    result = subprocess.run(command, cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    assert "Compile the Windows SDK ABI reference" in result.stdout

def test_reference_harness_avoids_mfapi_with_cinterface() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "tools" / "abi_harness" / "reference_layout.cpp").read_text(encoding="utf-8")
    assert "#define CINTERFACE" in source
    assert "#include <mfapi.h>" not in source
    assert "#include <mfobjects.h>" in source
    assert "#include <mfmediaengine.h>" in source

def test_reference_harness_closes_exactly_types_and_root() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "tools" / "abi_harness" / "reference_layout.cpp").read_text(encoding="utf-8")
    assert 'std::printf("}}\\n");' in source
    assert 'std::printf("}}}\\n");' not in source


@pytest.mark.parametrize("observed", [{}, {"unexpected": {}}])
def test_compare_layout_refuses_empty_or_incomplete_observation(observed: dict) -> None:
    assert "observed:type_set" in verify_windows_abi.compare_layout(_reference(), observed)


def test_compare_layout_rejects_extra_reference_type() -> None:
    reference = _reference()
    reference["types"]["UnexpectedVtbl"] = reference["types"]["GUID"]
    assert "reference:type_set" in verify_windows_abi.compare_layout(reference, verify_windows_abi.ctypes_layout())


@pytest.mark.parametrize("field,value", [("size", True), ("align", False), ("align", 3), ("offsets", {})])
def test_reference_rejects_invalid_layout_values(field: str, value: object) -> None:
    reference = _reference()
    reference["types"]["GUID"][field] = value
    assert "invalid_layout:GUID" in verify_windows_abi.compare_layout(reference, verify_windows_abi.ctypes_layout())


@pytest.mark.parametrize("payload", ['{"schema":"a","schema":"b"}', '{"value":NaN}', '{} {}'])
def test_read_reference_rejects_duplicate_nonfinite_and_trailing_data(tmp_path: Path, payload: str) -> None:
    path = tmp_path / "reference.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(verify_windows_abi.AbiVerificationError):
        verify_windows_abi._read_reference(path)


def test_read_reference_enforces_one_mib_budget(tmp_path: Path) -> None:
    path = tmp_path / "reference.json"
    path.write_bytes(b" " * (1024 * 1024 + 1))
    with pytest.raises(verify_windows_abi.AbiVerificationError, match="exceeds"):
        verify_windows_abi._read_reference(path)


def test_detailed_drift_identifies_numeric_slot_offset_and_tail() -> None:
    reference = _reference()
    table = reference["types"]["IMFTimedTextTrackVtbl"]
    table["offsets"]["GetId"] = 48
    table["size"] += 8
    del table["offsets"]["GetCueList"]
    details = verify_windows_abi.describe_differences(reference, verify_windows_abi.ctypes_layout())
    assert {"type": "IMFTimedTextTrackVtbl", "field": "offset", "member": "GetId",
            "expected": 48, "observed": 24} in details
    assert any(d.get("member") == "GetCueList" and d["expected"] is None for d in details)
    assert any(d["field"] == "size" and d["observed"] == 128 for d in details)


@pytest.mark.parametrize("suffix", ["bad%path", "bad!path", 'bad"path'])
def test_build_script_refuses_shell_expansion_paths(tmp_path: Path, suffix: str) -> None:
    with pytest.raises(verify_windows_abi.AbiVerificationError, match="expansion"):
        verify_windows_abi._build_script_text(tmp_path / suffix, tmp_path / "s.cpp",
                                            tmp_path / "r.exe", tmp_path / "r.json")


def test_build_script_keeps_object_inside_output(tmp_path: Path) -> None:
    exe = tmp_path / "evidence" / "reference.exe"
    script = verify_windows_abi._build_script_text(tmp_path / "dev.cmd", tmp_path / "ref.cpp", exe, exe.with_suffix(".json"))
    assert f'/Fo:"{exe.with_suffix(".obj")}"' in script
    assert "setlocal DisableDelayedExpansion" in script


def test_cli_native_refusal_still_writes_failure_evidence(tmp_path: Path) -> None:
    if sys.platform == "win32":
        pytest.skip("Only tests a non-Windows refusal, not a native failure")
    root = Path(__file__).resolve().parents[1]
    out = tmp_path / "fresh-output"
    result = subprocess.run([sys.executable, "-I", "-B", str(root / "tools/verify_windows_abi.py"),
                             "--root", str(root), "--output", str(out)],
                            capture_output=True, text=True, encoding="utf-8", check=False, timeout=30)
    assert result.returncode == 1, result.stderr
    report = json.loads((out / "abi-verification.json").read_text(encoding="utf-8"))
    assert report["verdict"] == "FAIL"
    assert "native Windows x64 is required" in report["findings"]
