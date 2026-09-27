# R5I-step07A — conservative cleanup, first repair only

Date: 2026-09-21. Baseline: R5I-step06A.

## Scope

Repair AUD-BUG-01 and AUD-BUG-02 from the cleanup audit. This is one bounded
five-path change set: two application modules, one new test module, this document
and CHANGELOG.md. It performs no unused-import or dead-code removal.

- `common` now owns `logging.getLogger(__name__)`. Existing column restore/save
  handlers can record their original diagnostic without raising a secondary
  NameError. No root logger configuration, handler, logging level or exception
  class is changed.
- `main_view_shell` imports the existing `create_flow_sizer` re-export from
  `common`. A missing page factory can construct the existing placeholder rather
  than raising NameError. Its text, labels, dimensions and factory policy remain.

## Preserved behavior and limits

All existing function/class bodies in the two production files are unchanged.
Restore still tries subsequent columns after an already-handled setter error.
Save still makes one attempt and does not retry or claim persistence succeeded.
A failed width read still does not submit a partial list. The exception tuple is
not broadened: OSError and KeyError still propagate where not previously caught.
The initial settings read remains outside the helper's existing catch boundary.
This patch does not make arbitrary settings/GUI/logging failures infallible.

The three real collection views and the real shell routing are exercised between
test-owned wx controls and controlled storage/widget faults. No native wx window,
user profile, actual storage failure or Windows DLL is exercised locally.

## Verification contract

The same new tests must fail on the exact baseline for the audited missing names
and pass after only the two binding additions. Existing consumer tests, a local
regression, source hygiene and byte/AST preservation are checked separately.
Counts, actual commands, environments and limitations belong to the external
report/evidence; historical Windows results never certify this new patch.

## Deferred work

AUD-BUG-03 (`format_file_size`), unused imports, private compatibility wrappers,
singleton and native release duplication are deliberately unchanged. They require
a later approved bounded step. Media playback, cursor residuals, Library geometry,
locks, dependency/build inputs, ABI declarations and test selection are unchanged.

Keep step06A and the restored Windows environment. Do not replace the working
application or run a legacy updater for this engineering checkpoint. No remote
commit, push, tag, Commit/Publish or public release is authorized. Native execution
of this new checkpoint and whole-release qualification remain NOT_VERIFIED.
