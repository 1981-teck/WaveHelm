# WaveHelm third-party license audit

## Runtime stack baseline

- wxPython — wxWindows Library Licence 3.1
- pygame — LGPL-2.1
- numpy — permissive mixed SPDX set on current metadata
- scipy — BSD-family
- soundfile + libsndfile — BSD wrapper + LGPL dependency
- tinytag==2.3.2 — MIT; exact selected wheel LICENSE bundled
- Pillow==13.0.0 — MIT-CMU; exact selected Windows CPython 3.12 wheel LICENSE and bundled notices included
- python-dateutil==4.0.0 — BSD-3-Clause and Apache-2.0 notices; exact selected wheel AUTHORS and LICENSE bundled
- tzdata==2026.6 — Apache-2.0; exact selected wheel notice and full license bundled
- opencv-python — Apache-2.0 with bundled notices
- matplotlib — PSF-based
- pywin32 — PSF
- comtypes — MIT

**Current conclusion:** no current GPL blocker is present in the active runtime stack for a GPL-3.0-or-later source distribution, provided the dependency set remains aligned with this audit and the notice bundle under `src/resources/legal/` stays included in packaging when redistributing binaries or packaged releases.

## Release checklist

1. Freeze the exact Windows wheel set used for release.
2. Archive upstream license/notice payloads from those exact wheels.
   The selected Pillow 13.0.0, TinyTag 2.3.2, python-dateutil 4.0.0, and tzdata 2026.6 payloads are already
   retained under `src/resources/legal/licenses/` with wheel and file hashes
   in `src/resources/legal/WHEEL_LICENSE_PROVENANCE.json`.
   The Windows packaging recipe also carries every LICENSE, NOTICE, COPYING,
   LICENCE and AUTHORS file discovered in the hash-locked runtime wheels into
   the installed legal bundle, with SHA-256 values recorded in build evidence.
3. Validate that the installed application exposes the notice bundle to end users.
4. Re-run the runtime dependency audit before release packaging.
5. Reconfirm that no current GPL blocker has entered the shipped dependency set.
