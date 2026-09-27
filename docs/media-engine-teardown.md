# Detached MediaEngine teardown — step08Q

## Scope and compatibility

Stage B follows the exclusive ComPtr ownership repair in 08P. This repair prevents
08O-F03: timeout-induced replay of stop/shutdown/release on one detached bundle.
It does not repair the remaining raw-address TTL heuristic (08O-F01), certify
native COM apartments, or prove that every acquired reference is reclaimed.

The public `shutdown()` signature and return of `None` remain. It is best effort,
not a completion receipt. `get_shutdown_snapshot()` observes the retained job.
The snapshot is absent until a bundle is detached. Existing local release leaf
helpers remain for compatibility and stage C, but public shutdown never chooses
a local fallback. The obsolete test requiring async/local retries is replaced
by a no-repost/no-local assertion; its original source and outcomes are archived.
The existing seek lifecycle fixture now explicitly refuses the new cleanup
boundary. Its cancellation assertions are unchanged; cleanup is not its test scope.

## One owner and one execution

A job is allocated before detachment. The core lock publishes it together with
the six-reference tuple before clearing the old fields. A second shutdown does
not detach or submit another bundle. Cache disposal stays outside the core lock.
The job claims execution before stop/Pause/SetCurrentTime/Shutdown/Release.
Reentrant, concurrent, delayed and duplicated callbacks cannot claim it again.
No state lock is held across the cleanup routine or foreign operations.

`PREPARED` -> `RUNNING` -> `RETURNED` or `FAILED_UNCERTAIN`.
A worker rejection before execution produces terminal `REJECTED`.
`RETURNED` means the existing best-effort cleanup routine returned, NOT proof that
all native methods succeeded or all references were released. Its retained
specific-error catches and the raw address guard remain unchanged.

Failed execution retains its original Python exception and bundle and cannot be
retried automatically. Process-control and memory exceptions are recorded by
finalization and propagate; no BaseException catch translates them into success.
When notify cleanup masks an earlier failure, Python's original exception context
is retained. A successful invocation inside an unrelated except block has no error.

## Submission and completion

The native adapter uses its already-existing manager, including after adapter
close. It neither creates nor starts a new worker. Detached cleanup is not a seek:
new source/core state does not invalidate ownership of the older detached bundle.

The dedicated manager submission validates a live, ready, non-closing worker.
Check and queue insertion share its shutdown lock; full or unavailable workers
return False. Admitted tasks recheck the same worker and run only on that worker.
If called on the managed COM thread, cleanup executes inline after the same
validation, avoiding a self-wait. New admission does not change the existing
general/seek submission methods or the legacy queue's unbounded capacity.

Admission (NOT_SUBMITTED/SUBMITTING/ACCEPTED/REJECTED/UNCONFIRMED/INCONSISTENT)
is distinct from execution. A transport failure can leave an admitted task alive.
It never revokes its claim or authorizes an automatic replacement callback.
The wait uses the existing manager timeout, validated as finite, nonnegative and
within threading.TIMEOUT_MAX. Expiration records wait_expired, not cancellation.
Callback finalization sets completion independently of the worker reply, so loss
of that reply cannot make a completed job eligible again. A successful bare reply
without observed callback execution cannot create a RETURNED state. A late worker
rejection retains its exact exception and cancels an unstarted callback.

Historical third-party adapters with only call_on_com_thread receive one guarded
synchronous attempt. Their existing routing contract is trusted, not independently
verified; admission remains UNCONFIRMED even if they return without invoking work.
No post_to_com_thread or local fallback follows a legacy exception. A contradictory
negative admission after observed execution is recorded as INCONSISTENT and raises.

## Bounds, retention and exclusions

There is one job, one six-reference bundle, one receipt and one completion event
per detached core. No new global registry, retry queue, lifetime address cache,
unbounded log or lock-held scan is introduced. Bookkeeping is fixed-cardinality;
Python allocations, exception tracebacks and waits are not hard-real-time bounded.
The legacy queue, registry costs and 08M observation override remain separate.

Resources after refusal/failure remain reachable from the job/core; while pending,
the queued callback also retains the job. Observation is available while that
owner is retained. This patch is not a global quarantine/recovery registry and
does not promise safe reclamation after the last owner is discarded, arbitrary
GC/finalizer timing, process death, inaccessible native allocations or every
partial-construction failure. Borrowed pointer lifetime and apartment correctness
remain caller obligations. ComPtr finalizer apartment validation is not introduced.
Do not release a retained uncertain raw reference manually on an arbitrary thread.

The direct _shutdown_and_release_local and rebind release leaf functions are not
converted to single-execution tokens. The guarantee concerns the bundle dispatched
by public shutdown; calling those leaves manually remains outside that guarantee.
Arbitrary private-state mutation, same-thread signal interleavings at each bytecode,
worker fairness, hard native hangs and native allocator reuse remain unqualified.

## Evidence and pending qualification

Tests use actual manager admission, queues, drain/rejection and Python threads,
with controlled gates. Test-owned IUnknown-shaped memory stays allocated and its
independent ledger counts references; native effects are recorded, not executed.
The original two timeout regressions fail on exact 08P and pass after the patch.
Native Windows COM/MediaEngine qualification is still required after the staged
repairs; the previous Windows gates are historical, not approval of this protocol.
