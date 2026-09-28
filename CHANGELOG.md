# Changelog

## [1.0.3] - 2026-09-28

- Suppress transient Windows console windows created by `ffprobe` metadata probes by using `CREATE_NO_WINDOW` on Windows only.
- Preserve the existing subprocess capture, timeout, output-bound and typed-error behavior; non-Windows launches retain zero creation flags.
- Add regression coverage for the Windows creation-flags contract.
- Preserve dependency locks, Windows ABI declarations and the qualified 1.0.2 playback/runtime baseline.


## Release integration RI03 — Windows strict-decoding test portability reconciliation (2026-09-27)

- Preserve the entire application runtime and dependency graph byte-for-byte from RI02.
- Reconcile the two remaining RI02 Windows failures in `test_encoding_lock_matrix.py`. On
  CPython 3.12 for Windows, `subprocess.run(..., text=True, encoding="utf-8",
  errors="strict")` performs pipe reads in background reader threads; malformed UTF-8 did
  raise `UnicodeDecodeError`, but pytest reported it as `PytestUnhandledThreadExceptionWarning`
  instead of propagating it through `subprocess.run`, so the outer `pytest.raises` assertion
  was not portable.
- Keep the production/tooling static contract that controlled subprocess decoders explicitly
  select UTF-8 with strict error handling. The corruption regression now captures bytes and
  applies `decode("utf-8", errors="strict")` in the calling test thread, preserving the same
  rejection invariant without depending on platform-specific subprocess reader-thread
  exception propagation.
- RI02 Windows evidence is retained as a failed qualification run: 1,644/1,644 focused PASS,
  72/72 metadata PASS, reconciliation 129 PASS / 2 FAIL / 3 SKIP, full suite
  4,763 PASS / 2 FAIL / 16 SKIP, build PASS, exact expected Windows skip set confirmed,
  and source test copy unchanged. A fresh RI03 Windows automated run is required.

## Release integration RI02 — Windows full-suite reconciliation (2026-09-27)

- Preserve the 1.0.2 runtime implementation byte-for-byte from RI01; no playback,
  seek, WIC, audio/DSP, persistence or dependency behavior changes in this checkpoint.
- Align stale cursor-continuity assertions with the 08AW submitted-thumb handoff:
  an accepted seek keeps its drawing-only preview until qualified observation replaces it,
  while refusal/failure still restores the observed cursor.
- Make controlled lock-tool subprocess decoders state `errors="strict"` explicitly so
  malformed UTF-8 rejection is deterministic on the qualified Windows interpreter.
- Update the native timed-text probe adapter to expose the WIC frame-admission surface
  required by the default Windows backend; the control-only probe still loads no media
  and never claims pixel/cue rendering.
- RI01 Windows evidence remains preserved as a failed qualification run: 1,644/1,644
  focused checks and 72/72 release-integration checks passed, while the complete suite
  exposed 14 test/probe-harness failures that pre-existed unchanged in the 08AX source.
  A fresh RI02 Windows full-suite run is required before release approval.


## [1.0.2] - 2026-09-28

WaveHelm 1.0.2 retains the qualified 08AX runtime while integrating the public
presentation/community files, the 1.0.2 package identity, and the release
qualification harness. Historical RI01/RI02/RI03 evidence remains preserved
separately from the final publication state.

- Preserve the 08AX seek/source-ownership fix and the 08AW visual cursor handoff.
- Preserve the accumulated WIC startup, rendering, focus, close/reopen and
  progress repaint corrections; WIC remains the default Windows backend.
- Retain pause-seek/audio clock, Ambient multichannel, volume/error-path and
  conservative cleanup work documented in the development history below.
- Restore README badges, hero screenshot, navigation and the 55-second demo.
- Include contribution guidelines, Code of Conduct, private security reporting,
  Issue Forms and the PR template in the source package.
