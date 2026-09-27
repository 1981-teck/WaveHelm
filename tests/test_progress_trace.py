"""Optional numeric ring, close-time I/O and explicit omission budgets."""
import json
from dataclasses import replace
from pathlib import Path
import pytest
from src.ui_wx.progress_trace import ProgressTrace, CAPACITY, MAX_FILES, FIELDS
from src.ui_wx.playback_presentation import DisplayReading
from tests.cursor_chain_fakes import ui, rig, tick


def test_disabled_trace_does_not_allocate_ring_or_write(tmp_path, monkeypatch):
    monkeypatch.delenv('WAVEHELM_PROGRESS_TRACE_DIR', raising=False)
    trace = ProgressTrace('mini')
    assert trace._rows == [] and trace.directory is None
    trace.record(None, None, DisplayReading(), 1., current=False, painted=False, reason='test')
    trace.close()
    assert list(tmp_path.iterdir()) == []


def test_ring_is_bounded_and_only_close_writes(tmp_path, monkeypatch):
    monkeypatch.setenv('WAVEHELM_PROGRESS_TRACE_DIR', str(tmp_path))
    trace = ProgressTrace('mini')
    for n in range(CAPACITY + 50):
        trace.record(None, None, DisplayReading(), float(n), current=False, painted=False, reason='test')
    assert len(trace._rows) == CAPACITY and list(tmp_path.iterdir()) == []
    trace.close(); trace.close()
    files = list(tmp_path.glob('progress-mini-*.json'))
    assert len(files) == 1
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data['total'] == CAPACITY + 50 and data['omitted'] == 50
    assert len(data['records']) == CAPACITY and data['records'][0][0] == 50.
    assert data['records'][-1][0] == CAPACITY + 49.


def test_folder_budget_refuses_additional_files(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv('WAVEHELM_PROGRESS_TRACE_DIR', str(tmp_path))
    for n in range(MAX_FILES):
        (tmp_path / f'progress-{n}.json').write_text('{}', encoding="utf-8")
    trace = ProgressTrace('mini'); trace.close()
    assert len(list(tmp_path.iterdir())) == MAX_FILES
    assert 'could not be saved' in caplog.text


@pytest.mark.parametrize('kind', ['relative', 'absent', 'project'])
def test_invalid_trace_destination_is_disabled(tmp_path, monkeypatch, kind):
    root = Path(__file__).resolve().parents[1]
    path = 'relative-dir' if kind == 'relative' else str(root if kind == 'project' else tmp_path / 'absent')
    monkeypatch.setenv('WAVEHELM_PROGRESS_TRACE_DIR', path)
    trace = ProgressTrace('mini'); trace.close()
    assert trace.directory is None


def test_actual_widgets_trace_measured_hold_and_preview_without_media_paths(ui, tmp_path, monkeypatch):
    r = ui
    monkeypatch.setenv('WAVEHELM_PROGRESS_TRACE_DIR', str(tmp_path))
    r.widget._presentation._trace = ProgressTrace(r.surface)
    r.widget._presentation.repaint()
    r.widget._seek_from_progress_ratio(.8)
    r.widget.close()
    data = json.loads(next(tmp_path.glob('progress-*.json')).read_text(encoding="utf-8"))
    records = [dict(zip(data['fields'], row)) for row in data['records']]
    assert any(row['reason'] == 'gesture-preview' and row['drawn_ratio'] == .8 for row in records)
    assert any(row['seek_phase'] == 'QUEUED' and row['measured_position'] is None
               and row['drawn_ratio'] == .8
               and row['reason'] == 'seek-preview-pending' for row in records)
    assert 'clip.mp4' not in json.dumps(data)
