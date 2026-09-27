# WaveHelm supply-chain evidence

## Scope

WaveHelm treats the Windows dependency graph selected from `requirements.txt` as the release-relevant runtime graph. The source tree pins every direct runtime dependency and pins `pip` and the two audit tools used by CI.

The CI supply-chain job creates a fresh Windows virtual environment and archives evidence for the graph that was actually installed. A direct-dependency SBOM generated without installation is useful for offline review, but it is not a substitute for the transitive Windows SBOM or the vulnerability scan.

## Required evidence

The authoritative CI artifact contains:

- `pip-bootstrap-exit-code.txt`, audit-tool installation report and exit codes;
- `runtime-install-report.json` and its exit code: pip installation report, including artifact URLs and SHA-256 hashes;
- `runtime-freeze.txt` and its exit code: exact installed runtime graph;
- `runtime-pip-check.txt` and its exit code;
- `pip-audit.json` and its exit code;
- `sbom.cdx.json` and the CycloneDX command exit code;
- `runtime-license-inventory.json` and its exit code;
- `supply-chain-tool-versions.json`;
- `supply-chain-status.json`;
- `supply-chain-evidence-manifest.json`;
- `supply-chain-manifest-verify.json`, stored beside the manifested evidence directory.

The validator binds the freeze, installation report, audit output, SBOM and license inventory to the same package names and versions. Downloaded artifacts require HTTPS URLs and SHA-256 hashes. Any unexcepted vulnerability is blocking.

## Vulnerability exceptions

The default policy contains no vulnerability exceptions. A temporary exception requires all of the following before it can be accepted:

1. exact normalized package name;
2. exact installed package version;
3. exact vulnerability identifier;
4. timezone-qualified expiration timestamp;
5. documented technical containment and rationale;
6. review in the same change-set as the policy modification.

Expired or malformed exceptions are ignored by the validator. An exception does not remove the finding from the upstream scanner; it only records a bounded, reviewable release decision.

## Reproduction

On a Windows checkout with the pinned tools installed:

```powershell
python tools/supply_chain.py validate --root . --evidence-dir supply-chain-evidence
python tools/supply_chain.py verify --root . --evidence-dir supply-chain-evidence
```

For an offline declaration of direct dependencies only:

```powershell
python tools/supply_chain.py direct-sbom --root . --output declared-direct-sbom.cdx.json
```

The latter output is explicitly scoped to declared direct dependencies. It must never be labelled as the installed transitive release SBOM.

## Reproducibility boundary

The checked-in Windows runtime/development bundles pin the selected wheel for every resolved package with SHA-256 and are target-specific for CPython 3.11, 3.12 and 3.13 x64. They were generated and installed on Windows and mechanically matched to the installed graphs on 2026-09-14. Direct requirements and tooling remain exact-version pins.

These locks improve future byte selection reproducibility, but they do not prove that an artifact is vulnerability-free, correctly licensed or suitable for release. The fresh installed-graph audit, CycloneDX SBOM, license inventory and human release review remain blocking evidence gates.
