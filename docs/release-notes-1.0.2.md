# WaveHelm 1.0.2

WaveHelm 1.0.2 focuses on reliability and verification rather than a new feature set.

## Playback and video

- Correct stale seek ownership after a video source change so the old seek does
  not block progress presentation for the new video.
- Keep the requested cursor position visible until a qualified observation takes
  over, avoiding the target/old-position/target jump on both progress surfaces.
- Retain the WIC startup and buffered-paint corrections for black/flickering
  video, with WIC selected as the default Windows rendering path.
- Retain focus handling, close/reopen, paused seek and progress-clock corrections.

## Audio and application maintenance

- Retain the Ambient stereo/multichannel shape and mixer-format correction.
- Retain volume initialization/error recovery, missing logger/fallback bindings
  and the GB-to-TB conversion correction from the development cycle.
- Remove verified unused helpers/imports conservatively and keep compatibility
  providers that are still used.
- Equalizer/effects are existing features, not additional 08AW/08AX changes.

## Release integration

- Align package, runtime/About, four localized manuals and supply-chain identity.
- Restore public README presentation, the short demo and community documents.
- Ship contribution, security, issue and PR guidance in the source distribution.
- Preserve historical failures and distinguish component evidence from native
  Windows acceptance.
- Preserve Windows lockfile bytes across Git checkouts and keep the CI coverage
  output bound to the runner execution context.

## Qualification

The qualified Windows CI matrix completed successfully on CPython 3.11, 3.12
and 3.13. On each interpreter, the complete suite reported 4,765 passed,
16 expected skips, 0 failures and 0 errors. Source hygiene, wheel/sdist build,
runtime-lock verification, installed dependency graph validation, pip check,
vulnerability audit, CycloneDX SBOM generation, license validation and Windows
SDK ABI/layout verification all passed. The qualified audit reported zero known
vulnerabilities in the resolved runtime graph.

The final publication preparation after runtime qualification changes release-state
documentation, its version-consistency assertions, and CI checkout/release
infrastructure only; it does not change application runtime logic, dependency
versions or lock contents. WaveHelm remains Windows-first and requires its declared
native dependencies; this source release is not an executable installer.
