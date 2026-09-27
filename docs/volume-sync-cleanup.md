# Mini-player volume synchronization: exception-safe cleanup (08E)

23 September 2026. Fix 08D-F01 on the exact 08C source. The external 08D review did
not produce a different application tree. This is not native Windows qualification.

## Contract and scope

`MiniPlayerChrome._set_volume_slider` saves the entry `_volume_sync` value, sets
it to True for the existing numeric conversion and widget write, and restores
that exact value in `finally`. It does not catch or translate the failure.

- A widget error, conversion error or process-control exception leaves the guard
  in its entry state and propagates unchanged. A normally idle view can accept the
  next user volume event without waiting for another programmatic refresh.
- A nested synchronous write must not clear its caller's active guard. Successful,
  propagated-error and caught-inner-error paths restore the entry state.
- Zero retains the existing last-nonzero value. The existing positive-value update
  still happens before the widget write, even if that write fails. This fix does
  not turn the entire operation into a state transaction.
- Programmatic callbacks remain suppressed while the guard is active. No backend
  call is added to this helper. Existing user and mute handlers retain their calls.
- Existing `_defer_ui` closure checks still reject queued volume events after close.
  A close during the widget write does not bypass finalization. Direct private
  handler calls after close are not a new supported contract.

The guard is GUI-thread synchronization for synchronous reentrancy, not a mutex.
No cross-thread serialization or native event-loop ordering guarantee is added.
Clamp, rounding, native callback policy, logging, timer, mute and last-nonzero
ordering are not redesigned. Direct non-finite private-helper inputs still raise
at integer conversion; the public payload handler retains its earlier clamp.
Video mute still issues its backend request before drawing and commits mute-label
state afterward. Failure there is not newly rolled back or relabeled success.

## Regression evidence

`tests/test_volume_sync_cleanup.py` exercises the actual MiniPlayer with the existing
repository wx controls and observable controller. It covers original error/input
recovery, both entry guard states, known/control errors, nested writes, conversion
errors, audio unmute, video mute, close during update and queued late delivery.
No pygame replacement, native-success stub, real GUI, mixer or device is introduced.

The original five-case external 08D file is preserved verbatim. Its two RED cases
and two ordinary controls pass after repair. Its fifth case intentionally observed
that the buggy flag stayed True until a later refresh: that intermediate assertion
now fails because cleanup works. It is a superseded defect-characterization check,
not a product regression or an existing project test to weaken. Both full before/
after outputs are retained. New regressions require immediate recovery instead.

Run the current focused contract:

```text
python -B -m pytest -p no:cacheprovider tests/test_volume_sync_cleanup.py tests/test_wx_mini_player.py tests/test_mini_player_initial_volume.py
```

The delivery REPORT and evidence contain actual local results, repeated runs,
byte/AST preservation checks and packaging verification. Repeated runs are not
additional unique cases. Tests under fake wx are not native Windows evidence.

## Preserved gates and installation

08A and earlier F/G/G2 and L/M Windows acceptances remain closed in their original
scopes; unchanged audio source is not a reason to repeat those kits. The logging
08C correction remains intact. Global typing/security/coverage, updated advisory
checks and cumulative Windows GUI/native/device/release qualification remain open.
Extract the full source into a new folder, retaining the working installation and
its data. This is a checkpoint, not an updater or publication approval.
