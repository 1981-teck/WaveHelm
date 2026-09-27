# R5I-step05B — visible continuity and terminal handoff

This checkpoint corrects the step05A cursor recheck. Local regression is distinct
from a new native Windows trial. No public release approval is implied.

## Measurement, drawn history and preview are different

`DisplayReading.position/duration/status` retain the observed-data contract. A
missing observation is never rewritten as a measured zero. A separate held ratio
can retain the last successfully drawn non-preview position. Input admission and
interpolation do not use that ratio as a fresh sample. Time labels remain unknown
when the measured pair is unavailable. An unconfirmed target is not native time.

The state manager supplies a visual continuity epoch: source/context/replay,
loading, stop and error transitions invalidate history. Same-playback seeks and
adjacent pause/resume revisions do not erase it merely to invalidate clock data.
The default for a custom facade lacking an epoch is conservative reset. Owner
replacement detected before or during a cache read clears history immediately.
The native clock cache also binds adapter/core identity, engine generation and
transport epoch. Those are cached observations, not native clock calls on the GUI.

A temporary refused view freezes the last actually drawn value, not an undrawn
interpolation endpoint or a pointer preview. A refused/cancelled seek can therefore
return from its preview to the previous confirmed drawing. That return is not
claimed as successful seeking. While the backend is pending, no historical visual
value authorizes another command. The next valid observed position may legitimately
be lower; backward seeks are not hidden behind a monotonic display clamp.

## Native end is a terminal fact, not an invented duration

A terminal snapshot contains the actual paired clock (possibly unknown), plus an
explicit native completion flag. Only native EOS sets that flag; the compatibility
metadata fallback is not upgraded to native evidence. A terminal flag draws the
end of the bar immediately without waiting for the causal interpolation segment.
It does not invent a numeric position equal to duration when the clock failed or
was acquired on the same coarse clock tick as a seek completion.

The shipped video observer now separates observing EOS from teardown. One cached
owner ticket binds adapter/core, engine generation, source and seek transport epoch.
The UI-owned transition requests terminal painting from the current cache, validates
ownership again, releases the native adapter, publishes the window-close event,
then advances the queue. `Refresh`/`Update` request final drawing before destruction;
there is no sleep or guaranteed visible dwell at 100 percent. Native paint behavior
and the user's actual videos still need Windows qualification.

A pending UI callback retains that terminal sample, including while the tracker
pumps native errors. It does not reacquire old media clocks or consume EOS again.
A missing UI dispatcher cannot move the GUI transition onto the polling worker.
A transient final view refusal can request at most three coalesced, normal-tick
retries of the read-only handoff. No native command, failed teardown or unknown EOS
is retried as success. Exhausted retries leave the route blocked and log the problem;
this does not promise automatic recovery from permanently unavailable UI state.

Source/seek/replay/stop changes invalidate an old ticket and retry. Reentrant input
during final painting cannot close a different generation or advance a stale queue.
The native operation and these before/after guards are not a global transaction,
ABA-proof ownership system or complete COM leak/invalid-pointer guarantee.

## Bounded, opt-in trace

Set `WAVEHELM_PROGRESS_TRACE_DIR` before launch to an existing absolute directory
outside the sources. No trace ring or trace I/O is enabled by default. A surface
retains at most 2,048 numeric/enum rows; older rows are counted as omitted. It writes
once on close, exclusively creating a JSON file with a 1 MiB/file budget and a
32-file directory budget. This is intended for a private user-owned diagnostic
directory, not protection against hostile concurrent filesystem replacement.

Records distinguish clock, measured pair, drawn ratio, request/seek phase,
revision/continuity, acquisition times, generation, terminal flag, validity and
preview. Paths and titles are not included. There is no per-frame file I/O or text
formatting. A crash may prevent close-time export. This records application inputs
and widget-write requests, not pixel presentation times or end-to-end video latency.

## Qualification and preserved scope

Regressions exercise the actual facade, both widgets, tracker, default event bus,
MainView end handler, seek queue and receipt between explicit wx/native fixtures.
Include rejected seeks, same-tick confirmation/EOS, repeated wakeups, source/core
replacement, late UI delivery, failed view capture and both possible clock/EOS
arrival orders. Retain native EOS authority and gesture admission guards.

Dependencies, six lock bundles, ABI declarations and queue-repeat policy are not
changed. The exact eight-file historical ABI receipt remains scoped; it does not
qualify the new handoff. Visible subtitles, broader COM/device lifetime, native
geometry/accessibility, translated statuses, audio seek acknowledgement, external
static/type/security gates, coverage, current supply-chain audits and canonical
Publish/Verify/remote CI remain separate. Never delete ordinary APPDATA user data.
