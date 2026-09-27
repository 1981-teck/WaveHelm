# Sensitive log fields — step08C contract

23 September 2026. This maintenance change fixes 08B-F01 and 08B-F02 in the
managed logging boundary. It is defense in depth, not permission to log credentials
and not a universal data-loss-prevention or structured-JSON logger.

## Covered fields and syntax

The existing field names are password, api_key, token and secret, matched
case-insensitively as complete names, followed by `:` or `=`. Names may be
unquoted, single-quoted or double-quoted. Substrings such as `token_count`,
`mypassword` and prose such as `password reset requested` are not field matches.

Values may be quoted strings (with backslash escapes), balanced bracket/brace/
parenthesis containers, or unquoted text. Unquoted values extend to a comma,
semicolon, ampersand, closing bracket/brace/parenthesis or line ending. This
conservative choice can remove ambiguous trailing prose: use quotes around values
or delimit fields explicitly to preserve unrelated following diagnostics.
Unterminated quotes/containers conceal the remaining input segment, and malformed
adjacent suffixes are concealed with their value. Empty values are marked as well.

Each recognized value becomes `***REDACTED***`; field names remain meaningful.
For quoted values their outer quote character is retained. Container replacement
does not promise to preserve machine-readable JSON structure. Unicode values,
escaped quote characters, repeated fields and repeated filtering are tested.

Examples:
- `token=example, count=3` becomes `token=***REDACTED***, count=3`.
- `PASSWORD="two words"; operation=load` conceals both words.
- Positional `token=%s` and mapping `token=%(token)s` are formatted normally first.
- `{"token": "example", "count": 3}` conceals the sensitive string.

## Formatting and record ownership

SensitiveDataFilter first calls LogRecord.getMessage, which resolves the normal
positional/mapping format. It then changes `record.msg` and `record.args` together:
the message is sanitized text and args is an empty tuple. An existing message
cache is synchronized. It still returns True so the sanitized diagnostic is emitted.

Caller-owned argument tuples, dictionaries and nested values are not modified.
The original record's severity, source location, timestamp and exception object
are preserved. The record itself is intentionally normalized; callers must not
expect to recover the raw message/args from it after a managed handler processes it.
The declared key table is unchanged and compiled per filter construction.

Both the rotating file handler and the console handler explicitly install the
filter. Console protection no longer depends on the file handler running first.
No root-logger-only filter is substituted for handler-level protection.

A private formatter delegates the existing message/time/level formatting on a
shallow record copy, then sanitizes the final rendered text. This covers labelled
fields introduced by a formatter, exception text and stack text as exercised by
the tests. The original exception text cache and exception object are not mutated.

For the standard traceback formatter, traceback chunks are sanitized individually
before the final formatting pass, so an unterminated field in a source-code snippet
cannot conceal later frames. Ordinary exception chains, exception groups and
trailing-newline behavior retain standard formatting in regression tests. Custom
formatException overrides remain invoked; their returned text is sanitized.
The traceback formatter still does not capture locals.

## Explicit failure and resource policy

An invalid format, mapping key, argument conversion or message conversion produces
`LOG_FORMAT_ERROR` without echoing the raw template, arguments or failure message.
A failing custom formatter produces a separate explicit formatter-failed diagnostic.
These are omission/error diagnostics, not success status or an empty dropped record.

The two generic Exception handlers are deliberate dynamic logging/formatter
boundaries. They never recurse into logging, and they do not catch BaseException:
KeyboardInterrupt and SystemExit continue to propagate. Handler I/O errors retain
their pre-existing behavior; this change does not claim reliable delivery if a
stream, filesystem or operating system fails.

Each sanitizer input and result has a 65,536-character budget. Oversized text is
replaced by `LOG_REDACTION_LIMIT`, never by an unredacted truncated prefix. This is
not a bound on allocations or side effects inside arbitrary user __str__, formatter
callbacks or traceback construction, which necessarily occur before text inspection.

## Scope exclusions and compatibility

There is no guarantee for unlabelled secrets, unknown credential field names,
encoded/escaped key spellings, arbitrary binary data, direct print/stderr writes,
external handlers configured without this boundary, or formatter fields rendered
without their sensitive label. Quoting/delimiters are syntax, not semantic proof:
ambiguous text may be conservatively over-redacted. Do not log actual credentials.

Raw traceback/argument objects remain owned by the caller; this patch protects the
text emitted by the managed handlers, not every in-memory reference or diagnostic
produced outside them. Existing files written before this patch are not scrubbed.
No user log or credential was searched; regression markers are synthetic only.

Logging levels, ordinary messages, timestamps, rotation thresholds, backup count,
primary/PID/workspace fallback paths, atexit registration and shutdown pruning are
unchanged. The bootstrap consumer is unchanged. No native COM, audio/DSP, dependency,
lockfile, version or CI change accompanies this patch. Prior Windows gates remain
closed in their original scope; no new Windows GUI or device qualification is implied.

## Reproduction

Run the existing logging/bootstrap tests and the two new modules in an isolated copy:

```text
python -B -m pytest -p no:cacheprovider tests/test_logging_config.py tests/test_runtime_bootstrap.py tests/test_sensitive_logging.py tests/test_sensitive_logging_handlers.py
```

The external evidence contains the unchanged nine 08B reproduction cases, before
and after runs, actual raw logs/receipts/JUnit, package/replay checks and limitations.
Successful local boundary tests are not complete security, typing or release approval.

Reference for the unchanged Python API mechanism:
https://docs.python.org/3.12/library/logging.html