- Align active package, About/runtime, localized manuals and supply-chain policy
  to 1.0.2 without changing runtime logic or dependency/lock versions.
- Correct the 08AX/08AW changelog heading hierarchy; preserve historical evidence.
- Finalize the public-release documentation and release-state version-consistency
  assertions without changing application runtime logic, dependencies or lock contents.

See `docs/release-integration.md` for preserved qualification history and evidence limits.

## R5I step08AX — seek state across video source changes
- Retire drained seek observations from earlier transport epochs only after a
  successful non-NULL SetSource selection for the current engine/source.
- Keep cancellation facts in the bounded slot; failed/stale loads, stop alone,
  missing worker replies and current-source seeks remain fail-closed.
- Preserve the 08AW drawing-only cursor handoff; add source-change/replay and
  negative integration tests for both progress surfaces. Windows validation pending.

## R5I step08AW — seek-thumb visual handoff (2026-09-27)

- Retain a submitted progress-bar preview until a qualified observation replaces it,
  avoiding target/old-position/target bounce in play and pause.
- Keep measured clocks, pending/error status and native completion gates unchanged.
- No audio DSP, equalizer, effects, mixer, WIC or MediaEngine changes.
- Windows visual confirmation is pending; see the delivery verification report.

## R5I step08AN — WIC becomes the default Windows video backend

- Promote the owner-qualified frame-server/WIC renderer to the default backend when `WAVEHELM_VIDEO_BACKEND` is unset.
- Keep `legacy_hwnd` only as an explicit diagnostic override; never fall back automatically from WIC after a renderer error.
- Preserve the 08AM progress repaint hardening and the existing WIC lifecycle, frame-transfer, seek, resize and shutdown behavior unchanged.
- Record the 08AM Windows evidence as the promotion basis: 13 source loads / 12 NEXT actions, stable progress controls, no monotonic resource growth, and resource recovery after closing the video window while the application remained alive.
- Treat absence of any residual leak as NOT PROVEN; broader resolution/frame-rate, long-duration and release qualification remain separate gates.
- Realign the stale implementation-detail annotation inventory test with the 20 functions already present in the qualified WIC playback module; no runtime behavior changes.
- Restore the existing fail-closed progress contract: a native slider write failure during pointer preview closes the affected surface and releases owned capture instead of leaving a partially usable control.


## R5I step08AM — progress slider repaint hardening

- Avoid redundant native `wx.Slider.SetValue()` calls on the mini-player and external-video progress controls.
- Request native double buffering for the two frequently updated progress sliders when wx supports it.
- Preserve seek/gesture behavior and the existing cache-only progress authority; no playback/WIC lifecycle change.


## R5I-step08AL — WIC paint flicker hardening

- Present WIC frames through `wx.AutoBufferedPaintDC` with `BG_STYLE_PAINT`.
- Do not expose a black `Clear()` immediately before a valid full-frame GDI blit.
- Use the supported `wx.DC.GetHandle()` native-context accessor.
- Keep black clearing only for missing/obsolete frames and retain fail-closed WIC behavior.


## R5I step08AJ — 2026-09-26

- Correct the WIC frame-server startup policy by explicitly setting autoplay off and preload to automatic before any source load, matching the Windows-proven diagnostic path.
- Read back both native settings and fail engine creation on HRESULT failure or policy drift; do not change the legacy HWND backend.
- Add typed IMFMediaEngine vtable specifications for Get/SetPreload and Get/SetAutoPlay plus focused regressions.
- Treat the 08AI black-window owner observation as a blocking Windows regression; WIC playback remains NOT VERIFIED until the 08AJ owner smoke succeeds.

## R5I step08AD — 2026-09-26

