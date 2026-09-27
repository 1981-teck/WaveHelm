# Progress and seek repair — checkpoints R5I steps 01–03 and step04A

## Scope and admission contract

This is an intermediate development checkpoint, not a smoothness acceptance or
release. Step A changes EngineController.seek and PlayerController.seek to return
SeekDispatch.REJECTED or SeekDispatch.FORWARDED_UNCONFIRMED. Boolean coercion is
forbidden: a request returning without explicit synchronous refusal is not proof
of queue admission, successful SetCurrentTime or a completed native seek.

The engine accepts finite plain int/float seconds; negative finite inputs clamp
to zero. Boolean, non-finite, string and overflowing inputs are rejected before
revision or native work. Loading, stopped, idle, error and shutdown states refuse
seeks. Missing/noncallable backend methods or revision guards fail closed.
A valid request invalidates old completion observations before calling its backend,
including when that call fails. Snapshot/revision checks are not a new transport
lock or proof of all-thread linearizability.

Existing void backend methods remain supported only as unconfirmed forwarding.
Step04A removes void-result acceptance on the shipped video path and captures
queue/HRESULT facts. Other legacy backends and native asynchronous seek completion
remain outside that earlier admission contract. The UI helper now uses only the facade. It maps explicit unconfirmed forwarding to
its historical boolean interface, not to native completion. A missing facade,
None, truthy non-bool result, or refusal cannot cause direct-backend fallback.
Both widget handlers call this helper. Their cache-only presentation migration
is documented in step03 below; native request completion remains separate. Compatible custom facades returning exact True
are treated as unconfirmed forwarding, not as proof of a guarded implementation.

The tests traverse real slider/pointer handlers with test-owned wx widgets through
the real helper, PlayerController, EngineController and PlaybackStateManager.
Native wx event ordering and actual playback remain NOT_RUN here. This does not
establish native SEEKED reconciliation. Step03 removes the optimistic target
from the observed display model; a request is still only unconfirmed forwarding.

## Remaining steps

1. COMPLETED LOCALLY: both GUI seek paths use the guarded facade with no backend fallback.
2. COMPLETED LOCALLY: remove the extra post-completion wait; regular ticks and EOS latches remain.
3. COMPLETED LOCALLY FOR PRODUCER: typed clock/sample acquisition and explicit unknown/error semantics.
   GUI use of this channel remains part of step 4.
4. COMPLETED LOCALLY: both GUI progress surfaces use cache-only current views,
   coalesced wakeups and lifetime guards. Full pending-seek/drag reconciliation remains open.
5. COMPLETED LOCALLY FOR COMMAND TRANSPORT (step04A): exact video queue admission,
   bound native invocation and cached HRESULT/error receipts. SEEKED is still open.
6. Integrate pending/native seek completion and complete gestures, then bounded
   display-only interpolation and native trace/manual acceptance. Never use interpolation for EOS.

No change to locks, ABI layout definitions, queue-repeat or user data is intended.
Historical receipts remain valid only for their original bytes/scopes. Do not
repeat native tests for intermediate commits merely to establish local progress.

## Cadence contract

The worker keeps its existing interruptible 250 ms sampling interval. Completion
no longer adds a one-second sleep. Pending or consumed completion is protected by
existing context/revision latches, not a timing delay. A controlled immediate-next
fixture gets its first new-clip observation after 250 ms, formerly 1,250 ms; this
is a virtual-clock regression, not native GUI or decoded frame timing. Shorter
clips may naturally end between polls. Pause, stop, repeated EOS, same-path replay,
callback failure and declined UI dispatch are explicitly exercised. Native EOS
authority, circular queue navigation and audio/video loop policy are unchanged.


## Step 02 — coherent producer observations (2026-09-19)

The shipped video controller/adapter/core now expose one strict `observe_progress`
route. It obtains position and native duration in one COM work item, retains one
interface reference during the read and validates engine/source identity around
calls. Audio has an analogous mixer-position/loaded-duration observation, with
explicit provenance and reload/seek/worker checks. Scalar legacy getters remain
compatibility APIs, not the source of the new strict observations.

The tracker keeps one immutable typed sample with stream, acquisition sequence,
playback revision, source/path, state, index, validity and monotonic interval.
Valid zero is preserved. Failed/absent/stale readings carry no number. Advertised
observer failures cannot trigger scalar fallback. Legacy backends are explicitly
labelled. The sequence is reserved before reading; old results cannot replace or
clear newer samples. Stop/restart invalidates in-flight samples through an epoch.

