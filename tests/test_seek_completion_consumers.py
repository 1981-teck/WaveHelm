"""Real EOS/view/GUI consumers; no callbacks or targets count as measured clocks."""
from dataclasses import replace
from types import SimpleNamespace
import pytest

from tests.test_seek_completion_receipt import clock, started, reply, events
from tests.test_progress_snapshot import setup
from tests.test_playback_presentation import setup_presentation
from tests.test_playback_view import make_sample
from src.controller.seek_observation import read_seek_observation
from src.controller.component_player.playback_state_manager import PlayerState
from src.controller.video_controller_transport import poll_end
from src.video.seek_receipt import SeekPhase as P, NativeSeekResult
from src.playback_observation import ReadingStatus


def pending(backend):
    slot, operation = started()
    reply(operation)
    backend.get_seek_receipt = operation.snapshot
    return slot, operation


@pytest.mark.parametrize('stage', ['queued', 'accepted', 'seeking', 'expired', 'cancelled', 'unknown-error'])
def test_pending_seek_samples_clock_but_cannot_advance(stage, clock):
    tracker, backend, published, ends = setup()
    slot, operation = pending(backend)
    if stage == 'queued':
        slot = type(slot)(); operation = slot.reserve('clip.mp4', 1, 0, 2.0)
        operation.mark_queued(); backend.get_seek_receipt = operation.snapshot
    if stage == 'seeking': operation.observe_event(16, 1, 0, 'clip.mp4')
    if stage == 'expired': clock[0] = 111.0
    if stage == 'cancelled': slot.invalidate('stop')
    if stage == 'unknown-error': backend.get_seek_receipt = lambda: 42
    backend.ended = True
    for _ in range(3):
        assert tracker._poll_once() is False
    assert backend.reads == 3 and len(published) == 3 and ends == []


def test_completed_seek_allows_native_eos_without_time_fallback(clock):
    tracker, backend, published, ends = setup()
    _, operation = pending(backend)
    backend.ended = True
    assert not tracker._poll_once()
    events(operation)
    assert tracker._poll_once()
    assert ends == [True]


def test_receipt_failure_before_insertion_does_not_freeze_natural_end(clock):
    tracker, backend, published, ends = setup()
    _, operation = started()
    operation.put_nowait(NativeSeekResult(P.FAILED, 0x80004005))
    backend.get_seek_receipt = operation.snapshot
    backend.ended = True
    assert tracker._poll_once() and ends == [True]


@pytest.mark.parametrize('completed', [False, True])
def test_paused_seek_pumps_and_samples_but_never_ends(completed, clock):
    tracker, backend, published, ends = setup()
    _, operation = pending(backend)
    if completed: events(operation)
    pump = []
    backend.pump_pending_events = lambda: pump.append(True) or True
    tracker.state_manager.update_state(PlayerState.PAUSED_VIDEO)
    backend.ended = True
    assert not tracker._poll_once()
    assert pump == [True] and backend.reads == 1 and backend.ends == 0
    assert tracker.get_progress_snapshot().state == 'PAUSED_VIDEO' and not ends


def test_paused_seek_error_pump_failure_is_not_success(clock):
    tracker, backend, published, ends = setup()
    pending(backend)
    backend.pump_pending_events = lambda: False
    tracker.state_manager.update_state(PlayerState.PAUSED_VIDEO)
    assert not tracker._poll_once()
    assert backend.reads == 0 and not ends


def test_real_controller_pumps_native_errors_even_when_seek_blocks(clock):
    pump, consumed, closed = [], [], []
    _, operation = started(); reply(operation)
    adapter = SimpleNamespace(pump_events=lambda: pump.append('error processed'),
                              has_ended=lambda: consumed.append(True) or True)
    controller = SimpleNamespace(_adapter=adapter, _shutting_down=False,
        _current_path='clip.mp4', get_seek_receipt=operation.snapshot,
        _loop_enabled=False, _publish_event=lambda *a, **k: None,
        get_position=lambda: 2.0, get_duration=lambda: 10.0,
        _close_adapter_internal=lambda: closed.append(True))
    assert not poll_end(controller)
    assert pump == ['error processed'] and consumed == [] and closed == []
    events(operation)
    assert poll_end(controller)
    assert len(pump) == 2 and consumed == [True] and closed == [True]


def test_pump_replacement_never_consumes_new_engine_end(clock):
    seen = []
    newer = SimpleNamespace(has_ended=lambda: seen.append('new engine') or True)
    ctrl = SimpleNamespace(_shutting_down=False)
    def replace_adapter(): ctrl._adapter = newer
    ctrl._adapter = SimpleNamespace(pump_events=replace_adapter)
    assert not poll_end(ctrl) and seen == []


@pytest.mark.parametrize('value', [None, 0, False, {}, 'ok'])
def test_advertised_malformed_access_is_not_no_seek(value):
    backend = SimpleNamespace(get_seek_receipt=value)
    result = read_seek_observation(backend, 'clip.mp4')
    assert result.supported and not result.available and result.blocks_end


