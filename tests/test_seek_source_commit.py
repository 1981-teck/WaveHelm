"""Source barriers are explicit successes, never timeouts or source-name guesses.

The old operation is retained with cancellation facts. Missing replies, same-epoch
work, failed/stale commits and stop alone cannot release overlapping native work.
"""
from dataclasses import replace

import pytest

from src.video.seek_receipt import SeekSlot, SeekPhase, NativeSeekResult
from tests.test_seek_completion_receipt import clock, started, reply


def test_invalidation_alone_cannot_be_a_successful_source_barrier(clock: list[float]) -> None:
    slot, op = started(); reply(op)
    epoch = slot.invalidate('stop')
    assert epoch == 1
    assert not slot.supersedes(op.snapshot(), 1, 'clip.mp4', epoch)
    assert slot.reserve('clip.mp4', 1, epoch, 3.0) is None


@pytest.mark.parametrize('source', ['clip.mp4', 'next.mp4'], ids=['replay', 'new-file'])
def test_commit_releases_prior_source_without_completing_its_seek(
        clock: list[float], source: str) -> None:
    slot, op = started(); reply(op)
    epoch = slot.invalidate('load')
    assert slot.commit_source(epoch, 1, source)
    assert slot.supersedes(op.snapshot(), 1, source, epoch)
    old = op.snapshot()
    assert old.phase is SeekPhase.CANCELLED and old.completed_at is None
    fresh = slot.reserve(source, 1, epoch, 3.0)
    assert fresh is not None and fresh is slot.current()
    assert fresh.snapshot().request_id == old.request_id + 1
    assert op.snapshot() == old
    assert not slot.supersedes(fresh.snapshot(), 1, source, epoch)


@pytest.mark.parametrize('change', ['epoch', 'source', 'generation', 'reply', 'future'])
def test_retirement_rejects_mismatching_ownership_or_unreplied_work(
        clock: list[float], change: str) -> None:
    slot, op = started(); reply(op)
    epoch = slot.invalidate('load')
    assert slot.commit_source(epoch, 1, 'next.mp4')
    record = op.snapshot()
    generation, source, expected_epoch = 1, 'next.mp4', epoch
    if change == 'epoch': expected_epoch -= 1
    elif change == 'source': source = 'elsewhere.mp4'
    elif change == 'generation': generation += 1
    elif change == 'reply': record = replace(record, worker_replied=False)
    else: record = replace(record, transport_epoch=epoch + 1)
    assert not slot.supersedes(record, generation, source, expected_epoch)


@pytest.mark.parametrize('epoch,generation,source', [
    (True, 1, 'new.mp4'), (-1, 1, 'new.mp4'), (1, False, 'new.mp4'),
    (1, -1, 'new.mp4'), (1, 1, ''), (1, 1, 'bad\0.mp4'),
    (1, 1, None), (1, 1, 'a' * 32769),
], ids=['bool-epoch', 'negative-epoch', 'bool-generation', 'negative-generation',
        'empty-path', 'null-in-path', 'missing-path', 'oversize-path'])
def test_invalid_commit_cannot_change_slot(
        epoch: object, generation: object, source: object) -> None:
    slot = SeekSlot()
    slot.invalidate('load')
    with pytest.raises(ValueError):
        slot.commit_source(epoch, generation, source)
    assert slot.commit_source(1, 1, 'new.mp4')


def test_stale_duplicate_commit_cannot_replace_a_valid_barrier(clock: list[float]) -> None:
    slot, op = started(); reply(op)
    epoch = slot.invalidate('load')
    assert not slot.commit_source(epoch - 1, 1, 'new.mp4')
    assert slot.commit_source(epoch, 1, 'new.mp4')
    assert not slot.commit_source(epoch, 1, 'another.mp4')
    assert slot.supersedes(op.snapshot(), 1, 'new.mp4', epoch)
    assert not slot.supersedes(op.snapshot(), 1, 'another.mp4', epoch)
    slot.invalidate('stop')
    assert not slot.supersedes(op.snapshot(), 1, 'new.mp4', epoch + 1)


def test_current_source_seek_still_needs_real_callback_pair(clock: list[float]) -> None:
    slot, old = started(); reply(old)
    epoch = slot.invalidate('load')
    assert slot.commit_source(epoch, 1, 'next.mp4')
    fresh = slot.reserve('next.mp4', 1, epoch, 5.0)
    fresh.mark_queued(); assert fresh.begin_native()
    fresh.put_nowait(NativeSeekResult(SeekPhase.NATIVE_ACCEPTED_UNCONFIRMED, 0))
    assert fresh.snapshot().blocks_end
    assert slot.reserve('next.mp4', 1, epoch, 7.0) is None
    assert fresh.observe_event(16, 1, epoch, 'next.mp4')
    assert fresh.observe_event(17, 1, epoch, 'next.mp4')
    assert not fresh.snapshot().blocks_end
    assert slot.reserve('next.mp4', 1, epoch, 7.0) is not None


def test_explicit_prequeue_refusal_does_not_require_a_nonexistent_worker_reply() -> None:
    slot = SeekSlot()
    op = slot.reserve('old.mp4', 1, 0, 2.0)
    op.refuse('queue refused before insertion')
    epoch = slot.invalidate('new source')
    assert not op.snapshot().worker_replied
    assert slot.commit_source(epoch, 1, 'new.mp4')
    assert slot.supersedes(op.snapshot(), 1, 'new.mp4', epoch)
    for bad in (replace(op.snapshot(), queued=True),
                replace(op.snapshot(), native_call_started=True)):
        assert not slot.supersedes(bad, 1, 'new.mp4', epoch)
