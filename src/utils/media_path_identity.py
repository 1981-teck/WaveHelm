"""Conservative, I/O-free identity for playback event paths.

Local identifiers retain the historical trimmed, case-insensitive comparison.
Stream URLs fold ASCII scheme/host case only; resource data is never case-folded.
This is an event-correlation boundary, not a URL admission or filesystem resolver.

Edge cases: path/query case can identify distinct streams; invalid authorities or
control characters must not gain equivalence through parser cleanup; oversized
identifiers are rejected before parsing. Unknown URI schemes compare exactly.
"""
from __future__ import annotations

import re
from typing import Final
from urllib.parse import urlsplit

_MAX_IDENTITY_CHARS: Final = 65_536
_STREAM_SCHEMES: Final = frozenset({"http", "https", "rtsp", "rtmp"})
_SCHEME: Final = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*):")
_ASCII_LOWER: Final = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def _fold_authority(authority: str) -> str:
    """Fold only the host; retain userinfo, port spelling and IPv6 zone spelling.

    Called after urlsplit host/port checks. Bracketed hosts, userinfo containing
    colons, and case-sensitive zone identifiers must not change the resource key.
    """
    userinfo, separator, host_port = authority.rpartition("@")
    if host_port.startswith("["):
        closing = host_port.index("]")
        address, zone_sep, zone = host_port[1:closing].partition("%")
        host = "[" + address.translate(_ASCII_LOWER) + zone_sep + zone + "]"
        tail = host_port[closing + 1:]
        if tail and not tail.startswith(":"):
            raise ValueError("Unexpected text after a bracketed host")
    else:
        host, colon, port = host_port.partition(":")
        host = host.translate(_ASCII_LOWER)
        tail = colon + port
    return userinfo + separator + host + tail


def _stream_key(value: str, scheme: str) -> str | None:
    """Return a minimally normalized stream key, or refuse an unsafe parse.

    Reject missing hosts, invalid ports/brackets and embedded ASCII whitespace,
    controls, backslashes or surrogates. Keep empty query/fragment delimiters and
    percent escapes exactly; do not reconstruct resource data from parsed fields.
    """
    if not value[len(scheme):].startswith("://"):
        return None
    if any(ord(char) <= 32 or ord(char) == 127 or char == "\\" or
           0xD800 <= ord(char) <= 0xDFFF for char in value):
        return None
    try:
        parts = urlsplit(value)
        if not parts.netloc or not parts.hostname:
            return None
        # Access validates the original port without normalizing its spelling.
        _ = parts.port
        authority = _fold_authority(parts.netloc)
    except ValueError:
        return None
    suffix = value[len(scheme) + 3 + len(parts.netloc):]
    return scheme + "://" + authority + suffix


def _comparison_key(value: str) -> tuple[str, str] | None:
    """Bound work and distinguish drive paths, supported streams and opaque URIs.

    Empty/non-text/oversized inputs have no key. Drive-relative Windows paths
    remain local. Unknown schemes gain no implicit path, host or case equivalence.
    """
    if not isinstance(value, str) or len(value) > _MAX_IDENTITY_CHARS:
        return None
    value = value.strip()
    if not value:
        return None
    match = _SCHEME.match(value)
    if match is None or len(match.group(1)) == 1:
        return "local", value.lower()
    scheme = match.group(1).translate(_ASCII_LOWER)
    if scheme not in _STREAM_SCHEMES:
        return "opaque", value
    key = _stream_key(value, scheme)
    return None if key is None else ("stream", key)


def same_media_path(expected: str, candidate: str) -> bool:
    """Compare without opening media or inventing redirect/decoded equivalence.

    Invalid inputs never match, even each other. Supported stream path/query,
    fragment, userinfo and port spelling remain exact. Local slash spelling and
    relative-vs-absolute distinctions retain their existing behavior.
    """
    expected_key = _comparison_key(expected)
    return expected_key is not None and expected_key == _comparison_key(candidate)
