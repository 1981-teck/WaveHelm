# WaveHelm engineering roadmap

This roadmap describes engineering work after the public `1.0.3` source release. It is not a promise of dates or release scope.

## Current source release: 1.0.3

WaveHelm `1.0.3` is the current public source release, with a [Windows x64 installer](https://github.com/1981-teck/WaveHelm/releases/tag/v1.0.3). It carries the public `1.0.2` baseline plus the Windows `ffprobe` no-console correction. The [release notes](docs/release-notes-1.0.3.md) record the maintainer's qualification results. Historical RI03 evidence retains its original scope and is not a statement that the current public release is still unpublished.

## Read status and evidence separately

**Implemented** means the relevant source or workflow exists; it does not mean every environment or adversarial case has been qualified. **Recorded qualification** refers only to the scenario and evidence named by its source. **Remaining** identifies a concrete extension or verification, not a feature presumed absent. Source inspection or this documentation update does not constitute a new test run.

| Area | Current basis | Remaining work |
| --- | --- | --- |
| Settings persistence and imports | Atomic replacement, schema normalization, transactional import handling, and failure regressions are present in [setting_manager.py](src/model/setting_manager.py) and [settings tests](tests/test_settings_security.py). | Extend failure injection and UI error-propagation coverage; distinguish failed writes from post-replacement directory-sync limitations. |
| Maintenance cleanup | Application-owned path constraints, canonicalization, managed-cache markers, protected-location checks, and active-cache handling are present in [settings_controller_cleanup.py](src/controller/settings_controller_cleanup.py). | Extend negative Windows filesystem tests and verify permissions, reparse-point changes, partial deletion, and concurrent use without weakening those guards. |
| Bounded ffprobe execution | [ffprobe_service.py](src/utils/ffprobe_service.py) already centralizes typed failures, timeout/output bounds, process cleanup, and the Windows no-console flag. | Verify consumers and their cancellation contracts; extend regression coverage rather than rebuild the service as a new feature. |
| Windows video lifecycle | The [WIC application baseline](docs/wic-application-baseline.md) records owner-observed playback/resource behavior and earlier standalone evidence, with explicit limits. | Long-duration runs, resolution/frame-rate envelopes, seek/EOS/loop synchronization, fullscreen/HiDPI/multi-monitor behavior, and residual ownership checks. |
| Source and release checks | The [CI workflow](.github/workflows/ci.yml) configures Windows Python 3.11/3.12/3.13 tests/builds, installed-runtime audit/SBOM, and ABI checks. The 1.0.3 notes report their release outcomes. | Explicit OS-version coverage, durable release evidence, and scheduled rescanning; workflow presence alone does not qualify a new change. |
| Public presentation | README, Windows installation guide, packaging notes, and homepage distinguish the published installer from source development and deferred release engineering. | Review user feedback and accessibility; keep version-specific downloads, instructions, and this roadmap consistent at each release. |

## Priority 0 — settings and maintenance safety

Preserve the existing protections and extend their evidence, rather than treating implemented guards as missing features.

- Exercise cleanup rejection of external paths, roots, traversal, unmanaged directories, symlink/junction escapes, and active processed-audio caches on Windows.
- Add or reconcile tests for path changes between validation and deletion, permission failures, partial deletion, and concurrent users of managed files.
- Extend persistence failure tests through the caller/UI boundary. Report failed writes accurately and distinguish a post-replacement directory-sync warning from a transaction that never committed.
- Preserve all-or-nothing validation of imported settings and protected settings fields; add malformed/oversized input cases where coverage is missing.

## Priority 1 — Windows runtime assurance

- Record explicit Windows 10 and Windows 11 scenarios; the `windows-latest` CI label alone is not a two-OS qualification matrix.
- Expand Media Foundation/COM/DXGI coverage for track selection, seek in play and pause, device reset, corrupted media, unsupported codecs, and repeated open/close cycles.
- Extend the bounded WIC baseline to long-duration and load-envelope runs, with resource counters and residual ownership evidence. Do not infer zero leaks from a short stable run.
- Measure fullscreen, HiDPI, multi-monitor, seek/EOS/loop, and audio/video synchronization behavior in the actual target environments.
- Document codec and driver-dependent behavior only from recorded evidence, not presumed hardware causes.

## Priority 2 — bounded I/O and error contracts

- Keep the centralized ffprobe timeout, output limits, typed errors, and process cleanup intact. Verify that callers consistently use the service and preserve its error semantics.
- Define and test caller-requested cancellation separately from internal timeout/output-limit cleanup; do not describe internal cleanup as a complete public cancellation contract.
- Reconcile subtitle size/encoding guards, media-validation state transitions, and Windows filename rules against current implementations and tests before adding work.
- Close confirmed gaps for malformed/oversized subtitles, partially valid domain objects, reserved filenames, and normalization collisions, with bounded behavior and focused regressions.

## Priority 3 — architecture and type safety

- Reduce oversized modules only where a cohesive, independently testable responsibility can be isolated; avoid cosmetic splitting and pass-through layers.
- Replace internal dynamic dictionaries and `Any`-based domain state with typed structures in the affected change-set, without spreading dynamic parsing beyond explicit boundaries.
- Extend static type checking and lint gates incrementally, preserving external contracts and recording legacy exceptions rather than hiding them.
- Separate presentation, controller, and persistence responsibilities where a measured maintenance or correctness need justifies the change.

## Priority 4 — localization, accessibility, and public presentation

- Reconcile the existing locale schema, key-parity tests, and translations before declaring missing coverage or rebuilding checks already present.
- Close confirmed English/Spanish/French key, placeholder, and plural-form gaps; record which languages and user-visible flows were actually reviewed.
- Add keyboard-focus, reduced-motion, screen-reader, and automated accessibility checks to GitHub Pages and the desktop UI where applicable.
- Optimize large screenshots only with measured benefit and retained legibility; establish site performance budgets.
- At each release, update the versioned installer link, source/install instructions, release evidence references, and roadmap status together.

## Release discipline

Each subsequent release should be created from a clean tagged commit, retain source tests, exclude generated runtime output and secrets, publish hashes and build evidence, and preserve dependency-audit/SBOM artifacts beyond temporary CI retention.

A published installer, source tests, signing verification, and public installer-to-tag reproducibility are separate claims. Follow [Windows source and release notes](docs/windows-packaging.md) for their current boundaries. Preserve historical manifests and failed attempts; generate a fresh inventory for a new artifact and leave any unexecuted check explicitly `NOT RUN` / `NOT VERIFIED`.
