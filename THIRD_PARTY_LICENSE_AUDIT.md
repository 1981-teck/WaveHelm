# WaveHelm third-party license audit

## Runtime stack baseline

- wxPython — wxWindows Library Licence 3.1
- pygame — LGPL-2.1
- numpy — permissive mixed SPDX set on current metadata
- scipy — BSD-family
- soundfile + libsndfile — BSD wrapper + LGPL dependency
- tinytag==2.2.1 — MIT
- Pillow — MIT-CMU
- opencv-python — Apache-2.0 with bundled notices
- matplotlib — PSF-based
- pywin32 — PSF
- comtypes — MIT

**Current conclusion:** no current GPL blocker is present in the active runtime stack for a GPL-3.0-or-later source distribution, provided the dependency set remains aligned with this audit and the notice bundle under `src/resources/legal/` stays included in packaging when redistributing binaries or packaged releases.

## Release checklist

1. Freeze the exact Windows wheel set used for release.
2. Archive upstream license/notice payloads from those exact wheels.
3. Validate that the installed application exposes the notice bundle to end users.
4. Re-run the runtime dependency audit before release packaging.
5. Reconfirm that no current GPL blocker has entered the shipped dependency set.
