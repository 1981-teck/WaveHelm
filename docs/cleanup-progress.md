# Step 08AD — rollback 08AC graphics-device regression

The first Windows smoke execution of 08AC opened the video window but did not start
video playback. 08AC was the first step to attach an app-owned D3D11 device and
IMFDXGIDeviceManager to the existing HWND rendering-mode MediaEngine. The experiment
created the D3D11 device on the default hardware adapter rather than an adapter proven
to own the playback target, so the integration is rejected as a production candidate.

08AD restores the 08AB MediaEngine creation/ownership path and keeps the already-verified
source replacement state machine (PAUSE, native SetSource(NULL), PURGE, new SetSource,
CANPLAY, Play). A regression guard prevents an additional DXGI SetUnknown attribute from
being silently reintroduced into HWND creation. Touched dynamic boundary annotations no
longer use Any. Windows playback is NOT VERIFIED until the dedicated 08AD smoke run; the
separate native handle/thread leak remains open and is not claimed fixed by this rollback.

# Step 08AA — rendering-mode output-format correction

Windows 08Z confirmed that serializing VIDEO->VIDEO replacement through PAUSE did not
change the native growth slope. Through the exact four-switch checkpoint, amdxx64.dll
thread starts still rose from 10 to 50 and Event handles from 713 to 2,901, matching
the prior 08Y behavior closely enough to falsify PAUSE-only quiescence as the remedy.
The 08Z run then timed out because no second group of four NEXT actions was performed;
that protocol incompleteness does not invalidate the completed four-switch checkpoint.

A separate setup defect was found in MediaEngine rendering-mode creation. WaveHelm sets
MF_MEDIA_ENGINE_PLAYBACK_HWND, so the engine is created in rendering mode, but the same
attribute set also forced MF_MEDIA_ENGINE_VIDEO_OUTPUT_FORMAT to numeric DXGI_FORMAT 21
(0x15). That attribute is intended to carry a DXGI_FORMAT and is principally required
for frame-server mode; forcing an unrelated format in HWND rendering mode is not part of
the rendering contract and can alter the graphics/decoder path. 08AA removes that
forced output-format attribute while preserving the callback, HWND target, factory,
ownership and shutdown behavior. Native resource stabilization remains NOT VERIFIED
until the bounded Windows run compares the four-switch slope against 08Y/08Z.

# Step 08W — direct MediaEngine source replacement

Windows 08V2 confirmed that removing the redundant explicit Load call did not stop
resource growth: one reused MediaEngine still accumulated native handles/threads as
video sources changed. The remaining VIDEO->VIDEO path was still issuing the normal
video stop sequence (Pause + SetCurrentTime(0)) immediately before SetSource. Those
native commands are not required to replace a MediaEngine URL and can overlap the
asynchronous source-loading transition.

EngineController now skips the native video stop only for VIDEO->VIDEO and
LOADING->VIDEO replacement while preserving the existing adapter. The following
SetSource call owns the source transition. Explicit stop, VIDEO->AUDIO, shutdown, error
recovery and adapter-close paths are unchanged. This does not change COM P/Q/R ownership
or claim native resource stabilization until a bounded Windows run confirms it.

The desired no-pre-stop contract fails three focused cases on exact 08V and passes on
08W. The local focused player/video/COM perimeter passes. Full release NOT_VERIFIED.

# Step 08V — single asynchronous source-load trigger

Windows 08U proved that VIDEO->VIDEO reuse now reaches the real PlayerController path:
the prescribed session created only two MediaEngine instances. Resource usage still
grew after each source replacement, however, even while the same engine was reused.

The source-load boundary was issuing IMFMediaEngine::SetSource() and then immediately
issuing IMFMediaEngine::Load() for every file. For URL/file sources, SetSource already
starts the Media Engine resource-loading algorithm; Load is primarily for explicitly
reloading source-element lists. WaveHelm now issues exactly one SetSource per source
replacement and preserves the existing BSTR lifetime, active-source bookkeeping,
playback commands, ownership P/Q/R and shutdown protocol.

Local source verification includes a red 08U comparison that observes the extra Load,
a 12-switch regression, SetSource failure cleanup, and a 540-case focused
player/video/COM campaign. Native Windows resource stabilization remains the required
acceptance gate before freezing the video lifecycle work.

# Step 08U — facade-level video-to-video reuse

Windows 08T proved that the previous 08T EngineController-only change did not reach the
real Next/Previous path: PlayerController pre-closed the video controller before
EngineController.play() could preserve the adapter. The Windows run therefore created
and shut down 28 MediaEngine instances and showed strong resource growth.

