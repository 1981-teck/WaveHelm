# R5I-step05A: qualify the tests and their native probe

This is a test/probe correction on exact R5I-step05. No `src/` file, dependency,
lock, packaging configuration or native ABI declaration is changed. Runtime
interpolation, strict timestamp guards and post-seek sampling remain unchanged.

## Returned Windows evidence

The 2026-09-19 run recorded 2,591 passed, 5 failed, 16 skipped and two phase errors
on one additional testcase (setup and teardown). All six other preflight commands,
including the real build frontend, succeeded. The timed-text operations reported
success, but their stderr contained an AttributeError in the bound notification
callback: the probe adapter lacked `_core`. That is not an unqualified native PASS.

## Deterministic time preconditions

Monotonic time is nondecreasing, not strictly increasing at every call. CPython
3.12.10 on Windows uses GetTickCount64. Two sequential operations can have the
same timestamp. One stale-clock test had not explicitly aged its old observation;
four widget cases did not ensure their clock sample started AFTER seek completion.
Controlled clocks now establish those preconditions without sleeps. Extra cases
retain the same-tick guard, then advance by a declared tick to observe recovery.
Neither `<` nor `<=` in runtime code was changed to accommodate the tests.

## Diagnostic metadata is not hostile input

The rejected 32,769-character source string is still sent intact to SeekReceipt.
Only its pytest parameter ID is shortened. A collection hook checks the whole
nodeid plus the longest phase suffix against a UTF-16 budget of 32,766 units,
reserving a terminator under the Windows 32,767-character limit. It fails collection
with a bounded explanation, never truncates test data, renames cases or deselects
anything. Tests include supplementary Unicode and the budget boundary.

## Probe adapter and diagnostic gate

ProbeAdapter owns the actual MediaEngineCore before constructing its native notify.
It exposes `_core`, `_closed` and `_shutdown_requested`, as required by the production
BoundSeekEvents boundary. Shutdown marks the probe closing and then closed. The
original production STA/core/service/metadata/selection route is retained; no fake
Media Foundation service replaces it. The callback exception is not suppressed in
production to make the probe pass.

A probe-only logging handler records video errors and warnings with exceptions.
It retains at most eight bounded scalar records plus a total count; ordinary
compatibility warnings remain nonblocking. Existing log delivery is preserved.
`diagnostics.json` records the result even for handled failure/foreign-platform
refusal. Any recorded fault vetoes PASS, including one captured during finalization.
The parent Windows test also checks that diagnostics are empty and that the raw
child logs contain no traceback. Limits: this is a logging error gate, not proof
against arbitrary code bypassing logging, late faults after shutdown, or a hostile
operator rewriting source and evidence. Twenty-nine named sources are now sealed,
including the newly relevant callback and dispatcher modules, not the whole OS/SDK.
A native Media Engine ERROR notification also enters the diagnostic gate; it cannot
be forgotten merely because later observations evict it from the bounded event ring.

## Required next native action

Extract the source separately as WaveHelm-continuation-R5I-step05A. Run only the
external Prepare collector with the existing Python 3.12 dev environment. Keep the
old source, failed ZIP, environment and user settings. Return the new results ZIP
on PASS or FAIL; no reinstall, lock regeneration or repeated ABI harness is needed.
No manual media trial is requested until the new test campaign is reviewed.

The local test doubles and controlled clocks are not Windows executions. Actual
native callback delivery after this correction is NOT_RUN locally. Native seeks
with media, GUI gestures, visual smoothness, audio acknowledgement, rendering of
subtitles and the remaining release gates are still separate qualification work.

## Primary references

- https://raw.githubusercontent.com/python/cpython/v3.12.10/Python/pytime.c
- https://learn.microsoft.com/en-us/windows/win32/procthread/environment-variables
- https://docs.pytest.org/en/stable/example/parametrize.html
- https://docs.pytest.org/en/stable/example/simple.html#pytest-current-test-environment-variable
