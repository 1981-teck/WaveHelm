# Legacy UI helper review — R5I-step07N

22 September 2026. Exact baseline: step07M after acceptance of the bounded L/M
Windows audio gate. Nine named decisions; not a global dead-code or API audit.

## Decisions

| Module | Name | Decision and reason |
|---|---|---|
| mini_player_chrome | `_sync_enabled_buttons` | REMOVE private unused helper. Only its own definition and an old presence test reference it. Active `_render_playback` reads button state from `PlaybackView` and uses the retained `_set_enabled`. |
| mini_player_chrome | `_extract_path` | REMOVE private unused helper. No production caller, callback or explicit reflective consumer found. Active `_render_playback` reads the authoritative view path directly. |
| mini_player | `_apply_player_state` | KEEP compatibility entry point. Its documented contract ignores payload authority and refreshes the current cache. |
| mini_player | `_apply_progress_payload` | KEEP the same compatibility contract, not a stale scalar-payload renderer. |
| mini_player | `_apply_video_duration_payload` | KEEP the same compatibility contract; no backend duration polling added. |
| video_overlay_controls | `_apply_state_payload` | KEEP documented compatibility and cache-only refresh. |
| video_overlay_controls | `_apply_progress_payload` | KEEP documented compatibility and current-view authority. |
| video_overlay_controls | `_apply_video_duration_payload` | KEEP documented compatibility and closed-widget containment. |
| mini_player_shared | `resolve_track_title` | KEEP public helper API. No current internal caller is claimed. A deliberate future deprecation would be a separate API decision, not import cleanup. |

The six wrappers' documentation is evidence of an intended compatibility surface,
not proof of active external users. They remain byte-identical. Their current
private name alone is not sufficient grounds to delete an explicit contract.
The public title helper also remains byte-identical; title/name/display/path and
missing-object cases are tested. No synthetic forwarding shim or new layer is added.

## Actual active routes retained

The event handlers request a presentation refresh; compatibility entry points call
`_poll_progress`. The presentation reads the current cached PlaybackView rather than
trusting delayed payloads. MiniPlayer renders button enablement via `_set_enabled`,
its title via `_refresh_track_and_state_text`, and the path directly from the view.
The overlay retains its separate video-view filtering. All these bodies are unchanged.

A refused cache read (`None`) is not an accepted empty view. Existing presentation
logic retains identity and revokes input authority on refusal; an explicit empty
PlaybackView clears visible media state. Both cases are covered separately without
changing existing policy to fit a mistaken test expectation.

The volume-related `_apply_*` methods in the chrome are actual event consumers and
are not among the six compatibility wrappers reviewed here. They remain unchanged,
including the initial-zero slider correction and no-backend-write bootstrap.

## Existing test changes

In `test_cleanup_chrome_imports.py`, the exact method count changes from 19 to 17.
The historical `test_legacy_chrome_helpers_remain` identity is retained, but now guards
`_set_enabled` and `_refresh_track_and_state_text` instead of the two retired methods.
These are implementation-detail checks from an earlier imports-only tranche, not
behavioral requirements to keep dead helpers forever. Inherited identity, annotation,
callback, localization, theme and volume tests are not changed or weakened.

The new module tests two retirements and retained compatibility behavior: malformed,
stale and absent payloads, closed widgets, empty/loading/paused/playing/stopped views,
refused reads and the public title helper. Test-owned wx widgets are not a native
Windows rendering, physical-device or timing qualification.

## Limits and future work

Repository searches include source, tests, tools and documentation. Unknown external
subclasses or computed reflection are not proven absent. Consumers of the retired
private methods must migrate to current view rendering; no universal external API
compatibility promise is made. Public APIs and documented wrappers are preserved.

No audio/DSP/native/COM/ABI/lock/dependency/resource/CI change. The accepted F/G/G2 and
L/M Windows gates remain closed. Full source-wide typing/security/coverage, current
advisory scanning and cumulative Windows GUI/device qualification remain separate.
The next bounded review is `ThreadSafeSingleton`/`src/utils/singleton.py`; do not
combine it with optional imports or removal of the six retained compatibility wrappers.
