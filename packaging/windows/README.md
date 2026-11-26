# WaveHelm 1.0.4 Windows build sources

These are the project-specific files used to create the frozen WaveHelm
executable and the Windows installer. They accompany the application source,
dependency locks, license material, tests, and documentation in the tagged
repository and source archive. The build environment, downloaded tools and
wheels, generated binaries, logs, private test evidence, and user data are not
part of this source directory.

## Inputs

| File | Purpose |
| --- | --- |
| `Build-WaveHelm-1.0.4-Candidate.ps1` | Verifies the exact source and locked runtime, freezes the app, checks legal files, and compiles the installer. |
| `WaveHelm-final.spec` | Selects the PyInstaller entry point, resources, runtime wheel notices, hidden imports, icon, and GUI subsystem. |
| `WaveHelm-1.0.4-candidate.iss.template` | Defines the Inno Setup identity, upgrade and uninstall behavior, files, and shortcuts. |
| `WaveHelm-version-info-1.0.4.txt` | Embeds Windows executable version information. |
| `WaveHelm.ico` | Supplies the Windows executable and installer icon. |
| `installed/README.md` | Supplies the README placed in the installed program. |

The script also reads `requirements.txt`, `locks/windows-py312-runtime`,
`src/resources/legal`, `LICENSE`, `GPL_SECTION7_ADDITIONAL_TERMS.md`, and the
public `tools/resolve_windows_lock.py` and `tools/supply_chain.py` from the
source checkout.

## Build

Use a **clean Windows x64 checkout** at the reviewed 1.0.4 release commit,
CPython 3.12 x64, Git, and Inno Setup 7.1.x. PyPI access is required to fetch
the hash-checked wheels and pinned build tools. Run PowerShell in this directory:

```powershell
.\Build-WaveHelm-1.0.4-Candidate.ps1 `
    -SourceRoot 'C:\path\to\WaveHelm' `
    -ExpectedCommit '<40-character reviewed release commit SHA>' `
    -PythonExe 'C:\path\to\Python312\python.exe' `
    -InnoCompiler 'C:\path\to\Inno Setup 7\ISCC.exe' `
    -OutputRoot 'C:\path\outside\WaveHelm\WaveHelm-1.0.4-build'
```

Replace each example path and commit SHA with the actual values. `OutputRoot`
must not already exist and must be outside the source checkout. If omitted, the
script creates a new timestamped output directory beside the checkout. It
refuses a dirty tree, a different commit, incompatible tools, or an output
directory inside the checkout.

The build compares the installed runtime to the Windows 3.12 lock, audits its
dependencies, creates an SBOM and license inventory, verifies the selected
upstream license payloads, and includes every discovered runtime wheel notice
in the frozen legal resources. It then validates the frozen GUI tree and writes
an unsigned candidate installer plus SHA-256 manifests and evidence under
`OutputRoot`.

The script's success establishes a build candidate, not release qualification.
The [versioned release](https://github.com/1981-teck/WaveHelm/releases/tag/v1.0.4)
records the final installer checksum and manual Windows checks. The public
sources and tool versions enable another build; bit-for-bit reproducibility
and code signing are not claimed.
