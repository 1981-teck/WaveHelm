# Native Windows lock and installation matrix — continuation R2

This collector resolves and independently installs six candidate dependency
locks: runtime and development for CPython 3.11, 3.12 and 3.13, Windows x64.
It never integrates a lock into the input source or runs a Git publication.
A complete collection is **not** vulnerability, ABI or application qualification.

## Start on Windows

Use the extracted source, not the installed application. Prerequisites are
operator-owned Windows x64, the three existing CPython versions, a working `py`
launcher and network access to the official PyPI index and its wheel hosts.
No developer packages need to be installed globally. Do not run as administrator.

From a PowerShell terminal at the extracted project root:

```powershell
.\tools\WaveHelm-LockMatrix.ps1
```

The driver defaults to 3.12. An existing 3.11 or 3.13 can drive the collection
using `-DriverVersion 3.11` or `-DriverVersion 3.13`. This does not remove the
other targets. A missing interpreter is a failed target, never an omitted one.
Automatic interpreter installation is disabled for child commands; the wrapper
restores its three temporary launcher environment settings on return. Use an
operator-controlled launcher and configuration, not an untrusted executable.

The default output is a NEW `%LOCALAPPDATA%\WaveHelm-locks-<UTC>-<id>` directory.
For a different location, its parent must already exist; the output must not:

- exist already or be a symlink/junction/reparse-point alias;
- be inside the source or contain the source;
- be writable by an untrusted account during collection.

```powershell
.\tools\WaveHelm-LockMatrix.ps1 -OutputDirectory 'C:\work\WaveHelm-lock-results-new'
```

If policy disallows PowerShell scripts, the equivalent Python entrypoint is:

```powershell
$env:PYTHON_MANAGER_AUTOMATIC_INSTALL = 'false'
Remove-Item Env:PYLAUNCHER_ALLOW_INSTALL, Env:PYLAUNCHER_ALWAYS_INSTALL -ErrorAction SilentlyContinue
py -3.12 -I -B .\tools\windows_lock_matrix.py
```

Do not weaken an organizational execution policy to run the wrapper. The direct
Python alternative does not change machine-wide environment settings; its three
launcher settings last for the terminal session. Missing launchers or a foreign
OS can fail before output creation, in which case keep the terminal error.

## What actually happens

The source is inventoried and copied to a separate sealed snapshot. For each
interpreter, a private resolver venv installs exact, wheel-hash-pinned pip 26.2.1
and packaging 25.0. The bootstrap uses ensurepip from the chosen existing Python;
this is not proof of a fully hash-locked operating system or interpreter image.

Each target then resolves the unchanged requirements, validates all root and
transitive edges and wheel hashes, creates a target venv **without pip**, and
uses the resolver's `pip --python` to install with `--require-hashes`. A separate
`pip check`, actual package-metadata inventory and report comparison are required.
The comparison binds names, versions, selected wheels, hashes, target and private
venv status. Explicitly requested frozen transitive entries are not misrepresented
as top-level project dependencies. No missing pygame implementation is substituted.

Failures preserve their own command, stdout, stderr, exit code and available raw
pip report. A failed bootstrap marks both targets for that interpreter failed.
Handled failures do not erase earlier targets. Ctrl+C is controlled incomplete
termination, not resumable success. Restart to a new output directory after fixing
an error; there is no blind resume or overwrite of an existing collection.

## Result to return for review

The terminal prints `results_archive`, normally ending in:

```text
WaveHelm-lock-matrix-results.zip
```

This ZIP contains `evidence/SUMMARY.json`, command logs, real reports/inventories,
candidate lock bundles and a verified SHA-256 manifest. It excludes the private
venvs and copied source. Keep the ZIP even on `INCOMPLETE`; it identifies the
failed interpreter or package. Logs and source inventory can contain local paths
and package URLs, so review them before sharing outside the project.

Exit zero requires six successful installations/comparisons, unchanged original
source and snapshot, and six unchanged exact lock bundles. The only full-matrix
status is `MATRIX_INSTALLED_NOT_AUDITED`; human review remains REQUIRED and release
readiness remains NOT_VERIFIED. Hashes are integrity checks, not signatures or
proof against an actor able to rewrite every input and evidence file.

The operator owns the external output folder. Delete it only after preserving the
verified result ZIP and when no command is still running. The collector does not
automatically delete venvs, snapshot or diagnostic files after a failed campaign.

## Explicit scope and budgets

The parent collector is standard-library only; packaging is installed privately
for graph validation. Subprocess timeouts bound direct children, not an entire
hostile process tree. Logs are size-checked after a command (32 MiB), not limited
by a live disk quota. Source/evidence inventories are bounded by the shared
inventory limits (10,000 entries, 256 MiB, 32 MiB per file). This is cold release
tooling, not a sandbox for malicious packages, concurrent accounts or device code.

No audit/SBOM/license verdict, full tests, build, ABI harness, app startup,
canonical branch update or remote CI is performed here. Those release gates
remain separate, including when all six installations match.

## Official references used for the implementation

Consulted 2026-09-10; metadata was read, not replaced with a claimed local download.

- https://pypi.org/project/pip/26.2.1/#files — wheel SHA-256
  `71138adf1f4ca900cdb7d289c21b7494329f2332b6d85f0e1c42108c0384ed3e`.
- https://pypi.org/project/packaging/25.0/#files — wheel SHA-256
  `29572ef2b1f17581046b3a2227d5c611fb25ec70ca1ba8554b24b0e69331a484`.
- https://pip.pypa.io/en/stable/topics/python-option/ — installation to a venv
  without seeding pip into the target inventory.
- https://pip.pypa.io/en/stable/reference/installation-report/ — report schema
  and requested-package semantics; a pip report is not itself a lock file.
- https://docs.python.org/3/using/windows.html — launcher auto-install controls.
