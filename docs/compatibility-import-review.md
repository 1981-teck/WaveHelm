# Current status amendment — step07P

22 September 2026. COMP-07 (`win32api`) and COMP-08 (`PIL.Image`) are now explicit
**KEEP_CURRENT_BASELINE** decisions after the source, failure and packaging review.
Neither is removed or certified indispensable. See `optional-import-review.md` for
observed contracts, actual tests, limits and requirements for any future removal.
Their earlier KEEP_PENDING_QUALIFICATION wording below is historical. Native/frozen
qualification remains separate; no new Windows execution is claimed.

The local 28-candidate catalog was completed by accepted step07M. The singleton
was retired in O. The following step07L record is retained as dated history;
its proposed M work and pending later decisions are not the current backlog.
Current continuation authority: `cleanup-progress.md`.

---

# Compatibility and initialization imports — step07L

Date: 22 September 2026. Exact application baseline: R5I-step07K.
STANDARD / SECURITY_CRITICAL / cleanup, bounded L2 source review. This document
records decisions about eight recovered catalog entries, not full native or release
qualification. It does not authorize modifying the user's working installation.

## Historical provenance recovered

The original `WaveHelm_R5I_step06A_CODE_CLEANUP_AUDIT_20260921_evidence.zip`
was recovered with its report. Its 50 payload hashes and exact inventory were
verified. `evidence/import-catalog.json` contains 175 named entries: six classified
`REVIEW_PRIVATE_NATIVE_COMPATIBILITY` and two side-effect/noqa entries.
The relevant K application files match the original audit's recorded source hashes.
The recovered report/catalog are retained in the new external evidence pack.

Unlike the incomplete later ledgers, the original report lists all seven native
candidates by path/name. They match the four J and three K removals exactly. The
old provenance NOT_VERIFIED statement is now superseded for those seven identities;
this does not re-execute their historical tests or establish global dead code.

## Item-level decisions

| ID | Path and binding | Decision | Reason / preservation |
|---|---|---|---|
| COMP-01 | `src/video/component_base/definitions.py`: `_MissingDLL` | REMOVE private alias only | Failure type remains in `definitions_runtime`; no discovered definitions-alias consumer, annotation or public export |
| COMP-02 | Same file: `_STRICT_DLL_LOAD` | REMOVE private alias only | Loader reads its policy in its defining runtime module; canonical policy and initialization remain unchanged |
| COMP-03 | Same file: `_kernel32` | REMOVE private alias only | Actual DLL owner and runtime bindings remain in `definitions_runtime`; no ownership transfer or unload is added |
| COMP-04 | Same file: `_load_windll` | REMOVE private alias only | All actual loader consumers import the canonical runtime function, not the definitions alias |
| COMP-05 | Same file: `_oleaut32` | REMOVE private alias only | Canonical owner and retained BSTR allocation/free function bindings stay alive and unchanged |
| COMP-06 | Same file: `_user32` | REMOVE private alias only | Canonical owner and retained `_IsWindow` binding remain; no native call changes |
| COMP-07 | `src/controller/app_controller.py`: `win32api` | KEEP_PENDING_QUALIFICATION | Optional ImportError/None branch retained. Absence of local reads does not qualify Windows package initialization without it |
| COMP-08 | `src/model/media_loader.py`: `PIL.Image` | KEEP_PENDING_QUALIFICATION | Explicit noqa import/alias retained. Real alias/resource probe is not evidence that removal preserves every image-loader/packaging path |

The two KEEP decisions are conservative preservation, **not proof that the imports
are essential**. They are not counted as removed or as completed removal campaigns.
No new dependency, optional fallback, fake native success or warning filter is added.

## Six-alias production delta

Only six LF import lines (100 bytes) are removed from `definitions.py`.
The original `definitions_runtime` import remains at the same position with thirteen
other bindings. Its import-time DLL loading still happens. The canonical classes,
functions, DLL references, policy and required facade bindings are unchanged.

The supported `mf_base` public roster did not include these six private names on K.
Removing the incidental `definitions` attributes is nevertheless a deliberate private
namespace change. Repository-wide consumer and annotation review cannot rule out
unknown external code importing those private attributes. Such code must use the
canonical runtime provider; it is not silently described as backward-compatible.

The whole `definitions_runtime.py`, app controller, media loader, audio/DSP, native
ABI declarations and all runtime bodies are byte-identical. COM ownership, thread
policy, vtable methods, exception policy, clocks, volume, dependencies, six lock sets,
CI and version are outside this delta and are not newly certified by it.

## Edge cases and evidence

- Missing required DLL: strict mode must still raise the specific acquisition error.
- Explicitly permitted missing DLL: the existing false-valued proxy remains visible;
  execution of a missing export raises instead of pretending success.
- Removed private facade attribute: canonical provider and required native bindings
  remain; actual loader consumers and public facade identities are checked.
- Optional package not installed or import initialization differs: keep the existing
  import/fallback rather than removing it on an unused-name count.

`tests/test_cleanup_compatibility_imports.py` has 37 cases: six cleanup absence
checks and 31 provider/export/error/optional-alias checks. On K plus the test file,
six absence checks fail and the other 31 pass. After the production delta all 37
pass with hash seeds 0 and 1. Existing tests are not edited, deleted or weakened.
Controlled DLL-failure injection is test-owned; it never fabricates native success.
Pillow is imported for real, with a one-pixel image check. The optional win32api
check is structural, not execution of its Windows initialization.

The accompanying external REPORT records the actual broader tests, import probes,
packaging checks and unexecuted gates. Local ctypes observations are not a new
Windows SDK comparison. No new L Windows GUI/device execution is claimed.

## Correction of the historical local-import ledger

The original 28-item local catalog contains `VIDEO_EXTS` in
`src/audio/audio_engine.py` and `EVENT_BUS_EXCEPTIONS` in
`src/audio/audio_engine_playback.py`. Both remain imported on K and L.
Actual reconciliation is **26 removed, two still awaiting review**.

The two H KEEP decisions, `ProgressSnapshot` and `finalize_video_end`, are valid
additional contract checks, but those bindings were not members of the original
28. Combining them with the 26 removals wrongly closed that historical list.
This document corrects the accounting; it does not label the two surviving imports
as runtime bugs or reopen the accepted R2 audio gate. Neither audio file is modified
here. Old reports are kept intact as dated records rather than silently rewritten.

## Remaining qualification / next scope

Next proposed step07M: review the two original surviving audio bindings against
consumer, annotation, re-export, initialization and test contracts. Keep required
bindings and preserve all runtime functions. Do not repeat the closed R2 check merely
because this container lacks pygame. A future audio delta needs its own evidence.

Separately open: optional win32api/PIL removal decisions; wrapper/legacy helper/
singleton review; 56 historical implicit-encoding calls; global typing/Ruff/Vulture/
security/coverage; current resolved/transitive advisory scanning; full cumulative
Windows/native/COM/GPU/device, subtitle/seek/DPI/accessibility, Pester and remote CI.
The old global Python may still warn; the isolated pygame remedy remains accepted.
Full release readiness remains NOT_VERIFIED.