- Roll back the 08AC app-owned D3D11/DXGI-manager attachment from HWND MediaEngine creation after the first Windows smoke run regressed basic video playback (video window opened but playback did not start).
- Restore the 08AB HWND rendering ownership path while retaining the 08AB PAUSE -> SetSource(NULL) -> PURGE source-replacement barrier.
- Add a regression guard that HWND engine creation has only the notify `SetUnknown` attribute and does not reattach an extra app-owned DXGI manager.
- Remove dynamic `Any` annotations from the touched DXGI/shutdown boundary types; no fallback or silent recovery is introduced.
- Treat the 08AC default-adapter D3D11 experiment as rejected pending an adapter-qualified design; native leak stabilization remains NOT VERIFIED.

## R5I step08AC — 2026-09-26

- Give each MediaEngine lifetime one app-owned hardware D3D11 video device and `IMFDXGIDeviceManager`, attached through `MF_MEDIA_ENGINE_DXGI_MANAGER` and reused across source switches.
- Use `D3D11_CREATE_DEVICE_VIDEO_SUPPORT | D3D11_CREATE_DEVICE_BGRA_SUPPORT` with multithread protection; do not use the debug/profiling-oriented internal-thread suppression flag.
- Transfer DXGI/D3D ownership into the shutdown job so native graphics resources are released deterministically after the MediaEngine.
- Retain 08AB source detach/PURGE as a correctness barrier, but record the returned Windows evidence as falsifying it as the native-resource leak solution.
- Windows native resource stabilization for 08AC remains pending validation.

## R5I step08AA

- Removed the invalid forced `MF_MEDIA_ENGINE_VIDEO_OUTPUT_FORMAT=0x15` from HWND rendering-mode MediaEngine creation; Windows native resource stabilization remains pending.


## R5I-step08V — 2026-09-25

- Remove the redundant explicit IMFMediaEngine::Load call after SetSource for ordinary
  URL/file source replacement; SetSource remains the single asynchronous load trigger.
- Preserve MediaEngine reuse, owned-reference P/Q/R teardown, BSTR cleanup and active
  source bookkeeping.
- Add repeated source-switch and failure-path regressions. Windows handle/thread
  stabilization remains pending native confirmation.

## R5I-step08N — 2026-09-24

- Correct Ambient stereo/multichannel expansion by padding missing channels with
  silence; preserve mono and existing reduction behavior, including mix export.
- Validate final PCM shape and actual mixer rate/channel/sample format, isolate cache
  keys by sample format, and reject observable format changes during preparation.
- Keep signed16 scaling; use explicit file decoding for other mixer sample formats.
  Add actual format/shape/dtype diagnostics and focused regressions. Native Windows
  confirmation and the separate COM address-reuse concern remain outstanding.


## R5I-step08E — restore volume guard after widget errors (2026-09-23)

- Restore the entry volume synchronization flag in a finally block, including nested writes.
- Preserve original exceptions, numeric rendering, mute/last-nonzero order and backend calls.
- Cover immediate user-input recovery and existing close/queued-event behavior.
- Keep the 08C logging correction and accepted earlier Windows gates unchanged.
- See docs/volume-sync-cleanup.md; cumulative native GUI and release qualification remain separate.

## R5I-step07O — retire unused generic singleton module (2026-09-22)

- Retire `src.utils.singleton.ThreadSafeSingleton`; no repository consumer was found.
- This deliberately removes an importable path; unknown external clients must migrate
  explicitly or retain the prior checkpoint. No drop-in replacement is supplied.
- Preserve the active audio-config, database, service and video-adapter singletons.
- Source trials require a new extraction directory, not copying over old files.
- See docs/singleton-retirement.md for compatibility, packaging and verification limits.
- No dependency, data, native runtime or public-release change is authorized.

## R5I-step07A — conservative cleanup: UI error-path bindings (2026-09-21)

- Define the module logger used by existing column restore/save error handlers.
- Import the existing flow sizer helper for missing-page factory fallback.
- Add before/after regressions, consumer shutdown checks and unchanged-path controls.
- Preserve function bodies, exception policy, playback, UI layout, data and dependencies.
- Do not remove imports, compatibility APIs or the deferred file-size helper in this step.
- This is an engineering checkpoint; native Windows and release qualification remain open.
- See docs/cleanup-step07a.md for the bounded scope and continuation point.

