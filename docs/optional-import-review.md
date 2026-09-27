# Retained dependency imports — step07P

22 September 2026. Exact source baseline: R5I-step07O. Decision review with
bounded regression evidence; no production, dependency or runtime-policy change.

## Decisions for the current continuation baseline

| ID | Import | Decision | Contract retained |
|---|---|---|---|
| COMP-07 | `win32api` in `src/controller/app_controller.py` | KEEP_CURRENT_BASELINE | Same import position, actual native initialization when installed, existing ImportError-to-None branch, and pywin32 Windows requirement |
| COMP-08 | `PIL.Image` in `src/model/media_loader.py` | KEEP_CURRENT_BASELINE | Same public incidental alias and unconditional compiled-provider import; Pillow declaration, notices and packaging import graph remain |

These are explicit maintenance decisions, not proof that either alias is read by
playback or that both dependencies are technically indispensable. No local
`ast.Name` load of either alias appears in its application module. Repository
search finds preservation tests and documentation, not a newly invented caller.
Unbounded computed reflection and unknown external clients are outside the search.

The previous pending *decision* is closed for this baseline. A future dependency
rationalization may propose removal together with revised failure contracts,
metadata/locks/notices, Windows import-order evidence and the actual packaging
route. That would be a separate change, not an automatic next cleanup step.
Native and frozen-package *qualification* is not declared complete by KEEP.

## Why these imports are not equivalent to unused local variables

`win32api` is optional only at this source import boundary: ImportError selects
None. Other exception classes are not caught there. The pinned distribution is
pywin32 311, conditionally required on Windows. Its upstream module initializer
gets or loads DLL handles and resolves exports. Therefore removing the import
can change process initialization even without a subsequent Python attribute
read. No new Windows execution or native necessity claim follows from source
inspection of that initializer.

Pillow is **not optional at the MediaLoader import boundary**. The `from PIL import
Image` statement is unconditional and carries `noqa: F401`; there is no local
fallback for a missing Pillow or compiled imaging core. Real missing-dependency
probes confirm import failure, not a partially initialized successful loader.
Image processing is not performed by MediaLoader methods themselves in this
source. Tiny PNG/BMP/TIFF tests demonstrate the genuine retained provider, not a
claim that playback needs those three formats.

OpenCV has a different contract: an ImportError selects cv2=None and
OPENCV_AVAILABLE=False, emits its existing warning and leaves Pillow available.
A separate real-module probe exercises that distinction. No third-party library
is patched, uninstalled or replaced by a fake successful module.

## Packaging boundary

The supplied wheel declares Pillow and Windows pywin32 dependencies but does not
bundle either library's implementation. `test_runtime_dependency_audit.py` compares
the eleven mapped direct import roots with the existing requirement set. Deleting
the sole direct roots would change that explicit contract; keeping a fake import
or weakening the audit merely to satisfy a count is not proposed.

`docs/windows-packaging.md` says prebuilt installer recipes/signing automation are
outside this source baseline. There is no `.spec` file in the source inventory.
PyInstaller documentation explains why import-graph changes can affect hook and
dependency collection; it does not establish that WaveHelm currently ships a
qualified frozen executable. No frozen executable is built or certified here.

## Verification and limits

The new 25-case suite covers both retained import statements, narrow exception
handling, original exception propagation, real Image/core identity, lossless
resource roundtrips, corrupt-resource rejection and current requirement pins.
Win32 failure injection executes only the actual import block in a test-owned
namespace. It does not emulate successful Win32 initialization or import the full
AppController when pygame is unavailable. Fresh-process MediaLoader probes load
real Pillow and inject only the absence of PIL, PIL._imaging or cv2 as indicated.

Two experimental removals are made only in a disposable diagnostic copy to verify
that the preservation guards fail. They are not release candidates and do not
prove all effects of an actual removal. No application-source file changes in P.
All outcomes, command receipts, broader regression scope and missing gates are
recorded in the delivery REPORT and evidence. No native Windows, global static
analysis, vulnerability scan or full release claim is implied.

## Primary external references consulted 22 September 2026

- pywin32 b311 module initialization, native source:
  https://raw.githubusercontent.com/mhammond/pywin32/b311/win32/src/win32apimodule.cpp
- Pillow 12.3.0 Image implementation, compiled core import and version check:
  https://raw.githubusercontent.com/python-pillow/Pillow/12.3.0/src/PIL/Image.py
- PyInstaller hook/import analysis documentation (general mechanism only):
  https://pyinstaller.org/en/stable/hooks.html

Those sources describe third-party mechanisms. WaveHelm-specific observations
come from the exact supplied source, generated inventories and executed checks.
