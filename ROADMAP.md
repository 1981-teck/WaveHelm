# WaveHelm engineering roadmap

This roadmap describes the next engineering work after the 1.0.1 source-release correction. It is not a promise of dates or release scope.

## Current baseline: 1.0.1

WaveHelm 1.0.1 is the corrected, development-ready source baseline. It retains the complete test suite, restores required third-party license texts, aligns the Python compatibility contract with pinned dependencies, adds public CI and supply-chain checks, and removes generated runtime artifacts from release archives.

## Priority 0 — settings and maintenance safety

- Constrain cleanup operations to WaveHelm-owned application-data directories.
- Canonicalize and validate every cleanup path before deletion.
- Reject filesystem roots, user-profile roots, project roots, symlink or junction escapes, and unmanaged directories.
- Make settings writes atomic and report persistence failures to the caller and UI.
- Make imported settings transactional and schema-constrained.
- Add regression tests for external paths, traversal, reparse points, missing permissions, partial writes, and malformed imports.

## Priority 1 — Windows runtime assurance

- Execute the CI and integration matrix on Windows 10 and Windows 11.
- Cover Media Foundation, COM, DXGI, playback lifecycle, track selection, seek, device reset, corrupted media, unsupported codecs, and repeated open/close cycles.
- Add leak and resource-lifecycle checks for COM and Media Foundation boundaries.
- Document supported codecs and environment-dependent behavior from measured evidence.

## Priority 2 — bounded I/O and error contracts

- Centralize `ffprobe` execution behind a typed service with a bounded timeout and output limits.
- Add deterministic cancellation and typed errors for subprocess failures.
- Bound subtitle file size and validate encoding before parsing.
- Prevent partially valid domain objects after failed media validation.
- Complete Windows filename sanitization for reserved names and normalization collisions.

## Priority 3 — architecture and type safety

- Reduce oversized modules only where a cohesive, independently testable responsibility can be isolated.
- Replace internal dynamic dictionaries and `Any`-based domain state with dataclasses, enums, protocols, and explicit DTOs.
- Add static type checking and lint gates without weakening existing runtime contracts.
- Separate UI presentation from controller and persistence logic in the largest wxPython modules.

## Priority 4 — localization, accessibility, and public presentation

- Make English the canonical localization schema and enforce key parity in CI.
- Complete Spanish and French translations and validate placeholders and plural forms.
- Add keyboard-focus, reduced-motion, screen-reader, and automated accessibility checks to GitHub Pages and the desktop UI where applicable.
- Optimize large screenshots and formalize site performance budgets.

## Release discipline

Each subsequent release should be created from a clean tagged commit, retain the source tests, exclude generated runtime output, publish hashes and build evidence, and archive the CI dependency-audit and SBOM artifacts.
