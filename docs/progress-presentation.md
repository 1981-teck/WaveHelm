# R5I step 03 — cache-only GUI progress consumers

## Ownership and scope

Both maintained progress bars consume `PlayerController.get_playback_view()`.
This accessor reads current Python playback/queue state and the step02 cached
`ProgressSnapshot`; it never invokes scalar clock getters, COM, or the mixer.
The context is checked before and after reading. These bounded guards are not
an atomic transaction with native playback or a proof against every ABA race.

`PlaybackView` is immutable and binds revision, state, path, queue index, title,
media kind, loop/shuffle flags and an optional matching cached sample. A malformed
or mismatched optional view is unavailable, not a request to poll a legacy backend.
A genuinely absent sample still permits a coherent current state/title.

## One source of progress, not two competing clocks

PLAYBACK_PROGRESS, PLAYER_STATE_CHANGED and VIDEO_DURATION_UPDATE are wakeups.
Their numeric/title/path payloads are not applied to either bar. The normal bus
subscriber requests at most one queued refresh per surface plus one executing
refresh. A delayed callback reads the current cache when it runs. It cannot replay
an old payload after a timer has drawn a newer sample. This bounds this consumer's
own work, not every callback or payload held elsewhere in the global event bus.

The existing timers remain 200 ms (mini-player) and 100 ms (overlay), but now only
read the cached view. Tracker sampling remains 250 ms. The overlay's pointer and
window-position maintenance can still call Win32; this is not a claim that every
GUI action is free of native calls, only that progress timers no longer wait for
native media-clock reads. A blocked producer is tested with an explicit Python
worker boundary, not as a measurement of Windows COM latency.

## Validity, resets and order

- KNOWN zero is displayed as zero, not discarded by truthiness.
- Position and duration are taken from one known pair; no metadata/native alternation.
- Unknown/error/expired samples may freeze the last complete pair for the same
  context, marked non-KNOWN. Mini-player time labels show `--:--`; the overlay has
  no textual time labels. There is no fabricated progress or interpolation.
- Acquisition older than one second or timestamped in the future is not fresh.
- Source/seek/replay revision changes clear the pair. One adjacent pause/resume
  revision for the same source can retain it as frozen; missed intermediate state
  transitions conservatively clear it instead of guessing a paused position.
- Stop/loading/error/no valid view clears both fields, never one old/new mixture.
- Decreasing revisions/sequences, same-sequence numeric substitution and older
  acquisition order cannot replace a newer accepted observation. Controller
  replacement resets the local ordering floor for that new owner.

## Seek and gesture boundary

The step01 canonical seek route is preserved. A forwarded request no longer stores
its target as an observed position or time label. The next cache read establishes
what can be shown; forwarding still is NOT native completion. No native SEEKING /
SEEKED acknowledgment, HRESULT/queue-admission repair or complete thumb-drag gesture
state machine is introduced here. While the existing drag guard is active, neither
rendered field changes; a skipped render is retried after the guard clears. The
existing click/EVT_SLIDER bindings remain and require later native gesture review.

## Lifecycle and failure behavior

Progress/state wakeups cannot write closed views. Mini-player's remaining deferred
UI callbacks also check closure at delivery. Failed widget writes clear the update
flag and close that surface. Timer creation failure closes it; cancellation failure
invalidates the timer handle and does not prevent subscription cleanup. These are
specific wx callback boundaries with logged causes, not retry-to-success or proof
that a failed native cancellation stopped the OS timer. Later callbacks are harmless
because the owner is closed. Partial rendering before a widget failure is possible.

Appearance/control construction and native placement have been extracted into
cohesive mixins; original public owner classes remain. Extraction-only methods were
AST-compared before migration. The two obsolete file-size exceptions were removed.
The dynamic wx/Win32 boundary retains existing `Any` annotations; the new internal
view and presentation models are typed and do not carry arbitrary event dictionaries.

## Remaining work and preserved legacy observations

Full asynchronous seek confirmation, a complete gesture preview/commit lifecycle,
bounded display-only interpolation and native sample/paint measurements remain.
No native wx/COM, real user clip, new dependency audit or release approval is claimed.
EOS, the strict producer, native ABI declarations and circular queue policy are
unchanged. Do not repeat ABI/lock installation or replace the working player for
manual testing at this intermediate checkpoint.

Unrelated appearance behavior is not globally repaired. For example the preserved
`MiniPlayerChrome._read_initial_volume` still uses the old `getter() or 1.0` default,
which can map an initial zero volume to full-scale display. This is outside the
progress-clock path and AST-identical to step02. Separate zero-volume startup tests
and a bounded audio-control correction are its closure criterion; it is not hidden
inside a claim of complete widget correctness.

Framework references: wxPython `wx.CallAfter`, `wx.CallLater`, `wx.Slider` event
contracts. Tests use explicit fake wx widgets and controlled delivery; those are
not a native paint-timing or end-to-end mouse-event qualification.
