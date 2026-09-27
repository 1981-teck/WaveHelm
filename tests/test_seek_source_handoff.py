"""Seek -> source replacement through real core, tracker, facade and both bars.

Only wx widgets, COM scheduling and the native pointer endpoints are test-owned.
No Windows/media qualification is claimed. Cases cover completed/pending seeks,
same-path replay, failed SetSource and late old-worker replies without fake success.
"""
from types import SimpleNamespace

import pytest

from src.controller.component_player.playback_state_manager import PlayerState as State
from src.model.media_file import MediaType
from src.playback_observation import ReadingStatus
from src.video import media_engine_core_playback as playback
from src.video.seek_receipt import SeekPhase as Phase, SeekOperation
from tests.test_native_seek_admission import rig as native_rig
from tests.test_ui_cached_progress import rig, tick, flush


@pytest.fixture
def linked(native_rig: SimpleNamespace, rig: SimpleNamespace,
           monkeypatch: pytest.MonkeyPatch) -> tuple[SimpleNamespace, SimpleNamespace]:
    """Bind the real receipt and clock acquisition to cache-only wx consumers."""
    native, ui = native_rig, rig
    ui.widget._presentation._clock = lambda: native.now[0]
    native.position, native.duration = 4.0, 10.0
    native.source_calls = []
    native.source_hr = 0
    native.controller._current_path = 'clip.mp4'
    native.controller._loop_enabled = False

    def invoke(engine: object, name: str, *args: object) -> object:
        if name == 'GetCurrentTime':
            return native.position
        if name == 'GetDuration':
            return native.duration
        if name == 'SetCurrentTime':
            native.calls.append(name)
            return native.hr
        raise AssertionError(name)

    def set_source(name: str, value: object) -> int:
        assert name == 'SetSource'
        native.source_calls.append(value)
        return native.source_hr

    monkeypatch.setattr(native.core, '_call_engine_ptr_method', invoke)
    monkeypatch.setattr(native.core, '_call_vtable_method', set_source)
    monkeypatch.setattr(native.adapter, 'call_on_com_thread', lambda name, job: job())
    monkeypatch.setattr(playback, '_SysAllocString', lambda value: value)
    monkeypatch.setattr(playback, '_SysFreeString', lambda value: None)
    ui.backend.observe_progress = native.adapter.observe_progress
    ui.backend.get_seek_receipt = native.controller.get_seek_receipt
    ui.backend.pump_pending_events = lambda: True
    native.now[0] += 0.25
    ui.tracker._poll_once(); tick(ui); flush(ui)
    assert ui.widget._display_reading.status is ReadingStatus.KNOWN
    return native, ui


def seek(linked: tuple[SimpleNamespace, SimpleNamespace], phase: str) -> SeekOperation:
    """Use production queue/native result handling; only callbacks are synthetic."""
    native, ui = linked
    if phase == 'refused':
        native.manager._com_ready_event.clear()
        assert native.controller.seek(8.0) is False
        native.manager._com_ready_event.set()
    else:
        native.hr = 0x80004005 if phase == 'failed' else 0
        assert native.controller.seek(8.0)
    op = native.core._seek_slot.current()
    if phase not in ('queued', 'refused'):
        native.manager._drain_tasks_com_thread()
    native.hr = 0
    if phase == 'completed':
        epoch = native.core._seek_slot.epoch
        assert op.observe_event(16, 1, epoch, native.adapter._source)
        assert op.observe_event(17, 1, epoch, native.adapter._source)
    native.now[0] += 0.25
    ui.tracker._poll_once(); tick(ui); flush(ui)
    return op


def select_context(linked: tuple[SimpleNamespace, SimpleNamespace], path: str) -> None:
    native, ui = linked
    ui.manager.update_state(State.STOPPED)
    track = SimpleNamespace(path=path, title='Replacement', duration=300.0,
                            media_type=MediaType.VIDEO)
    ui.queue.current_track = track
    ui.manager.set_context([track], 0, track)
    ui.manager.update_state(State.LOADING)
    tick(ui); flush(ui)
    assert ui.widget.progress_slider.GetValue() == 0


