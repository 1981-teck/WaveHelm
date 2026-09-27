# Progress-bar input lifecycle — R5I step04C

## Scope

Both maintained wx progress surfaces share one GUI-owned gesture state machine.
It reads only the current guarded PlaybackPresentation cache. No gesture calls a
scalar/native clock getter, changes playback state to pause/resume, or decides EOS.
Step04B remains the native request/completion implementation and is not rewritten.
This is a local engineering checkpoint; real Windows input/paint qualification is open.

## Intent, preview, release

A valid finite clock pair in playing/paused state can anchor a gesture. The anchor
holds controller identity, path, queue index, playback revision/state/media kind,
and the duration at begin. A known zero is valid. A paused retained pair is allowed
only with no current sample or native receipt; pending/error seek data refuse input.
The preview changes only the thumb. Measured position/duration and the cache are not
replaced with a target. Position samples may advance; source, revision, state,
duration, missing/error view or stale playing observations cancel the gesture.
A 120-second monotonic age bound prevents an indefinitely retained input intent.

The existing absolute horizontal pointer mapping is retained, but a click no longer
submits on LEFT_DOWN. Press starts preview, motion updates it, and LEFT_UP can send
one request. Coordinates outside the control clamp to the two endpoints. The normal
native pointer default is consumed to avoid a competing seek route. One explicit
wx mouse capture is acquired; it must still be owned at release. Capture is dropped
before sending the request and context is reread afterwards. Release-generated
scroll notifications are suppressed before calling ReleaseMouse. Intent is consumed
before the canonical helper/facade call. Duplicate endings cannot reuse that intent.
This does not provide an atomic transaction with all other playback threads.

## Keyboard and native notifications

Arrow, Home/End and PageUp/PageDown keys establish a fresh guarded intent; default
keyboard navigation is allowed to move the native control. SCROLL_CHANGED is the
single native scroll commit notification. Generic EVT_SLIDER and THUMBRELEASE are
not additional commit sources. A deferred key-release cleanup lets the ordinary
native end notification run first, then cancels a no-change gesture (for example
an endpoint key). At most one such cleanup is pending per surface. A stale cleanup
cannot cancel a newer intent. It never invents a missing seek.

Cancelled/completed native notification sequences cannot reopen themselves. A new
explicit pointer/key interaction re-arms admission. Bare synthetic/accessibility
scroll streams with no distinguishable new input are deliberately not treated as
an authenticated new gesture; assistive-technology behavior needs native review.
No cross-platform or arbitrary event-reordering guarantee is made. wx native input
ordering, DPI/thumb geometry, focus and capture must be checked on Windows.

## Cancellation and presentation

Escape, lost focus/capture, source/revision changes, overlay hiding, control
teardown and boundary failures cancel without a new request. Lost-capture handling
never recaptures or releases another owner's capture. A failing ReleaseMouse is
logged and intent is invalidated; that does not prove OS capture was released.
Forced hiding cancels the overlay; its idle hide timer cannot hide a live drag.
Control destruction closes the presentation so later callbacks cannot repaint it.

### 08AW: submitted-thumb handoff

Cancellation or synchronous refusal repaints observed data. After successful
forwarding, however, the local thumb stays at the selected ratio until a qualified
post-dispatch sample can replace it. This avoids the previous target -> old cursor
-> observed cursor bounce, both playing and paused. `DisplayReading.preview_ratio`
is explicitly drawing-only: position/duration/validity, backend receipts, EOS and
input admission continue to use the observed cache. The preview never updates the
acknowledged visual history. In-flight state/error labels continue to update.

Video still requires its new native completion receipt and a strictly later
matching-generation sample. A clock observed before dispatch returned, an old
receipt, or an equal-time sample cannot end the local handoff. The actual observed
position may differ from the target and is shown without fabricating a match.
Audio retains its existing observation contract; no decoder attestation is added.

The fixed-size handoff is installed before potentially reentrant dispatch. A new
explicit gesture supersedes it; it owns no mouse capture. Losing focus after
submission does not undo the already-submitted visual intent. Source/controller,
playback epoch/state, generation, duration or unrelated revision drift, rejection,
error, teardown and a fixed ten-second maximum end the hold. That maximum is not
a seek delay or a retry: normal valid observations release the hold immediately.
Missing cache data cannot confirm intent, and wakeups never renew the maximum.
The two surfaces retain independent local previews, with shared backend evidence.

## Validation and remaining boundaries

Tests cover both real widget handlers and the facade/controller/native-command
path between test-owned wx, clock and COM-shaped boundaries. Pointer/keyboard
preview, one commit, duplicates, malformed scalars, missing control capabilities,
failed capture/write/release, release reentrancy, lost focus, source drift, paused
state, no-change key cleanup and pending receipts are checked. These are not native
mouse/paint tests. Existing tests now provide a genuine typed cached test sample
and perform the new release phase; no product test is removed or newly skipped.

Display interpolation and sample/paint diagnostics are the next stage. This source
must not be promoted as fixing the user's observed fluidity until integrated native
tests and the short real-video trial succeed. No dependency, lock, SDK layout,
user profile, installed player or remote branch changes are part of this step.

## Primary API references

- https://docs.wxpython.org/wx.Slider.html
- https://docs.wxpython.org/wx.ScrollEvent.html
- https://docs.wxpython.org/wx.MouseCaptureLostEvent.html

These describe platform API contracts, not project execution results.


## Source changes after a seek (08AX)

The diagnostic receipt of an earlier source must not be exposed as the active
seek of a replacement. The core keeps that operation and its cancelled/native
facts in the existing bounded slot. Only a successful non-NULL SetSource (S_OK)
commits the newer transport epoch, engine generation and source. Once the old worker has replied (or was explicitly refused before queue
insertion), that older record no longer blocks the new source's cached clock or
a new seek. A replay of the same path still requires a newer commit.

This source-selection commit does not claim asynchronous loading, decoding or
any seek completed. Display still needs a current, valid observed clock; current
seeks still require their own native acknowledgements. Stop/invalidation alone,
failed or skipped loading, mismatched identities and outstanding worker replies
cannot manufacture an empty seek state. GUI/native Windows validation remains a
separate OWNER gate. The 08AW cursor preview code is unchanged.
