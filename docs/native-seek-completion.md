# Native seek completion — R5I step04B reconstruction

This checkpoint was reconstructed from delivered step04A. The previously unexported
step04B work and its claimed counts were not recovered or reused. It remains an
intermediate progress repair, not a Windows playback/smoothness release candidate.

## Completion facts and serialization

`SetCurrentTime` returning S_OK records call acceptance, not completion. One operation
continues to own the slot after its worker reply. The live engine callback must observe
SEEKING (16) followed by SEEKED (17), after native invocation has begun. Those facts and
a successful worker reply reconcile to `NATIVE_COMPLETED`, regardless of whether the
callback pair arrives before or after that reply. Completion time is not earlier than
either recorded event time or worker-reply time, including interleaved merges. The requested target never becomes
a measured playback position. Completion does not establish frame output or A/V sync.

The callback factory anchors a weak-owner proxy to its native notify object. The proxy
is bound to the engine generation for which the notify was created; it verifies the
current adapter/core and source/epoch before recording seek facts. It does not query a
clock, call AddRef/Release, or dispatch a GUI update. Normal non-seek events continue to
the existing adapter event queue. Callback errors are contained and logged, not success.

The SDK events contain no application request ID. Attribution relies on one serialized
native operation, a generation-bound callback and the normal provider notification
contract. It is not proof against a malicious provider or arbitrary duplicated/replayed
same-generation event pairs. Missing/incorrectly ordered notifications never confirm a
request. Native execution on Windows remains required to qualify the new route.

Cancellation, timeout and unknown worker errors do not undo native side effects. An
unresolved started operation retains its slot, including after cancellation/expiry.
A proven failing HRESULT permits another operation; an indeterminate result does not.
Changing the native generation may replace an old *replied* unresolved request; an
unreplied command remains blocking. There is no automatic retry, alternate worker,
forced completion or background timeout thread. Normal player Stop closes the adapter;
direct low-level same-generation reload cannot guess away indeterminate native effects.

## EOS, pause and error processing

The controller pumps normal events (including native errors) before consulting the
seek guard. It checks adapter/source and seek evidence again after the native end
query. A pending/unknown seek cannot consume end-of-stream or close the adapter in
these checked paths. An interleaved new seek invalidates the sampled end decision.
These bounded checks are not an all-thread atomic transaction with final teardown.

The tracker still acquires and publishes observations while seeking. A paused video
with seek evidence (including unavailable advertised evidence) is also pumped/sampled without calling end-of-stream or resuming
playback. Missing legacy receipt capability differs from a failing advertised getter;
malformed/foreign/failed advertised evidence blocks completion rather than looking idle.

## Cache-only presentation

`PlaybackView.seek` preserves typed availability and receipt facts. The view checks
state, backend identity and receipt stability around its cache read. No native query
is introduced on the GUI thread. `DisplayReading.seek_phase` exposes pending/failure/
completion separately from the current numerical observation.

While seek effects remain unresolved the two bars retain only the previous coherent
pair, flagged unavailable or error. The target is never painted as measured. Following
completion, only a matching-generation sample whose acquisition started strictly after
`completed_at` is admitted. A sample collected before the callback cannot confirm the
new position, even when its number happens to equal the target. Known failed HRESULTs
may coexist with an actual current clock and an explicit FAILED receipt phase.

This step does not add localized error/pending messages or a complete user interaction
state machine. Existing label validity behavior remains. Drag begin/preview/commit,
keyboard/capture-loss semantics, audio seek confirmation, bounded visual interpolation
and Windows sample/paint instrumentation remain subsequent work. Display values never
become EOS authority.

## Cohesion and preserved boundaries

Seven existing MediaEngineEx/stream helper bodies were moved, AST-identically, into
`media_engine_core_stream_api.py` and re-exported from setup. Native notification setup
was extracted into a cohesive helper. The obsolete setup-file and setup-function size
allowances were removed, not expanded. All existing dependency locks and ABI declarations
remain unchanged. The inherited resource-creation rollback and broad native lifetime
contracts are not redesigned or certified by this extraction.

Tests cover actual production receipt, callback forwarding, tracked worker path, both
widget timers, paused observations, native error pumping, stale callbacks, post-completion
sample admission and existing consumers. Native return values, clocks and wx widgets
in local tests are explicitly test-owned boundaries, not a Windows execution claim.

No new user-PC command, dependency installation, lock regeneration, identical ABI rerun,
manual app replacement, Commit or Publish is requested for this intermediate checkpoint.

## Primary API basis

- https://learn.microsoft.com/en-us/windows/win32/api/mfmediaengine/nf-mfmediaengine-imfmediaengine-setcurrenttime
- https://learn.microsoft.com/en-us/windows/win32/api/mfmediaengine/ne-mfmediaengine-mf_media_engine_event

These define API semantics, not project test results or native event provenance.
