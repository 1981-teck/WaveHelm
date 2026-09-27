# Acquired MediaEngine references — step08R

## Scope and ownership

CreateInstance and each successful QueryInterface acquire independent references.
Numerical pointer equality never merges them. EngineReference preallocates its native
output before dispatch and confirms successful, non-null output before publishing a
borrowed view. Its ComPtr storage uses the exclusive release protocol from 08P.

The live core continues exposing raw typed views to playback/seek code. Those are
borrowed views, not owners. EngineResources holds the base and Ex owners, along with
the previous factory/attribute/notify fields. A disagreement between a published view
and its owner rejects detachment rather than inventing a new owner. The public 08Q
shutdown job receives the actual owners and still claims before destructive effects.

The legacy recent-address registry is byte-identical. It is not cleared, disabled,
expired early or consulted for provenance-bearing owned releases. Raw compatibility
entrypoints, raw timed-text/DXGI uses and legacy queries without an owner are NOT migrated
or globally qualified. Their old raw-address failures remain visible.

## Acquisition and error disposition

An output slot transitions EMPTY -> RECEIVING -> ACQUIRED or NO_INTERFACE. A failed
call with unexpected non-null output remains UNCONFIRMED. An interrupted call remains
RECEIVING even if it wrote a pointer: incomplete dispatch is not a successful ownership
receipt. No dereference or Release is attempted through such unconfirmed output.

Creation retains its resource record on the core before external operations. Confirmed
engine output is captured before fallible post-acquisition casts or subsequent queries.
Factory/attributes and notify values are recorded as they become available. On failure,
rollback executes at most once on the calling COM-thread path and retains its primary
creation exception separately from a cleanup failure. A clean rollback clears the
pending record; failed/uncertain cleanup stays reachable and blocks recreation rather
than silently overwriting the old lifetime.

Factory/attributes/notify retain their existing best-effort cleanup rules. A returned
cleanup is not proof of every HRESULT or complete native reclamation. Their separate GC,
error and native lifetime contracts are not promoted by this repair.

A successful clean rollback now permits an explicit subsequent create on the same core.
Unlike the old self.shutdown() path it does not permanently close a clean core merely
because creation failed. Existing shutdown intent is never undone. This is an intentional
recovery-contract change, not a claim that every failure behavior remains identical.

## Late extension queries and rendering

An unavailable initial Ex interface leaves an empty unacquired slot. A later successful
query receives into that unused slot; a second overlapping query refuses while it is
receiving. Rendering and stream selection share this path outside the core state lock.
Publication rechecks shutdown, the raw base view, generation and resource-record identity.
A stale result consumes only the freshly acquired Ex reference.

Shutdown may detach a pending Ex owner: its queued job retains the same record. Normal
managed-worker execution completes the query before draining cleanup. Same-thread
reentrant cleanup can instead observe uncertainty and fail explicitly; this does not
authorize replay. General borrowed-use races still require leases/lifetime coordination.

## Explicit release, bounds and finalization

EngineReference never calls COM from __del__. Losing its last Python owner may therefore
leak unresolved references; it does not authorize guessing an apartment or retrying an
ambiguous native call. This is not leak-free, eventual cleanup or global quarantine.
Generic ComPtr finalization for other resource types is unchanged.

Owned release inspects ComPtr's retained disposition. An integer zero is not proof of
success. Undispatched failure or uncertain dispatch remains a cleanup failure, retaining
the owner in the job or retired creation/rebind record. There is no automatic retry.
Process-control and memory exceptions retain normal propagation.

Every record has fixed cardinality: two engine owners, the four existing resource fields
and bounded state. No new global table, worker, lock-held registry scan or foreign call
under a state lock is introduced. Python allocations, exception tracebacks, native calls
and scheduler waits are not hard-real-time bounded.

## Verification boundary

Tests run actual core acquisition, query, rebind, rendering, shutdown and ComPtr code.
Factories and native effects are explicitly recording test boundaries. Test-owned ctypes
storage remains allocated and independent ledgers count references. Real threads, queues
and 08Q worker admission are exercised. This is not Windows allocator reuse, an actual
COM apartment, MediaEngine playback or measured native memory growth.

The original raw-shell tests remain unmodified and continue demonstrating the unmodified
raw API limitation. Native Windows confirmation of the P/Q/R chain is required before an
operating release. Old accepted Windows gates remain historical evidence in their scopes.
