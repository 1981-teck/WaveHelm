# WaveHelm Windows source and release notes

## Scope

WaveHelm `1.0.3` is the **current public source release** and has a published Windows x64 installer. It derives from the public `1.0.2` baseline plus the Windows `ffprobe` no-console correction. The [1.0.3 release notes](release-notes-1.0.3.md) record the maintainer's qualification results; editing this document does not rerun or extend those results.

Keep three separate concerns explicit: installing the distributed application, building/testing the public sources, and reproducing/signing the frozen installer. Availability of a downloadable installer does not establish public installer-to-tag reproducibility or verify its signing status.

RI01/RI02/RI03 documents and `SOURCE_MANIFEST_R5.json` retain their historical scope. Do not rewrite old reports, inventories, failures, or `NOT RUN` / `NOT VERIFIED` states to describe a newer release. See [release-integration history](release-integration.md).

## Published 1.0.3 assets

Use the [versioned release](https://github.com/1981-teck/WaveHelm/releases/tag/v1.0.3), not an unlabelled development snapshot.

| Asset | Intended use |
| --- | --- |
| `WaveHelmSetup_1.0.3_win64.exe` | Windows x64 application installer |
| `wavehelm-1.0.3-py3-none-any.whl` | Python distribution; not the Windows installer and not a cross-platform support claim |
| `wavehelm-1.0.3.tar.gz` | Packaged source distribution |
| `wavehelm-1.0.3-sbom.cdx.json` | Published CycloneDX software bill of materials |
| `WaveHelm-1.0.3-SHA256SUMS.txt` | Published checksums for the listed release assets |

GitHub also provides automatic source archives. Match a downloaded file to its exact checksum entry; do not assume a checksum for one archive or package also covers another. A matching checksum is not an Authenticode verification or a reproducibility proof.

For download, checksum comparison, installation, upgrade, and removal, use the [Windows installation guide](../INSTALL_WINDOWS.md#install-the-windows-application).

## Runtime target

| Area | Maintained baseline |
| --- | --- |
| Operating system | Windows 10 or Windows 11, x64 |
| Python for source execution | Declared minimum 3.11; version-specific Windows locks and CI for 3.11, 3.12, and 3.13; 3.12 recommended |
| UI | `wxPython` |
| Audio runtime | `pygame`, `numpy`, `scipy`, `soundfile` |
| Video runtime | Media Foundation + `pywin32` + `comtypes` |
| Metadata helpers | `opencv-python`, optional `ffprobe` in `PATH` for source workflows |

The Python setup is for source execution and development, not an extra installation step for users of the frozen application. Available codecs and Windows media components affect video support. A job running on `windows-latest` is not evidence of a separate test run on both Windows 10 and Windows 11.

## Python dependencies and source setup

The root `requirements.txt` and `requirements-dev.txt` declare the runtime and development requirements. Reproduce the supported Windows environments with the matching lock bundle under `locks/windows-py311-*`, `locks/windows-py312-*`, or `locks/windows-py313-*`; select `runtime` or `dev` deliberately.

Follow [Run from source](../INSTALL_WINDOWS.md#run-from-source) and [Development, tests, and package builds](../INSTALL_WINDOWS.md#development-tests-and-package-builds). These procedures use separate environments outside the checkout, hash-checked binary dependency installation, explicit command failure checks, and external report directories. Installing only the root requirements does not reproduce the complete locked dependency graph.

## Native Dependencies

WaveHelm is not a pure-Python application. Its runtime-sensitive layer is the Windows video stack.

- `pywin32` provides Win32 integration used by the maintained Windows runtime path.
- `comtypes` provides COM interoperability for the Media Foundation stack.
- Media Foundation is the maintained native video backend.
- `ffprobe` is an optional external helper for metadata inspection and validation workflows.

## Repository assets required at runtime

Source-based execution requires these non-code assets:

- `src/config/app_info.json` for product identity, version, URLs, and general metadata;
- `ambient_sounds/` as the public-source location for user-provided ambient playback files;
- `src/resources/manual/` for embedded localized manuals;
- `src/resources/legal/` for legal notices and third-party license material;
- `src/locales/` for shipped translations.

## Source tests and package builds

Run the complete source suite and build checks before creating a new release artifact. The commands in the [development guide](../INSTALL_WINDOWS.md#development-tests-and-package-builds) produce test evidence and Python packages; a wheel/source-distribution build does not build or qualify the Windows installer.

The source archive must contain `tests/conftest.py`, `tests/test_*.py`, and deliberate static fixtures. Directories created by the tests under `tests/_*_runtime*` or `tests/_tmp_locales/` are generated output and must not be committed or included in a release.

`MANIFEST.in` already selects the root Markdown documents and the relevant documentation formats. `pyproject.toml` uses `README.md` as package metadata. Documentation changes therefore affect later source distributions and package metadata, even when runtime code and dependency locks are untouched. Preserve historical inventories and published asset checksums; generate and verify a new inventory for a new artifact instead of reusing an old digest.

## CI and supply-chain evidence

The [GitHub Actions workflow](../.github/workflows/ci.yml) configures these separate gates:

1. Tracked-source hygiene, plus Windows source tests and package builds on Python 3.11, 3.12, and 3.13. The version-specific development lock is verified and installed with `--require-hashes --only-binary=:all:`.
2. A separate runtime supply-chain job for each of those Python versions. It verifies the matching runtime lock, creates an isolated runtime environment without `pip`, installs the hashed binary dependencies, and records the install report and installed-package inventory.
3. `pip-audit --path` against that runtime environment's installed packages, including transitive dependencies, with strict failure handling. Audit/SBOM tooling runs outside the audited runtime environment.
4. A CycloneDX 1.6 SBOM generated from the installed runtime environment with reproducible-output options, plus license inventory and supply-chain evidence validation. Reproducible SBOM output is not a claim of reproducible frozen binaries.
5. Windows SDK/`ctypes` ABI-layout comparison and archived test, build, supply-chain, and ABI evidence.

Read each job's actual result and artifact scope; workflow configuration alone is not a `PASS`. The current workflow uses 14-day retention for test/build artifacts and 30 days for the other evidence groups. Preserve the selected release evidence beyond those retention windows when preparing a release record.

The workflow is triggered by push, pull request, or manual dispatch. Scheduled dependency rescanning and longer-term evidence archival remain separate improvements; this document does not imply that either is already enabled.

## Recorded 1.0.3 qualification and remaining limits

The [published release notes](release-notes-1.0.3.md) report focused regressions, Windows CI, dependency audit/SBOM, ABI checks, frozen-GUI identity, manual playback smoke tests, an in-place `1.0.2` to `1.0.3` upgrade, installed-tree comparison, and uninstall checks. These are attributed release results, not new tests performed by updating this documentation.

The [WIC application baseline](wic-application-baseline.md) records a bounded owner-observed scenario and its remaining work. Do not turn a finite playback/resource run into a general zero-leak, all-codec, all-driver, or all-Windows-version guarantee. Keep the [roadmap](../ROADMAP.md) aligned with those limits.

## Validation checklist for a new Windows release

1. Startup in a clean, supported Windows environment with the chosen interpreter/lock or frozen application identity recorded.
2. Complete source suite, hygiene gates, and wheel/source-distribution builds.
3. Current installed-runtime dependency audit, SBOM, license inventory, and applicable ABI/layout checks.
4. Audio playback, DSP, equalizer, effects, ambient playback, library, playlists, favorites, and visualizer rendering.
5. External/fullscreen video, seek in play and pause, track changes, repeated open/close, and relevant failure scenarios.
6. Legal notices and runtime resolution of metadata, locales, manuals, legal resources, and ambient assets.
7. Installer identity, the specific supported upgrade path, installed-tree integrity, uninstall behavior, and signing status where claimed.
8. Archive free of `.git`, virtual environments, caches, generated databases, logs, temporary test directories, credentials, and signing material.
9. New artifact hashes/inventory verified against that exact artifact; retained historical evidence left unchanged.
10. Missing checks recorded as `NOT RUN` / `NOT VERIFIED`, with their impact. Do not publish a completeness claim for an unqualified scope.

## Store listing references for maintainers

The public homepage is user-facing. When maintaining a Store listing, use these existing project pages for the corresponding fields; this URL mapping does not assert that a Store submission or listing has been approved.

| Listing field | Project URL |
| --- | --- |
| Website | <https://1981-teck.github.io/WaveHelm/> |
| Privacy policy | <https://1981-teck.github.io/WaveHelm/privacy-policy.html> |
| Applicable license terms | <https://1981-teck.github.io/WaveHelm/license-terms.html> |
| Support | <https://1981-teck.github.io/WaveHelm/support.html> |

## Deferred release engineering work

- Public, repeatable frozen-executable build recipes.
- Public installer automation, distinct from an already distributed installer.
- Documented code-signing and signed-release automation; verify the actual signature before making a signing claim.
- Verified installer-to-tag reproducibility with toolchain, native inputs, build steps, and evidence recorded.
- Durable release-evidence archival and scheduled scanning of the locked dependency graph.