## R5I-step06A — explicit UTF-8 in Library qualification (2026-09-20)

- Fix locale roundtrip tests that incorrectly used the platform default text encoding.
- Declare UTF-8 for adjacent probe test artifacts; retain strict decoding and all original inputs.
- Add cp1252/ASCII boundary reproductions, Unicode preservation and invalid-byte regressions.
- Preserve all application, locale, probe, dependency, lock and ABI files unchanged.
- Accept the received step06 geometry only in its measured scope; its full-suite failure
  remains historical. New step06A Windows tests and overall release remain unqualified.

## R5I-step06 — Library window resizing (2026-09-20)

- Keep FitInside limited to real scrolled containers; ordinary panels and the book follow their client area.
- Give search its own row and wrap labelled filter/sort groups without recreating controls.
- Recompute native control best sizes without feeding the previous assigned minimum back into the next measurement.
- Add independent geometry checks and an isolated real-wx Windows probe; native results remain pending.
- Playback, cursor residuals, persisted data, locks and release status are unchanged.


## R5I-step05B — cursor continuity and native terminal handoff (2026-09-20)

- Separate last drawn history from measured time, missing data and gesture preview.
- Preserve same-playback visual continuity during seek/cache refusal without false zero.
- Bind clock cache and terminal ownership to adapter/core/generation/transport epoch.
- Retain native terminal samples and paint before native release, HWND close and next.
- Bound read-only terminal handoff retries; do not replay native commands or inferred EOS.
- Add opt-in bounded numeric tracing for the subsequent Windows visual trial.
- Preserve dependencies, locks, native seek acknowledgement and ABI declarations.
- Local contract qualification is not native visual acceptance; see
  docs/cursor-continuity-step05b.md.

## R5I-step05A — native qualification portability and probe fidelity (2026-09-19)

- Make clock-order test preconditions explicit; preserve strict runtime freshness guards.
- Keep hostile source payloads intact behind short diagnostic IDs; reject oversized
  pytest current-test metadata at collection without truncating or dropping cases.
- Align the native probe adapter with the generation-bound callback ownership contract.
- Retain bounded callback/error diagnostics and refuse native-probe PASS on recorded faults.
- Preserve all src/ bytes, dependencies, locks and ABI. Native Windows rerun is required;
  this test/probe repair does not itself qualify media playback or visual smoothness.
- See docs/native-qualification-step05a.md.

## R5I-step05 — bounded causal progress motion (2026-09-19)

- Interpolate the graphical cursor only toward a newer already observed position.
- Keep measured clocks, gesture input and native EOS independent of visual motion.
- Cancel/rebase on stale/error/paused/seeking/replaced data and stationary clocks.
- Share 33 ms cache-only GUI updates; keep overlay native maintenance throttled.
- Preserve one pending refresh and one bounded segment; restore zero/reset behavior.
- Retain native playback, seek, dependency and ABI bytes; Windows tests and actual
  visual smoothness require new evidence. See docs/progress-motion.md.

## R5I-step04C — guarded progress gestures (2026-09-19)

- Share a bounded preview/release/cancel input lifecycle across both progress bars.
- Route one release through the existing canonical seek admission, not on pointer press.
- Reject drift, stale admission views, duplicate endings and lost ownership; preserve
  measured position separately from preview and native-unconfirmed requests.
- Cancel no-change keyboard gestures after native dispatch and suppress release reentrancy.
- Preserve native seek/EOS contracts, dependency locks and ABI; Windows input and
  visual smoothness remain unverified. See docs/seek-gestures.md.

## Progress repair checkpoint R5I step04A — 2026-09-19

