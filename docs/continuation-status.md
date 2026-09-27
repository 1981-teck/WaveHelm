> Historical development record through the 08AX lineage. For the current
> 1.0.2 RI03 candidate identity and outstanding gates, see [release integration](release-integration.md).
> Earlier version numbers and NOT VERIFIED statements below retain their original scope.

# Step 23 continuation — reconstructed from the verified 10B source

Date: 2026-09-10. Mode: STANDARD. Profile: RELEASE_CANDIDATE.
Triage: compliance. Audit scope: L2 release; native execution remains separate.

## Provenance

The input archive SHA-256 is
`4eeafac1574a56623406dab9df24a1f5b90c232fd7db01357c514917dd7902b6`.
Its reconstructed Git tree is `7a46eed8bb17edd4e9cc66da83ad455cd61f6587`.
The prior unshipped checkpoint could not be recovered. This continuation does not
claim to contain it or reuse its test results. Local reconstruction commits are
not the original remote history. No remote publication is authorized by this file.

## Change-set A — build and evidence parity

The backend is pinned to the locally available setuptools 82.0.1. Setuptools
provides its wheel builder; the unused unpinned wheel dependency was removed.
CI installs the same backend and invokes the build frontend with no isolation,
so it does not silently resolve a different backend. Runtime pins are unchanged.

Both Windows test/build and supply-chain jobs cover Python 3.11, 3.12 and 3.13.
Every matrix leg retains uniquely named evidence bound to its interpreter and
commit. JUnit and branch-aware coverage are written outside the source tree.
Coverage is measured; no calibrated global coverage-ratchet claim is made yet.
Native pip bootstrap and installation failures stop the test/build step.

## Change-set B — Windows lock contracts

The native generator resolves runtime/development graphs per interpreter and
validates wheel identity, hash, target markers, exact roots and all transitive
edges. CI requires checked-in, mechanically validated lock bundles before installing
with `--require-hashes`. Continuation R3 contains six native Windows bundles:
runtime and development for CPython 3.11, 3.12 and 3.13 x64. The 2026-09-14
collection reached installed-graph equality with clean `pip check` for all six.
Human release review remains a separate decision; CI does not label that review
as completed. See `docs/locked-dependencies.md` for generation and verification.

## Change-sets C/D — local snapshot staging and PowerShell delegation

The implemented scope is `Prepare`, local `Commit`, `Cancel` and `Status` on a
sealed, separate source copy. It is not the complete canonical branch updater.
`Publish`, remote Verify, native ABI proof and historical branch replay remain
unimplemented/unverified. Every local status retains `release_readiness=NOT_VERIFIED`.

Use `tools/WaveHelm-LocalStaging.ps1` to delegate to the same Python implementation:

```powershell
.\tools\WaveHelm-LocalStaging.ps1 -Command Prepare -Source C:\work\WaveHelm -Session C:\work\WaveHelm-session-R1
.\tools\WaveHelm-LocalStaging.ps1 -Command Status -Session C:\work\WaveHelm-session-R1
```

Native wrapper/Pester execution in this Linux continuation: NOT_RUN. On Windows,
use a controlled Pester 5 environment and run:

```powershell
Invoke-Pester -Path .\tests\powershell\LocalStaging.Tests.ps1 -Output Detailed -CI
```

These wrapper/state tests do not replace a real successful full project preflight.
The source distribution includes the wrappers and tests; their static inclusion
checks are not PowerShell parser or execution proof. See `docs/local-staging.md`.

## Change-set E — reproducible pytest plugin scope

A cumulative run produced all expected case outcomes but its process exit was
not observable with host pytest plugins autoloaded. The same selected suite with
third-party entry-point autoload disabled returned exit code 0. This associates
the observed difference with host plugin loading; no individual third-party
plugin is conclusively identified as the cause. Preflight and CI now disable
autoload consistently. Preflight also strips inherited plugin/addopts injection.
The project uses pytest built-ins and its own conftest; neither is disabled.
This change does not filter tests or provide missing pygame/wxPython modules.

## Continuation R2 — native lock/install collection, not native qualification

R2 starts from the delivered R1 source archive SHA-256
`eb418bf515bb87bb899f2fbd931fddaec2a69ad0bcb20fb78511346a4a552983`,
Git content tree `bf8e25089336ba2be2606758b9b842e61911bcb0` (396 files).
It does not recover or claim any lost historical checkpoint.

