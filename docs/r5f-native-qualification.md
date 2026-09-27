# R5F native qualification boundary

R5E's previous native suite/build is accepted for the R5E bytes, not a fresh R5F
run. R5F changes COM-string consumption and resource selection while preserving
all dependency locks and all eight accepted ABI boundary files. Do not regenerate
locks, rerun the unchanged ABI gate or reinstall the existing Python test environment
merely for this revision. Do not overwrite a public 1.0.1 application.

Use the already restored Windows CPython 3.12 development environment to run the
existing stage_candidate.py Prepare command against a separate R5F source folder.
The full test command has no -k/-m/ignore filters. Preserve state.json, all evidence,
JUnit, build artifacts and actual exit codes even on failure. Never Commit/Publish
as part of this qualification. The external delivery's WINDOWS_INSTRUCTIONS.md
contains the evidence-preserving PowerShell invocation.

The new Windows-only test `test_native_task_allocator_roundtrip_in_separate_process`
uses real CoTaskMemAlloc and the production LPWSTR decoder for 64 roundtrips. Other
ownership tests use explicit allocator spies and test-owned buffers; no borrowed
Python allocation is freed through Windows. A successful native allocator test is
not a real IMFTimedTextTrack getter, device playback or subtitle integration test.

Local delivery also verifies installed resource byte access in fresh no-dependency
wheel installations. The next Windows artifact review can check the same asset
inventory in the newly built wheel/sdist. No manual GUI launch is requested yet:
WH-R5F-TT-INTEGRATION remains open. Native timed-text acquisition, ownership of
COM interface references, audio/video devices and broader release gates need their
own actual evidence, not an empty query presented as a functional success.