- Add explicit tracked COM queue admission and bounded, cached native seek-command receipts.
- Bind queued work to its worker/adapter/source/engine/transport epoch; retain and release
  the addressed engine around SetCurrentTime and preserve actual HRESULT/errors.
- Distinguish cancellation, expiry, worker reply and native acceptance from SEEKED.
- Refuse another unresolved command without growing the core queue contribution.
- Propagate exact video admission through adapter/controller; remove false success on void results.
- Decompose oversized transport/track/worker API clusters with public method aliases preserved.
- Native completion reconciliation, complete gestures, interpolation and native qualification
  remain open; this is not a manual-use or release approval.

## Progress repair checkpoint R5I step 03 — 2026-09-19

- Migrate both progress bars to typed current-state and cached-clock views.
- Treat progress/state/duration events as coalesced wakeups; do not apply old payloads
  or synchronously query COM/mixer clocks from graphical refresh timers.
- Preserve valid zero, pair consistency, bounded age and source/revision/order;
  reset or freeze explicitly for loading, pause, errors, seeks and source changes.
- Guard late callbacks, failed widget writes and timer cleanup; keep canonical seek
  routing but never record an unconfirmed requested target as observed playback.
- Extract cohesive wx appearance/layout/native-placement clusters and remove their
  two obsolete oversize-file exceptions. Native window behavior is not certified.
- Add cache, delayed-default-bus, real-handler, lifecycle and blocked-reader contracts.
- Native seek completion, full gestures, display interpolation and Windows visual
  qualification remain open. Locks, native ABI and EOS decisions are unchanged.

## Progress repair checkpoint R5I step 01 — 2026-09-19

- Route both progress controls through the canonical seek/revision guard; reject
  unavailable, inactive, invalid or explicitly refused requests without backend fallback.
- Make facade seek admission explicit as REJECTED or FORWARDED_UNCONFIRMED; this
  does not certify queue acceptance, HRESULT success or native seek completion.
- Remove the extra one-second sleep after completion, retaining normal sampling,
  native end authority, duplicate suppression and circular queue behavior.
- Add real-handler routing and virtual-clock transition regressions.
- This is an intermediate checkpoint: coherent samples, GUI delivery, native seek
  confirmation and visual smoothness remain open. Windows execution is NOT_RUN.

## Continuation R5H -- 2026-09-18 (unreleased completion repair)

- Remove the 300 ms early-completion heuristic for audio and video. A native
  poll_end result is authoritative, including false, unknown and query failure.
- Add a strict audio mixer completion query that excludes pause, stop/reload,
  native repeat and unstable observations. Do not treat query errors as idle.
- Version completion observations on playback/context changes and seek; reject
  stale deferred UI work, deduplicate one unchanged completion and retain a
  consumed native end when UI dispatch temporarily declines it.
- Preserve circular queue navigation and the existing per-track repeat policy.
- Keep dependencies, all six hash locks and the accepted ABI boundary unchanged.
  Native R5H tests and real end-of-media playback remain required. No release
  approval, decoder/device measurement or all-thread linearizability is claimed.

## Continuation R5G -- 2026-09-16 (unreleased timed-text service control)

- Implement the three native timed-text hooks through an explicit MFGetService route.
  Enforce the owned COM thread and per-acquisition interface lifetime without a cache.
- Reject partial enumeration, invalid/duplicate track IDs and engine-generation drift.
  Prevalidate selection and confirm active IDs before reporting success; no atomic
  rollback is claimed for a partially failed native sequence.
- Add a real Windows Media Engine control probe in an isolated subprocess with
  separately retained logs and source seals. Native R5G execution remains required.
- Preserve accepted ABI declarations, allocator helper, assets and dependency locks.
  Cue notification/overlay rendering and end-to-end subtitle playback remain open.


## Continuation R5F -- 2026-09-15 (unreleased ownership and asset repair)