PlayerController now defers VIDEO->VIDEO stopping to EngineController.play(), allowing
the existing 08T media-aware stop to preserve the live adapter. VIDEO->AUDIO, explicit
stop, shutdown and error recovery retain the full-close path. This is a bounded
controller integration fix; COM ownership P/Q/R is unchanged. Native Windows resource
stabilization remains to be confirmed before freezing the low-level lifecycle work.

# Step 08T — video-to-video adapter reuse

Windows 08S3 showed successful COM ownership/shutdown qualification but also resource
retention correlated with repeated full MediaEngine reconstruction during rapid video
switching. EngineController now preserves the live video adapter only for VIDEO->VIDEO
and LOADING->VIDEO replacement. Explicit stop, shutdown, error recovery and transitions
to audio retain the full-close path. This is a controller-lifecycle correction; no new
COM ownership primitive or global release heuristic is introduced. Native Windows
resource stabilization remains to be confirmed.

# Current checkpoint: step08R — acquisition-proven engine references

25 September 2026. Exact base08Q. STANDARD / SECURITY_CRITICAL / bugfix.
The interrupted earlier08R candidate was not recoverable. This source and its evidence
are reconstructed from the hash-verified08Q archive; earlier unverified70-test/build
claims are not reused as execution evidence.

08O-F01: repaired ONLY on the actual acquired base/Ex engine paths, including late Ex
acquisition, rendering, rebind and creation rollback. Raw compatibility APIs and their
address-only registry remain unchanged and OPEN. Original raw regressions stay visible.
08O-F02/08O-F03: prior ComPtr and single-execution teardown contracts preserved locally.
Native Windows P/Q/R lifetime and allocator qualification remains pending.

There are no COM calls from the new engine owner's garbage collector. Unknown output
and failed release remain explicit and retained rather than guessed/retried. Clean
creation rollback permits explicit retry; unresolved rollback prevents overwrite.
See engine-reference-ownership.md for contracts and the raw/native exclusions.

The exact current executions, output identities, archives and counts are recorded in
the delivered report/evidence, not inferred from this ledger. No existing test, tool,
policy, dependency, lockfile or CI change is required by this stage.

Next08S: prepare bounded native Windows qualification of the P/Q/R acquisition/teardown
chain, with raw timed-text/DXGI ownership migration tracked separately. Do not treat an
ordinary player launch as proof of allocator reuse or uncertain-error handling.

Ambient08N native confirmation, C18-08M, same-path/pathless identity, mixer restart/ABA,
cache and decoder budgets, strict Matplotlib initialization and global typing/security/
coverage/advisory/GUI/release qualification remain separate. Historical analyzer queues
remain439 Ruff /363 Vulture /395 style. Full release NOT_VERIFIED.

---

# WaveHelm current checkpoint — step08Q / teardown stage B

25 September 2026. STANDARD / SECURITY_CRITICAL / bugfix, bounded L2.
Exact baseline 08P. Current repair prevents repeated execution of one detached
shutdown bundle, including timeout and late queued work. Native qualification
and full release remain NOT_VERIFIED.

08O-F03: FIXED_IN_DECLARED_LOCAL_SCOPE, native confirmation pending.
08O-F02: previous exclusive ComPtr repair preserved byte-identically.
08O-F01 / WIN08M-COM-01: OPEN; raw TTL guard untouched, eight original reds remain.
One old project expectation requiring async/local fallback is deliberately
replaced by no-repost/no-local behavior. An unchanged external 08O local-fallback
control is likewise obsolete under this explicit compatibility change, not a
new address-reuse failure. All its original assertions/output are retained.

Nine-path coordinated change-set: core shutdown, adapter threading, manager API;
two new test modules; one existing shutdown test contract; existing seek
fixture updated to refuse the new cleanup boundary; teardown contract;
this ledger. No dependency, policy, CI, ABI declaration or native vtable change.
Executed comparable campaign:4211 distinct cases,4174 passed/37 skipped; no failures
in19 completed groups.4139 previous identities/outcomes remain, one unsafe fallback
expectation is replaced,71 cases are new. Focused284=283 passed/one native skip.
Unfiltered collection retains four missing-pygame errors, not a passing full suite.
See REPORT/VERIFICATION for provenance, diagnostics and the native qualification limit.

Next step08R: stage C acquired raw MediaEngine/Ex ownership migration, preserving
separate references at equal addresses; only then retire address heuristics on
proven paths. Do not globally disable the registry or force a Release.

