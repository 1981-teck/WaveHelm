# ComPtr exclusive release ownership (08P / stage A)

## Scope and compatibility

One ComPtr owns one current acquired interface reference. A pointer returned by
`ptr`, `as_interface` or `as_iunknown` is a borrowed view, not another acquisition.
Only an explicit AddRef/QueryInterface or native creation permits another owner.
`attach` at the same current address is still a no-op; `adopt` must receive a NEW
reference even when its numerical address is equal. No implicit AddRef is added.

This is stage A of the 08O correction plan. It repairs 08O-F02. Raw-pointer TTL
quarantine (08O-F01) and replayed shutdown jobs (08O-F03) are NOT repaired here.
Native COM/allocator/apartment/MediaEngine qualification is still NOT_VERIFIED.

## Claim and finite state

`release`, `attach` and `adopt` exchange the current slot under the existing RLock.
At most one release attempt and one separately attached current reference exist
per wrapper. Native vtable lookup, argument preparation, Release and logging happen
outside that lock. Pointer storage and the attempt object are allocated beforehand.
The code performs constant-cardinality bookkeeping, not a global address registry.

A direct release clears its owned slot before resolving or invoking Release.
A duplicate release sees no current ownership and makes no call. Replacement during
DISPATCHED is allowed into the empty slot, and is not invalidated when the old call
finishes. A second destructive operation on a nonempty replacement is rejected with
RuntimeError while the first attempt is active. The rejected incoming reference is
still owned by its sender. No automatic waiting or cross-apartment invocation is added.

Replacement via attach/adopt is admitted atomically BEFORE cleanup of the displaced
reference. Consequently a cleanup error does not roll back that admitted replacement.
This is a deliberate compatibility change for overlapping/reentrant mutation: two
callers cannot overwrite each other's newly acquired references silently.

## Results and failure disposition

The existing `release() -> int` and best-effort specific-error logging are retained.
An integer zero, and particularly safe_release's None result, is NOT a completion
receipt. `release_snapshot` returns immutable latest-attempt metadata: address,
captured generation, state, exact Python exception object and optional reference count.
The COM reference count is diagnostic, not lifetime identity.

| Snapshot state | Meaning |
|---|---|
| preparing | Ownership claimed; no foreign call dispatched yet. |
| dispatched | The call boundary has been entered; completion is not yet confirmed. |
| released | The foreign call and return conversion completed normally. |
| not_dispatched | Preparation failed; the old reference was restored to an empty current slot. |
| held_not_dispatched | Preparation failed but a replacement is already current; the old reference is held separately. |
| uncertain | A dispatched call/conversion failed; the old reference is NOT made releasable again. |
| transferred | The held, proven-undispatched reference was explicitly detached. |

A known pre-dispatch failure permits a subsequent EXPLICIT release attempt. A held
undispatched reference can be transferred once with `detach_unreleased`; this never
invokes Release and never transfers an uncertain reference. The current slot is
unchanged. An uncertain/held receipt blocks subsequent release/attach/adopt operations
rather than overwriting unresolved ownership. `detach` may still transfer the separately
held current slot, never the quarantined uncertain old reference.

Specific existing exceptions retain the old logged-zero behavior, with exact error
identity recorded. KeyboardInterrupt, SystemExit, MemoryError and other unlisted
exceptions propagate unchanged after finalization. A failed return conversion remains
uncertain even if the receiver already decremented its reference count.

## Finalizers and observable limits

The finalizer never retries a recorded unsuccessful attempt, including an undispatched
one. The original exception can retain a traceback/reference cycle; late garbage
collection must not become a new destructive attempt after temporary conditions change.
This deliberately favors explicit unresolved disposition over an unverified retry.
One receipt is retained, not an unbounded history. Its traceback/exception graph has
no new universal byte-size bound. There is no complete memory-budget claim.

Read-only snapshots do not retain native ownership. The wrapper lock does not validate
arbitrary addresses, COM apartments or borrowed-pointer lifetime. In particular
concurrent borrowed use or add_ref versus release still requires caller synchronization;
this patch does not turn raw casts into leases. Hard termination, arbitrary private
mutation, every bytecode-level signal boundary and Python/native memory exhaustion
do not acquire exactly-once or eventual-release guarantees. Finite ownership claims
are not a proof that an in-flight foreign call terminates.

## Verification and operational boundary

The original four 08O reentry/overlap regressions run unchanged. Added tests use
allocated, never-freed IUnknown-shaped storage with independent reference accounting.
Python boundary recorders introduce exact exceptions without raising through ctypes.
Cases cover equal-address separate acquisitions, repeated release, eight simultaneous
callers, attach/adopt/detach transfer, failure before dispatch, uncertain effects,
missing callback admission, allocation failure and late garbage collection.

These tests do not initialize Windows COM or a MediaEngine. The local suite's success
is not native clearance. Do not replace a working installation on this checkpoint alone.
Stage B must bind teardown submission/execution/completion before stage C migrates
the raw engine references away from the TTL heuristic.

Microsoft mechanism references (not execution evidence), consulted 25 September 2026:
- https://learn.microsoft.com/en-us/windows/win32/com/implementing-reference-counting
- https://learn.microsoft.com/en-us/windows/win32/api/unknwn/nf-unknwn-iunknown-release
