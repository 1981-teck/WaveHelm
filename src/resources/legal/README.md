# WaveHelm third-party notices bundle

This directory is a **baseline compliance bundle** for the current WaveHelm
runtime dependency set.

What is included here:
- a project-level GPL section 7 additional terms notice
- a `THIRD_PARTY_NOTICES.md` summary for the project
- a machine-readable `NOTICE_INDEX.json`
- `WHEEL_LICENSE_PROVENANCE.json` with hashes for selected Pillow 13.0.0,
  TinyTag 2.3.2, python-dateutil 4.0.0, and tzdata 2026.6 wheel notices
- baseline license texts for the main license families currently used by the
  declared runtime stack
- the exact license and author files from those four selected wheels, including
  package-specific copyright and bundled third-party notices

The frozen Windows build also adds all license, notice, and author files from
the exact hash-locked runtime wheels under `licenses/wheels/`. WaveHelm copies
this directory to the user's `licenses/wheels/` folder on launch so the files
remain accessible without development tools.

Important limitation:
This is not a substitute for capturing the original upstream license files from
**the exact wheel / binary artifacts used for the final Microsoft Store build**.
Packages such as NumPy, SciPy, wxPython/wxWidgets, soundfile/libsndfile and
opencv-python can carry additional bundled notices or third-party components in
specific releases.

Before the final Store package is produced, freeze exact versions and archive
upstream notice/license files from the exact artifacts that are actually
bundled. Validate the baseline notice bundle against the maintained wx-only
runtime before release.


The file `GPL_SECTION7_ADDITIONAL_TERMS.md` preserves the project's additional attribution / marking / trademark-positioning terms in a runtime-accessible location.
