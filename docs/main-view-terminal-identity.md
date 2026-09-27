# MainView terminal-event path admission — R5I-step08L

24 September 2026. Bounded correction of 08K-F01 and 08K-F02 on source08I.
This is not native GUI, full session-identity or release qualification.

## Admission before mutation

MainView independently subscribes to VIDEO_PLAYBACK_ERROR, VIDEO_PLAYBACK_ENDED
and VIDEO_PLAYBACK_STOPPED. All three now use the existing private predicate
`_should_ignore_video_playback_stopped` before changing pending state, active state,
retry timers, close intent, status text or the external-window reference. The old
name is retained for compatibility; its documented scope is now terminal events.

A shutting-down view rejects queued terminal callbacks, including pathless ones.
Otherwise legacy payloads without a usable path retain their former admission.
For a nonempty path, the current pending request takes precedence over a previous
ready signature. With no pending path, the ready signature supplies the comparison.
The existing same_media_path helper compares supported stream schema/host without
changing resource path/query case, while keeping local Windows path case behavior.
A mismatching identity is rejected if a current path or active session is known.
With neither known path nor active session, the existing teardown admission remains.
The payload and the comparison utility are not mutated. Missing fields on a legacy
partial shell use the same no-known-identity admission, preserving existing terminal
handoff tests; this does not claim that an arbitrary partial view is initialized.

## Scope and deliberate limitations

This is path admission, not a newly versioned playback protocol. PREPARE currently
binds path and loop, not playback_revision; the current controller publishes that
shape. One modern ENDED producer includes a revision, but that is not a shared
revision contract across PREPARE, ERROR, STOPPED and older producers. The patch
therefore does not compare unbound revisions or claim same-path replay protection.
A terminal event for the same path can still be accepted after a same-path restart.
Pathless events cannot be attributed to a distinct session and remain legacy behavior.
The existing coercion of malformed/uncoercible path payloads is unchanged; this
patch does not promise validation of every arbitrary object supplied by a caller.

The helper is unchanged: no filesystem access, redirects, network request, decoding
of escapes, query reordering or default-port equivalence is added. Its existing
bounded invalid-stream rejection applies. STARTED, CANCEL, PREPARE and other producer
contracts are not rewritten. No resolved-URL authority or revision is invented.

Matching ERROR keeps the existing status/optional deferred-close behavior; matching
ENDED and requested STOPPED retain their existing close behavior. Close flags are
consumed before side effects. Reentrant close is idempotent in the tested protocol,
while destruction errors still propagate; this is not transactional rollback or
an automatic retry. The legacy refresh_force keyword remains an accepted no-op.
No native adapter, COM lifetime, engine, event-bus scheduling or history retry changes.

## Regression evidence

The unchanged20-case external08K finding suite is retained for before/after proof.
The new project module tests direct and delayed UI-dispatch delivery using the real
MainView, AudioEventBus and ExternalVideoWindow with existing test-owned wx controls.
It covers pending/ready identities, current-request precedence, timer/status/flag
preservation, valid local/URL matches, legacy pathless events, queued shutdown,
original exception propagation, repeated/reentrant close and the same-path limit.
No stream, native window or playback device is opened. The retained08I identity
suite and prior logging/volume/factory tests remain unchanged.

Run `python -B -m pytest -p no:cacheprovider tests/test_main_view_terminal_identity.py`
in a suitable test environment. Actual run counts, interpreter differences, original
RED evidence, broad results and packaging hashes belong in the delivery REPORT and
VERIFICATION, not an unexecuted success claim here.

08K-V01, the intermittent history snapshot test, stays an open separate validation
investigation. A later successful run does not clear the earlier failure. No retries,
assertions, budgets or exclusions are changed to accommodate it.