Ambient08N targeted Windows confirmation, C18-08M performance deviation, raw
registry costs, borrowed-pointer coordination, same-path/pathless identity,
mixer restart/ABA/cache limits, strict Matplotlib initialization and global
typing/security/coverage/advisory/native/GUI/device/release work remain separate.
Queues remain 439 Ruff / 363 Vulture / 395 style historical records, not unique bugs.
Old Windows gates stay closed in their original scope; no repeat kit requested.

---

# WaveHelm — source checkpoint 08P, COM ownership stage A

25 September 2026. STANDARD / SECURITY_CRITICAL / bugfix, bounded L2 evidence.
Exact base08N (08O was read-only). This is stage A of the accepted correction plan,
not a claim that the entire COM ownership migration is complete.

08O-F02: fixed in the declared local ComPtr release/transfer/failure scope.
The original four regressions pass unchanged; new contract tests are in the source.
08O-F01 and 08O-F03: OPEN, raw address quarantine and teardown dispatch unchanged.
Their ten original regressions intentionally remain red in separate external evidence.
No wrapper repair is promoted into native COM lifetime or leak clearance.

Five paths: component_base/com_helpers.py; two new ComPtr tests;
docs/comptr-release-ownership.md; this ledger. No old test/ABI declaration/export roster,
dependency, lockfile, policy or CI alteration. Error receipt and explicit failed-transfer
handling are documented; GC is not a retry mechanism. Incoming replacement is admitted
before displaced-owner cleanup; overlapping destructive replacement is rejected.

Next08Q (stage B): exactly-once detached teardown jobs, explicit submission/reply/
completion state, timeout versus cancellation and rejection; no cross-apartment fallback.
Then stage C: migrate acquired raw engine references and validate same-address reuse.
The raw TTL guard remains unchanged until ownership paths are proved.

Native Windows confirmation of COM changes and targeted Ambient08N return are pending.
C18-08M, same-path/pathless events, borrowed reference/add_ref concurrent use, same-format
mixer restart, caches, strict Matplotlib initialization and full release checks remain
separate. Historical queues439 Ruff/363 Vulture/395 style unchanged; no new analyzer scan.
Full release NOT_VERIFIED. Do not overwrite the user's operating installation.

## Preserved preceding checkpoint
# WaveHelm — current source checkpoint R5I-step08N

24 September 2026. STANDARD / SECURITY_CRITICAL / bugfix, bounded L2 engineering
review. Exact source base08M. New Windows-run evidence takes priority over generic
analyzer cleanup. This candidate is not an installer or full release qualification.

## Ambient repair

WIN08M-AMBIENT-01: source-side shape defect corrected and locally regression-tested.
Stereo/multichannel inputs with fewer channels than the target preserve existing
channels in order and pad missing channels with silence. Mono duplication and the
legacy mean-to-mono / leading-channel reduction remain. This is not surround remixing.
The same helper serves exported mixes; four/six-channel synthetic file exports are
covered. The final PCM boundary rejects wrong shapes rather than flattening frames.

Preparation uses get_init() rate, channels AND sample format. Signed16 keeps its
existing clipping/scaling. Other formats explicitly use pygame's existing file
loader, not guessed PCM conversion. Cache keys now include sample format. Observable
format changes before/after construction are rejected without caching a result.
Same-format mixer restart/ABA and mutation after return remain lifecycle limits.
New DEBUG diagnostics report actual format/array shape/dtype, never sample contents.
The exact array shape/format at the user's historical failure remains unavailable;
this repair must not be represented as a reproduction on that original MP3/device.

The diagnostic native-mixer probe exits2/NOT_RUN locally because pygame is absent.
The local tests use real NumPy/SoundFile plus a recording mixer contract boundary;
they are not successful native SDL/device runs. No package is installed or updated.

## Preserved open work

WIN08M-COM-01: OPEN_SEPARATE_OWNERSHIP_REVIEW. Address reuse may be confused with a
recent double Release; no leak magnitude or correct native cleanup is claimed.
No COM guard, native lifetime, playback controller or dependency is edited here.
C18-08M snapshot capture deviation remains explicit. The prior snapshot correction,
MainView/URL/factory/volume/logging/annotation fixes remain unchanged in source.
Queues stay439 Ruff deeper-review,363 Vulture,395 style: historical overlapping
records, not fresh scans or unique defect counts. Same-path/pathless event attribution,
strict Matplotlib encoding initialization and complete Windows/release qualification
remain separate. Historical accepted gates are not relabeled as new08N evidence.

## Next bounded step

08O: isolated COM ownership/address-reuse investigation and a repair only against
proven ownership contracts, not removal of the guard to silence a log. Targeted Windows
Ambient confirmation on08N can now record the actual mixer/array diagnostic. Do not
repeat earlier accepted kits. Full release remains NOT_VERIFIED.

