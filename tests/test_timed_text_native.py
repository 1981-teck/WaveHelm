"""Native subprocess gate: actual Media Engine operations, never a mock PASS."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _run_probe(output: Path) -> tuple[int, str]:
    command = [sys.executable, '-I', '-B', str(ROOT / 'tools/probe_timed_text_native.py'),
               '--output', str(output)]
    try:
        run = subprocess.run(command, capture_output=True, timeout=120, check=False)
        code, stdout, stderr = run.returncode, run.stdout, run.stderr
    except subprocess.TimeoutExpired as exc:
        code, stdout, stderr = -1, exc.stdout or b'', exc.stderr or b''
        stderr += b'\nNATIVE PROBE TIMEOUT (direct child killed by subprocess.run).'
    output.mkdir(parents=True, exist_ok=True)
    (output / 'stdout.log').write_bytes(stdout)
    (output / 'stderr.log').write_bytes(stderr)
    record = {'command': command, 'exit_code': code, 'timeout_seconds': 120}
    (output / 'process.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    inventory = {p.name: {'bytes': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                 for p in sorted(output.iterdir()) if p.is_file() and p.name != 'manifest.json'}
    (output / 'manifest.json').write_text(json.dumps(inventory, indent=2), encoding='utf-8')
    return code, stdout.decode('utf-8', errors='replace') + stderr.decode('utf-8', errors='replace')


@pytest.mark.skipif(sys.platform != 'win32', reason='Real Windows Media Engine service, no replacement implementation')
def test_real_media_engine_timed_text_controls_in_separate_process(tmp_path) -> None:
    base = Path(os.environ.get('WAVEHELM_NATIVE_EVIDENCE_DIR', str(tmp_path)))
    output = base / ('timed-text-' + uuid.uuid4().hex[:12])
    code, logs = _run_probe(output)
    assert code == 0, logs
    report = json.loads((output / 'probe.json').read_text(encoding='utf-8'))
    assert report['status'] == 'NATIVE_TIMED_TEXT_CONTROL_PASS'
    assert report['source_unchanged'] is True
    assert report['diagnostic_failures'] == 0, logs
    diagnostics = json.loads((output / 'diagnostics.json').read_text(encoding='utf-8'))
    assert diagnostics == {'failure_count': 0, 'records': [], 'omitted_count': 0}
    assert 'Traceback (most recent call last)' not in logs, logs
    assert report['media_file_loaded'] is False
    assert report['steps'] == [
        'production_sta_started', 'production_media_engine_created_on_sta',
        'real_timed_text_service_acquired', 'two_native_tracks_added',
        'native_metadata_copied_and_task_memory_released',
        'both_native_tracks_selected_and_read_back', 'native_disable_confirmed',
        'native_tracks_removed', 'core_shutdown_returned_and_worker_terminated',
    ]


@pytest.mark.skipif(sys.platform == 'win32', reason='Foreign-platform refusal test only')
def test_foreign_native_probe_retains_failure_not_pass(tmp_path) -> None:
    code, logs = _run_probe(tmp_path / 'unsupported')
    report = json.loads((tmp_path / 'unsupported/probe.json').read_text(encoding='utf-8'))
    assert code == 1
    assert report['status'] == 'FAIL'
    assert report['steps'] == []
    assert 'Windows x64 is required' in logs


def test_probe_adapter_exposes_default_wic_contract() -> None:
    from tools.probe_timed_text_native import ProbeAdapter
    for name in ("submit_frame_to_com_thread", "frame_ready", "frame_pump_ready"):
        assert callable(getattr(ProbeAdapter, name, None)), name


def test_native_probe_source_inventory_covers_new_hooks_and_called_ownership() -> None:
    from tools.probe_timed_text_native import SOURCE_PATHS, source_seals
    seals = source_seals()
    assert len(seals) == len(SOURCE_PATHS)
    assert 'src/video/media_engine_timed_text_backend.py' in seals
    assert 'src/video/media_engine_core_timed_text.py' in seals
    assert 'src/video/component_base/iid_registry.py' in seals
    assert 'src/video/media_engine_seek_events.py' in seals
    assert 'src/video/component_adapter/com_thread_manager_api.py' in seals
    assert 'src/video/seek_receipt.py' in seals
    for entry in seals.values():
        assert len(entry['sha256']) == 64 and entry['bytes'] > 0


def test_probe_cleanup_attempts_all_owned_track_releases(monkeypatch) -> None:
    from types import SimpleNamespace
    from tools.probe_timed_text_native import _remove_tracks
    from src.video import media_engine_timed_text_backend as backend
    attempts = []
    service = SimpleNamespace(contents=SimpleNamespace(lpVtbl=SimpleNamespace(contents=SimpleNamespace(RemoveTrack=lambda s, t: 0))))
    def release(owner, context):
        attempts.append(owner)
        if owner == 1:
            raise backend.TimedTextBackendError('injected release error')
    monkeypatch.setattr(backend, 'release_owned', release)
    with pytest.raises(RuntimeError, match='injected release error'):
        _remove_tracks(service, [1, 2])
    assert attempts == [1, 2]
