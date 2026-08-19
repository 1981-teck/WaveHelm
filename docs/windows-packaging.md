# WaveHelm Windows source and release notes

## Scope

WaveHelm 1.0.1 is maintained as a **source-first Windows project**. The repository includes application source, runtime assets, legal material, the complete test suite, public CI, and package-build metadata. A source release must exclude generated test/runtime output while retaining every test source file required for continued development.

Prebuilt installer recipes and signing automation remain outside this source baseline.

## Runtime target

| Area | Maintained baseline |
| --- | --- |
| Operating system | Windows 10 or Windows 11 |
| Python | 3.11 or newer; 3.12 recommended |
| UI | `wxPython` |
| Audio runtime | `pygame`, `numpy`, `scipy`, `soundfile` |
| Video runtime | Media Foundation + `pywin32` + `comtypes` |
| Metadata helpers | `opencv-python`, optional `ffprobe` in `PATH` |

## Python dependencies

Use the pinned files from the repository root:

- `requirements.txt` for the application runtime;
- `requirements-dev.txt` for tests, package builds, dependency auditing, and CycloneDX SBOM generation.

The declared Python minimum is 3.11 because the pinned NumPy and SciPy releases require Python 3.11 or newer.

## Native Dependencies

WaveHelm is not a pure-Python application. Its runtime-sensitive layer is the Windows video stack.

- `pywin32` provides Win32 integration used by the maintained Windows runtime path.
- `comtypes` provides COM interoperability for the Media Foundation stack.
- Media Foundation is the maintained native video backend.
- `ffprobe` is an optional external helper for metadata inspection and validation workflows.

## Repository assets required at runtime

Source-based execution requires these non-code assets:

- `src/config/app_info.json` for product identity, version, URLs, and general metadata;
- `ambient_sounds/` as the public-source location for user-provided ambient playback files;
- `src/resources/manual/` for embedded localized manuals;
- `src/resources/legal/` for legal notices and third-party license material;
- `src/locales/` for shipped translations.

## Source setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
python main.py
```

## Test and package build

Run the complete source suite before creating a release artifact:

```powershell
python -m pytest -q tests
python -m build
```

The source archive must contain `tests/conftest.py`, `tests/test_*.py`, and deliberate static fixtures. Directories created by the tests under `tests/_*_runtime*` or `tests/_tmp_locales/` are generated output and must not be committed or included in a release.

## Supply-chain evidence

The GitHub Actions workflow performs:

- a tracked-source hygiene gate;
- the Windows test and build matrix on Python 3.11, 3.12, and 3.13;
- a direct dependency audit using `pip-audit`;
- a reproducible CycloneDX 1.6 SBOM from the pinned requirements;
- artifact upload for package builds and supply-chain evidence.

A release should be cut only from a clean tagged commit after these jobs complete successfully.

## Validation checklist for Windows source runs

1. startup in a clean Windows virtual environment;
2. complete source test suite;
3. wheel and source-distribution build;
4. audio playback, DSP, equalizer, effects, and ambient playback;
5. library, playlist, and favorites flows;
6. visualizer rendering;
7. external and fullscreen video playback;
8. legal notices accessible from the runtime;
9. runtime resolution of metadata, locales, manuals, legal resources, and ambient assets;
10. release archive free of `.git`, caches, generated databases, logs, and test runtime directories.

## Deferred release engineering work

- frozen executable recipes;
- installer automation;
- code-signing and signed-release automation;
- verified installer-to-tag reproducibility.