## Previous checkpoint (preserved)

# WaveHelm — current source checkpoint R5I-step08M

24 September 2026. STANDARD / SECURITY_CRITICAL / bugfix. Exact base08L, 576 files.
08M is a source repair authorized by the request to investigate thoroughly and correct
08K-V01, not merely the previously proposed read-only investigation.

## Current result

08K-V01: FIXED_FUNCTIONAL_SCOPE_WITH_DOCUMENTED_C18_OVERRIDE.
Both optimistic snapshot helpers retain their 16 attempts. On exhaustion they capture
actual structural state under the existing RLock, then release it before payload cloning,
user properties and result processing. Public callers and all existing tests are unchanged.
The deterministic concurrent-writer reproduction fails on08L and succeeds after repair.

OVERRIDE C18-08M: shallow slow-path capture is proportional to reference counts, not O(1).
History retains its 10,000-record cap; aggregate subscriber/type/rate registries remain
uncapped in the inherited contract. No real-time/no-blocking/worst-case latency claim.
See event-bus-snapshots.md for rationale, rejected alternatives, risks, tests and closure.
Publication code, subscriptions, rate-limit rules, resets and shutdown are not edited.
No silent empty snapshot, increased retry count or global publisher serialization.

Five source paths: audio_event_bus_observation.py; two new snapshot regression modules;
event-bus-snapshots.md; this ledger. No pre-existing test or runtime dependency changes.
Detailed actual results, repeats, diagnostics and source/package checks are in REPORT
and evidence; those results must not be inferred from this source ledger alone.

## Preserved open work

439 historical Ruff deeper-review records,363 Vulture records and395 style records.
No new full analyzer scan or removal from those counts. 08K-V01 was a separately tracked
validation defect. No blanket suppression, retry-limit increase or policy relaxation.

Same-path/pathless terminal-event attribution remains the explicit08L limit.
Matplotlib3.10.8 strict font-cache encoding initialization remains unqualified.
Prior MainView08L, URL08I, factory08G, volume08E, logging08C and annotation08A fixes stay.
Accepted Windows08A/F/G/G2/L-M and recorded Z scans remain closed in their original scope;
new native Windows08M execution is not implied or required as a repetition of those gates.

Full intended-runtime/unfiltered testing; full application/tool/checker typing; global
security/coverage and current resolved/transitive advisory review; native Windows SDK,
allocator, COM/DXGI/GPU/resources/devices; GUI/subtitles/seek/DPI/accessibility; Pester,
remote CI and separately authorized publication remain separate. C18-08M performance
deviation remains explicit; runtime observation registry bounds are not newly certified.

## Next bounded step

08N: review a finite named subset of the remaining functional analyzer queue, while
tracking C18-08M and cumulative intended-runtime/performance qualification separately.
No re-review of closed APIs without new evidence. No user-machine changes or repeated
old kit is requested. Full release readiness remains NOT_VERIFIED.

## Historical 08L ledger — preserved below

# WaveHelm — continuation checkpoint R5I-step08L

24 September 2026. STANDARD / SECURITY_CRITICAL / bugfix. Bounded L2 engineering
scope; full release NOT_VERIFIED. Exact baseline is08I,574 files, SHA-256
`ca6cefbef53291eb3039620db28ed23d7c02c5e38738224fd3faebc31847b267`.
08J and08K were read-only reviews, not intervening application source releases.

## Current bounded repair

08K-F01 and08K-F02: all three MainView terminal handlers now admit identity before
mutating pending/active/timer/window state. Reuse same_media_path unchanged; retain
local Windows matching and distinguish stream path/query case. Prefer the current
pending request over a stale ready signature; reject queued callbacks after shutdown.
The historical predicate name and refresh_force no-op keyword remain. No native
lifetime, audio, event-bus scheduling or close-error policy redesign.

Five project paths: main_view.py, the new test_main_view_terminal_identity.py,
new main-view-terminal-identity.md, tools/source_hygiene_policy.json and this ledger.
The policy's exact existing main_view line allowance decreases833 to831; all other
policy fields stay unchanged. No threshold increases or new exclusion. All old project
tests, dependencies, real locks, executable tool code and CI remain unchanged. Current actual
execution, integrity and packaging outcomes are recorded in the external REPORT.
See the dedicated contract for pathless, inactive/no-identity and same-path limitations.
A revision on only some terminal producers is not a bound current PREPARE revision.
The same-path replay gap is explicit; this is not universal session isolation.

## Preserved open validation item and finite queues

