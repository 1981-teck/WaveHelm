> Historical transport scope: this document describes step04A. Step04B extends
> the slot through native completion and integrates EOS/cache-only view status.
> Current completion rules and remaining limitations are in `native-seek-completion.md`.

# Native seek command admission — R5I step04A

This is the first native-transport part of step04, not completed seek/gesture or
smoothness qualification. Step03 cache-only GUI progress remains unchanged.

## Actual path

Both controls keep the canonical helper -> PlayerController -> EngineController
revision/state guard. The shipped video route then crosses VideoController ->
IMFMediaEngineAdapter -> MediaEngineCore -> tracked COM queue -> SetCurrentTime.
Only exact `True` from the adapter is queue admission. Legacy void results no
longer become video admission. The facade still returns FORWARDED_UNCONFIRMED:
it deliberately does not certify native execution or the completed position.
The existing audio path and its unconfirmed semantics are unchanged in step04A.

A core owns a SeekSlot with one pending/unreplied operation and a transport epoch.
A request captures source, generation and engine identity. Stop, reload (including
the same path) and shutdown invalidate prior intent. Missing cancellation state
is an error, never a no-op to accommodate a test. Callback execution rechecks the
worker, adapter, core, source, engine generation and epoch. Native work uses the
existing COM worker and holds one AddRef/Release pair for that command. Native
calls and error formatting do not occur under new state locks.

## Receipts are facts, not truthy success

`VideoController.get_seek_receipt()` is cache-only. The record is immutable,
pointer-free, typed and bounded; boolean coercion raises TypeError. Its request
ID/source/generation/epoch identify historical command evidence, not a current
playback-position sample. Only one current receipt per core is retained.

- RESERVED: local slot reserved, no queue insertion established.
- QUEUED: task inserted; execution has not been established.
- RUNNING: SetCurrentTime is about to be invoked on the held engine.
- NATIVE_ACCEPTED_UNCONFIRMED: the native call returned S_OK. **Not SEEKED.**
- REJECTED: known insertion refusal; the port does not claim the worker replied.
- FAILED: native HRESULT, worker error, or malformed worker output.
- CANCELLED: source/transport/owner invalidation. Existing effects are not undone.
- EXPIRED_UNCONFIRMED: ten-second command deadline elapsed; no success inferred.
- SUBMISSION_UNCERTAIN: a dispatcher exception cannot prove whether it inserted.

The ten-second age is an admission/execution evidence bound evaluated by reads and
execution, not a background watchdog or cancellation of native code already running.
An expired/cancelled/uncertain unreplied task still occupies the slot. Repeated seeks
cannot fill the queue by replacing it. There is no forced retry or synthetic reply.
This favors refusal over unbounded work. It is not a claim that the shared legacy
COM queue is globally bounded or that commands from different cores are serialized
as one logical seek. A busy refusal leaves the earlier admitted command intact.

The port separately retains the worker phase/HRESULT/error even after cancellation
or expiry. A late S_OK does not revive old intent. A rapid worker reply before the
submit call returns is preserved. A claimed native acceptance without a recorded
call becomes a failure. None of these records is a signature or hostile-operator
attestation, and arbitrary invalid native pointers are not sandboxed.

## Boundaries deliberately remaining open

SEEKING/SEEKED correlation, actual seek completion and preview/drag commit behavior
are **not implemented by step04A**. The cached command result is exposed for the
next integration but is not yet part of PlaybackView or GUI error presentation.
The progress tracker does not use these receipts to suppress EOS while a native
seek is pending. Multiple SetCurrentTime calls after prior worker replies can
still overlap at the Media Foundation asynchronous-operation level. Step04B must
reconcile that state before this checkpoint is proposed for user playback.

The before/after guards do not establish all-thread linearizability: stop/load
and a concurrent new admission are not one atomic transaction. In-flight native
side effects cannot be undone. Shutdown now detaches a typed group of owned resources and clears the old function
cache outside the core lock. Existing best-effort release routes and global
reference/address handling are otherwise unchanged. Error during AddRef/Release
prevents unqualified acceptance but does not prove native memory was released.
No event is renamed into a completion acknowledgment, and no fallback changes the
accepted nine-layout ABI or bypasses COM-thread ownership.

No new external dependency, lock, source version, timer frequency, library/database
setting, queue-repeat contract, canonical Git update or publication is introduced.
The source archive is an engineering checkpoint, not an instruction to replace the
user's working R5H or rerun unchanged ABI/lock campaigns.

## Verification

Tests exercise actual GUI handlers, facade, controller, adapter, core, manager
queue/drain and reference-retention helpers between controlled wx/worker/native
boundaries. Native return values use test-owned tables; no SDK DLL or media is
executed locally. Cases include dead/not-ready workers, full queue, shutdown after
insertion, lost submit reply, wrong thread, same-source reload, generation drift,
retention/release errors, exact HRESULT checking, timeout, late reply, invalid
inputs, concurrent reservations, cached reads and unchanged unrelated behavior.

The shared queue remains the existing implementation. Only its response contract
is structurally broadened from queue.Queue to a put_nowait response port; ordinary
synchronous and fire-and-forget tasks still use ordinary queues. Extracted public
methods are re-exported and retain owner-module patch points. Current extraction
and regression results are preserved separately from native Windows acceptance.

Reference: Microsoft IMFMediaEngine::SetCurrentTime documents S_OK versus error
and asynchronous SEEKING/SEEKED completion:
https://learn.microsoft.com/en-us/windows/win32/api/mfmediaengine/nf-mfmediaengine-imfmediaengine-setcurrenttime
