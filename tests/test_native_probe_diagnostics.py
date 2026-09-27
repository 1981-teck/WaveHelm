"""Probe contracts tested with injected operations, never native qualification.

Missing ownership must not hide callbacks; logging failures must veto a probe PASS;
benign warnings stay nonblocking and diagnostic retention remains bounded.
"""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest

from tools import probe_timed_text_native as probe
from src.video.media_engine_seek_events import BoundSeekEvents, create_bound_notify


@pytest.fixture
def adapter():
    value = probe.ProbeAdapter()
    try:
        yield value
    finally:
        # This fixture never starts a native worker or acquires a COM object.
        value._core._shutdown_requested = True


def test_constructed_probe_has_exact_current_owner_for_creation_callback(adapter) -> None:
    handler = create_bound_notify(adapter._core, adapter, lambda owner: SimpleNamespace())
    handler._wavehelm_seek_event_owner.on_media_engine_event(6, 7, 9)
    assert list(adapter.events) == [(6, 7, 9)]
    assert not adapter._closed and not adapter._shutdown_requested
    assert adapter._core._adapter_ref() is adapter
    assert adapter.manager._com_thread is None


@pytest.mark.parametrize('field', ['_closed', '_shutdown_requested', '_core'])
def test_probe_ownership_loss_rejects_callback_without_exceptions(adapter, field, caplog) -> None:
    core = adapter._core
    bound = BoundSeekEvents(core, adapter, 1)
    if field == '_core':
        adapter._core = None
    else:
        setattr(adapter, field, True)
    try:
        bound.on_media_engine_event(5, 0, 0)
        assert list(adapter.events) == []
        assert not [r for r in caplog.records if r.exc_info]
    finally:
        adapter._core = core


def test_old_missing_core_contract_is_detected_at_real_callback_boundary(adapter) -> None:
    core = adapter._core
    bound = BoundSeekEvents(core, adapter, 1)
    diagnostics = probe.ProbeDiagnostics()
    root = logging.getLogger()
    root.addHandler(diagnostics)
    del adapter._core
    try:
        bound.on_media_engine_event(5, 0, 0)
        facts = diagnostics.snapshot()
        assert facts['failure_count'] == 1
        assert facts['records'][0]['exception_type'] == 'AttributeError'
        assert list(adapter.events) == []
    finally:
        adapter._core = core
        root.removeHandler(diagnostics)
        diagnostics.close()


def emit(kind: str) -> None:
    logger = logging.getLogger('src.video.media_engine_seek_events')
    if kind == 'exception':
        try:
            raise AttributeError("'ProbeAdapter' object has no attribute '_core'")
        except AttributeError:
            logger.warning('Native seek notification could not be recorded.', exc_info=True)
    elif kind == 'error':
        logger.error('Native worker failed without a traceback')
    elif kind == 'benign':
        logger.warning('Compatibility export warning without an exception')
    elif kind == 'raise':
        raise RuntimeError('Injected native probe failure')


@pytest.mark.parametrize('kind,code,count', [
    ('exception', 1, 1), ('error', 1, 1), ('benign', 0, 0), ('clean', 0, 0), ('raise', 1, 0),
])
def test_main_preserves_diagnostics_and_never_passes_after_native_error(monkeypatch, tmp_path,
                                                                      kind, code, count) -> None:
    output = tmp_path / 'probe-output'
    def operation(path, steps):
        assert path == output
        steps.append('test-owned operation; not native execution')
        emit(kind)
    monkeypatch.setattr(probe.sys, 'argv', ['probe', '--output', str(output)])
    monkeypatch.setattr(probe.sys, 'platform', 'win32')
    monkeypatch.setattr(probe, 'run_probe', operation)
    before_handlers = list(logging.getLogger().handlers)
    assert probe.main() == code
    assert logging.getLogger().handlers == before_handlers
    report = json.loads((output / 'probe.json').read_text(encoding="utf-8"))
    facts = json.loads((output / 'diagnostics.json').read_text(encoding="utf-8"))
    assert facts['failure_count'] == report['diagnostic_failures'] == count
    expected = 'NATIVE_TIMED_TEXT_CONTROL_PASS' if code == 0 else 'FAIL'
    assert report['status'] == expected
    assert (output / 'traceback.txt').exists() is (code == 1)
    assert report['media_file_loaded'] is False


def test_bounded_diagnostics_keep_total_and_do_not_retain_exception_objects() -> None:
    handler = probe.ProbeDiagnostics()
    record = logging.LogRecord('src.video.native', logging.ERROR, '', 1, '%s', ('x' * 5000,), None)
    try:
        for _ in range(100):
            handler.handle(record)
        facts = handler.snapshot()
        assert facts['failure_count'] == 100 and facts['omitted_count'] == 92
        assert len(facts['records']) == 8
        assert all(len(item['message']) == 1024 for item in facts['records'])
        json.dumps(facts, allow_nan=False)
        facts['records'][0]['message'] = 'changed snapshot only'
        assert handler.snapshot()['records'][0]['message'] == 'x' * 1024
    finally:
        handler.close()


def test_unrelated_logger_is_not_mislabeled_a_native_callback_error() -> None:
    handler = probe.ProbeDiagnostics()
    try:
        handler.handle(logging.LogRecord('unrelated', logging.ERROR, '', 1, 'other', (), None))
        assert handler.snapshot()['failure_count'] == 0
    finally:
        handler.close()


def test_malformed_diagnostic_format_cannot_hide_the_error() -> None:
    handler = probe.ProbeDiagnostics()
    try:
        handler.handle(logging.LogRecord('src.video.native', logging.ERROR, '', 1, '%d', ('bad',), None))
        assert handler.snapshot()['failure_count'] == 1
        assert 'formatting failed' in handler.snapshot()['records'][0]['message']
    finally:
        handler.close()


def test_late_diagnostic_before_capture_closes_cannot_preserve_pass(monkeypatch, tmp_path) -> None:
    original = probe.ProbeDiagnostics.snapshot
    calls = []
    def observed(handler):
        result = original(handler)
        if not calls:
            calls.append(True)
            handler.handle(logging.LogRecord('src.video.native', logging.ERROR, '', 1,
                                              'late recorded error', (), None))
        return result
    output = tmp_path / 'late-output'
    monkeypatch.setattr(probe.ProbeDiagnostics, 'snapshot', observed)
    monkeypatch.setattr(probe, 'run_probe', lambda path, steps: None)
    monkeypatch.setattr(probe.sys, 'platform', 'win32')
    monkeypatch.setattr(probe.sys, 'argv', ['probe', '--output', str(output)])
    assert probe.main() == 1
    report = json.loads((output / 'probe.json').read_text(encoding="utf-8"))
    assert report['status'] == 'FAIL' and report['diagnostic_failures'] == 1


def test_native_error_event_fails_gate_even_if_it_rolls_out_of_event_ring(adapter) -> None:
    diagnostics = probe.ProbeDiagnostics()
    root = logging.getLogger()
    root.addHandler(diagnostics)
    try:
        adapter.on_media_engine_event(5, 3, 0x80004005)
        for _ in range(140):
            adapter.on_media_engine_event(6, 0, 0)
        assert len(adapter.events) == 128
        assert all(event[0] != 5 for event in adapter.events)
        assert diagnostics.snapshot()['failure_count'] == 1
        assert 'ERROR notification' in diagnostics.snapshot()['records'][0]['message']
    finally:
        root.removeHandler(diagnostics)
        diagnostics.close()
