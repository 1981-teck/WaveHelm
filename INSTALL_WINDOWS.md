# Install WaveHelm on Windows

Choose one route:

- [Install the Windows application](#install-the-windows-application): use the
  published x64 installer; no separate Python installation is required.
- [Run from source](#run-from-source): use an existing Windows x64 Python and the
  matching checked-in dependency lock.

WaveHelm remains a **Windows source-first public project**. Publishing the source
and providing a convenience installer are complementary, not competing routes.

## Install the Windows application

### Download and requirements

The public `1.0.3` release provides
[`WaveHelmSetup_1.0.3_win64.exe`](https://github.com/1981-teck/WaveHelm/releases/download/v1.0.3/WaveHelmSetup_1.0.3_win64.exe).
Use Windows 10 or Windows 11 x64 with Windows COM and Media Foundation available.
Video decoding depends on the codecs supported by the local Windows installation.
The installer route does not require the source checkout, pip, or a virtual environment.

Open the [v1.0.3 release page](https://github.com/1981-teck/WaveHelm/releases/tag/v1.0.3)
and select the `.exe` under **Assets**. The `.whl`, `.tar.gz`, and automatic
**Source code** downloads are developer/source artifacts, not the installer.

### Check the download

Download `WaveHelm-1.0.3-SHA256SUMS.txt` from the same release. In the directory
containing the downloaded installer, use PowerShell:

```powershell
$ErrorActionPreference = 'Stop'
Get-FileHash -LiteralPath '.\WaveHelmSetup_1.0.3_win64.exe' -Algorithm SHA256
```

Compare the complete hash with the entry for that exact filename. Stop if it
differs. A matching hash checks consistency with the published checksum; it does
not establish an Authenticode signature or replace the operating system's security
checks. Do not disable Windows security protections to complete an installation.

### Install and first launch

1. Close any running instance of WaveHelm.
2. Run the downloaded installer and follow its prompts.
3. Launch WaveHelm and check that **About** reports `1.0.3`.
4. Import a local file in **Library** and try playback, pause, seek, and stop.
   The [README quick start](README.md#quick-start) covers the other pages.

Application settings, the library database, logs, and app-managed exports are
stored under `%APPDATA%\WaveHelm`; see [data and persistence](README.md#data-logs-exports-and-persistence).

### Upgrade an existing installation

Close WaveHelm and back up `%APPDATA%\WaveHelm` before upgrading. Run the new
installer for the existing installation rather than mixing installed application
files with a source checkout. Confirm the version in **About** after the upgrade
and check your library, settings, playlists, and favorites.

The [1.0.3 release notes](docs/release-notes-1.0.3.md) report a real
`1.0.2 -> 1.0.3` in-place upgrade and exact installed-tree verification.
That evidence covers the stated transition; it is not a guarantee for every older
version, custom install location, or rollback scenario.

### Uninstall

Close WaveHelm, then select WaveHelm's uninstall action in Windows app settings
and follow the uninstaller. Keep your application-data backup until you have
confirmed which data you intend to retain.

The recorded 1.0.3 uninstall check covers the registry identity, installation tree,
and shortcuts. It is not a blanket promise about personal-data retention under
every installer option. Do not delete your media folders as an uninstall step.

## Run from source

### Supported source environment

The declared minimum is Python 3.11. The checked-in Windows x64 locks and CI matrix
target CPython **3.11, 3.12, and 3.13**; use **3.12** for the commands below.
A newer interpreter satisfying the package minimum is not automatically qualified.
Windows COM, Media Foundation, and compatible local codecs remain runtime requirements.
macOS and Linux are not supported product targets.

Get a clean source checkout or source archive first and open PowerShell in its root
(the directory containing `main.py`, `requirements.txt`, and `locks/`). Use the
`v1.0.3` source for the published release; `main` may contain later development.
Do not copy an old source tree over a new one.

### Create an isolated, hash-locked runtime

Use an **already installed Python 3.12 x64 executable**. The first prompt requests
its exact path; it does not install Python or invoke a launcher. The commands create
a unique environment under `%LOCALAPPDATA%\WaveHelm-development`, outside the repo.
No activation, administrator session, or execution-policy change is required.
Stop on any error and retain its output; do not drop `--require-hashes` or reuse
a partially installed environment. Network access to the package index is required.

```powershell
$ErrorActionPreference = 'Stop'
$BasePython = (Resolve-Path -LiteralPath (Read-Host 'Full path to installed Python 3.12 x64 python.exe')).Path
$RuntimeLock = (Resolve-Path -LiteralPath '.\locks\windows-py312-runtime\requirements.lock').Path
if (-not (Test-Path -LiteralPath '.\main.py' -PathType Leaf)) { throw 'Open PowerShell in the source root.' }
& $BasePython -I -c "import struct, sys; sys.exit(0 if sys.platform == 'win32' and struct.calcsize('P') == 8 and sys.version_info[:2] == (3, 12) else 1)"
if ($LASTEXITCODE -ne 0) { throw 'Windows x64 CPython 3.12 is required for this lock.' }
$RuntimeEnv = Join-Path $env:LOCALAPPDATA ('WaveHelm-development\py312-runtime-' + [guid]::NewGuid().ToString('N'))
if (Test-Path -LiteralPath $RuntimeEnv) { throw 'Use a new runtime environment directory.' }
& $BasePython -I -m venv $RuntimeEnv
if ($LASTEXITCODE -ne 0) { throw 'Runtime environment creation failed.' }
$Runtime = Join-Path $RuntimeEnv 'Scripts\python.exe'
& $Runtime -I -m pip --isolated install --only-binary=:all: pip==26.2.1
if ($LASTEXITCODE -ne 0) { throw 'Pinned pip bootstrap failed.' }
& $Runtime -I -m pip --isolated install --require-hashes --only-binary=:all: -r $RuntimeLock
if ($LASTEXITCODE -ne 0) { throw 'Locked runtime installation failed.' }
& $Runtime -I -m pip --isolated check
if ($LASTEXITCODE -ne 0) { throw 'Runtime dependency consistency failed.' }
```

The pip bootstrap version matches the existing CI configuration; it is a bootstrap
tool, not a claim that the bootstrap itself is hash-locked. Application dependency
wheels are installed from the selected hash lock. `requirements.txt` declares root
pins; installing it alone is not the same as reproducing the locked transitive graph.
The complete metadata/lock verification is described in
[Windows artifact hash locks](docs/locked-dependencies.md) and implemented in
[CI](.github/workflows/ci.yml); a successful installation is not the whole CI gate.

For Python 3.11 or 3.13, select that installed x64 interpreter, validate that exact
version, and use its matching `windows-py311-*` or `windows-py313-*` lock bundle.
Never mix interpreter versions and lock targets or regenerate locks just to bypass
a failed installation.

### Start WaveHelm

In the same PowerShell session, from the source root:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
& $Runtime -B .\main.py
if ($LASTEXITCODE -ne 0) { throw 'WaveHelm exited with an error; retain the log.' }
```

The optional `--ui-backend wx` flag remains accepted for compatibility; wx is the
only maintained UI runtime. Starting the app can use the same `%APPDATA%\WaveHelm`
data as an installed copy, so close that copy and back up your data before testing.
A separate Python environment does not, by itself, isolate application data.

## Development, tests, and package builds

For contributors, create a **separate** development environment with the matching
dev lock. Run this after the source setup above, in the same PowerShell session
and source root. It uses the already validated `$BasePython` without altering the
runtime environment or global packages.

```powershell
$DevLock = (Resolve-Path -LiteralPath '.\locks\windows-py312-dev\requirements.lock').Path
$DevEnv = Join-Path $env:LOCALAPPDATA ('WaveHelm-development\py312-dev-' + [guid]::NewGuid().ToString('N'))
if (Test-Path -LiteralPath $DevEnv) { throw 'Use a new development environment directory.' }
& $BasePython -I -m venv $DevEnv
if ($LASTEXITCODE -ne 0) { throw 'Development environment creation failed.' }
$DevPython = Join-Path $DevEnv 'Scripts\python.exe'
& $DevPython -I -m pip --isolated install --only-binary=:all: pip==26.2.1
if ($LASTEXITCODE -ne 0) { throw 'Pinned pip bootstrap failed.' }
& $DevPython -I -m pip --isolated install --require-hashes --only-binary=:all: -r $DevLock
if ($LASTEXITCODE -ne 0) { throw 'Locked development installation failed.' }
& $DevPython -I -m pip --isolated check
if ($LASTEXITCODE -ne 0) { throw 'Development dependency consistency failed.' }
```

Keep test reports outside the repository. The following runs the complete source
suite, with the same coverage/test entry point used by CI:

```powershell
$Evidence = Join-Path $env:LOCALAPPDATA ('WaveHelm-development\checks-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $Evidence -ErrorAction Stop | Out-Null
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
$env:COVERAGE_FILE = Join-Path $Evidence 'wavehelm.coverage'
& $DevPython -B -m coverage run --branch --source=src -m pytest -p no:cacheprovider -q tests --junitxml="$Evidence\tests.junit.xml"
if ($LASTEXITCODE -ne 0) { throw 'Tests failed; retain the evidence and do not declare qualification.' }
& $DevPython -B -m coverage json -o "$Evidence\coverage.json"
if ($LASTEXITCODE -ne 0) { throw 'Coverage export failed.' }
& $DevPython -B -m build --no-isolation --outdir "$Evidence\dist"
if ($LASTEXITCODE -ne 0) { throw 'Package build failed.' }
```

The source release includes the complete test suite. Tests and builds can generate
additional working files; do not commit or distribute them. Follow the repository's
[packaging and source-hygiene checks](docs/windows-packaging.md) before publishing.
These commands build the wheel and source distribution, **not** the Windows installer.
Native GUI, dependency auditing/SBOM, and ABI checks remain separate verification steps.

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
