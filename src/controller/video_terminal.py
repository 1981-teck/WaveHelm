"""One cached native-end owner ticket, no native calls during capture/validation."""
from __future__ import annotations
from dataclasses import dataclass
from src.controller.seek_observation import SeekObservation, read_seek_observation


@dataclass(frozen=True, slots=True)
class VideoEndTicket:
    """Python ownership identity; not a native refcount, command or numeric clock."""
    adapter: object
    core: object | None
    generation: int | None
    epoch: int | None
    live: bool
    path: str | None
    seek: SeekObservation


def capture_video_end(controller: object, seek: SeekObservation) -> VideoEndTicket:
    adapter = getattr(controller, '_adapter')
    core = getattr(adapter, '_core', None)
    generation = getattr(core, '_engine_generation', None)
    epoch = getattr(getattr(core, '_seek_slot', None), 'epoch', None)
    for value in (generation, epoch):
        if value is not None and (type(value) is not int or value < 0):
            raise TypeError('Invalid terminal owner generation')
    live = (adapter is not None and not getattr(adapter, '_closed', False)
            and not getattr(adapter, '_shutdown_requested', False)
            and not getattr(core, '_shutdown_requested', False))
    return VideoEndTicket(adapter, core, generation, epoch, live,
                          getattr(controller, '_current_path', None), seek)


def same_video_end(a: VideoEndTicket, b: VideoEndTicket) -> bool:
    return (a.adapter is b.adapter and a.core is b.core and a.generation == b.generation
            and a.epoch == b.epoch and a.path == b.path and a.seek == b.seek
            and a.live and b.live and not b.seek.blocks_end)


def finalize_video_end(controller: object) -> bool:
    """Close the observed adapter before destroying its HWND, once and fail-closed."""
    ticket = getattr(controller, '_video_end_ticket', None)
    if type(ticket) is not VideoEndTicket:
        return False
    current = capture_video_end(controller, read_seek_observation(controller, ticket.path))
    if (getattr(controller, '_shutting_down', False) or getattr(controller, '_loop_enabled', False)
            or not same_video_end(current, ticket)):
        return False
    setattr(controller, '_video_end_ticket', None)  # Consume before reentrant teardown.
    close = getattr(controller, '_close_adapter_internal')
    close()
    return getattr(controller, '_adapter', None) is None


VideoClockOwner = tuple[int, int, int | None, int | None, bool] | None

def video_clock_owner(controller: object | None) -> VideoClockOwner:
    """Capture cached ownership for the shipped split-EOS observer, not native state.

    Legacy backends without that advertised route retain their historical context.
    Adapter/core references are held by the normal owners; this token is not a COM
    reference or proof against arbitrary address reuse/ABA transitions.
    """
    if not callable(getattr(controller, 'observe_end', None)):
        return None
    ticket = capture_video_end(controller, SeekObservation())
    return (id(ticket.adapter), id(ticket.core), ticket.generation, ticket.epoch, ticket.live)
