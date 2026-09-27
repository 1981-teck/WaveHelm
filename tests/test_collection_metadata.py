"""Collection guardrails concern metadata only, not hostile application inputs."""
from types import SimpleNamespace
import pytest
from tests.conftest import pytest_collection_modifyitems, _CURRENT_TEST_MAX_UNITS


def test_short_ids_preserve_items_and_large_parameter_payloads() -> None:
    payload = 'x' * 32769
    item = SimpleNamespace(nodeid='tests/test_input.py::test_input[over-limit]', payload=payload)
    items = [item]
    pytest_collection_modifyitems(items)
    assert items == [item] and items[0] is item
    assert len(item.payload) == 32769


@pytest.mark.parametrize('delta,accepted', [(-1, True), (0, True), (1, False)])
def test_teardown_suffix_and_terminator_are_included(delta: int, accepted: bool) -> None:
    nodeid = 'x' * (_CURRENT_TEST_MAX_UNITS - len(' (teardown)') + delta)
    items = [SimpleNamespace(nodeid=nodeid)]
    if accepted:
        pytest_collection_modifyitems(items)
    else:
        with pytest.raises(pytest.UsageError, match='explicit short parameter id'):
            pytest_collection_modifyitems(items)
    assert len(items) == 1 and items[0].nodeid == nodeid


def test_utf16_supplementary_characters_count_as_two_units() -> None:
    items = [SimpleNamespace(nodeid='\U0001f3b5' * 16400)]
    assert len(items[0].nodeid) < _CURRENT_TEST_MAX_UNITS
    with pytest.raises(pytest.UsageError, match='Windows budget'):
        pytest_collection_modifyitems(items)


def test_failure_diagnostic_is_bounded_and_does_not_rewrite_inventory() -> None:
    items = [SimpleNamespace(nodeid='first'), SimpleNamespace(nodeid='x' * 32769)]
    original = list(items)
    with pytest.raises(pytest.UsageError) as exc:
        pytest_collection_modifyitems(items)
    assert len(str(exc.value)) < 400
    assert items == original


def test_empty_inventory_is_not_filled_with_synthetic_cases() -> None:
    items = []
    pytest_collection_modifyitems(items)
    assert items == []