def load(linked: tuple[SimpleNamespace, SimpleNamespace], path: str) -> None:
    native, ui = linked
    select_context(linked, path)
    native.core.load_source(None)
    native.core.load_source(path)
    native.adapter._source = path
    native.controller._current_path = path
    native.position, native.duration = 2.0, 20.0
    ui.manager.update_state(State.PLAYING_VIDEO)
    native.now[0] += 0.25
    ui.tracker._poll_once(); tick(ui); flush(ui)


@pytest.mark.parametrize('paused', [False, True], ids=['playing', 'paused'])
@pytest.mark.parametrize('phase', ['completed', 'pending', 'failed', 'refused'])
@pytest.mark.parametrize('path', ['next.mp4', 'clip.mp4'], ids=['next', 'replay'])
def test_new_source_progress_does_not_inherit_old_seek(
        linked: tuple[SimpleNamespace, SimpleNamespace], paused: bool, phase: str, path: str) -> None:
    native, ui = linked
    if paused:
        ui.manager.update_state(State.PAUSED_VIDEO)
    op = seek(linked, phase)
    load(linked, path)
    snapshot = ui.tracker.get_progress_snapshot()
    assert snapshot.clock.position.seconds == 2.0
    assert snapshot.clock.duration.seconds == 20.0
    assert ui.widget._display_reading.status is ReadingStatus.KNOWN
    assert ui.widget.progress_slider.GetValue() == 100
    # The historical operation is cancelled, not fabricated into a completed seek.
    assert op.snapshot().phase is (Phase.REJECTED if phase == 'refused' else Phase.CANCELLED)
    assert native.controller.get_seek_receipt() is None
    assert native.controller.seek(3.0) is True
    native.manager._drain_tasks_com_thread()
    assert native.controller.get_seek_receipt().phase is Phase.NATIVE_ACCEPTED_UNCONFIRMED


@pytest.mark.parametrize('boundary', ['failure', 'non_sok', 'skipped', 'epoch_drift'])
def test_unproven_source_replacement_does_not_retire_receipt(
        linked: tuple[SimpleNamespace, SimpleNamespace],
        monkeypatch: pytest.MonkeyPatch, boundary: str) -> None:
    native, ui = linked
    op = seek(linked, 'pending')
    select_context(linked, 'next.mp4')
    if boundary == 'failure':
        native.source_hr = 0x80004005
    elif boundary == 'non_sok':
        native.source_hr = 1
    elif boundary == 'skipped':
        monkeypatch.setattr(native.adapter, 'call_on_com_thread', lambda name, job: None)
    else:
        def changed(name: str, value: object) -> int:
            native.core._seek_slot.invalidate('reentrant replacement')
            return 0
        monkeypatch.setattr(native.core, '_call_vtable_method', changed)
    if boundary == 'skipped':
        native.core.load_source('next.mp4')
    else:
        with pytest.raises(playback.MediaEngineError):
            native.core.load_source('next.mp4')
    assert native.core.get_seek_receipt() is not None
    assert op.snapshot().phase is Phase.CANCELLED
    assert op.snapshot().completed_at is None
    ui.manager.update_state(State.PLAYING_VIDEO)
    ui.tracker._poll_once(); tick(ui); flush(ui)
    assert ui.widget._display_reading.status is not ReadingStatus.KNOWN
    assert ui.widget.progress_slider.GetValue() == 0