- Release successful COM timed-text LPWSTR outputs through CoTaskMemFree;
  preserve the separate BSTR source-loading allocator. Bound copying and
  enumeration, clear consumed pointers and expose typed ownership failures.
- Extract timed-text and audio-stream clusters without changing installed method
  names. Reduce the modified playback file below 450 lines and remove its old
  oversize allowance. Unchanged legacy video-rendering logic remains separate.
- Reject successful selection/disable when native timed-text acquisition is
  unavailable. Actual acquisition/routing is still NOT_IMPLEMENTED; see
  docs/timed-text-ownership.md. Passing layout tests never qualified subtitles.
- Include the existing ambient profile JSON and 14 SVG icons in wheel and sdist.
  The historical SOURCE_MANIFEST_R5.json remains history, not current evidence.
- Native R5F tests/allocator roundtrips remain required. Prior R5E native test,
  build and unchanged ABI/lock evidence are preserved in their original scopes.
  No public release, GUI playback or full COM lifecycle qualification is claimed.

## Continuation R5E -- 2026-09-15 (unreleased Windows portability repair)

- Use fresh no-follow filesystem metadata consistently during library discovery
  and loading; keep identity, mutation, cancellation and traversal-error gates.
- Map a missing portable playlist leaf to the correct read/write operation error;
  retain denial of non-regular files, links and hard links.
- Write canonical test lock bytes without platform newline conversion; require
  precise negative-test reasons, normalized database paths and short pytest IDs
  without shortening hostile parameter data.
- Preserve all dependency locks and the eight R5D native ABI boundary files.
  The returned R5D Windows preflight failed tests but built real wheel/sdist via
  python -m build. R5E still requires its own unfiltered Windows test execution.

## Continuation R5D — 2026-09-14 (unreleased ABI correction)

- Restore full SDK declaration order and tail slots for timed-text and Media
  Engine factory vtables; fix the pointer parameter of RemoveTrack.
- Expand the SDK reference and independent tests to all 39 affected slots and
  signatures; retain exact mismatch details, compiler log and boundary hashes.
- Reject partial ABI observations and malformed reference JSON. Dependencies
  and all six Windows locks are unchanged. Native Windows verification remains
  required; no release or native application qualification is asserted.


All notable source-release changes are documented in this file.

## [1.0.2.dev23] - Development history (not a public release)

### Development identity

- Assigned the PEP 440 development version `1.0.2.dev23` to package metadata, runtime metadata, fallback metadata, and embedded manuals.
- Added hard-fail consistency tests for active version surfaces.
- Reserved `1.0.1` exclusively for the existing public source release and historical release records.
- Required wheel and source-distribution filenames produced from this tree to use the `1.0.2.dev23` identity.

### Release engineering

- Added six checked-in Windows runtime/development hash-lock bundles for CPython 3.11, 3.12 and 3.13.
- Added native Windows supply-chain collection for pip-audit, CycloneDX 1.6 SBOMs, license evidence and tamper-evident manifests.
- Corrected installed-graph validation so a complete hash-locked pip report is accepted while partial or hybrid requested-package sets remain blocking.
- Bound fresh Windows supply-chain evidence to the exact R4 source inventory with zero validator findings and zero reported runtime vulnerabilities across Python 3.11/3.12/3.13.
- Corrected the `IMFDXGIDeviceManager::LockDevice` ctypes signature and completed the `IMFTimedTextNotify` callback vtable.
- Added a compiled Windows SDK ABI/layout reference gate and CI evidence job for Media Foundation ctypes boundaries.

### Release status

This entry describes an unreleased development snapshot. It does not create, replace, retag, or modify the public `v1.0.1` release.

## [1.0.1] - 2026-08-18

### Corrected

