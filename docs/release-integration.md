# WaveHelm 1.0.2 — release integration RI03

**Status: CANDIDATE_NOT_PUBLISHED. No tag, push or release is authorized by this document.**

## RI03 reconciliation scope

The native Windows RI02 automated run used the qualified CPython 3.12 lock environment.
It passed `pip check`, source hygiene, the 1,644 focused 08AX tests, the 72 release/metadata
tests and the package build. The complete suite executed 4,781 tests with 4,763 PASS,
2 FAIL, 16 intentional platform SKIP and 0 ERROR. The exact expected Windows skip set
was confirmed and the source test copy remained unchanged.

Both failures were the two parameterizations of
`test_controlled_process_rejects_corrupt_utf8`. The Windows traceback proves that the
malformed byte *did* trigger `UnicodeDecodeError` inside `subprocess.py`'s background
`_readerthread`; pytest surfaced those exceptions as `PytestUnhandledThreadExceptionWarning`
rather than as an exception raised by `subprocess.run`. Therefore the RI02 test's outer
`pytest.raises(UnicodeDecodeError)` assumption was platform-specific and incorrect.

RI03 preserves the intended invariant without weakening it: the corruption regression runs
the child in binary capture mode, verifies the exact malformed byte, and performs explicit
`decode("utf-8", errors="strict")` in the calling test thread. Separate AST/static tests still
require the reviewed controlled subprocess decoders to specify `encoding="utf-8"` and
`errors="strict"`. No application runtime file or dependency is changed by RI03.

A fresh native Windows automated run is required. This reconciliation is not a waiver and
does not authorize publication.

## Identity and permitted changes

RI03 retains RI02 runtime content, which starts from the exact 08AX source archive, SHA-256
`4f25aefe4ff440c5b5ac91d6703cb6155e8b8a7ef7914bb5d9c6c45d01393915`.
Active package/runtime/manual identity is 1.0.2. Its version format is prepared
for release; that fact is not proof of qualification or public availability.

The application implementation is preserved. Within `src/`, the only changed Python runtime
literal relative to 08AX is the version fallback in `src/config/app_metadata.py`; the matching
JSON version and four localized manual version strings are also updated.
There are no playback, seek, WIC, audio/DSP, persistence or dependency changes.

Requirements, all six Windows lock bundles, CI workflow and legal-license
payloads remain those of 08AX. Metadata tests are aligned to the new identity
without turning historical test fixtures into claims about current validation.

## Public presentation and community

README hero/badges/navigation/demo are reconciled with the public README content.
The newer 08AX technical body is retained. CONTRIBUTING, Code of Conduct,
SECURITY, Issue Forms and the PR template are restored from the user-approved
artifacts, with their text checked against public pages during integration.
Markdown formatting is preserved from the original authored files.

A network Git fetch and immutable remote commit binding were not available in
the preparation container. Therefore this is a content-level integration, not a
claim of byte-identical synchronization with the current remote tree. The owner
must fetch and review the current branch before publication. No .git history is
invented or rewritten by the source package.

## Verification boundaries

The delivery evidence distinguishes fresh Linux component/packaging checks from
historical 08AX reports and native Windows observations. The latest supplied
Windows follow-up log is preserved outside the public source package. It has no
source-hash binding or complete progress-surface trace; it cannot independently
certify the visible slider fix, all functions, or absence of resource leaks.

Historical documents and `SOURCE_MANIFEST_R5.json` retain their original scope.
The outer `SOURCE_MANIFEST_RI03.json` is the current source inventory. Do not
edit old reports or replace NOT VERIFIED with PASS based on an unrelated run.

## Remaining publication gates

1. Run the supplied Windows qualification on an unmodified fresh extraction
   using the already-qualified Python environment; do not upgrade dependencies.
2. Check the full source suite, native application behavior and version/About
   on the integrated candidate. Keep all skipped, failed and unrun checks explicit.
3. Run the current Windows dependency audit, resolved SBOM and ABI/CI gates in
   the repository's existing workflow. A direct-dependency SBOM is not that audit.
4. Reconcile the actual GitHub branch and community files before a merge/tag.
5. Obtain explicit maintainer acceptance, then prepare the publication commit
   and its evidence. Any subsequent source change requires renewed validation.

The supplied scripts do not push, tag, create a release, install dependencies,
change the ordinary user profile, or silently promote a partial test run.
The optional native smoke uses a disposable copy of the existing WaveHelm user
profile so real preferences/media references can be exercised without writing
the normal profile. Logs/evidence may contain local paths; do not publish them
without review.
