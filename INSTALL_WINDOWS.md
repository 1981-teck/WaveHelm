# Install WaveHelm on Windows from source

This repository is maintained as a **Windows source-first public project**.

## Supported environment

- Windows 10 or Windows 11
- Python 3.11 or newer
- Recommended Python runtime: 3.12
- PowerShell or Command Prompt

## Runtime notes

WaveHelm depends on Windows-specific APIs for its current video path.

The application expects:

- Windows COM support
- Windows Media Foundation availability
- Python dependencies installed from `requirements.txt`

The repository can be browsed on other operating systems, but the maintained runtime target is **Windows only**. Public screenshots or demo material may show the product, but source execution is expected on Windows.

## Setup steps

### 1. Create a virtual environment

```powershell
python -m venv .venv
```

### 2. Activate the virtual environment

PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Command Prompt:

```cmd
.venv\Scripts\activate.bat
```

### 3. Upgrade pip

```powershell
python -m pip install --upgrade pip
```

### 4. Install project dependencies

```powershell
python -m pip install -r requirements.txt
```

### 5. Install development and test tooling (for contributors)

```powershell
python -m pip install -r requirements-dev.txt
```

### 6. Start WaveHelm

```powershell
python main.py
```

The optional `--ui-backend wx` flag is still accepted for compatibility, but wx is the only maintained UI runtime.

## Running tests

The source release includes the complete test suite. Generated test runtime directories are intentionally excluded from version control and release archives.

```powershell
python -m pytest -q tests
```

## Building source and wheel artifacts

```powershell
python -m build
```

## Troubleshooting

### Video playback does not start

Check the Windows runtime first:

- confirm you are running on Windows
- confirm Media Foundation is available
- confirm the media file uses codecs supported by the local Windows installation

### Video opens without audio

The current build works best with formats the local Windows installation can decode through Media Foundation.
AAC remains the most reliable choice for broad compatibility in this repository baseline.

### Metadata duration looks incomplete

`ffprobe` is optional, but when it is available in `PATH` it can improve some duration and metadata probing paths.
The application still runs without it.

### The repository installs but the UI does not behave as expected

Run the test suite first and then verify the local environment:

- Python version
- active virtual environment
- wxPython installation
- Windows-only runtime assumptions

### pygame reports `pkg_resources is deprecated as an API`

This warning originates in installed pygame 2.6.1, not in WaveHelm's DSP code.
Replacing the WaveHelm source alone does not repair an existing global Python
package environment. Do not downgrade or uninstall global setuptools, edit
site-packages, or disable warnings. See [the log review](docs/startup-log-review.md).

The isolated remedy below is for **Windows x64, existing Python 3.12**, from the
extracted candidate source directory. It creates a NEW environment outside the
repository and installs the already checked-in hashed runtime lock. It does not
modify the working virtualenv, global Python, APPDATA, database, media or presets.
Network access is required. This procedure is **NOT_RUN in the Linux delivery
environment**; completion requires the real import/resource probe below.

Run as an ordinary user in PowerShell. No activation or execution-policy change
is required. The explicit base-interpreter path matches the supplied log and
avoids launcher-triggered interpreter installation. Stop at any error; do not remove hash checks or retry into the same
partially created directory.

```powershell
$ErrorActionPreference = 'Stop'
$CandidateEnv = Join-Path $env:LOCALAPPDATA 'WaveHelm-runtime-step07G2-check'
if (Test-Path -LiteralPath $CandidateEnv) { throw 'Choose a new environment directory; keep the existing one.' }
$Lock = (Resolve-Path -LiteralPath '.\locks\windows-py312-runtime\requirements.lock').Path
$BasePython = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
if (-not (Test-Path -LiteralPath $BasePython -PathType Leaf)) { throw 'Select the exact path of an existing Python 3.12 executable.' }
& $BasePython -I -c "import struct, sys; sys.exit(0 if sys.platform == 'win32' and struct.calcsize('P') == 8 and sys.version_info[:2] == (3, 12) else 1)"
if ($LASTEXITCODE -ne 0) { throw 'An existing Windows x64 Python 3.12 is required.' }
& $BasePython -I -m venv $CandidateEnv
if ($LASTEXITCODE -ne 0) { throw 'Environment creation failed.' }
$Runtime = Join-Path $CandidateEnv 'Scripts\python.exe'
& $Runtime -I -c "import importlib.util, sys; sys.exit(0 if sys.prefix != sys.base_prefix and importlib.util.find_spec('pkg_resources') is None else 1)"
if ($LASTEXITCODE -ne 0) { throw 'Environment is not isolated from legacy pkg_resources.' }
& $Runtime -I -m pip --isolated install --index-url https://pypi.org/simple --require-hashes --only-binary=:all: -r $Lock
if ($LASTEXITCODE -ne 0) { throw 'Locked installation failed; keep its error output.' }
& $Runtime -I -m pip --isolated check
if ($LASTEXITCODE -ne 0) { throw 'Installed dependency consistency failed.' }
```

Then execute the following strict, real dependency probe. It does not start the
WaveHelm GUI or touch user playback data. A warning, missing resource, wrong pygame
version or non-isolated interpreter causes a nonzero exit. A successful probe is
not a complete audio-device or application qualification.

```powershell
$Probe = @'
import importlib.metadata as metadata
import importlib.util
import sys

if sys.prefix == sys.base_prefix or importlib.util.find_spec('pkg_resources') is not None:
    raise RuntimeError('Expected an isolated runtime without pkg_resources')
if metadata.version('pygame') != '2.6.1':
    raise RuntimeError('Unexpected pygame version')
import pygame
from pygame.pkgdata import getResource

with getResource('freesansbold.ttf', 'pygame') as resource:
    if not resource.read(4):
        raise RuntimeError('Empty pygame font resource')
print('DEPENDENCY_IMPORT_RESOURCE_CHECK_PASSED', sys.executable)
'@
& $Runtime -I -B -W error::UserWarning -c $Probe
if ($LASTEXITCODE -ne 0) { throw 'The pygame warning/resource gate is still open.' }
```

Keep the console output and exact interpreter path. Existing settings and media
remain unchanged because this procedure does not launch WaveHelm. The final GUI
and media smoke on this interpreter is a separate, deliberate qualification step.
Never describe a documented but unexecuted command as a fixed user environment.

## Related repository files

- [README.md](README.md)
- [docs/windows-packaging.md](docs/windows-packaging.md)
- [ROADMAP.md](ROADMAP.md)
- [AUTHORS](AUTHORS)