08K-V01 remains OPEN: the existing concurrent history/stats snapshot test had one
observed failure in08K and five later diagnostic passes. No result here erases that
failure. Do not retry until green or alter the16-attempt bound in this MainView patch.
Next proposed08M: read-only source/test/consumer contract review of that mismatch,
with deterministic failure/convergence evidence before selecting a correction.

Historical Y-based deeper-review queues remain439 Ruff and363 Vulture; separate
Ruff style/layout backlog395. These overlap semantically, not unique defect totals.
08J's eight binding decisions and08K's refresh_force KEEP decision remain accepted.
The two MainView defects are a separate remediation queue, not subtracted again.
No new global Ruff/Vulture scan or blanket suppression is claimed.

## Prior maintenance and qualification boundaries

08I stream identity,08G factory ownership,08E volume cleanup,08C logging and08A
annotations remain preserved. Prior Windows08A/F/G/G2/L-M gates and Z scan execution
remain accepted in their exact scopes; no repeat kit. The28 local/seven native
import removals, six retired private aliases, optional imports and required H exports
KEEP, two private UI helpers retired, six UI wrappers/title helper KEEP, generic
singleton retired, theme callbacks/eight legacy modules KEEP, Y COM corrections,
56 explicit Path text sites and five subprocess decoders all remain unchanged.

Full intended-runtime/unfiltered suite; application/tool/checker typing; global
security/coverage; current resolved/transitive advisories; Windows SDK/compiler/
allocator/COM/DXGI/GPU/device lifetime; native GUI/subtitles/seek/DPI/accessibility;
Pester, remote CI and separately authorized Publish/Verify remain outstanding.
Existing hygiene allowances16 large files/three functions/17 mixed endings/one
runtime assert are not increased. Both COM shutdown paths remain unchanged.
No user-PC installation/environment/database/media change or new native GUI run.
Extract sources in a new directory; do not overwrite a working installation.

## Historical08I ledger (older queues and next-step labels superseded above)

# WaveHelm — continuation checkpoint R5I-step08I

24 September 2026. STANDARD / SECURITY_CRITICAL / bugfix; bounded L2 engineering
scope. Full release NOT_VERIFIED. Exact baseline is 08G, 571 files, source SHA-256
`345fb8af1717a0233b6718393f49ad064d6b38ca1ad3990075e31cd111ad4a21`.
08H was read-only; it supplied six review dispositions and the separate 08H-F01 finding.

## Current repair — 08H-F01 stream event identity

The matcher no longer lowercases complete stream URLs. Its new pure comparison
boundary folds only ASCII scheme/host case for HTTP(S), RTSP and RTMP. It preserves
resource data, userinfo, port spelling and IPv6 zones; it does not decode percent
escapes or invent redirects. Local trimmed case-insensitive matching remains.
Direct and resolved metadata identities use the same comparator. Malformed recognized
stream authorities/controls and comparison values over the declared character cap
are rejected rather than gaining equivalence. See stream-path-identity.md for exact
limits, conservative non-equivalences and retained legacy event behavior.

Five changed paths: player_event_handler_video.py; new media_path_identity.py;
new test_stream_path_identity.py; new stream-path-identity.md; this ledger. No old
test, event consumer body, attachment table, COM/decoder/audio path, dependency,
lockfile, CI or source-hygiene allowance is changed. The legacy handler stays at
657 lines rather than increasing its allowance. The utility is a complete I/O-free
comparison boundary, not a placeholder or a new native/provider service.

The original 08H tests are retained and rerun unchanged. ERROR/STOPPED/READY stale
URL cases are checked directly and over the actual synchronous bus. Valid controls
still reach the original recovery/reset/admission paths. READY's test receiver reports
not-ready, never native success. No URL is opened. Current execution results and any
limits are in the delivered REPORT/VERIFICATION/evidence, not inferred from old runs.

## Current finite queues and preserved decisions

After 08H's six individual dispositions: 447 historical Ruff deeper-review records,
364 Vulture records, 395 separate Ruff style/layout records. The adjacent 08H-F01
repair is not subtracted again; no new global analyzer scan or suppression occurs.
08G creation ownership, 08E volume recovery, 08C labelled-value logging and 08A
annotation corrections remain. The accepted Windows 08A/F/G/G2/L-M gates and Z scans
stay CLOSED in their actual scopes, not relabeled as new 08I executions.
The 28 local/seven native import removals, six private-alias retirements, explicit
KEEP decisions, generic-singleton retirement, theme callback contracts, eight-module
legacy decisions and 56-Path/five-process encoding accounting remain intact.

## Separate qualification and next bounded step

