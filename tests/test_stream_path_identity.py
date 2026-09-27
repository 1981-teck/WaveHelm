"""08I event-correlation contracts; no network, decoder or native adapter is run."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from src.audio.audio_event_bus import AudioEventBus
from src.audio.audio_events import AudioEventType
from src.controller.component_player.player_event_handler import PlayerEventHandler
from src.controller.component_player.playback_state_manager import PlaybackStateManager, PlayerState
from src.model.media_file import MediaFile, MediaType

MATCH = PlayerEventHandler._matches_current_track_path
CASES = [
    ("https://HOST/Live", "HTTPS://host/Live", True),
    ("http://HOST/Live", "HTTP://host/Live", True),
    ("rtsp://HOST/Live", "RTSP://host/Live", True),
    ("rtmp://HOST/Live", "RTMP://host/Live", True),
    ("https://host/Live", "https://host/live", False),
    ("https://host/f?id=AB", "https://host/f?id=ab", False),
    ("https://host/f?ID=ab", "https://host/f?id=ab", False),
    ("https://host/f#AB", "https://host/f#ab", False),
    ("https://User:Ab@HOST/f", "HTTPS://User:Ab@host/f", True),
    ("https://User:Ab@host/f", "https://user:Ab@host/f", False),
    ("https://User:Ab@host/f", "https://User:ab@host/f", False),
    ("https://HOST:8443/f", "https://host:8443/f", True),
    ("https://host:8443/f", "https://host:8444/f", False),
    ("https://host:443/f", "https://host/f", False),
    ("https://host:0443/f", "https://host:443/f", False),
    ("https://host/f?", "https://host/f", False),
    ("https://host/f#", "https://host/f", False),
    ("https://host", "https://host/", False),
    ("https://host/a/../f", "https://host/f", False),
    ("https://host/%41", "https://host/A", False),
    ("https://host/%2F", "https://host/%2f", False),
    ("https://HOST/音楽/É.mp4?id=Ω", "HTTPS://host/音楽/É.mp4?id=Ω", True),
    ("https://host/É", "https://host/é", False),
    ("https://host/é", "https://host/e\u0301", False),
    ("https://[2001:DB8::1]:8000/F", "HTTPS://[2001:db8::1]:8000/F", True),
    ("http://[fe80::AB%Eth0]/F", "HTTP://[fe80::ab%Eth0]/F", True),
    ("http://[fe80::ab%Eth0]/F", "http://[fe80::ab%eth0]/F", False),
    ("https://host/f", "http://host/f", False),
    ("other://host/F", "other://host/f", False),
    ("other://host/F", "other://host/F", True),
    (r"C:\Media\CLIP.MP4", r"c:\media\clip.mp4", True),
    (r"\\SERVER\Media\CLIP.MP4", r"\\server\media\clip.mp4", True),
    (r"C:CLIP.MP4", r"c:clip.mp4", True),
    ("CLIP.MP4", "clip.mp4", True),
    (r"C:\Media\clip.mp4", "C:/Media/clip.mp4", False),
    (" https://HOST/Live ", "https://host/Live", True),
]
STALE = [
    ("https://streams.example/Live/Feed.m3u8", "https://streams.example/live/feed.m3u8"),
    ("https://streams.example/f?id=AB12", "https://streams.example/f?id=ab12"),
]
BAD = [
    "https:///no-host", "https:/no-authority", "https://", "https://:80/F",
    "https://host:invalid/F", "https://host:65536/F", "https://[::1/F",
    "https://[::1]tail/F", "https://ho\nst/F", "https://host/F\tQ",
    "https://host/F Q", "https://host\\other/F", "https://host/F\x00",
    "https://host/F\ud800",
]


@dataclass
class PendingVideo:
    """A negative admission receiver: it never claims a native adapter is ready."""
    calls: list[tuple[int, bool]] = field(default_factory=list)

    def ensure_video_adapter(self, hwnd: int, loop_enabled: bool = False) -> bool:
        self.calls.append((hwnd, loop_enabled))
        return False


@dataclass
class EngineProbe:
    video_controller: PendingVideo = field(default_factory=PendingVideo)
    stop_calls: int = 0

    def stop(self) -> None:
        self.stop_calls += 1


@dataclass
class PublishProbe:
    calls: list[tuple[object, object]] = field(default_factory=list)

    def publish(self, event: object, data: object = None, **kwargs: object) -> None:
        self.calls.append((event, data))


def make_handler(current: str, resolved: bool) -> PlayerEventHandler:
    track = MediaFile(
        "https://catalog.example/watch/selected" if resolved else current,
        media_type=MediaType.VIDEO,
        metadata={"_resolved_stream_url": current} if resolved else {},
    )
    state = PlaybackStateManager()
    state.update_state(PlayerState.PLAYING_VIDEO)
    handler = PlayerEventHandler(state, SimpleNamespace(current_track=track),
                                 EngineProbe(), PublishProbe(), object())
    handler._last_video_start_signature = (current, 44, False)
    return handler


def send_event(handler: PlayerEventHandler, kind: str, path: str) -> None:
    payload = {"path": path, "hwnd": 44, "error": "synthetic delayed error"}
    if kind == "error":
        handler._on_video_playback_error(payload)
    elif kind == "stopped":
        handler._on_video_playback_stopped(payload)
    else:
        handler._on_video_playback_ready(payload)


@pytest.mark.parametrize("direct,candidate,expected", CASES, ids=[f"pair-{i:02}" for i in range(len(CASES))])
@pytest.mark.parametrize("resolved", [False, True], ids=["direct", "resolved"])
def test_exact_component_identity(direct: str, candidate: str, expected: bool, resolved: bool) -> None:
    track = SimpleNamespace(path="unrelated.mp4" if resolved else direct,
                            metadata={"_resolved_stream_url": direct} if resolved else {})
    previous = copy.deepcopy(track.__dict__)
    assert MATCH(track, candidate) is expected
    assert track.__dict__ == previous


@pytest.mark.parametrize("value", BAD, ids=[f"invalid-{i:02}" for i in range(len(BAD))])
def test_malformed_stream_never_matches_itself(value: str) -> None:
    assert MATCH(SimpleNamespace(path=value, metadata={}), value) is False
    assert MATCH(SimpleNamespace(path="catalog.mp4", metadata={"_resolved_stream_url": value}), value) is False


@pytest.mark.parametrize("resolved", [False, True], ids=["direct", "resolved"])
@pytest.mark.parametrize("current,stale", STALE, ids=["path-case", "query-case"])
@pytest.mark.parametrize("kind", ["error", "stopped", "ready"])
def test_obsolete_event_cannot_change_current_session(current: str, stale: str, resolved: bool, kind: str) -> None:
    handler = make_handler(current, resolved)
    signature = handler._last_video_start_signature
    send_event(handler, kind, stale)
    assert handler.engine_controller.stop_calls == 0
    assert handler.engine_controller.video_controller.calls == []
    assert handler.event_bus.calls == []
    assert handler.state_manager.state is PlayerState.PLAYING_VIDEO
    assert handler._last_video_start_signature == signature


@pytest.mark.parametrize("resolved", [False, True], ids=["direct", "resolved"])
@pytest.mark.parametrize("kind", ["error", "stopped", "ready"])
def test_matching_event_reaches_original_consumer(resolved: bool, kind: str) -> None:
    current = "https://STREAMS.EXAMPLE/Live?id=AB"
    handler = make_handler(current, resolved)
    send_event(handler, kind, "HTTPS://streams.example/Live?id=AB")
    if kind == "error":
        assert handler.engine_controller.stop_calls == 1
        assert handler.state_manager.state is PlayerState.STOPPED
        assert len(handler.event_bus.calls) == 3
    elif kind == "stopped":
        assert handler._last_video_start_signature is None
        assert handler.state_manager.state is PlayerState.PLAYING_VIDEO
    else:
        assert handler.engine_controller.video_controller.calls == [(44, False)]
        assert handler.state_manager.state is PlayerState.LOADING
    assert handler.engine_controller.stop_calls == (1 if kind == "error" else 0)


@pytest.mark.parametrize("resolved", [False, True], ids=["direct", "resolved"])
@pytest.mark.parametrize("current,stale", STALE, ids=["path-case", "query-case"])
@pytest.mark.parametrize("kind,event_type", [
    ("error", AudioEventType.VIDEO_PLAYBACK_ERROR),
    ("stopped", AudioEventType.VIDEO_PLAYBACK_STOPPED),
    ("ready", AudioEventType.VIDEO_PLAYBACK_READY),
])
def test_synchronous_bus_rejects_stale_url(current: str, stale: str, resolved: bool,
                                          kind: str, event_type: AudioEventType) -> None:
    handler = make_handler(current, resolved)
    signature = handler._last_video_start_signature
    callback = {"error": handler._on_video_playback_error,
                "stopped": handler._on_video_playback_stopped,
                "ready": handler._on_video_playback_ready}[kind]
    bus = AudioEventBus()
    bus.subscribe(event_type, callback)
    try:
        bus.publish(event_type, {"path": stale, "hwnd": 44, "error": "synthetic stale error"})
        stats = bus.get_stats()
        assert stats["errors"] == 0
        assert stats["callbacks_dispatched"] == 1
        assert handler.engine_controller.stop_calls == 0
        assert handler.engine_controller.video_controller.calls == []
        assert handler._last_video_start_signature == signature
        assert handler.state_manager.state is PlayerState.PLAYING_VIDEO
        assert handler.event_bus.calls == []
    finally:
        bus.shutdown()


@pytest.mark.parametrize("candidate", [None, "", "   "], ids=["none", "empty", "whitespace"])
def test_missing_candidate_is_not_an_identity(candidate: str | None) -> None:
    assert MATCH(SimpleNamespace(path="current.mp4", metadata={}), candidate) is False
    assert MATCH(None, "current.mp4") is False


@pytest.mark.parametrize("length,expected", [(65_536, True), (65_537, False)], ids=["limit", "over-limit"])
def test_identity_size_budget(length: int, expected: bool) -> None:
    prefix = "https://streams.example/"
    current = prefix + "x" * (length - len(prefix))
    assert MATCH(SimpleNamespace(path=current, metadata={}), current) is expected


def test_local_budget_and_local_case_remain_explicit() -> None:
    value = "A" * 65_536
    assert MATCH(SimpleNamespace(path=value, metadata={}), value.lower()) is True
    assert MATCH(SimpleNamespace(path=value + "A", metadata={}), value + "A") is False


def test_unavailable_metadata_does_not_forge_url_identity() -> None:
    track = SimpleNamespace(path="https://host/Live", metadata=object())
    assert MATCH(track, "https://host/live") is False
    assert MATCH(track, "HTTPS://HOST/Live") is True


@pytest.mark.parametrize("error_type", [RuntimeError, ValueError, KeyError])
def test_declared_metadata_failure_remains_a_miss(error_type: type[Exception]) -> None:
    class BrokenMetadata:
        def get(self, key: str) -> object:
            raise error_type("synthetic metadata refusal")
    track = SimpleNamespace(path="current.mp4", metadata=BrokenMetadata())
    assert MATCH(track, "other.mp4") is False


def test_undeclared_metadata_error_still_propagates() -> None:
    class BrokenMetadata:
        def get(self, key: str) -> object:
            raise OSError("synthetic metadata failure")
    with pytest.raises(OSError, match="synthetic metadata failure"):
        MATCH(SimpleNamespace(path="current.mp4", metadata=BrokenMetadata()), "other.mp4")


def test_legacy_missing_stop_path_contract_is_unchanged() -> None:
    handler = make_handler(STALE[0][0], False)
    handler._on_video_playback_stopped({})
    assert handler._last_video_start_signature is None
    assert handler.engine_controller.stop_calls == 0


def test_existing_exact_signature_stop_fallback_is_preserved() -> None:
    handler = make_handler(STALE[0][0], False)
    prior = "https://previous.example/Other"
    handler._last_video_start_signature = (prior, 44, False)
    handler._on_video_playback_stopped({"path": prior})
    assert handler._last_video_start_signature is None
    assert handler.engine_controller.stop_calls == 0
