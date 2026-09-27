# Theme JSON callback review — step07V

Date: 22 September 2026. Decision: **KEEP_CURRENT_BASELINE_PUBLIC_CALLBACK**.
No application function, error type, parser option or import is changed.

## Public helper and actual consumers

`src.model.theme_schema.reject_nonstandard_json_number(value: str) -> object`
is an importable, unprefixed helper shipped by the existing package. The supplied
source has no runtime caller, explicit re-export or documented external client.
Its only pre-review references are its definition and the cleanup ledger. This is
not evidence that every possible external or computed consumer is absent.

Keep the existing import path, signature, ValueError type and message. The reason
is deliberate compatibility for a small callable API, not a claim that the player
uses it or that it is required for playback. Deletion and a compatibility shim were
considered; neither provides a demonstrated benefit over retaining three lines of
existing implementation. No alias, wrapper, deprecation warning or new runtime layer
is introduced. The maintenance decision is closed for this baseline.

## Correct use and limits

The helper can be passed as `json.loads(..., parse_constant=...)`. It rejects the
three named constants `NaN`, `Infinity` and `-Infinity`. Standard finite numbers,
booleans, null and quoted strings keep normal decoder behavior.

It is **not a general strict or bounded JSON parser**. In particular, the standard
library's float path parses exponent tokens separately. With this callback alone,
`1e999` and `-1e999` yield non-finite floats on the tested interpreter. The helper
also supplies no document, depth, node, string or container budget and does not
reject duplicate object keys. Those limitations do not constitute a bypass of the
active ThemeManager route, which does not use this callback.

Primary mechanism reference, consulted 22 September 2026:
https://docs.python.org/3.13/library/json.html
The reference documents parse_constant/parse_float dispatch, not WaveHelm outcomes.

## Maintained application route

`ThemeManager._read_custom_catalog()` calls `read_json_file` with
`THEME_JSON_LIMITS` and `root="object"`. The bounded utility performs a limited byte
read, strict UTF-8 decoding, its own `_reject_constant`, bounded integer/float token
conversion and finite-float checks, duplicate-object rejection, then complete-tree
and root validation. Schema validation follows successful parsing. The custom-theme
budget remains 512 KiB, depth 4, 5,000 nodes, 128 items per container and the other
unchanged limits specified in theme_manager.py.

Invalid custom-theme files cause `_load_custom_themes` to retain trusted built-ins
and block later custom writes. They are not automatically replaced with defaults.
Existing serialization validates in-memory values and rejects non-finite floats
before durable persistence. Neither path is rewritten or replaced by the legacy
helper during this review.

## Regression scope

`tests/test_theme_json_compatibility.py` protects:

- Kept callback identity, annotations, exact ValueError and real json.loads use;
  ordinary JSON values, quoted constants and the explicit exponent-overflow limit.
- Independent real bounded parsing even when the legacy callback is replaced by a
  failing trap; invalid numeric values in unexpected or nested members are rejected.
- The real ThemeManager loading path, with temporary invalid numeric-color files;
  save/color/reset stay blocked, bytes/catalog/event observations stay unchanged.
- Duplicate keys, incorrect roots, invalid UTF-8, trailing data, depth/document/
  integer-token limits, in-memory serialization rejection and valid Unicode-theme
  read/save/reload through the actual durable writer.

A diagnostic copy with the legacy callback deliberately weakened is used only to
prove sensitivity of the retention tests. It is not the delivered source and is not
an observed application defect. Temporary files and a recording event publisher are
test fixtures; no running audio backend, GUI, COM object or user database is used.

## Boundaries

All application/resource and project-tool bytes remain identical to step07U. The
public duplicate-pair helper is a separate API and stays unchanged. Existing
logging/provider exception boundaries, arbitrary external callers, resource usage
inside Python's decoder before tree normalization, global static typing, security,
coverage, advisory state and native device behavior are not certified by these
bounded tests. The old Windows F/G/G2 and L/M gates stay accepted in their own scope.
A future retirement or consolidation requires explicit compatibility and error-type
decisions rather than a global unused-function deletion.
