# Bounded progress presentation — R5I step05

This step follows exact step04C. The producer cache, native seek receipt, EOS and
input-admission contracts are unchanged. A visual cursor is not a playback clock.

## Causal interpolation, not prediction

The GUI maintains measured position/duration separately from `visual_position`.
A recent new forward observation can move the cursor from the last drawn value
toward that **already observed** position. It never extrapolates beyond the latest
position, resumes a backend, submits a seek, or declares end of stream. Time labels
and gesture duration/admission use the measured data, not the animated value.

A segment lasts at most 250 ms, shortened by the interval between samples and the
remaining 500 ms observation-age allowance. A first sample is drawn immediately.
A repeated observation does not restart the segment; a late timer directly draws
the endpoint. Interpolation may introduce up to 250 ms of extra display lag under
normal promptly delivered sampling. This is not a native timestamp accuracy claim.
Unbounded UI scheduling delays are not fixed by this policy.

The two samples must have identical stream, source, queue index, playback revision,
state, generation, clock origin and duration. Sequence must increase, acquisition
intervals must not overlap, sample gaps must be 25–500 ms, query span at most 100 ms,
and the position advance must be positive and at most 500 ms. Only explicitly
native-video or mixer-audio observations are eligible; legacy-origin data is drawn
without animation. These are conservative presentation policy bounds, not codec or
API guarantees. A disallowed segment rebases to the measured value, not a clamped
fake position. Legitimate backward seeks and large jumps must remain visible.

## Freeze/rebase boundaries

Pause, stop, loading, error, pending/unknown seek, missing/stale observations and
invalid/regressive monotonic time cancel the segment. Rebase means that the latest
coherent measured value may be drawn once, then remains stationary. No claim is
made that the last interpolated pixel is retained in every state transition.

A new stationary observation also cancels interpolation. There is no new native
buffering detector: an unreported buffering onset cannot be known immediately.
Nevertheless the cursor cannot move beyond its latest measured endpoint. No new
sample means no continuing prediction. The existing unavailable labels remain.

Context changes reset both semantic fields. Failed reads cannot borrow the last
video's duration. After an acknowledged seek, step04B's fresh-generation and strictly
post-completion observation requirements remain. Targets are never motion endpoints.

## Both GUI consumers

Both timers now request a 33 ms delay for cache-only rendering. This nominal request
is not a measured frame rate; wx timers are main-thread and best-effort. Events
remain coalesced wakeups with no copied numeric authority. Interpolation retains one
segment, not a backlog of frames. Native clock calls and the 250 ms tracker cadence
are unchanged. The overlay's existing pointer/window maintenance is throttled to
at least 100 ms between such checks instead of running at every animation tick.

A motion-only mini-player tick updates only the cursor, not title/button/time label
strings. Existing per-sample rendering still updates labels and controls. Gesture
preview owns the thumb while active; rejected or cancelled previews repaint from
current measured data and reset motion. Closing or losing rendering ownership
invalidates outstanding motion. No per-tick log, disk access or native-clock call
is added. Work/state are fixed-size and bounded, but Python/wx creates managed
objects: this is **not** zero-allocation hard-real-time or GUI latency certification.

The preserved private reset helper now clears the measured/drawn record together;
a following current-view read may restore current playback, as before. It is not
a command to stop playback or erase the shared tracker cache.

## Qualification and next native gate

Local model and both-widget tests use controlled clocks, samples, wx controls and
native receipts. Native dependencies and decoder output are not simulated as proof.
Run the provided external step05 Prepare collector on Windows before a new manual
trial. Preserve the existing Python environment and normal application data.
No lock regeneration or repeat of unchanged nine-layout ABI declarations is needed.

Outstanding: actual Windows paint/capture/keyboard behavior, integrated source/seek
callback execution, measured sample-to-paint latency and visible smoothness. A full
native telemetry capture is not implemented by the model tests. Audio acknowledgement,
localized detailed seek captions, accessibility and larger project release gates
remain separate. The release is NOT_VERIFIED; previous native R5H results are not
relabelled as new step05 tests.

## Primary framework basis

- https://docs.wxpython.org/wx.Timer.html — main-thread, platform-dependent timing.
- https://docs.python.org/3.13/library/time.html#time.monotonic — elapsed-time clock.

These document APIs, not test execution or measured application performance.
