# Legacy COM maintenance — step07Y

Date: 22 September 2026. Baseline: exact step07W; step07X was read-only.
This document resolves X-F01 and X-F02 within their bounded diagnostic and
compatibility-documentation scope. It is not a new Windows/native qualification.

## X-F01: signed HRESULT recognition and formatting

The specialized `src.boot.com_bootstrap.init_com()` helper keeps its existing
`CoInitializeEx.argtypes`, `ctypes.c_long` result, STA flag, one call, `None` return,
exception tuple and caller-owned COM lifetime. Main startup remains separate.

Windows HRESULT is a signed 32-bit LONG. The code `0x80010106` therefore arrives
as `-2147417850` at this boundary. The previous direct comparison with a positive
Python constant missed the known changed-mode warning and printed an invalid
negative-looking hexadecimal diagnostic. A local `hr_code = hr & 0xFFFFFFFF` now
provides the bit pattern for comparison and unknown-failure formatting.

The existing raw S_OK/S_FALSE checks remain unchanged. Known changed-mode remains
a failed-initialization warning; unknown codes remain ERROR. No new success,
automatic retry, implicit fallback, CoUninitialize call or runtime result is added.
The original X finding passes after correction. Expanded tests cover signed and
unsigned controls, low-level signature/arguments, minimum signed and -1 codes,
missing exports, all five caught exception classes, traceback identity and
propagation of undeclared/process-control errors. DLL returns in those tests are
controlled unit fixtures, not successful or failed native apartment executions.

## X-F02: truthful notification-provider description

`src.video.mf_media_engine_notify` still aliases the canonical
`_MediaEngineNotifyCOM` and delegates construction unchanged. That provider uses
comtypes when available and its existing ctypes fallback otherwise. The old header,
alias comment and factory docstring incorrectly implied comtypes was mandatory.
Only those descriptive strings/comments change; signatures, error behavior, event
names, provider identity, callback code, QueryInterface/Release and ownership do not.

Tests construct the genuine installed provider and invoke its Python EventNotify
entry point with a recording adapter. They also check weak adapter lifetime,
provider error propagation and all 22 event-name mappings. This does not qualify
native Media Engine calls, COM apartments, the Windows SDK or GPU/device lifetime.
The available local branch is the ctypes fallback; a native comtypes branch is not
newly executed or simulated as successful.

## Preservation and limits

All eight modules reviewed in X remain KEEP for this baseline: audio_utils,
Playlist, playlist_legacy_contract, playlist_legacy_store, Song, com_bootstrap,
dxgi_device_manager and mf_media_engine_notify. These are finite maintenance
decisions, not an assertion of all external clients or complete safety coverage.
Both previously accepted Windows audio gates remain closed in their original scope.
No working application, global Python, Windows environment or user data is changed.

Native COM-lifetime balancing remains the specialized caller's responsibility and
is not corrected or redefined by this diagnostic-only patch. Full unfiltered target
runtime, global type/security/coverage, resolved advisory and Windows/release
qualification remain separate. Current results are in the external REPORT/STATUS.

## Primary API references

Consulted 22 September 2026. These establish API representation, not test outcomes:
- https://learn.microsoft.com/en-us/windows/win32/winprog/windows-data-types
- https://learn.microsoft.com/en-us/windows/win32/api/combaseapi/nf-combaseapi-coinitializeex
