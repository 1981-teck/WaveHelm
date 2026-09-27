# Native Windows supply-chain matrix — continuation R3

This collector evaluates the three release-relevant Windows runtime graphs for
CPython 3.11, 3.12 and 3.13 x64. It installs each checked-in runtime hash lock in
a private environment, runs current vulnerability auditing, builds a CycloneDX
1.6 SBOM from the installed environment, inventories license evidence and feeds
all artifacts to WaveHelm's existing supply-chain validator.

It does not modify source, global Python, Git history or the installed WaveHelm
application. It does not run the ABI harness, media/application qualification or
remote CI. A clean supply-chain matrix is therefore not release authorization.

## Prerequisites and execution

Use the R3 source on the same operator-controlled Windows x64 host that provides
CPython 3.11, 3.12 and 3.13 to the `py` launcher. Network access to PyPI and the
configured vulnerability service is required. Do not run as administrator.

From the project root:

```powershell
.\tools\WaveHelm-SupplyChainMatrix.ps1
```

The default output is a new directory below `%LOCALAPPDATA%`. The terminal prints
`results_archive`, ending in `WaveHelm-supply-chain-matrix-results.zip`. Preserve
and return that archive even when the command exits nonzero.

## Result semantics

- `MATRIX_AUDIT_PASS`: all three runtime evidence sets passed WaveHelm's validator
  and their evidence manifests verified. This is supply-chain evidence only.
- `MATRIX_AUDIT_BLOCKED`: collection completed and manifests verified, but at
  least one target has a blocking vulnerability or other validator finding.
- `INCOMPLETE`: a target, evidence command, manifest or source-integrity check did
  not complete. It must not be treated as a vulnerability verdict.

`pip-audit` exit 1 is retained as vulnerability evidence and does not prevent
SBOM/license collection. The policy currently has no vulnerability exceptions.
A reported vulnerability remains visible even if a future bounded exception is
approved. human review is still required before release integration.

The result ZIP contains per-target validated evidence plus raw command receipts
and a top-level SHA-256 manifest. Private virtual environments and the sealed
source snapshot are excluded. Local paths and package metadata can appear in the
archive, so review it before sharing outside the project.

Every summary retains `release_readiness=NOT_VERIFIED`, `abi=NOT_RUN` and
`application_tests=NOT_RUN`. These states are intentional and cannot be promoted
by a successful dependency audit.
