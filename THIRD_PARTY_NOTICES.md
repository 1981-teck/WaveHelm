# WaveHelm third-party notices

This file is a **baseline third-party notices bundle** for the WaveHelm runtime
stack declared in `requirements.txt`.

Important limitation:
This file is not a perfect per-wheel legal manifest. Before the final Microsoft Store release, freeze the exact Windows wheel versions used by the build and
archive the original upstream license / notice files from those exact artifacts.

## Current declared runtime dependencies

- wxPython
- pygame
- numpy
- scipy
- soundfile
- tinytag==2.2.1
- Pillow
- opencv-python
- matplotlib
- pywin32
- comtypes

## Package notice summary

### UI-stack notices that must remain in the maintained runtime bundle

- **wxPython** — distributed under the wxWindows Library Licence. Keep the wxPython/wxWidgets notice set in the package while the maintained wx runtime is shipped.

### Copyleft or bundled-component cases that need attention in the final release bundle

- **pygame** — LGPL-2.1. Include the license text in the redistributed package.
- **soundfile** — the Python wrapper is permissive, but it relies on `libsndfile`, which is LGPL. Keep both wrapper and `libsndfile` notices in the final package.
- **opencv-python** — packaging scripts are permissive, OpenCV itself is Apache-2.0 on current PyPI metadata, and wheels ship FFmpeg under LGPL-2.1. Keep the exact third-party wheel notices in the final package.

### Permissive / low-friction cases

- **tinytag** — current PyPI project metadata is MIT. This project now pins `tinytag==2.2.1` to stay on the permissive line.
- **comtypes** — MIT.
- **Pillow** — MIT-CMU.
- **matplotlib** — PSF-based license.
- **pywin32** — PSF.
- **scipy** — BSD-family license, plus bundled notices for exact wheels.
- **numpy** — current PyPI metadata reports a mixed permissive SPDX expression; include the exact wheel notices selected for release.

## Files included in this baseline bundle

- `src/resources/legal/NOTICE_INDEX.json`
- `src/resources/legal/README.md`
- `src/resources/legal/licenses/Apache-2.0.txt`
- `src/resources/legal/licenses/BSD-3-Clause.txt`
- `src/resources/legal/licenses/LGPL-2.1.txt`
- `src/resources/legal/licenses/MIT.txt`
- `src/resources/legal/licenses/MIT-CMU.txt`
- `src/resources/legal/licenses/PSF-2.0.txt`
- `src/resources/legal/licenses/wxWindows-Library-Licence-3.1.txt`

## Final Store-build checklist

1. Freeze exact Windows wheel versions.
2. Capture original upstream notice/license files from the exact wheel or binary artifacts shipped.
3. Keep this baseline bundle plus any package-specific upstream notices required by the selected versions.
4. Ensure the final Store package or installed app bundle exposes these notices somewhere accessible.
5. Validate the notice bundle against the maintained wx-only runtime before release.
