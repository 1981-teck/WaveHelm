from __future__ import annotations

import inspect
from typing import get_type_hints

import pytest

from src.utils.helpers import format_file_size


@pytest.mark.parametrize(
    ("size_bytes", "expected"),
    [
        pytest.param(0, "0 B", id="zero"),
        pytest.param(1, "1 B", id="one-byte"),
        pytest.param(1023, "1023 B", id="below-kb"),
        pytest.param(1024, "1.00 KB", id="exact-kb"),
        pytest.param(1025, "1.00 KB", id="above-kb"),
        pytest.param(1536, "1.50 KB", id="fractional-kb"),
        pytest.param(1024**2 - 1, "1024.00 KB", id="below-mb-rounded"),
        pytest.param(1024**2, "1.00 MB", id="exact-mb"),
        pytest.param(1024**2 + 1, "1.00 MB", id="above-mb"),
        pytest.param(3 * 1024**2 // 2, "1.50 MB", id="fractional-mb"),
        pytest.param(1024**3 - 1, "1024.00 MB", id="below-gb-rounded"),
        pytest.param(1024**3, "1.00 GB", id="exact-gb"),
        pytest.param(1024**3 + 1, "1.00 GB", id="above-gb"),
        pytest.param(3 * 1024**3 // 2, "1.50 GB", id="fractional-gb"),
        pytest.param(1024**4 - 1, "1024.00 GB", id="below-tb-rounded"),
        pytest.param(1024**4, "1.00 TB", id="exact-tb"),
        pytest.param(1024**4 + 1, "1.00 TB", id="above-tb"),
        pytest.param(5 * 1024**4 // 4, "1.25 TB", id="fractional-tb"),
        pytest.param(3 * 1024**4 // 2, "1.50 TB", id="one-and-half-tb"),
        pytest.param(2 * 1024**4, "2.00 TB", id="two-tb"),
        pytest.param(1024**5 - 1, "1024.00 TB", id="below-pb-still-tb"),
        pytest.param(1024**5, "1024.00 TB", id="no-new-pb-unit"),
        pytest.param(1024**6, "1048576.00 TB", id="large-value-in-tb"),
        pytest.param(2**63 - 1, "8388608.00 TB", id="signed-64-bit-size"),
    ],
)
def test_format_file_size_unit_boundaries(size_bytes: int, expected: str) -> None:
    assert format_file_size(size_bytes) == expected
    assert format_file_size(size_bytes=size_bytes) == expected


def test_format_file_size_public_signature_is_preserved() -> None:
    signature = inspect.signature(format_file_size)
    assert list(signature.parameters) == ["size_bytes"]
    parameter = signature.parameters["size_bytes"]
    assert parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert parameter.default is inspect.Parameter.empty
    assert get_type_hints(format_file_size) == {"size_bytes": int, "return": str}
    assert format_file_size.__module__ == "src.utils.helpers"


@pytest.mark.parametrize(
    ("size_bytes", "expected"),
    [
        pytest.param(-1, "-1 B", id="negative-legacy"),
        pytest.param(False, "False B", id="false-legacy"),
        pytest.param(True, "True B", id="true-legacy"),
        pytest.param(1.5, "1.5 B", id="fractional-byte-legacy"),
        pytest.param(float("nan"), "nan TB", id="nan-legacy"),
        pytest.param(float("inf"), "inf TB", id="infinity-legacy"),
    ],
)
def test_format_file_size_does_not_expand_input_policy(size_bytes, expected) -> None:
    # Characterize incidental legacy behavior, not endorsement of invalid byte counts.
    assert format_file_size(size_bytes) == expected


@pytest.mark.parametrize(
    ("size_bytes", "exception"),
    [
        pytest.param(None, TypeError, id="none"),
        pytest.param("1024", TypeError, id="text"),
        pytest.param(b"1024", TypeError, id="bytes"),
        pytest.param(1 + 2j, TypeError, id="complex"),
        pytest.param(10**400, OverflowError, id="float-overflow-legacy"),
    ],
)
def test_format_file_size_preserves_existing_error_policy(size_bytes, exception) -> None:
    with pytest.raises(exception):
        format_file_size(size_bytes)
