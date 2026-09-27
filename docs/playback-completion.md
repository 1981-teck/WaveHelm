# End-of-playback contract — R5H

## Defect and corrected decision

R5G accepted position >= duration - 0.3 even when a video poll_end() returned
false. The supplied manual trace reported completion at 2.00–2.08 seconds for
a duration of 2.26. An audio track was also reported within the same early
window. The trace does not measure which frames/samples reached the output.

R5H prefers the existing optional poll_end() boundary for both normal engines:

| Observation | Advance decision |
|---|---|
| exact True | Eligible end, subject to context/loop/dispatch checks |
| exact False | Not ended; no position/duration override |
| None, invalid result or query failure | Unknown/failed; do not advance |
| poll_end is absent | Legacy fallback only at finite positive duration, never early |
| present but non-callable poll_end | Broken capability, not legacy fallback |

Fallback additionally respects an available is_playing() veto: only exact False
can allow it. Without any native end/activity observer, a duration estimate
cannot prove decoder completion; this compatibility route is not used by the
shipped audio and video engines. Duration is display metadata on those routes.
No forced timeout advances playback when an observer fails indefinitely.

## Audio

The mixer is queried directly instead of using AudioEngine.is_playing(), which
maps its errors to false. A valid initialized mixer and an established progress
worker are required. Pause, stopped progress, missing current source, DSP reload
and per-play native loop suppress completion. Invalid initialization or flags,
errors and source/render/seek/worker changes across the query are not an end.
Short tracks require neither a prior busy sample nor a known duration.

The active player state and the engine's lifecycle guards distinguish stopped
or paused playback from idle completion. get_busy() reports activity, not the
reason every third-party decoder stopped; this change does not introduce new
decoder-error telemetry. Direct uncontrolled calls to pygame.mixer are outside
this lifecycle contract. No new application lock is held during mixer I/O.

## Observation freshness and UI dispatch

PlaybackStateManager.playback_revision increases on context/state updates,
including same-state replays, and before a seek is delegated to the backend.
ProgressTracker compares revision, player state, backend identity, track identity
and queue index around a query. PlayerController carries the revision in the
signature used to recheck deferred UI callbacks, even for the same path/index.

One completed context is dispatched once. A callback returning False explicitly
declines acceptance; the tracker keeps one pending observation (including native
signals already consumed by video poll_end) and retries on later ticks only while
the context remains current. A successful or legacy-None callback consumes it.
A callback exception is logged and not retried blindly. A user transport/context
change invalidates pending observations. No unbounded queue or history is added.

This is a bounded freshness check, not a transaction or a proof of linearizability
of the entire legacy player. The existing UI-dispatch model, native callback
reentrancy and whole-engine lifecycle are not globally redesigned. The class's
old start/stop thread lifecycle and existing worker ownership assumptions remain.

## Loop and compatibility

QueueManager.next retains its existing circular wrap-around. R5H does not add a
stop-at-end-of-queue policy or change shuffle/repeat UX. Native audio repeat uses
the mode captured for the current play; changing the next-play flag does not
interrupt that play. Video repeat remains owned by VideoController/Media Engine.

The existing legacy-fallback positive test now supplies position==duration,
not duration-0.1. It still tests callback behavior; anticipatory behavior is
intentionally removed, not kept as a compatibility requirement. No test is removed.

## Verification boundaries

Tests cover the original 2.26-second failure, delayed EOS, inaccurate/unknown
metadata, malformed/failed queries, short clips, pause/stop/loading, seek, repeat,
context replacement, callback exceptions, duplicates and delayed UI dispatch.
Most tests use test-owned backends/mixer observations, not native media or device
output. One assembled AudioEngine/pygame error-boundary test requires real pygame.
Native R5H execution is separate from prior successful R5G Windows evidence.

Run the supplied R5H Prepare collector in the existing private Windows Python
3.12 dev environment, with no test selection exclusions. Preserve its ZIP for
both success and failure. Then check actual short-file completion (with the
last frame/audio tail observed), pause/seek near the end and automatic next.
Do not regenerate unchanged locks or repeat the identical accepted ABI gate.
Preserve ordinary APPDATA/WaveHelm user data; it is not a test temporary directory.

## Primary API references

- https://www.pygame.org/docs/ref/music.html#pygame.mixer.music.get_busy
  returns false for paused music as well as idle music.
- https://www.pygame.org/docs/ref/music.html#pygame.mixer.music.get_pos
  reports elapsed play time, not starting offsets.
- https://learn.microsoft.com/en-us/windows/win32/api/mfmediaengine/nf-mfmediaengine-imfmediaengine-isended
  defines native end independently of an estimated time threshold.

These describe APIs; project verification results are in the separate delivery
report/evidence. No fresh vulnerability scan or public release is implied.
