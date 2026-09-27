from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


import pytest


@pytest.fixture(autouse=True)
def _progress_control_test_capabilities(monkeypatch):
    """Only extend existing fake widgets; application/native imports stay untouched."""
    from tests.gesture_fakes import install_progress_fakes
    install_progress_fakes(monkeypatch)


# Reserve a UTF-16 terminator within the documented Windows variable value limit.
_CURRENT_TEST_MAX_UNITS = 32766


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Reject overlong diagnostics before setup; never truncate payloads or drop cases.

    The teardown suffix is the longest pytest phase. UTF-16 supplementary characters
    count twice, and the full nodeid (not just the parameter) must fit on Windows.
    """
    for item in items:
        value = item.nodeid + ' (teardown)'
        units = len(value.encode('utf-16-le', errors='surrogatepass')) // 2
        if units > _CURRENT_TEST_MAX_UNITS:
            name = item.nodeid[:160]
            raise pytest.UsageError(
                f'PYTEST_CURRENT_TEST exceeds Windows budget ({units} UTF-16 units): '
                f'{name!r}. Add an explicit short parameter id; keep test data unchanged.'
            )
