# Step06A: portable Library qualification text I/O

This checkpoint changes tests and documentation only. It does not change Library
layout, localized resources, media playback, dependency locks, or the native probes.

## Failure and repair

The returned step06 Windows suite recorded 2,793 passes, four failures and 16 skips.
All four failures were one parametrized locale roundtrip test: Path.read_text was
called without an encoding on UTF-8 localization files and selected cp1252 on that
machine. The test stopped while loading each locale, before checking its resize
roundtrip. The separate native geometry probe did explicitly read UTF-8 and passed.

The roundtrip test now declares encoding='utf-8'. Adjacent Library probe tests also
use explicit UTF-8 when reading/writing qualification JSON and retained text. No
errors='ignore' or replacement decoding, altered locale bytes, new exclusion,
platform skip, process-wide encoding override, or exception swallowing is used.

## Regression boundaries

The new test module reuses the actual Library roundtrip assertions. It emulates
cp1252 and ASCII only at the four locale read boundaries, checks lossless Unicode
JSON contents, and verifies invalid UTF-8 bytes remain an error. Named Library
qualification files have an AST guard against newly implicit text reads/writes.
Controlled boundary tests are not a native Windows wx execution.

Before repair these new tests have 14 failures and one positive control. The latter
confirms the native probe already uses explicit encoding. A targeted scan found no
implicit Path.read_text/write_text calls under src or tools in the baseline, but
other older tests have implicit text I/O and are recorded as out-of-delta review
scope. This is not an all-Python-I/O encoding certification.

## Evidence allocation

The exact returned step06 archive was independently checked: all 60 native geometry
cases, 420 control rectangles, ancestor widths, 12 source seals and four geometry
manifest payloads agree. Recorded native wx is 4.2.5 / wxWidgets 3.2.9, at 96x96 DPI.
Font factors 1 and 1.5 are font tests, not multi-monitor DPI qualification. Focus,
control identity and input-retention assertions are producer observations.

Geometry acceptance is limited to those unchanged source/probe bytes and that run.
The failed step06 full-suite receipt stays FAILED; passing geometry does not change
it. A corrected step06A Windows full-suite run is still needed. Detailed counts,
commands, hashes and preserved scopes are in the external report/evidence package.

Do not reinstall Python, reset settings/database/columns, regenerate locks, repeat
ABI compilation or overwrite the public installation. The previously tolerated
cursor residuals and all unrelated release qualification gates remain separate.
