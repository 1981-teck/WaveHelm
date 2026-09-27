# WaveHelm 1.0.2 — draft release notes

**Draft for RI03. Do not announce this as a published or fully qualified release.**

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

## Before publication

Insert the exact final commit, artifact hashes and Windows/CI/audit results only
after they have actually passed and have been reviewed. Do not use a zero-vulnerability
claim from a prior release for this candidate. The source remains Windows-first
and requires its declared native dependencies; this is not an executable installer.