- Aligned the project, runtime metadata, embedded manuals, and documentation on version `1.0.1`.
- Raised the declared Python minimum from 3.10 to 3.11 to match the pinned NumPy and SciPy requirements.
- Anchored repository-root runtime ignore rules so bundled legal license texts are no longer excluded.
- Restored and retained the complete third-party license-text directory required by the runtime and tests.
- Removed generated test databases, logs, caches, media stubs, and runtime directories from the release source tree while retaining all test source files.
- Removed the stale embedded Git repository from the distributable source archive.
- Kept README screenshot references on the existing `docs/screenshots/` assets and rejected unresolved `docs/images/` references.
- Corrected Markdown escaping in Windows paths and repository filenames.

### Added

- Pinned development, build, dependency-audit, and CycloneDX tooling in `requirements-dev.txt`.
- GitHub Actions jobs for source hygiene, the full Windows/Python test matrix, package builds, dependency auditing, and SBOM generation.
- Source-distribution inclusion rules for tests, GitHub Pages assets, legal resources, development requirements, changelog, roadmap, and security policy.
- Release-oriented Windows packaging documentation and a public security-reporting policy.

### Scope note

This release corrects source packaging, metadata, licensing, CI, and release hygiene. Runtime hardening and architectural improvements identified during the technical review are tracked in `ROADMAP.md` and are intentionally deferred to subsequent change-sets.

## [1.0.0] - 2026-04-08

- Initial public source release and GitHub Pages baseline.


## R5I step 02 — typed progress producer checkpoint (2026-09-19)

- Read paired native video clocks on the COM thread with scoped AddRef/Release.
- Add guarded audio clock observations with explicit mixer/metadata provenance.
- Preserve valid zero and distinguish unavailable, failed and stale observations.
- Bind producer samples to captured source, revision, stream and acquisition order.
- Expose a cached facade accessor; preserve event bus scalar/container restrictions.
- Keep the GUI migration, seek acknowledgement and native visual acceptance open.
- No dependency, native ABI declaration, queue-repeat or public-release change.

## R5I step04B reconstruction — 2026-09-19

- Hold a started seek through S_OK plus ordered native SEEKING/SEEKED; do not admit overlapping unresolved operations.
- Bind seek callbacks to live engine generation, retaining no native pointers or callback clock queries.
- Keep native error pumping and paused clock sampling active while pending seeks block EOS.
- Expose cached seek facts to both bars; require a post-completion sample rather than painting a requested target.
- Extract existing stream API helpers without body changes and remove obsolete setup size allowances.
- Reconstructed from exact step04A; old unexported step04B claims are not reused. Windows execution, complete gestures and interpolation remain unverified.

## R5I step08AB — 2026-09-25

- Insert a fail-closed native source-detach barrier for MediaEngine source replacement: Pause when active, `SetSource(NULL)`, wait for `PURGEQUEUEDEVENTS`, then commit the next source epoch and defer Play until CANPLAY.
- Prevent stale/duplicate PURGE or PAUSE events from releasing another source generation and coalesce rapid pending replacements to the latest uncommitted path.
- Add direct regression coverage for null-source detach, purge ordering, duplicate purge handling and detach failure rollback.
- Close the stale mixed-line-ending hygiene allowance for the modified adapter-event test.
- Record that 08AA Windows evidence still reproduced the native Event-handle / AMD-thread growth; 08AB remains a validation candidate until its Windows resource run completes.



## R5I step08AI — controlled application WIC preview (2026-09-26)

- Added explicit software frame-server/WIC rendering in the real wx video surface, with COM-owner resources, a bounded single-flight handoff, viewport-sized reused buffers and checked native source identity.
- Preserved the archived 08AD baseline; WIC is opt-in until owner validation. No automatic fallback, driver or registry modifications.
- Made native Shutdown failures observable, requested shutdown on engine replacement, and prevented explicitly stale events from publishing to consumers.
- Added native-boundary, pipeline, application-integration and UI failure regressions. Windows playback/performance/resource acceptance remains pending. See docs/wic-integration-08ai.md and the external delivery report.