Full intended-runtime/unfiltered tests; application/tool/checker typing; global
security/coverage; current resolved/transitive advisories; Windows SDK/compiler/
allocator/COM/DXGI/GPU/resource lifecycle and physical devices; GUI/subtitles/seek/
DPI/accessibility; Pester, remote CI and authorized Publish/Verify remain separate.
Existing hygiene debt is 16 oversized files, three functions, 17 mixed-ending files
and one runtime assert; neither these allowances nor both COM shutdown routes change.

Next proposed 08J: resume bounded read-only review of a finite named subset of the
remaining functional-risk analyzer records before any further patch. Do not reopen
settled APIs or replace a confirmed defect with cosmetic suppression. No repeated
Windows audio/annotation kit, user installation, global Python, environment, database,
preset or media changes are requested by this checkpoint. Full release NOT_VERIFIED.

## Historical 08G ledger (older next-step labels and counts are superseded above)

# WaveHelm — continuation checkpoint R5I-step08G

23 September 2026. STANDARD / SECURITY_CRITICAL / bugfix. Bounded L2 evidence;
full release NOT_VERIFIED. Exact source baseline 08E (569 files), SHA-256
`98fc9b30ff5719c7446ce818f8530eac9f316b039255d4ded0ebc67a0d4d0d24`.
08F is a read-only review, not another source tree.

## Current correction: 08F-F01 creation ownership

The exported compatibility factory now releases its uncommitted creation in a
finally clause, including undeclared constructor errors and intercepted control
exceptions. Cleanup holds the existing RLock/Condition and requires creating=True,
the same creation_seq and the current creator_tid. It clears the unpublished state
and notifies all waiters. A stale frame cannot erase a later generation or another
thread's state. A completed publication is not rolled back by cleanup.

Declared errors retain their original last_error text and logging/re-raise path.
Undeclared exceptions propagate unchanged, with no newly invented error translation;
last_error stays None and already-waiting callers receive the existing explicit
unknown-failure MediaEngineError. A subsequent independent caller may retry. No
automatic retry or fake adapter is introduced. Callback construction failures never
schedule a creation callback. The initial creation log is inside the protected try.

Callback execution remains outside the lock and after publication. Declared callback
errors keep their best-effort debug behavior; undeclared/control errors propagate.
The callback finalizer clears only its current thread's callback marker, not another
thread's newer callback. Existing shutdown waits/timeouts and status-probe policy
remain; this is not a redesign of native COM lifetime or cancellation semantics.

Two small helpers in the SAME module centralize release and callback finalization.
The main factory drops from 59 to 48 recursively counted statements, within the
existing 50-statement limit. Only its now-stale allowance is removed from the hygiene
policy; no threshold or exclusion is relaxed. Other three function allowances remain.

Five changed paths: adapter_factory.py, source_hygiene_policy.json, two new factory
regression modules, and this ledger. All previous project tests, runtime modules
outside adapter_factory, dependency locks and CI remain unchanged. The two new test
modules use real Python threads/locks/conditions. Successful return values are inert
Python identity/lifecycle tokens for synchronization assertions, NOT native adapters
or evidence of COM/GPU/device success. Failure cases use injected exceptions, not
actual memory exhaustion. All workers are joined and test barriers have bounded waits.

## Ownership and qualification limits

No claim covers forcible process termination, an uncatchable native crash, arbitrary
private-state mutation, allocation failure during partial publication, or a second
failure of lock acquisition/exception-string conversion/logging while handling an
original error. The original logging and error-message conversion policies are not
rewritten. Existing callback/shutdown timeouts remain best-effort, not hard real-time
or proven cancellation. The normal controller still creates the native adapter via
create_imf_media_engine_adapter directly and is byte-identical.

Run-specific before/after, concurrent, broad, replay and packaging evidence is in the
external REPORT and VERIFICATION. 08F-F01 is closed only in that executed local scope;
new native Windows GUI/COM/device qualification remains separate. No repeat of old
08A, F/G/G2 or L/M kits is requested. The supplied manual G/G2 logs remain historical
playback evidence, not 08G runs. No user application/environment/data was modified.

## Finite review queues

The 13 records reviewed in 08F remain individually disposed. Ruff deeper review:
453 historical Y-based records; Vulture: 364; separate Ruff style: 395. Fixing the
adjacent08F finding does NOT subtract it again. No global Ruff/Vulture rescan or new
project analyzer suppression is claimed. Prior08E/08C/08A fixes and closed import,
API,56-Path-site and five-process-decoder decisions remain retained.

## Next bounded step: 08H

