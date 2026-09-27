"""Compile the Windows SDK ABI reference and compare it with ctypes layouts.

Edge cases:
- a missing Visual Studio C++ toolchain fails without inventing a reference;
- malformed or partial compiler output fails closed;
- any size, alignment, member-order, or offset drift is reported explicitly.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import asdict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.staging_integrity import (
    StagingError, read_record, seal_file, trusted_directory, write_record,
)

SCHEMA = "wavehelm-windows-abi-reference-v1"
REPORT_SCHEMA = "wavehelm-windows-abi-verification-v1"
ABI_TYPES = (
    "GUID", "PROPVARIANT", "IMFAttributesVtbl", "IMFDXGIDeviceManagerVtbl",
    "IMFTimedTextNotifyVtbl", "IMFTimedTextTrackVtbl", "IMFTimedTextTrackListVtbl",
    "IMFTimedTextVtbl", "IMFMediaEngineClassFactoryVtbl",
)
BOUND_SOURCES = (
    "src/__init__.py", "src/video/__init__.py", "src/video/component_base/__init__.py",
    "src/video/component_base/definitions_abi.py", "tools/staging_integrity.py",
    "tools/verify_windows_abi.py", "tools/abi_harness/reference_layout.cpp",
    "tools/WaveHelm-VerifyABI.ps1",
)


class AbiVerificationError(RuntimeError):
    """Raised when the ABI reference cannot be produced or verified."""


def _field_layout(kind: type[ctypes.Structure]) -> dict[str, object]:
    offsets = {name: int(getattr(kind, name).offset) for name, *_ in kind._fields_}
    return {"size": ctypes.sizeof(kind), "align": ctypes.alignment(kind), "offsets": offsets}


def ctypes_layout() -> dict[str, dict[str, object]]:
    """Return the ctypes layouts that are contractually bound to Windows SDK."""
    from src.video.component_base import definitions_abi as abi

    return {name: _field_layout(getattr(abi, name)) for name in ABI_TYPES}


def _valid_layout(raw: object) -> bool:
    """Reject partial, boolean-as-integer, unbounded or noncanonical layouts."""
    if not isinstance(raw, dict) or set(raw) != {"size", "align", "offsets"}:
        return False
    size, align, offsets = raw["size"], raw["align"], raw["offsets"]
    if any(type(v) is not int or not 0 < v <= 1048576 for v in (size, align)):
        return False
    if align & (align - 1) or not isinstance(offsets, dict) or not 0 < len(offsets) <= 128:
        return False
    return all(isinstance(k, str) and 0 < len(k) <= 128 and
               type(v) is int and 0 <= v < size for k, v in offsets.items())


def compare_layout(reference: dict[str, object], observed: dict[str, dict[str, object]]) -> list[str]:
    """Compare all nine types; empty observations and extra/missing types fail."""
    if reference.get("schema") != SCHEMA:
        return ["reference:schema"]
    findings: list[str] = []
    if set(reference) != {"schema", "pointer_size", "types"}:
        findings.append("reference:keys")
    pointer_size = reference.get("pointer_size")
    if type(pointer_size) is not int or pointer_size != ctypes.sizeof(ctypes.c_void_p):
        findings.append("reference:pointer_size")
    raw_types = reference.get("types")
    if not isinstance(raw_types, dict):
        return findings + ["reference:types"]
    if set(raw_types) != set(ABI_TYPES):
        findings.append("reference:type_set")
    if set(observed) != set(ABI_TYPES):
        findings.append("observed:type_set")
    for name in ABI_TYPES:
        expected, actual = raw_types.get(name), observed.get(name)
        if not isinstance(expected, dict):
            findings.append(f"missing:{name}")
            continue
        if not _valid_layout(expected) or not _valid_layout(actual):
            findings.append(f"invalid_layout:{name}")
            continue
        for field in ("size", "align", "offsets"):
            if expected[field] != actual[field]:
                findings.append(f"{field}:{name}")
    return findings


def describe_differences(reference: dict[str, object], observed: dict[str, dict[str, object]]) -> list[dict[str, object]]:
    """Expose actual numeric drift instead of reporting only a vtable name."""
    differences: list[dict[str, object]] = []
    types = reference.get("types")
    if not isinstance(types, dict):
        return differences
    for name in ABI_TYPES:
        expected, actual = types.get(name), observed.get(name)
        if not _valid_layout(expected) or not _valid_layout(actual):
            continue
        for field in ("size", "align"):
            if expected[field] != actual[field]:
                differences.append({"type": name, "field": field,
                                    "expected": expected[field], "observed": actual[field]})
        left, right = expected["offsets"], actual["offsets"]
        for member in sorted(set(left) | set(right)):
            if left.get(member) != right.get(member):
                differences.append({"type": name, "field": "offset", "member": member,
                                    "expected": left.get(member), "observed": right.get(member)})
    return differences


def _visual_studio_path() -> Path:
    root = os.environ.get("ProgramFiles(x86)")
    if not root:
        raise AbiVerificationError("ProgramFiles(x86) is unavailable")
    vswhere = Path(root) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if not vswhere.is_file():
        raise AbiVerificationError("vswhere.exe is unavailable")
    command = (
        str(vswhere), "-latest", "-products", "*", "-requires",
        "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath",
    )
    result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    install = result.stdout.strip()
    if result.returncode != 0 or not install:
        raise AbiVerificationError("Visual Studio C++ build tools are unavailable")
    return Path(install)


def _build_script_text(devcmd: Path, source: Path, exe: Path, reference: Path) -> str:
    """Return a cmd.exe build script with each path quoted exactly once."""
    paths = (devcmd, source, exe, reference)
    if any(any(char in str(path) for char in '\r\n%!?"') for path in paths):
        raise AbiVerificationError("build paths contain unsupported cmd.exe expansion characters")
    return "\r\n".join((
        "@echo off",
        "setlocal DisableDelayedExpansion",
        f'call "{devcmd}" -no_logo -arch=amd64 -host_arch=amd64',
        "if errorlevel 1 exit /b %errorlevel%",
        f'> "{exe.parent / "toolchain.txt"}" echo WindowsSDKVersion=%WindowsSDKVersion%',
        f'>> "{exe.parent / "toolchain.txt"}" echo VCToolsVersion=%VCToolsVersion%',
        f'cl /nologo /std:c++17 /W4 /WX /permissive- /DUNICODE /D_UNICODE "{source}" /Fe:"{exe}" /Fo:"{exe.with_suffix(".obj")}"',
        "if errorlevel 1 exit /b %errorlevel%",
        f'"{exe}" > "{reference}"',
        "exit /b %errorlevel%",
        "",
    ))


def compile_reference(root: Path, output: Path) -> Path:
    """Compile and execute the SDK-bound C++ reference in a VS developer shell."""
    installation = _visual_studio_path()
    devcmd = installation / "Common7" / "Tools" / "VsDevCmd.bat"
    source = root / "tools" / "abi_harness" / "reference_layout.cpp"
    if not devcmd.is_file() or not source.is_file():
        raise AbiVerificationError("ABI build prerequisites are incomplete")
    output.mkdir(parents=True, exist_ok=True)
    exe = output / "wavehelm_abi_reference.exe"
    reference = output / "windows-sdk-reference.json"
    script = output / "build-windows-sdk-reference.cmd"
    script.write_text(_build_script_text(devcmd, source, exe, reference), encoding="utf-8", newline="")
    command = (os.environ.get("ComSpec", "cmd.exe"), "/d", "/q", "/c", str(script))
    log_path = output / "build-output.log"
    with log_path.open("wb") as log:
        result = subprocess.run(command, cwd=root, stdout=log, stderr=subprocess.STDOUT,
                                timeout=180, check=False)
    with log_path.open("rb") as log:
        preview = log.read(65537)
    print(preview[:65536].decode("utf-8", errors="replace"), end="")
    if len(preview) > 65536:
        print("\nBuild output truncated on console; complete raw log retained.")
    if result.returncode != 0 or not reference.is_file():
        raise AbiVerificationError(f"Windows SDK reference build failed with exit {result.returncode}")
    return reference


def _read_reference(path: Path) -> dict[str, object]:
    """Read a bounded, regular, duplicate-free JSON object without silent coercion."""
    try:
        before = seal_file(path, path.name)
        if before.size > 1024 * 1024:
            raise AbiVerificationError("ABI reference exceeds 1 MiB")
        raw = read_record(path)
        if seal_file(path, path.name) != before:
            raise AbiVerificationError("ABI reference changed during read")
    except (OSError, UnicodeError, StagingError) as exc:
        if str(exc) == "record must be an object":
            raise AbiVerificationError("ABI reference root must be an object") from exc
        raise AbiVerificationError(f"invalid ABI reference: {exc}") from exc
    return raw


def _source_binding(root: Path) -> list[dict[str, object]]:
    return [asdict(seal_file(root / name, name)) for name in BOUND_SOURCES]


def verify(root: Path, output: Path) -> dict[str, object]:
    """Build the reference and retain both layouts and exact boundary-source seals."""
    if sys.platform != "win32" or ctypes.sizeof(ctypes.c_void_p) != 8:
        raise AbiVerificationError("native Windows x64 is required")
    if root.resolve() != PROJECT_ROOT:
        raise AbiVerificationError("root differs from the verifier source tree")
    before = _source_binding(root)
    reference_path = compile_reference(root, output)
    reference = _read_reference(reference_path)
    observed = ctypes_layout()
    write_record(output / "ctypes-layout.json", {"schema": SCHEMA,
                 "pointer_size": ctypes.sizeof(ctypes.c_void_p), "types": observed})
    findings = compare_layout(reference, observed)
    unchanged = before == _source_binding(root)
    if not unchanged:
        findings.append("source:changed_during_verification")
    return {
        "schema": REPORT_SCHEMA,
        "verdict": "PASS" if not findings else "FAIL", "findings": findings,
        "differences": describe_differences(reference, observed),
        "reference": str(reference_path),
        "reference_sha256": seal_file(reference_path, reference_path.name).sha256,
        "boundary_sources": before, "boundary_sources_unchanged": unchanged,
        "python": sys.version.split()[0], "python_executable": sys.executable,
        "pointer_size": ctypes.sizeof(ctypes.c_void_p),
        "scope": "nine layouts; SDK signature assertions, not multimedia lifecycle",
    }


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    root = args.root.resolve()
    output = (args.output or (root / "abi-evidence")).resolve()
    try:
        output.mkdir(parents=True, exist_ok=True)
        trusted_directory(output)
        report = verify(root, output)
    except (AbiVerificationError, StagingError, OSError, subprocess.SubprocessError) as exc:
        report = {"schema": REPORT_SCHEMA, "verdict": "FAIL", "findings": [str(exc)]}
    try:
        write_record(output / "abi-verification.json", report)
    except (OSError, StagingError) as exc:
        print(f"Cannot persist ABI evidence: {exc}", file=sys.stderr)
        return 1
    summary = {k: report[k] for k in ("schema", "verdict", "findings", "differences", "python") if k in report}
    summary["evidence_directory"] = str(output)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if report.get("verdict") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