The standard-library parent collector orchestrates the six real Windows targets
through isolated environments, exact bootstrap wheel hashes, preserved command
outputs, installation reports, pip checks and live metadata comparisons. Source
and snapshot identity plus exact lock bundles are checked before an aggregate
result can be complete. No generated lock is called human-reviewed.

Portable tests cover the collector contracts and handled failures. A real Windows
collection was subsequently executed on 2026-09-14 and returned
`MATRIX_INSTALLED_NOT_AUDITED`; all six target installations matched their lock.
The candidate source now includes those exact bundles. Native Pester execution,
vulnerability/SBOM/license audit, application qualification and human release
approval remain separate. Runtime requirements, application code and public
version are unchanged.


## Continuation R3 — integrated locks and native supply-chain audit route

R3 incorporates all six Windows lock graphs collected on 2026-09-14. The
`requirements.lock` files remain byte-identical to the returned evidence; the
supporting pip reports use LF repository line endings instead of the original
Windows CRLF, with parsed content and lock rendering unchanged. The accompanying
evidence showed clean `pip check` and exact installed-graph equality for
runtime/development on CPython 3.11, 3.12 and 3.13 x64. The source inventory in
that campaign matched the delivered R2 source byte-for-byte.

R3 also adds `WaveHelm-SupplyChainMatrix.ps1` and its Python implementation. The
collector creates fresh runtime environments from the checked-in runtime locks,
runs `pip-audit`, emits CycloneDX 1.6 SBOMs, inventories license evidence and
invokes the existing supply-chain validator for each supported interpreter.
This native audit route is implemented and portable-contract tested here, but its
Windows execution remains NOT_RUN until a returned evidence archive is reviewed.

## Continuation R4 — hash-locked install-report semantics

The 2026-09-14 native supply-chain campaign completed pip-audit, CycloneDX 1.6
SBOM generation, runtime license inventory and evidence manifests for CPython
3.11, 3.12 and 3.13. All three pip-audit executions returned zero
vulnerabilities and all three evidence manifests verified. The R3 validator
blocked only because pip marks every line of a fully pinned `--require-hashes`
lock file as `requested=true`, while the earlier validator accepted only direct
requirement roots.

R4 accepts exactly two pip report root shapes: the direct requirement set used
by the legacy direct-install path, or the complete installed package set used by
the reviewed hash-locked path. Partial or hybrid requested sets remain blocking.
The returned Windows evidence revalidates with zero findings under this corrected
contract. Runtime requirements and the six checked-in locks are unchanged.

A fresh R4 Windows run is still required before final release qualification so
the returned matrix evidence is bound to the exact R4 source inventory rather
than the preceding R3 source inventory.

## Continuation R5 — R4-bound supply-chain closure and ABI hardening

The fresh R4 Windows supply-chain matrix is bound to the exact R4 source
inventory (`source_digest=ac6f05740808a73a9dd1f4aab875b42a84d3857b38c9354b96e6627ef5831d69`).
All three runtime targets report `AUDIT_PASS`, zero validator findings and zero
`pip-audit` vulnerabilities. Each target also produced a CycloneDX 1.6 SBOM
with 21 components, a runtime license inventory and a verified evidence
manifest. The installed-graph audit gap is therefore closed for this source
checkpoint; human release approval remains separate.

R5 also hardens the Media Foundation ABI declarations. The DXGI device-manager
`LockDevice` binding now includes the required `BOOL fBlock` argument, and the
`IMFTimedTextNotify` vtable contains its complete callback sequence. The ABI
declarations were extracted from the former oversized `definitions.py` into a
cohesive module, removing that stale legacy size exception. A native Windows
SDK reference compiler/verifier and CI job are included, but native execution
of that new ABI gate is still NOT_RUN until evidence is returned from Windows.

## Open release gates

- Human release review/approval of the six mechanically validated Windows lock bundles.
- Native Windows SDK ABI/layout proof from the R5 verifier.
- Native PowerShell/Pester and COM/media runtime qualification.
- Real pygame/wxPython full-suite and installed application startup.
- External static/type/security tools and calibrated coverage non-regression.
- Final remote CI evidence on the exact authorized branch commit.
- Historical direct Step 10A artifact provenance: unavailable, not fabricated.

The source version remains 1.0.2.dev23. A source checkpoint is not a qualified
release candidate and does not change public release 1.0.1.
