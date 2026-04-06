# Install WaveHelm on Windows from source

This repository is maintained as a **Windows source-first public project**.

## Supported environment

- Windows 10 or Windows 11
- Python 3.10 or newer
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

### 5. Start WaveHelm

```powershell
python main.py
```

The optional `--ui-backend wx` flag is still accepted for compatibility, but wx is the only maintained UI runtime.

## Running tests

```powershell
python -m pytest -q tests
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

## Related repository files

- [README.md](README.md)
- [docs/windows-packaging.md](docs/windows-packaging.md)
- [ROADMAP.md](ROADMAP.md)
- [AUTHORS](AUTHORS)