Resume read-only individual review of a finite named subset of remaining functional
analyzer records. No mass fixes, API retirement or re-opening accepted gates without
new evidence. Complete typing, global security/coverage, current resolved/transitive
advisories, intended-runtime/unfiltered tests, Windows SDK/compiler/allocator,
COM/DXGI/GPU/resources/devices, GUI/subtitles/seek/DPI/accessibility, Pester, remote CI
and separately authorized Publish/Verify remain open. The remaining hygiene debt is
16 large files, THREE oversized functions,17 mixed-ending files and one runtime assert.
Both owned/fallback COM shutdown routes remain. Full release NOT_VERIFIED.

## Historical 08E ledger (superseded only by the current findings and counts above)

# WaveHelm — continuation checkpoint R5I-step08E

23 September 2026. STANDARD / SECURITY_CRITICAL / bugfix. Bounded L2 engineering
evidence; full release NOT_VERIFIED. Exact baseline 08C source SHA-256:
`9ac9c8d13e5690f03723666f1ef6b021b91b82874b17693f0c4b2d27c56ffdef`.
08D was a read-only review. The current production change is only
MiniPlayerChrome._set_volume_slider: preserve and finally restore the entry
_volume_sync state. Widget/conversion errors propagate; nested writes retain the
outer guard. No backend call, catch, import, lock, timer or numeric-cast edit is added.

## Current result and retained history

08D-F01 is corrected in the locally tested guard/recovery scope. The original two
failing cases now pass without changing them. A historical observation requiring
the buggy flag to stay True is preserved as a superseded characterization, not
counted as a successful after-fix check. No existing project test is edited.
See volume-sync-cleanup.md and the external delivery REPORT for actual test,
replay, package and source-preservation results and native limits.

Change-set: mini_player_chrome.py; new test_volume_sync_cleanup.py; new
volume-sync-cleanup.md; CHANGELOG.md; this current ledger. Prior logging, annotation,
import, API and encoding corrections remain. Native Windows 08E is NOT_RUN locally;
prior Windows gates are not relabeled as unexecuted or as new 08E evidence.

## Finite queues after the 08D decisions

| Queue | Current remaining work |
|---|---|
| Ruff individual deeper review | 466 historical Y-based records; not confirmed bugs |
| Vulture deeper review | 364 historical records with cross-tool overlap |
| Ruff style/layout | 395 historical records, separate from bugfixes |
| 08D-F01 | FIXED_IN_DECLARED_LOCAL_SCOPE in 08E; native GUI qualification separate |
| 08B-F01 / 08B-F02 | Logging repair retained byte-identically from 08C |

No new analyzer scan or suppression occurred. The 15 prior 08D cast decisions
remain KEEP; this fix is an adjacent state-cleanup correction and is not subtracted
again from those queues. Raw historical diagnostic totals remain historical.

## Prior maintenance closures retained

08B-F01/F02 logging value masking and mapping-format repair remain corrected in08C's
declared local scope; unknown/unlabelled secrets, old log cleanup and full security
qualification are not inferred.08A annotation corrections and its208/233 Windows
comparison, four-to-zero F821 delta, prior F/G/G2 and L/M gates remain CLOSED. No repeat
of these kits is needed because the local container lacks pygame.

The28 local and seven native import decisions, six retired private aliases, optional
win32api/PIL.Image KEEP, required H exports, two retired private UI helpers, six UI
wrappers/title helper KEEP, generic singleton retirement, theme callbacks KEEP,
eight legacy/native modules KEEP, Y COM fixes,56 explicit Path text sites and five
separate process decoders remain. Prior column logger, shell fallback, file-size,
initial-zero slider and ctypes fixes are not reopened. Manual G/G2 Windows smoke
remains historical; no new08C/08D native or physical-device run is claimed.

## Separate qualification still required

Full intended-runtime/unfiltered suite; complete application/project-tool/checker
typing; global security and coverage; current resolved/transitive advisory audit;
Windows SDK/reference/compiler/allocator; COM/DXGI/GPU ownership and real devices;
GUI/subtitles/audio seek/DPI/accessibility; Pester, remote CI and separately authorized
Publish/Verify remain open. Historical hygiene allowances and both COM shutdown
routes remain unchanged. The current report distinguishes actual local runs from
historical Windows evidence. Source preservation is not full release qualification.

## Next bounded step — 08F

Resume read-only deep triage of a finite, named subset of the remaining historical
functional-risk records, with source/consumer binding before proposing another patch.
Do not reopen accepted annotation/audio/import/API/encoding gates without new evidence,
and do not apply blanket style fixes. The volume guard repair is not a new scan.
Keep the working application, global Python, existing Windows environments, APPDATA,
databases, presets, media and original evidence. No new user-PC test is requested by
this source checkpoint. Full release readiness remains NOT_VERIFIED.

## R5I step08AB — native source detach barrier (2026-09-25)

