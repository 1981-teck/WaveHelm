# Playback event path identity — step08I

The event handler must not treat case differences in a stream resource as identity.
08H-F01 demonstrated stale ERROR and STOPPED events being accepted after whole-URL
lowercasing. The same matcher is used by READY/start. This correction replaces only
the comparison, preserving the surrounding event, state and native-controller code.

## Supported comparison

`src.utils.media_path_identity.same_media_path` is a pure comparison boundary.
For the four existing streaming schemes (HTTP, HTTPS, RTSP, RTMP), it folds ASCII
scheme and host case only. Userinfo, path, query, fragment, explicit port spelling,
percent-encoded text and IPv6 zone identifiers remain exact. Empty `?` and `#`
delimiters remain present. It does not equate default ports, empty paths with `/`,
dot segments, percent-decoded names, reordered query parameters, IDNA spellings or
Unicode normalization forms. No URL is fetched or resolved, and no server redirect
is inferred. This intentionally conservative identity is not full URI canonicalization.

Local paths keep the old trimmed, lowercase comparison, on every reviewer platform.
Single-letter colon prefixes remain Windows drive paths, including drive-relative
forms. Slashes, relative/absolute spelling, symlinks and filesystem identity are not
normalized. Other URI schemes compare exactly. File URIs are not converted to local
paths. This does not add new supported playback protocols.

The old matcher still checks the current track first, then the existing
`_resolved_stream_url` metadata alias. It retains its public attachment, string
coercion, metadata exception tuple and direct-match short circuit. It does not
mutate the track, metadata or event URL, or change what is forwarded for playback.

## Explicit edge cases and limits

- Path/query case differences, including Unicode, are distinct identities. Case in
  userinfo and the fragment is also preserved; host-only and scheme-only changes match.
- Missing hosts, invalid ports/brackets, embedded ASCII whitespace/control characters,
  backslashes and unpaired surrogates in recognized stream URLs are rejected. Parsing
  failures do not fall back to a case-insensitive local comparison. This is not an
  exhaustive URI syntax validator or a network-admission/authorization policy.
- Comparison rejects empty/non-text helper inputs and strings longer than 65,536
  characters, before parsing. The existing handler performs string coercion and trim
  before calling it; arbitrary user-defined coercion costs are not bounded by this cap.
- IPv6 bracket parsing uses the standard library; address case is folded while the
  zone spelling after `%` is preserved. Non-ASCII host letters are not case-folded.
- STOPPED events without a path and its exact stored-signature fallback retain their
  legacy behavior. This repair does not introduce a generation ID or redesign that
  lifecycle policy. Equal URLs alone do not distinguish successive sessions of one URL.

## Architecture and preservation

The new module owns the independently testable identity boundary. It is not a wrapper
around a new service and performs no I/O. The existing model is 448 lines and the
video-handler module already has a 657-line legacy allowance; placing the comparison
in either would expand those unrelated responsibilities or the allowance. No policy
is relaxed: the handler remains 657 lines, all other functions and its attachment
roster remain unchanged, and the new comparison functions fit the existing limits.
No native, decoder, DSP, event-bus, dependency, lockfile or CI change is made.

## Verification and interpretation

Run `python -B -m pytest -p no:cacheprovider tests/test_stream_path_identity.py` from a
fresh source extraction in an existing suitable test environment. The tests use the
real matcher, handler, state manager and synchronous AudioEventBus. Stop/publish
receivers record effects. READY controls reach an explicitly NOT-ready receiver;
no successful COM adapter, network response or physical playback is simulated.
The original 18-case external 08H regression is retained for the before/after check.
All run-specific results, original failures, inventories and package checks are in
the external REPORT and evidence; this document is not a native Windows acceptance.

Primary mechanism references consulted 24 September 2026:
https://www.rfc-editor.org/rfc/rfc3986#section-6.2.2.1
https://docs.python.org/3.12/library/urllib.parse.html#url-parsing-security
The references explain URI case and parser limits; project results derive from tests.
