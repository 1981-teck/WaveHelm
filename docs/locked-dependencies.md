# Windows artifact hash locks

The lock validator consumes pip report schema 1 from pip 26.2.1, validates a
complete Windows x64 CPython graph and renders pinned SHA-256 requirements.
It checks root pins, recursive dependency closure, extras/markers, Python
compatibility, wheel identity/tags, duplicate names, yanked artifacts and URLs.
Only HTTPS files.pythonhosted.org wheel artifacts are accepted.

A validated report is not an installation, vulnerability scan or signed proof
of origin. Human review and version control of the report and lock are required.
The report is trusted metadata; neither it nor its hashes defend against a
malicious actor allowed to rewrite both the source policy and the lock bundle.

## Recommended: collect all six real installed targets

Continuation R2 adds `tools/WaveHelm-LockMatrix.ps1`. It creates private resolver
and target environments, captures failures, independently checks the installed
graphs, and returns one evidence ZIP without changing the source. See
`docs/windows-lock-matrix.md` for prerequisites, commands and return instructions.
The generated header says `resolved-artifact`, not `reviewed-artifact`: actual
human review remains a separate required decision.

## Resolve on each supported native interpreter (manual route)

Use a clean Windows x64 environment with the declared developer tools. Create an
empty `locks` directory in the candidate source, then run these for each of
Python 3.11, 3.12 and 3.13 (shown for 3.12):

```powershell
py -3.12 tools/resolve_windows_lock.py resolve --requirements requirements.txt --bundle locks/windows-py312-runtime
py -3.12 tools/resolve_windows_lock.py resolve --requirements requirements-dev.txt --bundle locks/windows-py312-dev
py -3.12 tools/resolve_windows_lock.py verify --requirements requirements.txt --bundle locks/windows-py312-runtime
py -3.12 tools/resolve_windows_lock.py verify --requirements requirements-dev.txt --bundle locks/windows-py312-dev
```

The manual resolver does not install the resulting graph or overwrite a bundle.
It now keeps command diagnostics and raw pip output outside the accepted bundle
(default: `<bundle>-diagnostics`, or explicit `--diagnostics <new-directory>`).
Failed or malformed reports remain diagnostic, never an accepted lock. Avoid
leaving manual diagnostic directories in the candidate source before packaging.
Review the two files `requirements.lock` and `pip-report.json` per target and
commit them together. For regeneration, use a new destination and review its
diff; do not bypass hash enforcement. Run a fresh installation with
`--require-hashes --only-binary=:all:` before qualification. Because a lock pins
one selected wheel per package, it is intentionally target-specific.

## Current candidate status

Continuation R3 incorporates the six locks produced by the native Windows
collection dated 2026-09-14. Each `requirements.lock` is byte-identical to the
returned evidence. The supporting `pip-report.json` files were normalized only
from Windows CRLF to repository LF line endings; parsed JSON content and lock
re-rendering remain identical. The original evidence archive is retained as the
byte-exact provenance source. Installed target graphs matched the corresponding
locks and `pip check` returned clean for all six targets; the evidence archive
itself passed SHA-256/size manifest verification.

This closes generation and installed-graph matching, not release approval. The
lock header intentionally continues to say `Human review: REQUIRED before
integration.` Human release review, current vulnerability auditing, installed
CycloneDX evidence and license verification remain separate gates. CI verifies
checked-in lock bytes before installation; it does not infer human approval.

Failure classes: missing/changed requirements, foreign target, incomplete graph,
invalid or missing hashes, report tampering, network timeout and existing output.
Any such failure returns nonzero and does not publish an accepted new bundle.
