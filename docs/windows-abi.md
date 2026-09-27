# Windows ABI/layout verification

WaveHelm binds selected Media Foundation COM interfaces with `ctypes`. Those
bindings are release-critical ABI surfaces: Python-only tests cannot prove that
method order, function signatures, structure size, alignment, and field offsets
match the Windows SDK installed on the target build host.

## Native gate

Run from a clean x64 Windows checkout with Visual Studio C++ Build Tools and a
Windows SDK installed:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\tools\WaveHelm-VerifyABI.ps1
```

The wrapper launches `tools/verify_windows_abi.py`. The verifier locates the
latest Visual Studio installation through `vswhere.exe`, enters its x64
developer environment, compiles `tools/abi_harness/reference_layout.cpp`
against the installed Windows SDK, executes the reference, and compares the
result with WaveHelm's `ctypes` declarations.

The reference has compile-time signature assertions for
`IMFDXGIDeviceManager::LockDevice` and the `IMFTimedTextNotify` callbacks. It
also emits size, alignment and member offsets for `GUID`, `PROPVARIANT`,
`IMFAttributesVtbl`, the DXGI manager vtable, the timed-text vtables, and the
Media Engine class-factory vtable.

## Evidence contract

The output directory contains:

- `windows-sdk-reference.json`: values emitted by the compiled SDK reference;
- `abi-verification.json`: WaveHelm comparison result.

A release ABI claim requires `abi-verification.json` with `verdict=PASS` from
native Windows x64. Missing Visual Studio Build Tools, a missing SDK, compiler
failure, malformed output, pointer-width mismatch, or any layout drift is a
hard failure. A Python-only contract test is not a substitute for this gate.

## Boundary changes in R5

The R4 declaration of `IMFDXGIDeviceManager::LockDevice` omitted the required
`BOOL fBlock` argument. R5 restores the Windows signature. R4 also declared
`IMFTimedTextNotifyVtbl` with only `IUnknown`; R5 declares the callback slots in
SDK order, including `TrackReadyStateChanged`, `Error`, `Cue`, and `Reset`.

These changes affect ABI declarations only. No multimedia behavior is claimed
as qualified until the native ABI gate and the separate Windows application
qualification both pass.

## R5D: complete vtables and independent contract tests

The R5C native failure exposed real binding errors, not a missing SDK. The
method order must follow the declarations in `mfmediaengine.h`, not the
alphabetical method index in reference documentation:

| Vtable | R5C slots | SDK slots | Correct size on x64 |
|---|---:|---:|---:|
| IMFTimedTextTrackVtbl | 14 | 16 | 128 bytes |
| IMFTimedTextVtbl | 16 | 17 | 136 bytes |
| IMFMediaEngineClassFactoryVtbl | 4 | 6 | 48 bytes |

R5D restores SDK order throughout the two timed-text tables and adds
`GetReadyState`, `GetCueList`, `AddTrack`, `CreateTimeRange`, and `CreateError`.
`RemoveTrack` takes `IMFTimedTextTrack *`, not a DWORD track identifier. This
corrects its C signature even though pointer width alone cannot test it.
`AddDataSource` now explicitly takes an opaque `IMFByteStream *`. Opaque
cue-list, byte-stream and time-range references expose only IUnknown here;
no implementation of their remaining methods is claimed.

Primary basis (inspected 2026-09-14):
https://raw.githubusercontent.com/microsoft/win32metadata/main/generation/WinSDK/RecompiledIdlHeaders/um/mfmediaengine.h

The Microsoft Learn RemoveTrack syntax agrees with the header, but the prose
parameter description still says DWORD. The compiled header is authoritative
for this ABI; the documentation mismatch is not silently reconciled.

The reference now emits every slot in all three repaired tables and has 39
exact C++ signature assertions for those entries, including IUnknown. The
independent tests check all 39 ctypes method signatures and slot positions,
plus raw test-owned pointer-table dispatch. These are contract tests, not a
Windows COM activation, allocator, or multimedia lifecycle qualification.
Size/offset agreement alone does not establish full function-call correctness.
The broader inherited ctypes consumer behavior is not globally certified here.

The verifier refuses empty/incomplete observed sets, unknown reference types,
duplicate JSON keys, booleans used as layout numbers, invalid alignments,
trailing JSON data and references over 1 MiB. It preserves expected/observed
numeric differences, raw compiler output and before/after SHA-256 bindings to
8 relevant boundary source files. This is not a signature of the whole source
repository or a sandbox against an account that can edit the verifier.

## Preserve the next native result, whether PASS or FAIL

Use a new output directory outside the source. Do not delete earlier evidence:

```powershell
$Evidence = Join-Path $env:LOCALAPPDATA ("WaveHelm-ABI-R5D-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
.\tools\WaveHelm-VerifyABI.ps1 -Output $Evidence
Compress-Archive -Path (Join-Path $Evidence '*') -DestinationPath "$HOME\Desktop\WaveHelm-abi-evidence-R5D.zip" -Force
```

Additional retained files are `ctypes-layout.json`, `build-output.log`,
`toolchain.txt`, the generated command file, executable and object. They are
native evidence, not source-distribution members. When compilation fails,
`abi-verification.json` remains FAIL; a leftover or old reference cannot promote
that failed run. Compiler/SDK execution after R5D remains NOT RUN until an
actual Windows result is supplied. Do not reinstall Python or Visual Studio
merely to address the already diagnosed vtable declaration errors.
