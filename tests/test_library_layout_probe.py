"""Independent rectangle acceptance and explicit native-probe refusal."""
from __future__ import annotations

import json

import pytest

from tools import probe_library_layout as probe


@pytest.mark.parametrize('rect', [[-1, 0, 10, 10], [0, -1, 10, 10], [95, 0, 10, 10],
                                  [0, 95, 10, 10], [0, 0, 0, 10], [0, 0, 10, 0]])
def test_probe_refuses_outside_or_empty_control(rect):
    assert probe.rectangle_findings((100, 100), {'filter': rect}) == ['out-of-client:filter']


def test_probe_accepts_right_bottom_boundary_and_adjacent_controls():
    assert not probe.rectangle_findings((100, 100), {'a': [0, 0, 50, 100], 'b': [50, 0, 50, 100]})


def test_probe_refuses_overlap_even_within_client():
    assert probe.rectangle_findings((100, 100), {'a': [0, 0, 60, 10], 'b': [50, 0, 50, 10]}) == ['overlap:a:b']


def test_non_windows_run_is_retained_as_failure_not_acceptance(monkeypatch, tmp_path):
    monkeypatch.setattr(probe.sys, 'platform', 'linux')
    assert probe._worker(tmp_path) == 1
    result = json.loads((tmp_path / 'geometry.json').read_text(encoding='utf-8'))
    assert result['status'] == 'FAIL'
    assert result['cases'] == []
    assert 'not executed' in result['exception']


def test_successful_child_exit_without_geometry_is_not_pass(monkeypatch, tmp_path):
    from types import SimpleNamespace
    monkeypatch.setattr(probe.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0))
    assert probe.main(['--output', str(tmp_path)]) == 1
    result = json.loads((tmp_path / 'command.json').read_text(encoding='utf-8'))
    assert result['child_exit_code'] == 0 and result['exit_code'] == 1


def test_probe_will_not_overwrite_previous_evidence(tmp_path):
    (tmp_path / 'geometry.json').write_text('prior evidence', encoding='utf-8')
    with pytest.raises(SystemExit):
        probe.main(['--output', str(tmp_path)])
    assert (tmp_path / 'geometry.json').read_text(encoding='utf-8') == 'prior evidence'