def test_old_queued_operation_keeps_slot_until_real_worker_reply(
        linked: tuple[SimpleNamespace, SimpleNamespace]) -> None:
    native, ui = linked
    op = seek(linked, 'queued')
    load(linked, 'next.mp4')
    assert native.controller.get_seek_receipt() is not None
    assert native.controller.seek(3.0) is False
    assert native.manager._task_queue.qsize() == 1
    native.manager._drain_tasks_com_thread()
    assert native.calls == []  # Cancelled job never invoked SetCurrentTime.
    assert op.snapshot().worker_replied
    assert op.snapshot().completed_at is None
    native.now[0] += 0.25
    ui.tracker._poll_once(); tick(ui); flush(ui)
    assert ui.widget._display_reading.status is ReadingStatus.KNOWN
    assert ui.widget.progress_slider.GetValue() == 100
    assert native.controller.seek(3.0)


def test_late_old_seek_events_cannot_confirm_new_request(
        linked: tuple[SimpleNamespace, SimpleNamespace]) -> None:
    native, ui = linked
    old = seek(linked, 'pending')
    load(linked, 'next.mp4')
    assert native.controller.seek(3.0)
    fresh = native.core._seek_slot.current()
    native.manager._drain_tasks_com_thread()
    assert not old.observe_event(16, 1, 0, 'clip.mp4')
    assert not old.observe_event(17, 1, 0, 'clip.mp4')
    assert not fresh.observe_event(16, 1, 0, 'clip.mp4')
    assert not fresh.observe_event(17, 1, 0, 'clip.mp4')
    assert fresh.snapshot().phase is Phase.NATIVE_ACCEPTED_UNCONFIRMED
    assert fresh.snapshot().completed_at is None
    assert native.controller.seek(7.0) is False


@pytest.mark.parametrize('change', ['stop', 'owner', 'slot'])
def test_retired_observation_read_rejects_reentrant_drift(
        linked: tuple[SimpleNamespace, SimpleNamespace],
        monkeypatch: pytest.MonkeyPatch, change: str) -> None:
    native, ui = linked
    seek(linked, 'completed'); load(linked, 'next.mp4')
    slot = native.core._seek_slot
    supersedes = slot.supersedes
    def changing(record: object, generation: int, source: str, epoch: int) -> bool:
        result = supersedes(record, generation, source, epoch)
        assert result is True
        if change == 'stop':
            slot.invalidate('stop')
        elif change == 'owner':
            native.core._active_source = None
        else:
            # Simulate a concurrent reservation after the retired snapshot.
            with monkeypatch.context() as patch:
                patch.setattr(slot, 'supersedes', supersedes)
                assert slot.reserve(source, generation, epoch, 3.0) is not None
        return result
    monkeypatch.setattr(slot, 'supersedes', changing)
    with pytest.raises(RuntimeError, match='ownership changed'):
        native.controller.get_seek_receipt()


def test_repeated_seek_next_same_engine_does_not_accumulate_old_receipts(
        linked: tuple[SimpleNamespace, SimpleNamespace]) -> None:
    native, ui = linked
    for index in range(12):
        operation = seek(linked, 'pending' if index % 2 else 'completed')
        path = 'clip.mp4' if index % 2 else 'next.mp4'
        load(linked, path)
        assert native.core._seek_slot.current() is operation
        assert native.controller.get_seek_receipt() is None
        assert ui.widget.progress_slider.GetValue() == 100
        assert ui.widget._display_reading.duration == 20.0
    assert native.core._engine_generation == 1


def test_new_source_end_is_not_blocked_by_previous_source_seek(
        linked: tuple[SimpleNamespace, SimpleNamespace], monkeypatch: pytest.MonkeyPatch) -> None:
    native, ui = linked
    old = seek(linked, 'pending'); load(linked, 'next.mp4')
    monkeypatch.setattr(native.adapter, 'pump_events', lambda: None)
    monkeypatch.setattr(native.adapter, 'has_ended', lambda: True)
    assert native.controller.observe_end() is True
    assert old.snapshot().phase is Phase.CANCELLED
    assert old.snapshot().completed_at is None
    assert native.controller.seek(3.0)
    assert native.controller.observe_end() is False  # Current queued seek still blocks EOS.