08AA Windows evidence is COMPLETE for the four-switch protocol but does not reduce the
native resource slope: handles 1,994 -> 4,336, Event handles 759 -> 2,917, and
`amdxx64.dll` start-module threads 10 -> 50. After the 20-second dwell, the AMD thread
count remains 50 and Event handles remain approximately flat at the elevated level.
Therefore removing the forced HWND video-output format is retained as a correctness fix
but is falsified as the resource-leak solution in this scope.

08AB inserts a real native source detach between generations. An active replacement now
uses `PAUSE -> SetSource(NULL) -> PURGEQUEUEDEVENTS -> SetSource(new) -> CANPLAY -> Play`.
Already-paused sources skip only the redundant Pause; they still execute the detach/purge
barrier. The committed source epoch is not advanced until the detach purge arrives, stale
or duplicate purge events cannot release a replacement, rapid pending selections coalesce
to the latest path, and detach failure clears the pending replacement fail-closed.

Microsoft documents PURGEQUEUEDEVENTS as notification that pending MediaEngine events were
flushed, and Microsoft sample code uses `SetSource(nullptr)` to clear a MediaEngine source.
This step does not claim that PURGE proves GPU/driver resource destruction; the Windows
resource run is still required to test that hypothesis. No MediaEngine recreation, sleep,
or fake-success fallback is introduced.

Local focused and expanded MediaEngine/controller regression gates pass after the state
machine change, and source hygiene is green after removing the now-stale mixed-line-ending
legacy allowance for the modified event test. Native Windows handle/thread behavior remains
NOT VERIFIED until the 08AB validation kit is executed.


## R5I step08AC — app-owned DXGI video-device lifetime (2026-09-26)

08AB Windows evidence is COMPLETE for the four-switch protocol and falsifies the
explicit source-detach/PURGE barrier as the native-resource solution. The sequence was
actually exercised four times, but FIRST_VIDEO -> AFTER_4_SWITCHES still grows from
1,919 -> 4,317 handles, 714 -> 2,904 Event handles and 10 -> 50 `amdxx64.dll`
start-module threads. After the 20-second dwell, Event handles remain 2,903 and the AMD
thread count remains 50. The detach/PURGE correctness work is retained, but no further
source-lifecycle sequencing is justified by this evidence.

08AC moves one level lower in the rendering stack. Each MediaEngine lifetime now owns a
single hardware D3D11 device created with `D3D11_CREATE_DEVICE_VIDEO_SUPPORT |
D3D11_CREATE_DEVICE_BGRA_SUPPORT`, enables `ID3D10Multithread` protection, creates one
`IMFDXGIDeviceManager`, calls `ResetDevice`, and attaches that manager through
`MF_MEDIA_ENGINE_DXGI_MANAGER` before MediaEngine creation. The device manager and D3D11
device are explicitly owned by the engine lifetime and transferred into the shutdown
job so they are released deterministically only after the MediaEngine references are
released. Source switches continue to reuse that one engine/device-manager lifetime.

Microsoft documents the DXGI manager as an optional rendering-mode initialization
attribute, requires the caller to provide the initial D3D device to `ResetDevice`, and
recommends `D3D11_CREATE_DEVICE_VIDEO_SUPPORT` plus multithread protection. The official
MediaEngine sample uses the same VIDEO_SUPPORT | BGRA_SUPPORT device flags, enables
multithread protection, creates/reset a DXGI manager and attaches it to MediaEngine.
`D3D11_CREATE_DEVICE_PREVENT_INTERNAL_THREADING_OPTIMIZATIONS` is intentionally not used:
Microsoft states that flag is not recommended for general use.

Local exact-candidate, ownership/rollback/shutdown, MediaEngine/controller, hygiene,
packaging and focused supply-chain gates pass in the declared Linux scope. The complete
unfiltered pytest suite remains NOT RUN TO COMPLETION because pygame is unavailable in
this environment; mypy is NOT RUN because the module is not installed. Real Windows
D3D11 creation and the native handle/Event/AMD-thread slope remain NOT VERIFIED until the
08AC validation kit is executed.


## R5I step08AI — WIC application integration candidate

Scope: native WIC/GDI boundary, COM ownership, single-flight GUI handoff, application selection, resize/source/seek/shutdown barriers, and corresponding tests. This is a separately staged application candidate, not another stand-alone MediaEngine probe and not a qualified release. Existing 08AD source remains archived. Per-file changes and executed checks are recorded in the external 08AI report; implementation and outstanding acceptance requirements are in `wic-integration-08ai.md`. The historical R5 manifest remains historical; `SOURCE_MANIFEST_08AI.json` is delivered externally for current bytes.
