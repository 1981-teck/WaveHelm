# WaveHelm Windows runtime notes

## Scope

This repository is maintained as a **source-first Windows project**.
It documents the runtime and setup expectations for local development and source-based execution.
Prebuilt executables, installer recipes, and signed release automation are intentionally out of scope for the current repository baseline.

## Runtime target

| Area | Current baseline |
| --- | --- |
| Operating system | Windows |
| UI | `wxPython` |
| Audio runtime | `pygame`, `numpy`, `scipy`, `soundfile` |
| Video runtime | Media Foundation + `pywin32` + `comtypes` |
| Metadata helpers | `opencv-python`, optional `ffprobe` in `PATH` |

## Python dependencies

The repository currently expects these Python packages for the maintained Windows runtime:

- `wxPython`
- `pygame`
- `numpy`
- `scipy`
- `soundfile`
- `tinytag==2.2.1`
- `Pillow`
- `opencv-python`
- `matplotlib`
- `pywin32`
- `comtypes`

Use the pinned `requirements.txt` from the repository root when preparing a local environment.

## Native Dependencies

## Native and platform dependencies

WaveHelm remains a Windows-targeted desktop application. The runtime-sensitive layer is the video stack.

- `pywin32` is required for Win32 integration used by the Windows runtime path.
- `comtypes` is required for COM interop used by the Media Foundation stack.
- Media Foundation is the maintained native video backend.
- `ffprobe` is an optional external helper for metadata inspection and validation workflows.

## Repository assets required at runtime

Source-based execution requires these non-code assets to remain present in the repository checkout:

- `src/config/app_info.json` for product identity, version, URLs, and general metadata.
- `ambient_sounds/` as a public-source placeholder for user-provided ambient playback files.
- `src/resources/manual/` for the embedded user manual pages.
- `src/resources/legal/` for bundled legal notices and third-party license material.
- `src/locales/` for shipped translations.

## Source setup expectations

1. Create a Windows virtual environment.
2. Install the pinned dependencies from `requirements.txt`.
3. Ensure Media Foundation is available on the target Windows system.
4. Optionally add `ffprobe` to `PATH` if metadata fallback probing is desired.
5. Run the application from the repository root so runtime assets resolve correctly.

## Validation checklist for source-based runs

1. startup on a clean Windows machine or virtual environment
2. audio playback, DSP/equalizer/effects, ambient playback
3. library / playlist / favorites flows
4. visualizer rendering
5. external / fullscreen video playback
6. legal notices accessible from the runtime
7. runtime still resolves `app_info.json`, locales, manual resources, legal resources, and `ambient_sounds`

## Deferred release engineering work

The following items are intentionally not maintained in the current source-first repository baseline:

- frozen executable recipes
- installer automation
- signed release automation
