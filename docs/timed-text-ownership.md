# Timed-text ownership boundary — R5F foundation, R5G control integration

## Contract and primary basis

`IMFTimedTextTrack::GetLanguage` and `GetLabel` return `LPWSTR *` out parameters.
Their method pages specify a NUL-terminated string, not a BSTR and not a per-method
allocator exception. The correction applies Microsoft's general public COM rule:
callee-allocated out storage is freed by the caller through the COM task allocator.
This is a documented contract inference, not an observation of the Media Foundation
implementation's allocator in a debugger. `CoTaskMemFree`, not `SysFreeString`, is
used for these results. The true BSTR input to MediaEngine SetSource still uses the
existing SysAllocString/SysFreeString pair.

- https://learn.microsoft.com/en-us/windows/win32/com/memory-management-rules
- https://learn.microsoft.com/en-us/windows/win32/api/mfmediaengine/nf-mfmediaengine-imftimedtexttrack-getlabel
- https://learn.microsoft.com/en-us/windows/win32/api/mfmediaengine/nf-mfmediaengine-imftimedtexttrack-getlanguage
- https://learn.microsoft.com/en-us/windows/win32/api/combaseapi/nf-combaseapi-cotaskmemfree
- https://learn.microsoft.com/en-us/windows/win32/api/oleauto/nf-oleauto-sysfreestring

## Behavior, ownership and bounds

The decoder accepts only an owned LPWSTR from a successful getter (or NULL). Type
checking cannot establish allocation provenance: borrowed c_wchar_p values remain
forbidden by the caller contract. It copies at most 32,768 native wchar units, frees
the exact allocation once in a finally block, and clears the pointer before freeing.
Empty non-NULL allocations are freed. Decode, size-limit and allocator errors are
explicit TimedTextMemoryError exceptions, not empty success. A release exception
leaves an invalidated owner and propagates; it is not evidence that memory was freed.

A failed HRESULT must leave the out-pointer NULL under COM's failure rules. With
NULL, this optional metadata is unavailable and the displayed label can use the
language or track ID. A non-NULL result on failure is a provider contract violation:
no dereference or speculative free is attempted and ownership remains unknown.
That nonconforming-provider case can retain storage; guessing an allocator is not a
safe recovery. A getter exception before transfer likewise aborts the descriptor.

Traversal has a maximum of 1,024 tracks. No lock surrounds COM reads or frees. Copy
cost is O(string units), enumeration O(track count), with fixed hard caps. The
native pointer must be valid and NUL-terminated: these bounds do not make ctypes a
sandbox against corrupt native pointers. From R5G, acquired timed-text track/list/service references use explicit linear
release without the legacy address registry; see timed-text-backend.md. The
general COM cleanup behavior elsewhere remains outside this scoped repair.

## Explicit test scopes

Local unit tests use test-owned buffers and allocator spies: never free a Python
buffer through a native allocator. They cover NULL/empty/Unicode, wrong types,
limits, duplicate consumption, copy and release failure, failed HRESULT, malformed
ownership transfer, descriptor cleanup, enumeration budget and error propagation.
A Windows-only subprocess test performs 64 real CoTaskMemAlloc/copy/CoTaskMemFree
roundtrips. Its native execution is NOT_RUN in the Linux delivery environment.
Even a successful Windows roundtrip is not a real Media Foundation GetLabel call.

## Integration status after R5G

R5F had no production acquisition/routing hooks. R5G supplies the service backend,
thread checks and per-acquisition interface ownership, and strengthens enumeration
and selection. See timed-text-backend.md for the current contract and native probe.
Native Media Foundation execution of R5G remains NOT_RUN at delivery; passing local
callback tests is not proof of native availability. The prior accepted R5F Windows
allocator roundtrips keep their original scope.

Cue notification, overlay rendering, external subtitle admission and playback
synchronization remain outside the control implementation. Public query failure
still maps to logged empty results for compatibility. No end-to-end subtitle or
complete UI error-reporting qualification is claimed.

## Compatibility

Playback remains the import surface and installs the same method names. Timed-text
and audio-stream clusters were extracted to cohesive modules to keep the modified
playback file below 450 lines. Protocols in the existing shared module describe
existing COM-thread behavior; they are type declarations, not backend implementations.
The decoder's accepted input and failure contract intentionally become stricter.
All accepted ABI declaration files and dependency locks remain unchanged.

## Non-blocking unchanged legacy observation

WH-R5F-LEGACY-001: `media_engine_core_playback.update_video_stream` retains 58
non-comment physical token-body lines when nested function bodies are included.
Its entire AST is unchanged from R5E; no new rendering, locking or ownership
logic is introduced. Each nested callback remains below 50 lines. The historical
source-hygiene metric counts recursive statements instead and does not flag this
function. No all-function logical-line compliance claim is made. Keep its exact
AST unchanged and preserve the adjacent playback tests until a dedicated render
boundary change can restructure it without expanding this memory correction.
Closure: cohesive rendering callback extraction with native update/resize tests.