A facade accessor reads the cache without native calls. The unchanged event bus
receives only serialized scalar/container data, not arbitrary Python objects.
Old numeric payload keys remain for known pairs. Failed pairs are retained in the
cache but do not emit fake-zero numeric progress. Native EOS remains authoritative;
the clock read occurs before poll_end so native teardown cannot erase the logged
pair retroactively. No clock sample is a seek-completion acknowledgement.

At the step02 checkpoint the GUI still used scalar getters. Step03 now migrates
both maintained surfaces to the cache. The additional audio legacy publisher is
unchanged but its numeric events no longer control these bars. Native seek
acknowledgement, complete gestures and interpolation remain open; see
`progress-observations.md` and `progress-presentation.md`.


## Step 03 — current cache views at both GUI consumers (2026-09-19)

The former giant view modules are decomposed without changing public owner class
names. Current state/title and progress are read through one typed cache-only
facade view, with no GUI media-clock query or legacy scalar fallback. Progress,
state and duration events are coalesced wakeups, not queued mutable observations.
Expired/error data freezes a marked old pair rather than inventing zero; context
changes reset both values. Close, failed repaint and failed timer operations retain
deterministic invalidation/cleanup. Forwarded seeks do not store a confirmed target.

Local contracts exercise the actual tracker, default bus, facade and both widget
methods with test-owned wx/native observations. A native Windows GUI run, native
paint/smoothness acceptance and async seek completion have NOT_RUN. This remains
an intermediate source checkpoint, not a new user installation or a release.


## Step04A — tracked native seek commands (2026-09-19)

See `native-seek-admission.md` for the exact receipt phases, bounded pending slot,
thread/source/engine checks and remaining completion/gesture/EOS integration. This
substep does not complete step04. GUI display remains the step03 cache-only path.
No new user-PC command, dependency installation, ABI rerun or release is requested.


## Step04B — serialized completion and presentation (reconstruction, 2026-09-19)

The unrecovered prior in-session work is not used as source or test evidence. This
checkpoint is rebuilt from exact step04A. Seek slots now extend through native
SEEKING/SEEKED plus S_OK, not only queue/worker completion. Notifications bind to
current core/generation without native clock calls. EOS gates preserve error pumping;
paused seeks continue clock acquisition. Both bars read typed cached seek status and
require a clock sample acquired after native completion before showing new values.
See `native-seek-completion.md` for attribution, timeout and lifecycle limits.

Still open: full drag/preview/commit interaction, audio command acknowledgement,
localized pending/failure feedback, bounded interpolation and native sample/paint
qualification. No changed-code Windows success is inferred from earlier R5H receipts.

## Step04C — GUI gesture checkpoint (2026-09-19)

Both progress bars now separate preview, one canonical release request and measured
position. A bounded context/duration anchor, explicit capture/key cancellation,
no-change keyboard cleanup, stale input-view refusal and duplicate end suppression
are locally implemented. See `seek-gestures.md` for contracts and native limitations.
Input cancellation does not undo an already-issued native operation. No interpolation
or native Windows qualification is included. Next: bounded display-only interpolation
and diagnostics, then integrated Windows input/playback validation. Localized detailed
seek-status captions, audio acknowledgement and assistive-technology review remain
explicit limitations; no current release approval follows from this checkpoint.


## Step05 — causal cursor interpolation (2026-09-19)

Both bars animate only between already observed positions, never beyond the latest
clock sample. Each fixed-size segment is capped at 250 ms and recent-sample bounds;
no update can renew the segment's deadline. State, receipt, source, error, stationary
clock and input-ownership changes cancel or rebase motion. Measured position remains
separate from the visual cursor and native EOS. Nominal GUI delay is 33 ms, with
overlay native pointer maintenance still limited to its previous 100 ms cadence.
See `progress-motion.md` for all policy bounds, latency and buffering limitations.

This completes the local interpolation implementation, not the user's visual
acceptance or native qualification of the cumulative R5I code. Next is integrated
Windows Prepare using the existing environment, then a targeted real-media/gesture
trial. Native sample/paint telemetry, audio acknowledgement, translated seek status
and remaining release gates are not silently marked closed.


## Step05B — correction after the step05A manual cursor recheck

The observed false-zero and dropped terminal-presentation mechanisms are corrected
in a separate candidate. Measured/held/preview values remain distinct. The final
native fact is retained and handed to the UI before native release/window close.
The clock owner and bounded UI retry have explicit negative/interleaving tests.
See cursor-continuity-step05b.md for contracts and trace limits. Native tests and
the actual-media visual trial must identify the new bytes; earlier step05A PASS
cannot be relabeled as step05B execution. Existing environment/locks/ABI need no
reinstallation or identical rerun solely for this correction.
