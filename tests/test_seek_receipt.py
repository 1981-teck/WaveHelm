"""Pointer-free command evidence; synthetic times/results are not native seeks."""
from __future__ import annotations
from dataclasses import FrozenInstanceError, replace
import queue
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest
from src.video import seek_receipt as api
from src.video.seek_receipt import NativeSeekResult, SeekPhase as P, SeekSlot, SeekReceipt, SeekOperation


def record() -> SeekReceipt:
    return SeekReceipt(1, 'clip.mp4', 1, 0, 2.0, P.RESERVED, 100.0, 110.0)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    value = [100.0]
    monkeypatch.setattr(api.time, 'monotonic', lambda: value[0])
    return value


@pytest.mark.parametrize('value', [True, False, None, '1', [], float('nan'), float('inf'), -float('inf'), 10**400])
def test_invalid_seconds_are_rejected(value: object) -> None:
    assert api.seek_seconds(value) is None


@pytest.mark.parametrize('value,expected', [(0, 0.0), (-2.0, 0.0), (2, 2.0), (2.25, 2.25)])
def test_finite_targets_keep_zero_and_negative_clamp(value: object, expected: float) -> None:
    assert api.seek_seconds(value) == expected


@pytest.mark.parametrize('field,value', [
    ('request_id', True), ('generation', -1), ('transport_epoch', 1.0),
    ('source', ''), pytest.param('source', 'x'*32769, id='source-over-limit'),
    ('target', float('inf')),
    ('target', -1.0), ('created_at', float('nan')), ('deadline', 100.0),
    ('phase', 'QUEUED'), ('queued', 1), ('native_call_started', None),
    ('worker_replied', 1), ('detail', 'x'*257), ('error_type', None),
    ('hresult', True), ('hresult', -1), ('hresult', 1<<32),
])
def test_record_schema_refuses_bad_fields(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        replace(record(), **{field:value})


def test_record_is_immutable_and_not_truthy_success() -> None:
    value = record()
    with pytest.raises(FrozenInstanceError):
        value.target = 9.0
    with pytest.raises(TypeError):
        bool(value)


@pytest.mark.parametrize('kwargs', [
    {'phase':P.QUEUED}, {'phase':P.NATIVE_ACCEPTED_UNCONFIRMED},
    {'phase':P.NATIVE_ACCEPTED_UNCONFIRMED, 'hresult':1},
    {'phase':P.FAILED, 'hresult':True}, {'phase':P.FAILED, 'hresult':-1},
    {'phase':P.FAILED, 'detail':'x'*257},
])
def test_invalid_native_result_is_not_a_success(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        NativeSeekResult(**kwargs)


def test_queue_refusal_never_claims_worker_ran_and_reopens_slot() -> None:
    slot = SeekSlot()
    first = slot.reserve('clip.mp4', 1, 0, 0.0)
    first.refuse('not ready')
    value = first.snapshot()
    assert value.phase is P.REJECTED
    assert not value.queued and not value.worker_replied and not value.native_call_started
    second = slot.reserve('clip.mp4', 1, 0, 3.0)
    assert second is not None and second.snapshot().request_id == 2


def test_slow_queue_is_bounded_even_after_expiry(clock) -> None:
    slot = SeekSlot()
    operation = slot.reserve('clip.mp4', 1, 0, 1.0)
    operation.mark_queued()
    clock[0] = 111.0
    assert operation.snapshot().phase is P.EXPIRED_UNCONFIRMED
    assert not operation.begin_native()
    for _ in range(40):
        assert slot.reserve('clip.mp4', 1, 0, 3.0) is None
    operation.put_nowait(NativeSeekResult(P.EXPIRED_UNCONFIRMED))
    assert slot.reserve('clip.mp4', 1, 0, 3.0) is not None


def test_late_success_after_native_deadline_is_still_unconfirmed(clock) -> None:
    op = SeekOperation(record())
    op.mark_queued()
    assert op.begin_native()
    clock[0] = 111.0
    op.put_nowait(NativeSeekResult(P.NATIVE_ACCEPTED_UNCONFIRMED, 0))
    result = op.snapshot()
    assert result.phase is P.EXPIRED_UNCONFIRMED and result.hresult == 0
    assert result.native_call_started and result.worker_replied


def test_invalidation_keeps_outstanding_slot_and_late_reply_does_not_revive_it() -> None:
    slot = SeekSlot()
    op = slot.reserve('clip.mp4', 1, 0, 1.0)
    op.mark_queued()
    slot.invalidate('stop')
    assert slot.epoch == 1 and op.snapshot().phase is P.CANCELLED
    assert slot.reserve('clip.mp4', 1, 1, 2.0) is None
    op.put_nowait(NativeSeekResult(P.NATIVE_ACCEPTED_UNCONFIRMED, 0))
    assert op.snapshot().phase is P.CANCELLED
    assert slot.reserve('clip.mp4', 1, 1, 2.0) is not None


def test_fast_reply_before_submit_returns_is_preserved() -> None:
    op = SeekOperation(record())
    assert op.begin_native()
    op.put_nowait(NativeSeekResult(P.NATIVE_ACCEPTED_UNCONFIRMED, 0))
    op.mark_queued()
    result = op.snapshot()
    assert result.phase is P.NATIVE_ACCEPTED_UNCONFIRMED
    assert result.queued and result.worker_replied and result.native_call_started
    with pytest.raises(ValueError):
        op.refuse('too late')
    with pytest.raises(queue.Full):
        op.put_nowait(NativeSeekResult(P.FAILED))


@pytest.mark.parametrize('result', [None, True, 0, 'success', object()])
def test_malformed_worker_results_fail_closed(result: object) -> None:
    op = SeekOperation(record())
    op.put_nowait(result)
    assert op.snapshot().phase is P.FAILED
    assert op.snapshot().error_type == 'TypeError'


def test_worker_exception_is_bounded_scalar_evidence_not_an_exception_object() -> None:
    op = SeekOperation(record())
    op.put_nowait(ValueError('x'*900))
    value = op.snapshot()
    assert value.phase is P.FAILED and value.error_type == 'ValueError'
    assert len(value.detail) == 256 and value.worker_replied


def test_uncertain_submission_does_not_allow_duplicate_work() -> None:
    slot = SeekSlot()
    op = slot.reserve('clip.mp4', 1, 0, 1.0)
    op.submission_uncertain(RuntimeError('dispatcher exception'))
    assert op.snapshot().phase is P.SUBMISSION_UNCERTAIN
    assert not op.begin_native() and not op.snapshot().worker_replied
    assert slot.reserve('clip.mp4', 1, 0, 2.0) is None


def test_concurrent_reservations_have_one_winner() -> None:
    slot = SeekSlot()
    barrier = threading.Barrier(8)
    def attempt(_: int):
        barrier.wait(timeout=3)
        return slot.reserve('clip.mp4', 1, 0, 1.0)
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(attempt, range(8)))
    assert sum(item is not None for item in outcomes) == 1


@pytest.mark.parametrize('now', [True, float('nan'), -1, float('inf'), '100'])
def test_bad_observation_time_rejected(now: object) -> None:
    with pytest.raises(ValueError):
        SeekOperation(record()).snapshot(now)


def test_cancelled_intent_retains_original_late_worker_error() -> None:
    op=SeekOperation(record());op.mark_queued();assert op.begin_native()
    op.cancel('stop')
    op.put_nowait(OSError('release failed'))
    value=op.snapshot()
    assert value.phase is P.CANCELLED and value.detail=='stop'
    assert value.worker_phase is P.FAILED and value.worker_error_type=='OSError'
    assert value.worker_detail=='release failed'


def test_acceptance_without_native_invocation_cannot_pass_receipt_boundary() -> None:
    op=SeekOperation(record());op.mark_queued()
    op.put_nowait(NativeSeekResult(P.NATIVE_ACCEPTED_UNCONFIRMED,0))
    value=op.snapshot()
    assert value.phase is P.FAILED and not value.native_call_started


@pytest.mark.parametrize('length,accepted', [(32768, True), (32769, False)],
                         ids=['source-limit', 'source-one-over-limit'])
def test_source_budget_preserves_full_hostile_payload(length: int, accepted: bool) -> None:
    payload = 'x' * length
    if accepted:
        assert replace(record(), source=payload).source == payload
    else:
        with pytest.raises(ValueError):
            replace(record(), source=payload)
    assert len(payload) == length
