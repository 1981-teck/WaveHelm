# Changelog

All notable source-release changes are documented in this file.

## [1.0.1] - 2026-08-18

### Corrected

- Aligned the project, runtime metadata, embedded manuals, and documentation on version `1.0.1`.
- Raised the declared Python minimum from 3.10 to 3.11 to match the pinned NumPy and SciPy requirements.
- Anchored repository-root runtime ignore rules so bundled legal license texts are no longer excluded.
- Restored and retained the complete third-party license-text directory required by the runtime and tests.
- Removed generated test databases, logs, caches, media stubs, and runtime directories from the release source tree while retaining all test source files.
- Removed the stale embedded Git repository from the distributable source archive.
- Kept README screenshot references on the existing `docs/screenshots/` assets and rejected unresolved `docs/images/` references.
- Corrected Markdown escaping in Windows paths and repository filenames.

### Added

- Pinned development, build, dependency-audit, and CycloneDX tooling in `requirements-dev.txt`.
- GitHub Actions jobs for source hygiene, the full Windows/Python test matrix, package builds, dependency auditing, and SBOM generation.
- Source-distribution inclusion rules for tests, GitHub Pages assets, legal resources, development requirements, changelog, roadmap, and security policy.
- Release-oriented Windows packaging documentation and a public security-reporting policy.

### Scope note

This release corrects source packaging, metadata, licensing, CI, and release hygiene. Runtime hardening and architectural improvements identified during the technical review are tracked in `ROADMAP.md` and are intentionally deferred to subsequent change-sets.

## [1.0.0] - 2026-04-08

- Initial public source release and GitHub Pages baseline.
