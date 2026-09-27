# Event-bus observation snapshots — 08M repair

24 September 2026. STANDARD / SECURITY_CRITICAL / bugfix. Bounded L2 engineering review.
This changes observation acquisition only, not event admission, dispatch or history ownership.

## Cause and decision

08K-V01 is a reproducible liveness defect, not just a flaky-test expectation.
The old reader captures references/version under the bus lock, copies containers after
unlocking, and requires the version still to match at a later lock acquisition. An
ordinary completed publication between those steps invalidates that attempt. Sixteen
such legitimate interleavings exhaust the reader even when all producers complete and
no data is corrupt. The same mechanism affects history, statistics and active-subscription
listing. A finite optimistic retry budget cannot itself establish convergence.

All discovered direct in-repository users of the public observation APIs are the event-bus
binding tables and tests; no new playback incident or external consumer is invented.
The old stress test requests completed reads under concurrent publications. Its assertion
and existing 16-attempt constant remain unchanged.

## Chosen repair

Retain the exact optimistic algorithm and its 16 attempts. Only after exhaustion, take
one shallow structural snapshot while holding the SAME existing RLock:

* history: tuple of retained EventRecord references;
* observation state: copied fixed counters, subscriber membership, per-event counters,
  rate-window references, current history size and shutdown flag.

This is a real synchronized acquisition, not a fabricated empty/default result, a retry
budget increase, a quiescence wait or a new scheduler. Writers still use their existing
critical sections. No global publisher queue, pause protocol or replacement event bus is
introduced. A writer needing that lock can be delayed while the slow capture is in progress.

The lock is released before payload cloning, filtering, subscription is_active properties,
rate-window config access and final result construction. Existing callback execution is
unchanged. MemoryError, invalid payload errors and process-control exceptions propagate;
the new branch catches nothing and releases the lock with the ordinary context manager.

A completed getter observes an actual structural cut during its call, not necessarily
state at return. Separate history and stats calls are NOT one atomic combined observation.
Subscription objects remain live references: their is_active/config properties are evaluated
after capture, as before. Arbitrary concurrent external mutation of subscription internals
or private containers is outside the existing supported owner protocol.

## Explicit controlled deviation — OVERRIDE C18-08M

Rule: C18 normally permits only O(1) worst-case work under a shared lock.
Scope: the TWO new exhausted-reader branches in audio_event_bus_observation.py only.
Reason: ordinary concurrent producers must not make a valid observation fail simply by
using all optimistic attempts. Returning stale/inconsistent data or increasing retries
does not fix this. No snapshot-read fallback exists in the baseline.

Impact: shallow history capture is O(H), with the unchanged H <= 10,000 hard cap.
The observation capture is O(S + E + R), for subscriber slots, event-key counters and
rate-limit keys. Those registries have no aggregate hard cap in the inherited API; this
patch does NOT claim bounded worst-case latency/memory for arbitrary registry growth.
Copies allocate. Neither getter is real-time safe. This is a documented exception to
C18, not an O(1), wait-free, nonblocking or deadline guarantee and not a HOT-to-COLD relabel.

Alternative considered: immutable/persistent observation state with generation-bound
roots and bounded retention could reduce reader locking, but requires a coordinated
rewrite of subscribe/unsubscribe, rate limits, publication, reset and shutdown. It would
alter the hot writer path and compatibility surfaces across multiple modules. That is
not selected as a small repair. Increasing 16, weakening the test or serializing entire
publish operations were rejected.

Containment: unchanged optimistic fast path; fallback only after exhaustion; no payload
walk, I/O, callback or property invocation while the new lock section is held. Tests force
all 16 conflicts with real writers, check clone/property execution outside the lock,
preserve errors, exercise zero/max history, resets/shutdown and multiple readers/writers.
Local performance observations are separately retained, not promoted to Windows deadlines.
Closure of this performance deviation requires a separately qualified bounded-state design
or an explicit supported registry/cost policy. No new cap or hidden truncation is imposed here.

## Regression evidence design

test_event_bus_snapshot_liveness uses a reader-local trace hook to schedule one actual
bus mutation after the initial unlock in each optimistic attempt. A separate Python
thread executes real publish/clear/reset/subscribe/rate/shutdown APIs. All ordinary
16-attempt interleavings are completed; no failure is injected into tuple/dict copying.

test_event_bus_snapshot_contracts additionally forces the slow branch with a test-only
zero retry setting, without changing the shipped constant. Those branch fixtures are
not normal runtime configuration. They cover real data, detached outputs, exception
identity, lock release, reentrant callbacks, 10,000 retained records and real-thread
contention. Deliberately broken private containers/properties are negative-test fixtures.

Original project tests are byte-identical. Fixed repeated stress runs are supplementary;
the deterministic before/after regression is the primary liveness evidence.

## Limits

Assumes cooperative writers using the existing bus lock and normally completing Python
operations. No guarantee against starvation in the OS lock scheduler, forcible termination,
native crashes, hostile monkeypatches, arbitrary same-thread signal/finalizer mutation,
allocation failure or corruption is added. No secret, user database or media is used.
This is not a native Windows/GUI/COM/audio-device test or whole-application performance
certification. Accepted older Windows gates remain separate and closed.

Python references: https://docs.python.org/3.12/library/threading.html
and https://docs.python.org/3.12/library/copy.html (lock and shallow-copy semantics only).
