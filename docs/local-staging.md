# Local candidate staging — continuation R1

This is a **local-development snapshot workflow**, not the complete Step 10C
canonical-branch updater. It never changes the caller's source, configures a
remote, pushes, tags or publishes. `Publish` and remote `Verify` remain absent
rather than returning artificial success. A local PASS does not close Windows,
ABI, current vulnerability, license, transitive-lock or remote CI qualification.

## Prerequisites and execution

Use a trusted Python environment with the project and development dependencies
installed from checked-in, mechanically validated hash locks. Human release review
remains separate. Git must be available. The source and session
storage, their ancestors and installed executables must be controlled by the
operator. Do not use this as a sandbox for untrusted executable source code.

```powershell
python -B tools/stage_candidate.py Prepare --source C:\work\WaveHelm --session C:\work\WaveHelm-session-R1
python -B tools/stage_candidate.py Status --session C:\work\WaveHelm-session-R1
python -B tools/stage_candidate.py Commit --session C:\work\WaveHelm-session-R1
python -B tools/stage_candidate.py Cancel --session C:\work\WaveHelm-session-R1
```

`Prepare` requires a new, disjoint session directory. Its parent must exist.
Parent traversal spellings, links/reparse points, hard links, Windows-reserved
names, case/Unicode aliases and exceeded file/count/byte budgets are rejected.
The source is copied and checked against its original byte inventory. Root Git
administration is excluded and a separate, initially uncommitted Git repository
is created for the local snapshot. This is not historical commit provenance.

## Executed gates

The default preflight performs syntax parsing, `pip check`, source hygiene,
**unfiltered** pytest, cleanup, post-test hygiene and `python -m build
--no-isolation`. Build output, test temporary files and logs live outside the
staged source. Cleanup after build and a final inventory comparison are required.
Third-party pytest entry-point autoload and inherited `PYTEST_PLUGINS`/
`PYTEST_ADDOPTS` are disabled. Built-in plugins and the project conftest remain
active. This controls host instrumentation, not test selection.
No dependency download, pygame substitute, test exclusions or fallback backend
are used by this preflight. Missing tools/dependencies, nonzero exit, timeout,
source drift or missing wheel/sdist result in `FAILED`.

Each command gets a raw log, exit code and elapsed time. Log and artifact hashes
are checked again before a commit. The complete command list, current source
inventory, receipt hash and maximum receipt age of 24 hours are also checked.
A receipt with a future timestamp is rejected. Installed-environment mutation
by another process is outside this local source-seal boundary; use a dedicated,
operator-owned virtual environment, not a concurrently modified shared one.

## State and recovery

States are `PREPARING`, `FAILED`, `PREPARED_LOCAL`, `COMMITTING_LOCAL`,
`COMMITTED_LOCAL` and `CANCELLED`. State always retains
`release_readiness=NOT_VERIFIED`.

Only `PREPARED_LOCAL` may begin a local commit. A stopped commit operation may be
retried while its receipt is fresh; an already created matching single root
commit is recovered rather than duplicated. Unexpected history, dirty staging,
changed bytes or any configured remote are rejected. Git environment redirection
and global/system configuration are disabled for staging commands.

`Cancel` preserves source copies, artifacts, logs and any existing local commit;
it does not erase user files or perform a destructive reset. A failed/expired
preparation must be repeated into a new session. An OS-owned, nonwaiting file
lease prevents simultaneous operations and is released when its process exits.
Never remove the lease file while an operation is active.

## Limits and threat boundary

All tools run in a cold, operator-initiated workflow; this is not a runtime/hot
path. Hashes detect drift but do not authenticate a malicious operator rewriting
both state and evidence. No hostile concurrent filesystem-writer guarantee,
whole-descendant process containment, physical power-loss guarantee or signed
attestation is claimed. Subprocess timeouts are failures; a subprocess that
creates detached descendants may require operator cleanup before a new session.
Atomic state replacement does not prove directory/power-loss durability.

PowerShell delegation and native Windows behavior require separate native tests.
Canonical Step 21 provenance replay, branch integration, pre-publication approval,
`PUSHED_CI_PENDING`, exact-commit remote verification and evidence acquisition
are still-open parts of the full updater contract.

## Test scope

Contract tests cover missing tools, invalid paths, source/evidence/artifact drift,
lease contention, timestamp expiry, remote rejection, cancellation and recovery.
Successful state-machine fixtures explicitly use synthetic receipts and do not
count as actual successful dependency/build/native executions. Real negative
preflight and subprocess executions are tested separately. Project release
qualification must use actual logs from its real environment.
