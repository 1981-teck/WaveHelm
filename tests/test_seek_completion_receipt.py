"""Serialized native completion facts; controlled events are not Windows evidence."""
from dataclasses import replace
import pytest
from src.video import seek_receipt as api

P = api.SeekPhase

@pytest.fixture
def clock(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(api.time, 'monotonic', lambda: now[0])
    return now


def started():
    slot = api.SeekSlot()
    op = slot.reserve('clip.mp4', 1, 0, 2.0)
    op.mark_queued()
    assert op.begin_native()
    return slot, op


def reply(op):
    op.put_nowait(api.NativeSeekResult(P.NATIVE_ACCEPTED_UNCONFIRMED, 0))


def events(op):
    assert op.observe_event(16, 1, 0, 'clip.mp4')
    assert op.observe_event(17, 1, 0, 'clip.mp4')


def test_sok_does_not_allow_overlapping_native_seeks(clock):
    slot, op = started()
    reply(op)
    assert op.snapshot().blocks_end
    assert slot.reserve('clip.mp4', 1, 0, 3.0) is None


@pytest.mark.parametrize('fast_callback', [True, False])
def test_event_pair_and_sok_complete_in_either_reply_order(clock, fast_callback):
    slot, op = started()
    if fast_callback:
        events(op)
        assert op.snapshot().phase is P.RUNNING
        reply(op)
    else:
        reply(op)
        events(op)
    value = op.snapshot()
    assert value.phase is P.NATIVE_COMPLETED
    assert value.worker_phase is P.NATIVE_ACCEPTED_UNCONFIRMED
    assert value.completed_at == 100.0 and not value.blocks_end
    assert slot.reserve('clip.mp4', 1, 0, 3.0) is not None


@pytest.mark.parametrize('order', [[], [17], [16], [17, 16], [18, 19]])
def test_missing_or_unordered_events_cannot_complete(clock, order):
    slot, op = started()
    reply(op)
    for event in order:
        op.observe_event(event, 1, 0, 'clip.mp4')
    assert op.snapshot().phase is P.NATIVE_ACCEPTED_UNCONFIRMED
    assert slot.reserve('clip.mp4', 1, 0, 3.0) is None


@pytest.mark.parametrize('generation,epoch,source', [(2, 0, 'clip.mp4'), (1, 1, 'clip.mp4'), (1, 0, 'other.mp4')])
def test_wrong_callback_owner_is_rejected(clock, generation, epoch, source):
    _, op = started()
    reply(op)
    assert not op.observe_event(16, generation, epoch, source)
    assert not op.observe_event(17, generation, epoch, source)
    assert op.snapshot().seeking_at is None


def test_early_callback_cannot_be_reused_after_start(clock):
    slot = api.SeekSlot()
    op = slot.reserve('clip.mp4', 1, 0, 0.0)
    assert not op.observe_event(16, 1, 0, 'clip.mp4')
    op.mark_queued()
    assert not op.observe_event(16, 1, 0, 'clip.mp4')
    assert op.begin_native()
    reply(op)
    assert not op.observe_event(17, 1, 0, 'clip.mp4')


@pytest.mark.parametrize('cancel', [True, False])
@pytest.mark.parametrize('before_reply', [True, False])
def test_cancel_or_timeout_never_revived_by_late_callbacks(clock, cancel, before_reply):
    slot, op = started()
    if not before_reply:
        reply(op)
    if cancel:
        slot.invalidate('stop')
    else:
        clock[0] = 111.0
    if before_reply:
        reply(op)
    assert not op.observe_event(16, 1, 0, 'clip.mp4')
    assert not op.observe_event(17, 1, 0, 'clip.mp4')
    assert op.snapshot().phase is (P.CANCELLED if cancel else P.EXPIRED_UNCONFIRMED)
    assert op.snapshot().blocks_end
    assert slot.reserve('clip.mp4', 1, slot.epoch, 3.0) is None
    # A different native generation cannot inherit an old engine operation.
    assert slot.reserve('clip.mp4', 2, slot.epoch, 3.0) is not None


def test_failed_hresult_cannot_be_overridden_by_fast_callback(clock):
    slot, op = started()
    events(op)
    op.put_nowait(api.NativeSeekResult(P.FAILED, 0x80004005))
    assert op.snapshot().phase is P.FAILED and op.snapshot().completed_at is None
    assert not op.snapshot().blocks_end
    assert slot.reserve('clip.mp4', 1, 0, 4.0) is not None


def test_unknown_worker_error_remains_blocking_after_native_call(clock):
    slot, op = started()
    op.put_nowait(RuntimeError('reply lost'))
    assert op.snapshot().blocks_end
    assert slot.reserve('clip.mp4', 1, 0, 2.0) is None


def test_completed_receipt_does_not_expire_or_accept_duplicate_events(clock):
    _, op = started()
    reply(op)
    events(op)
    before = op.snapshot()
    clock[0] = 115.0
    assert op.snapshot() == before
    assert not op.observe_event(16, 1, 0, 'clip.mp4')
    assert not op.observe_event(17, 1, 0, 'clip.mp4')


@pytest.mark.parametrize('field,value', [('seeking_at', float('nan')), ('seeking_at', True),
    ('seeking_at', 99.0), ('seeked_at', 100.0), ('completed_at', 100.0), ('phase', P.NATIVE_COMPLETED)])
def test_invalid_event_facts_are_not_receipts(clock, field, value):
    _, op = started()
    with pytest.raises(ValueError):
        replace(op.snapshot(), **{field: value})


@pytest.mark.parametrize('field,value', [('native_call_started', False), ('worker_phase', P.FAILED),
    ('completed_at', 110.0), ('worker_replied', False), ('hresult', 1)])
def test_completed_receipt_requires_all_native_and_worker_facts(clock, field, value):
    _, op = started(); reply(op); events(op)
    with pytest.raises(ValueError):
        replace(op.snapshot(), **{field: value})


@pytest.mark.parametrize('event,generation,epoch,source', [(16.0,1,0,'clip.mp4'),
    (16,True,0,'clip.mp4'),(16,1,False,'clip.mp4'),(16,1,0,None)])
def test_event_identity_is_not_coerced(clock, event, generation, epoch, source):
    _, op = started(); reply(op)
    assert not op.observe_event(event, generation, epoch, source)
    assert op.snapshot().seeking_at is None


def test_callback_between_worker_time_capture_and_reply_merge(clock, monkeypatch):
    _, op = started()
    assert op.observe_event(16, 1, 0, 'clip.mp4')
    original = op.snapshot
    fired = [False]
    def interleaved(now=None):
        result = original(now)
        if now is not None and not fired[0]:
            fired[0] = True
            clock[0] = 100.1
            assert op.observe_event(17, 1, 0, 'clip.mp4')
        return result
    monkeypatch.setattr(op, 'snapshot', interleaved)
    reply(op)
    record = original()
    assert record.phase is P.NATIVE_COMPLETED
    assert record.completed_at >= record.seeked_at


def test_worker_reply_between_event_capture_and_event_merge(clock, monkeypatch):
    _, op = started()
    assert op.observe_event(16, 1, 0, 'clip.mp4')
    original = op.snapshot
    fired = [False]
    def interleaved(now=None):
        result = original(now)
        if now is not None and not fired[0]:
            fired[0] = True
            clock[0] = 100.2
            reply(op)
        return result
    monkeypatch.setattr(op, 'snapshot', interleaved)
    clock[0] = 100.1
    assert op.observe_event(17, 1, 0, 'clip.mp4')
    record = original()
    assert record.phase is P.NATIVE_COMPLETED
    assert record.completed_at >= 100.2