def test_failed_or_foreign_receipt_is_not_absence(clock):
    _, op = started()
    assert read_seek_observation(object(), 'clip.mp4').available
    assert not read_seek_observation(object(), 'clip.mp4').supported
    assert read_seek_observation(SimpleNamespace(get_seek_receipt=op.snapshot), 'other.mp4').blocks_end
    def fail(): raise OSError('unavailable')
    result = read_seek_observation(SimpleNamespace(get_seek_receipt=fail), 'clip.mp4')
    assert result.blocks_end and result.error_type == 'OSError'


def presentation(clock):
    player, samples, jobs, renders, now, presenter = setup_presentation()
    now[0] = clock[0]
    samples[0] = make_sample(player, stamp=clock[0])
    backend = SimpleNamespace()
    player.engine_controller = SimpleNamespace(video_controller=backend)
    presenter.refresh()
    return player, samples, renders, now, presenter, backend


def test_gui_waits_for_completed_event_and_post_completion_observation(clock):
    player, samples, renders, now, p, backend = presentation(clock)
    _, op = pending(backend)
    samples[0] = make_sample(player, position=8.0, sequence=2, stamp=clock[0])
    p.refresh()
    assert renders[-1][1].position == 4.0
    assert renders[-1][1].status is ReadingStatus.UNAVAILABLE
    assert renders[-1][1].seek_phase is P.NATIVE_ACCEPTED_UNCONFIRMED
    clock[0] = now[0] = 100.1; events(op); p.refresh()
    assert renders[-1][1].position == 4.0  # Old acquired sample cannot confirm 8.0.
    clock[0] = now[0] = 100.2
    samples[0] = make_sample(player, position=1.9, sequence=3, stamp=clock[0])
    p.refresh()
    assert renders[-1][1].position == 1.9  # Observed value, not the requested 2.0.
    assert renders[-1][1].seek_phase is P.NATIVE_COMPLETED
    assert renders[-1][1].status is ReadingStatus.KNOWN


@pytest.mark.parametrize('status', ['failed', 'expired', 'cancelled', 'malformed'])
def test_unknown_native_effect_never_becomes_a_target_position(status, clock):
    player, samples, renders, now, p, backend = presentation(clock)
    slot, op = pending(backend)
    if status == 'failed':
        # Unknown transport/native error has no proof of failure before side effect.
        slot, op = started(); op.put_nowait(RuntimeError('native result missing'))
        backend.get_seek_receipt = op.snapshot
    if status == 'expired': clock[0] = now[0] = 111.0
    if status == 'cancelled': slot.invalidate('closed')
    if status == 'malformed': backend.get_seek_receipt = lambda: False
    samples[0] = make_sample(player, position=8.0, sequence=2, stamp=clock[0])
    p.refresh()
    assert renders[-1][1].position == 4.0
    assert renders[-1][1].status is ReadingStatus.ERROR


def test_receipt_or_backend_change_during_cache_view_refuses_mixed_result(clock):
    player, samples, renders, now, p, backend = presentation(clock)
    _, op = pending(backend)
    def change():
        events(op)
        return samples[0]
    player.progress_tracker.get_progress_snapshot = change
    assert player.get_playback_view() is None


def test_failed_native_hresult_keeps_actual_clock_and_exposes_failed_phase(clock):
    player, samples, renders, now, p, backend = presentation(clock)
    _, op = started(); op.put_nowait(NativeSeekResult(P.FAILED, 0x80004005))
    backend.get_seek_receipt = op.snapshot
    p.refresh()
    assert renders[-1][1].position == 4.0 and renders[-1][1].seek_phase is P.FAILED


def test_seek_arriving_during_end_query_does_not_close_engine(clock):
    slot, operation = started(); reply(operation)
    current = [None]; closed = []
    def native_end():
        current[0] = operation.snapshot()
        return True
    ctrl = SimpleNamespace(_adapter=SimpleNamespace(pump_events=lambda: None, has_ended=native_end),
        _current_path='clip.mp4', _shutting_down=False, get_seek_receipt=lambda: current[0],
        _loop_enabled=False, _close_adapter_internal=lambda: closed.append(True),
        _publish_event=lambda *a, **k: None, get_position=lambda: 10.0, get_duration=lambda: 10.0)
    assert not poll_end(ctrl) and closed == []


def test_completed_seek_without_new_sample_is_explicitly_unavailable(clock):
    player, samples, renders, now, p, backend = presentation(clock)
    _, op = pending(backend); events(op)
    samples[0] = None; p.refresh()
    assert renders[-1][1].status is ReadingStatus.UNAVAILABLE
    assert renders[-1][1].seek_phase is P.NATIVE_COMPLETED


def test_paused_uncertain_receipt_still_pumps_native_errors(clock):
    tracker, backend, published, ends = setup()
    backend.get_seek_receipt = lambda: 'malformed evidence'
    pumped = []
    backend.pump_pending_events = lambda: pumped.append(True) or True
    tracker.state_manager.update_state(PlayerState.PAUSED_VIDEO)
    assert not tracker._poll_once()
    assert pumped == [True] and not ends
