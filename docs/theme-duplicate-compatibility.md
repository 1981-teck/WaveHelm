# Theme duplicate-key callbacks: compatibility and active validation

Checkpoint: R5I-step07W, 22 September 2026. Decision:
**KEEP_CURRENT_BASELINE_PUBLIC_CALLBACK** for
`src.model.theme_schema.reject_duplicate_object_pairs`.
No application or tool code changes. This is a bounded compatibility review,
not a full security, resource-consumption or native runtime certification.

## Existing consumers and decision

`tests/test_theme_schema.py::test_duplicate_json_keys_are_rejected_at_every_depth`
already imports and exercises the callback through `json.loads(object_pairs_hook=...)`.
The current repository search finds no application caller through this helper.
It is nevertheless a shipped public callable with a tested contract. Preserve its
signature, exact ValueError and message instead of removing an API without a
functional benefit. Unknown external or computed consumers are not proved absent.
No wrapper, deprecation warning or replacement parser is added.

## Two independent mechanisms

| Layer | Actual behavior | Deliberately separate concern |
|---|---|---|
| Public pairs callback | Builds one dict from ordered pairs; rejects a repeated decoded key with `ValueError("duplicate JSON key: ...")` | It does not impose byte/depth/node limits, root shape or Unicode normalization |
| Active bounded JSON parser | Uses its own `_build_object`, with an item cap and `BoundedJsonError("Duplicate JSON key: ...")`; validates the full tree and configured limits | No dependency on the public pairs callback |
| Theme schema | Normalizes theme identities using NFKC, stripping/spacing and casefold; rejects canonical collisions | Distinct JSON keys may still name the same theme |
| ThemeManager file boundary | On invalid custom-theme input, retains the file, records a write block and refuses save/color/reset mutations | No automatic repair or overwrite of invalid input |

`object_pairs_hook` receives the ordered pairs for each JSON object while decoding.
Nested duplicate rejection depends on using the hook in that decoding traversal;
a direct call on a previously decoded dict cannot recover keys already discarded.
A key repeated in a different object is not a duplicate in the first object.
Escapes are decoded before comparison, so `"a"` and `"\u0061"` collide. Case or
normalization-equivalent keys such as `"A"` / `"a"` or precomposed/decomposed text
are distinct at the JSON layer and can collide later at the theme-name layer.

Unique keys preserve insertion order and value identity. The public helper builds
a new outer dict without mutating the input sequence; it does not deep-copy or
validate arbitrary nested Python objects. Its annotated input is a sequence of
string-key pairs from the JSON decoder, not a general hostile-provider boundary.
It is not by itself a strict complete JSON parser or a numeric validator.

## Evidence and negative controls

`tests/test_theme_duplicate_compatibility.py` checks the public API, empty and unique
objects, equal/conflicting duplicates, nested object/array paths, escaped Unicode,
independent sibling objects, hook precedence, canonical-name collisions, file/state
preservation, blocked mutations and valid Unicode palette roundtrips.
The active parser and manager tests replace the legacy hook with an exception trap:
any unintended call fails, rather than silently selecting a permissive substitute.
The file tests use real isolated temporary files and a recording publisher, not
user data, a native GUI or an audio service.

Two separately owned counterfactual source copies remove only the legacy duplicate
check or only the active duplicate check. They are intentionally invalid, never
shipped as application sources and never counted as candidate failures. Their
expected failing regressions demonstrate sensitivity to each independent guard.
Actual counts, hashes, command receipts and limitations are in the external REPORT,
STATUS and evidence archive. No complete type-check or independent audit is implied.

## Preserved scope

The existing numeric callback, active non-finite/overflow/UTF-8/resource checks,
serialization, theme normalization, built-ins, durable-write behavior, logging,
threading, dependency pins and all native code remain byte-identical to V. Earlier
Windows audio acceptances remain closed within their original scopes; this review
is not a new Windows playback run. The historical Path-encoding catalog remains
closed; new text reads/writes explicitly select UTF-8.

Primary mechanism reference, consulted 22 September 2026:
https://docs.python.org/3.13/library/json.html#json.load
The Python API reference explains `object_pairs_hook` and the default duplicate
handling; it does not attest to WaveHelm test results.
