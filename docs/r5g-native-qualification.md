# R5G native qualification

Keep the previously accepted R5F evidence. R5G changes the timed-text service and
selection control path; its Windows test/build result must be acquired separately.
The six dependency locks, requirements, build backend and eight previously sealed
ABI files remain unchanged. No identical lock matrix or layout rerun is requested.

Use the restored CPython 3.12 development interpreter with the R5G source extracted
to a separate directory. Run Prepare only; no Commit, Publish or application upgrade.
The external WaveHelm_Run_R5G_Tests.ps1 collector uses the same interpreter path as
the accepted R5F run and sets WAVEHELM_NATIVE_EVIDENCE_DIR outside the source.

Prepare runs the complete tests directory without ignore/-k/-m filtering and builds
wheel/sdist. The new native pytest case launches the actual Media Engine control
probe in a separate process. It does not open a user video/audio file or a URL.
A missing service or a failed operation remains a native failure and must be returned
with the logs, not bypassed with a skip or by substituting a simulated backend.

Return the collector ZIP on either success or failure. It includes the preflight,
JUnit, logs, artifacts and separately retained native probe directory. Do not remove
the restored Python environment or modify Python/Visual Studio registration for this
step. Preserve errors from before ZIP creation as terminal output.

A successful probe would establish scoped real control/getter behavior, not cue
rendering, external subtitles, device playback, general COM lifetime, Pester, full
static/type/security qualification, coverage ratchets or remote CI/publication.
See timed-text-backend.md for the exact ownership, error and nontransactional limits.
