# WaveHelm third-party notices bundle

This directory is a **baseline compliance bundle** for the current WaveHelm
runtime dependency set.

What is included here:
- a project-level GPL section 7 additional terms notice
- a `THIRD_PARTY_NOTICES.md` summary for the project
- a machine-readable `NOTICE_INDEX.json`
- baseline license texts for the main license families currently used by the
  declared runtime stack

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
