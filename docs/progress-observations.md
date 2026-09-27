# Progress observation contract — R5I step 02

This is an intermediate producer-side repair, not completed bar smoothness.
An immutable `ProgressSnapshot` binds one stream, sequence, playback revision,
state, source identity, queue index and a typed clock observation. Zero position
is valid. Unavailable, failed and stale readings carry no numeric value. Errors
retain only bounded diagnostic text; records do not retain exception objects.

The acquisition start/end timestamps use the monotonic clock. Position and
native duration are read on one video COM task and tied to the same engine/source
before and after the read. This is an acquisition interval, not a sample-accurate
atomic clock or proof against all concurrent source transitions. Audio position
comes from the mixer and duration from its current loaded-file metadata; that
origin is explicit. Neither route falls back to another media kind's clock.

Existing scalar getters remain compatibility APIs and may retain their old
error behavior. The strict observer does not call them for shipped engines.
An absent observer may use explicitly labelled legacy separate-getter reads;
a broken or failing observer is not treated as permission for a legacy fallback.

The tracker retains at most one immutable current sample. Future display
consumers must validate stream, revision, sequence, source and age. Unknown
pairs are not projected to the old numeric progress event as a false zero.
This does not migrate the GUI timers or the audio worker's older event producer.

Native seek queue admission, HRESULT/SEEKED acknowledgement, latest-only GUI
handoff, state-event ordering, drag lifecycle and display interpolation remain
separate steps. A sample never authorizes EOS or confirms a pending seek.
Windows execution and the user's exact visual timing remain NOT_VERIFIED.


## Scope and consumers

- Implemented: native/core/adapter/controller pairing, typed audio observations,
  frozen tracker context, before/after guards, one-slot cache, acquisition ordering,
  stale-result suppression and nonnumeric error status.
- Implemented: explicit wire serialization for the real event bus; its accepted
  payload types and defensive history-copy rules are not widened.
- Not migrated: either GUI bar timer/state consumer, audio's older progress worker,
  GUI latest-only callback scheduling, visual interpolation and pending seek UI.
- Not asserted: atomic multimedia time, all-thread linearizability, captured samples
  proving buffering state, seek completion, rendered output or native leak freedom.

The facade accessor defaults to a one-second acquisition-age limit and performs
no native query. An expired same-context sample becomes STALE; a changed playback
context returns no sample. Paused/idle states do not acquire new tracker samples
in this checkpoint; later display consumers must freeze/rebase from state events.
The clock read now precedes completion polling because the native end observer
may close the adapter. EOS decision does not depend on clock validity.

Each video observation holds one scoped COM reference in the serialized native
task and releases it in finally, including query error and observed replacement.
This is not a repair of all other legacy COM ownership paths. Before/after guards
cannot detect arbitrary ABA transitions; concurrent playback mutation must still
respect the existing ownership/dispatch contract.

Storage is one bounded record, not a sample history. Error type/message fields
are capped at 256 characters each, source identities at 32,768 characters. Sample
sequence and playback epoch use monotonic Python counters. Native timing, GUI
queue age and end-user smoothness have not been measured here.

## Primary references

- https://learn.microsoft.com/en-us/windows/win32/api/mfmediaengine/nf-mfmediaengine-imfmediaengine-getduration
- https://www.pygame.org/docs/ref/music.html#pygame.mixer.music.get_pos
- https://learn.microsoft.com/en-us/windows/win32/com/rules-for-managing-reference-counts

These document API contracts, not test results or current vulnerability status.
