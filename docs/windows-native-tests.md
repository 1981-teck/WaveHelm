# Windows application-test continuation R5E

R5E repairs the concrete failures in the returned R5D native preflight. It does
not represent a successful native execution of the new source. Keep the original
R5D evidence, successful ABI receipt and restored Python environment.

## Diagnostic mapping

- Five library discovery/loading failures shared one identity-source bug:
  Windows DirEntry.stat provides zero st_ino/st_dev/st_nlink, whereas os.stat
  supplies real values. Fresh no-follow os.stat is now used at discovery too.
  The repair preserves all equality, time-budget, cancellation and failure gates.
- One portable legacy playlist load raised PathError for a missing leaf. The
  reader now maps FileNotFoundError to ReadError; post-commit missing files map
  to WriteError. lstat preserves denied inspection versus genuine absence.
- Three failures used text-mode lock fixtures whose LF bytes became CRLF on
  Windows. Canonical fixtures now use UTF-8 bytes. Production lock verification
  is unchanged and still rejects a CRLF-altered lock.
- One database test compared a canonical normcase path with unnormalized spelling.
  The assertion now checks normalized spelling AND samefile identity. Production
  database normalization is unchanged.
- Two errors were setup and teardown of ONE oversized parameter node. Short
  descriptive IDs keep PYTEST_CURRENT_TEST bounded; the 32769-character hostile
  input remains unchanged and is still exercised by the migration test.

All changed tests are contract/fixture corrections. No test was deleted, no new
skip or xfail was introduced, and exact hashes/identity checks were not weakened.
The negative installation tests now require the relevant error, rather than
passing when a broken fixture causes an earlier unrelated lock failure.

## Execute without installing dependencies or changing the existing source

Use an operator-controlled, still-present private Python 3.12 development venv
that has already passed the hash-lock restoration. Select its exact python.exe;
never silently substitute a global Python, or install packages to make tests pass.

From the extracted R5E source, invoke:

```powershell
& $PythonTest -I -B .\tools\stage_candidate.py Prepare --source . --session $Session
```

$Session must be a NEW directory outside the source with an existing parent.
The delivered per-session PowerShell instructions redirect APPDATA, LOCALAPPDATA,
TEMP and TMP into a new diagnostic directory, and restore them in finally.
This is accidental-state isolation, not a hostile-code/system sandbox.

Prepare makes a sealed disposable source copy and runs dependency checks,
syntax/source hygiene, the UNFILTERED test suite and python -m build --no-isolation.
Build can succeed even when tests fail; only a fully successful preflight can
produce PREPARED_LOCAL. No Commit, Publish or GUI/manual launch is performed.

Preserve console.txt, launcher.json, state.json, evidence/ and dist/ in the result
ZIP even on failure. Do not archive the venv, test-temp or staged source. The raw
JUnit report may contain local paths; inspect before sharing outside the project.

The original 15 Windows skips were POSIX-only semantics or foreign-platform refusal
checks. They must remain visible, not disappear from accounting. R5E does not make
Windows coverage claims for those excluded behaviors.

## Remaining gates

Native R5E unfiltered tests/build remain required. The successful nine-layout ABI
receipt belongs to the unchanged R5D boundary and is not full multimedia validation.
The LPWSTR allocation/free observation, GUI/media behavior, device activation,
external static/type/security checks, coverage ratchet and canonical Publish/Verify
remain separate. No release is approved by this continuation.

## Primary semantic references

- https://docs.python.org/3.12/library/os.html#os.DirEntry.stat
- https://docs.python.org/3.12/library/pathlib.html#pathlib.Path.write_bytes
- https://docs.python.org/3.12/library/os.path.html#os.path.normcase
- https://docs.pytest.org/en/stable/example/parametrize.html#different-options-for-test-ids
- https://docs.pytest.org/en/stable/example/simple.html#pytest-current-test-environment-variable
