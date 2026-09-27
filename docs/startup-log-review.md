# Startup log review — R5I-step07G2

## Scope and status

This checkpoint addresses the two startup warnings in the operator-supplied
step07G Windows log. It is not a cleanup tranche and does not change audio/DSP,
COM calls, ABI structures, dependency pins or the six Windows lock bundles.

- `LOG-01`: duplicate `ctypes` provider in the compatibility facade — source fix
  applied, with local before/after regression evidence.
- `LOG-02`: pygame's optional `pkg_resources` import — external environment issue;
  root cause confirmed, isolated remediation documented, **not executed or closed
  on the operator's computer**. Replacing only this source will not remove the
  third-party warning from an unchanged global Python environment.

The operator's step07G smoke is **MANUAL_WINDOWS_SMOKE_POSITIVE** for documented
startup, video, audio navigation, DSP refresh and orderly shutdown. It is not a
hash-bound comparison, a complete test suite, or Windows qualification of step07G2.
Do not replace this observation with a generic claim that Windows was never run.

## LOG-01: canonical facade provider

`definitions` exposes the standard-library `ctypes` module. `com_helpers` exposes
its own `_CtypesProxy`, whose `WinDLL` patch point is local to that module. The
facade formerly exported the first object and then overwrote it with the second,
producing the reported warning. On reload it also performed the reverse temporary
replacement before overwriting again.

The fix omits only the intermediate `definitions.ctypes` facade binding. The
historical final value remains `com_helpers.ctypes`, including its patch isolation.
The `__all__` union and legacy `ctypes` export are unchanged. `_export` is unchanged:
unexpected conflicts, including an unrelated `ctypes` provider, still warn. Missing
attributes and unexpected resolver errors retain the existing tested semantics.
No warning filter, COM initialization, new layer or dependency is introduced.

The new tests cover fresh import, reload, the complete export-name roster, required
object identities, proxy delegation and patch isolation, genuine collision warning,
idempotence and logger ownership. Their native calls are not executed.

## LOG-02: external pygame/setuptools interaction

The supplied log identifies `pygame/pkgdata.py` importing `resource_stream` and
`resource_exists` from `pkg_resources`. It does not report the installed setuptools
version. Do not infer an exact version from that warning or claim the user's
interpreter was inventoried remotely.

The upstream pygame 2.6.1 source treats `pkg_resources` as optional: without it,
`getResource` resolves package files through the package's `__file__`. In a normal
unpacked wheel installation this avoids the deprecated API; frozen/zip importers
need their own qualification. Python 3.12 no longer seeds setuptools into a fresh
venv. WaveHelm's runtime lock does not include setuptools; the existing development
lock pins setuptools 82.0.1. Setuptools removed `pkg_resources` in 82.0.0.

Therefore the conservative remediation is a separate, correctly isolated Python
3.12 x64 runtime using the **existing hashed runtime lock**, followed by a real
import/resource test. Instructions are in `INSTALL_WINDOWS.md`. Do not uninstall
or downgrade the user's global setuptools, alter pygame in site-packages, add a
synthetic `pkg_resources` module, or hide warnings to make a result appear clean.
Do not install development packages into this diagnostic runtime.

The delivery environment still lacks pygame and cannot acquire its official
wheel. The remedy is **DOCUMENTED / NOT_RUN**, not a successful installation or
proof of disappearance of the warning. No automatic application launch, package
installation, virtualenv replacement or user-data change is performed here.

## Other log observations

The pygame community greeting is informational. The missing custom-theme message
is followed by successful theme initialization; it is not an error in this log.
The recorded audio/video starts, four DSP refreshes and clean shutdown are positive
smoke evidence, not grounds to infer coverage of every media format or device.
Initial volume zero, subtitles, seek outcomes, accessibility and GPU/native gates
remain separate; this log does not establish their closure.

## Continuation

First verify this facade patch on the integrated Windows candidate and complete
LOG-02's isolated dependency/resource check. Keep the existing step07G automated
paired-audio gate visible; the manual smoke does not fabricate its missing results.
Then resume step07H's three local import reviews (ProgressSnapshot,
finalize_video_end and WX_CALLBACK_EXCEPTIONS). All other cleanup and release debt
remains in `docs/cleanup-progress.md` and the delivery's external ledger.

## Primary references consulted on 21 September 2026

- pygame 2.6.1 resource loader:
  https://raw.githubusercontent.com/pygame/pygame/2.6.1/src_py/pkgdata.py
- Upstream report of the same warning:
  https://github.com/pygame/pygame/issues/4557
- Proposed upstream replacement (a proposal is not an installed fix):
  https://github.com/pygame/pygame/pull/4583
- Setuptools 82.0.0 removal:
  https://setuptools.pypa.io/en/latest/history.html#v82-0-0
- Python 3.12 venv isolation and setuptools seeding change:
  https://docs.python.org/3.12/library/venv.html

Current dependency/advisory qualification remains a release gate. A current
upstream changelog or unchanged lock bytes are not a vulnerability scan.
