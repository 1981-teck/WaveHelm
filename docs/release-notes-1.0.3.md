# WaveHelm 1.0.3 — draft release notes

**Release candidate. WaveHelm 1.0.2 remains the current public release until v1.0.3 is published.**

WaveHelm 1.0.3 is a focused Windows patch release based on the qualified 1.0.2 runtime.

## Fixed

- Prevent transient console windows from appearing while WaveHelm invokes `ffprobe` for metadata inspection on Windows.
- Use the Windows `CREATE_NO_WINDOW` subprocess creation flag only on Windows; non-Windows behavior remains unchanged.
- Preserve the existing bounded timeout, bounded output capture and typed ffprobe error contracts.

## Verification

Before publication, the candidate must retain:
- focused ffprobe regression tests;
- source-hygiene PASS;
- complete Windows CI on Python 3.11, 3.12 and 3.13;
- supply-chain/audit/SBOM PASS;
- Windows ABI/layout PASS;
- a real frozen-GUI smoke test confirming WaveHelm works and no transient black console windows appear;
- compatible installer upgrade and uninstall verification.

No dependency-lock or Windows ABI declaration change is intended for this patch.
