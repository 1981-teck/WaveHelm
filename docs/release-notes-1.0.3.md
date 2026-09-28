# WaveHelm 1.0.3 — release notes

WaveHelm 1.0.3 is a focused Windows patch release based on the qualified 1.0.2 runtime.

## Fixed

- Prevent transient console windows from appearing while WaveHelm invokes `ffprobe` for metadata inspection on Windows.
- Use the Windows `CREATE_NO_WINDOW` subprocess creation flag only on Windows; non-Windows behavior remains unchanged.
- Preserve the existing bounded timeout, bounded output capture and typed ffprobe error contracts.

## Qualification

Release qualification completed successfully with:
- focused ffprobe regression tests;
- source-hygiene PASS;
- complete Windows CI on Python 3.11, 3.12 and 3.13;
- supply-chain/audit/SBOM PASS;
- Windows ABI/layout PASS;
- frozen-GUI verification with correct 1.0.3 executable identity;
- manual audio/video/library/seek/effects smoke testing with no transient black console windows;
- a real 1.0.2 -> 1.0.3 in-place installer upgrade;
- exact installed-tree verification: 1433 expected frozen files, 0 missing, 0 changed and 0 unexpected;
- uninstall verification with registry identity, install tree and shortcuts removed.

No dependency-lock or Windows ABI declaration change was introduced by this patch.