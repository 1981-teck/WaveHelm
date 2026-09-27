# Timed-text service control — R5G

## Scope and native status

The three formerly absent MediaEngineCore hooks now have production implementations:
`_get_text_tracks_on_com_thread`, `_get_active_timed_text_tracks_on_com_thread`,
and `_call_timed_text_method_on_com_thread` (only SelectTrack is admitted).
They are installed by the canonical core attacher. The implementation invokes
Mf.dll!MFGetService on the live Media Engine using MF_MEDIA_ENGINE_TIMEDTEXT and
IID_IMFTimedText from the SDK header. It does not cast an arbitrary engine pointer
into IMFTimedText or attempt undocumented vtable slots. No speculative alternate
QueryInterface route is tried after failure.

Local tests exercise this real control flow using explicitly test-owned callbacks,
not a Windows Media Foundation implementation. The native service route and the
full user-machine probe are NOT_RUN at delivery. The public helper documentation
specifies MFGetService and ownership; the header supplies the SID/IID and method
signatures. Availability on the installed Media Engine must be established by
native execution, not inferred from the presence of a constant or a mocked test.

## Ownership and threading

The backend checks the adapter's actual COM worker identity without starting a
new worker. It rejects foreign threads, missing adapters, shutdown, NULL engines
and malformed generation state. All native operations happen outside the core
state lock. The caller is the existing synchronous adapter dispatcher.

A successful MFGetService owns one service reference. Each operation releases it
in finally; there is no persistent cache, shutdown callback or address-based
release registry for these new references. Each successful list/track acquisition
owns one independent reference. Returning the same pointer address twice does
not suppress either Release. The owner is cleared before Release. NULL or unknown
output ownership is never guessed after a failed HRESULT; a nonconforming provider
can consequently retain storage, which is reported as an error rather than freed
through an unproven pointer. Native pointers are trusted COM pointers, not sandboxed
addresses. A release exception is a failure, not proof that the allocation was freed.

The R5F task-memory decoder and its CoTaskMemFree rule remain unchanged. This step
replaces only the timed-text interface-reference cleanup with explicit per-acquisition
release. General safe_release use elsewhere, callback lifetime and shutdown remain
legacy boundaries; this does not claim global COM leak freedom.

## Complete queries and selection semantics

Lists are bounded to 1,024 entries, checked for duplicate/invalid uint32 IDs and
length drift. A failed/NULL GetTrack or a descriptor error aborts the complete
query; it is not silently skipped. Previously copied tracks are released. No
metadata pointer crosses to the controller: results are plain typed descriptors.
COM lists do not expose a version token here. Same-length concurrent replacement
cannot be ruled out merely from count/ID checks; no atomic native snapshot is claimed.

The public core select method now accepts only a plain integer in 0..2**32-1;
bools, strings, floats and overflow values return false without native dispatch.
The controller's existing normalization is unchanged. Unknown IDs are rejected
before any track is deselected. A valid target is selected first, then the other
text tracks are deselected. The active-ID set must confirm the requested state
before true is returned. Active metadata tracks outside the text-list IDs are not
changed. Engine identity/generation is rechecked across the sequence.

Selection is NOT transactional. Failure after a successful native mutation returns
false and stops the remaining work; it can leave partial native state. No rollback,
retry-to-success or invented result is used. False or an exception requires a new
query by a caller, not an assumption that the old selection is preserved. Query
methods retain their existing logged-empty compatibility mapping; typed memory
errors still propagate. This is not complete user-visible capability reporting.

## Native probe

`tools/probe_timed_text_native.py` uses a new real production STA manager, a hidden
STATIC HWND and the production core engine-creation path. It loads no media URL or
user file. Through the service it adds two real native tracks, reads label/language
through the production allocator helper, selects both tracks in turn, checks active
IDs, disables them and removes them. Task-memory outputs and owned track/service
references follow the production helpers. Public core shutdown and worker termination
are observed, but successful shutdown return is not a native reference-count/leak audit.

The Windows-only pytest case launches this probe in an isolated child Python process
with a 120-second direct-child timeout. Unsupported/missing native capabilities are
failures on Windows, not skipped test cases. Only non-Windows execution skips the
native case; a separate test requires explicit foreign-platform refusal.

Set WAVEHELM_NATIVE_EVIDENCE_DIR to an operator-owned directory outside the source to
retain the probe's source-before/source-after seals, progress, result, traceback,
stdout/stderr, command exit and member manifest. The R5G collector sets this value.
The named source seals cover 20 files, not the whole dependency/SDK/OS installation.
Manifests are unsigned inventories, not hostile-operator attestations. Parent timeout
control kills the direct child; it is not a system-wide subprocess sandbox.

## Remaining subtitle functionality

These hooks implement native track control, NOT an on-screen subtitle renderer.
RegisterNotifications/cue delivery, formatted-text interpretation, overlay rendering,
external subtitle-file admission and synchronization with real playback are not
implemented by this step. In-band availability and complete application/device/COM
lifecycle also require native qualification. Do not claim end-to-end working subtitle
playback from control tests or a successful nine-layout ABI receipt.

## Primary references

- https://learn.microsoft.com/en-us/windows/win32/api/mfidl/nf-mfidl-mfgetservice
- https://raw.githubusercontent.com/microsoft/win32metadata/main/generation/WinSDK/RecompiledIdlHeaders/um/mfmediaengine.h
- https://learn.microsoft.com/en-us/windows/win32/api/mfmediaengine/nf-mfmediaengine-imftimedtext-gettexttracks
- https://learn.microsoft.com/en-us/windows/win32/api/mfmediaengine/nf-mfmediaengine-imftimedtext-getactivetracks
- https://learn.microsoft.com/en-us/windows/win32/api/mfmediaengine/nf-mfmediaengine-imftimedtext-selecttrack
- https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-createwindowexw

Only API contracts/identifiers are used. No external sample implementation or SDK
header is copied into this project. Public documentation is not native-run evidence.
