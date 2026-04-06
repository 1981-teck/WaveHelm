from __future__ import annotations

import inspect
import logging
from typing import Any, Callable, Optional

from src.audio.audio_events import AudioEventType
from src.controller.video_controller_state import VideoState
from src.video.adapter_factory import create_imf_media_engine_adapter

logger = logging.getLogger(__name__)

EVENT_BUS_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)
ADAPTER_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError, OSError)
HWND_EXCEPTIONS = (TypeError, ValueError)
INSPECT_EXCEPTIONS = (AttributeError, OSError, RuntimeError, TypeError, ValueError)



def _setup_event_subscriptions(self) -> None:
    """Configura le sottoscrizioni agli eventi."""
    if not self._event_bus:
        logger.warning('[VideoController] Event bus not available')
        return

    subscriptions = [
        (AudioEventType.FULLSCREEN_TOGGLE_REQUESTED, self._on_fullscreen_toggle_requested),
        (AudioEventType.VIDEO_WINDOW_MOVED, self._on_video_window_moved),
        (AudioEventType.VIDEO_PLAYBACK_ERROR, self._on_video_playback_error),
    ]

    for event_type, callback in subscriptions:
        try:
            self._event_bus.subscribe(event_type, callback)
            logger.debug('[VideoController] Subscribed to %s', event_type.name)
        except EVENT_BUS_EXCEPTIONS as exc:
            logger.warning(
                '[VideoController] Failed to subscribe to %s: %s',
                event_type.name,
                exc,
                exc_info=True,
            )



def _publish_event(
    self,
    event_type: AudioEventType,
    data: Optional[dict] = None,
    *,
    require_ui_thread: bool = False,
) -> None:
    """Pubblica un evento in modo sicuro."""
    if not self._event_bus:
        return

    try:
        self._event_bus.publish(
            event_type,
            data or {},
            require_ui_thread=require_ui_thread,
        )
    except EVENT_BUS_EXCEPTIONS as exc:
        logger.warning(
            '[VideoController] Failed to publish event %s: %s',
            event_type.name,
            exc,
            exc_info=True,
        )



def _update_state(self, new_state: VideoState) -> None:
    """Aggiorna lo stato interno e logga la transizione."""
    if self._state == new_state:
        return
    logger.info('[VideoController] State changed: %s -> %s', self._state.name, new_state.name)
    self._state = new_state



def _validate_hwnd(self, hwnd: int) -> bool:
    """Verifica che l'HWND sia valido."""
    try:
        hwnd_int = int(hwnd)
        if hwnd_int <= 0:
            raise ValueError('HWND must be a positive integer')
        return True
    except HWND_EXCEPTIONS as exc:
        logger.error('[VideoController] Invalid HWND: %s (%s)', hwnd, exc, exc_info=True)
        self._update_state(VideoState.ERROR)
        return False



def _safe_adapter_call(self, func: Callable, *args, **kwargs) -> Any:
    """Esegue una chiamata all'adapter gestendo le eccezioni."""
    if not self._adapter:
        logger.warning('[VideoController] No adapter available for call')
        return None

    try:
        return func(*args, **kwargs)
    except ADAPTER_EXCEPTIONS as exc:
        logger.error('[VideoController] Adapter call failed: %s', exc, exc_info=True)
        self._update_state(VideoState.ERROR)
        return None



def _debug_adapter_methods(self) -> None:
    """Logga info diagnostiche sul video adapter (metodi e origine)."""
    try:
        adapter = self._adapter
        if adapter is None:
            return
        cls = adapter.__class__
        try:
            src_file = inspect.getsourcefile(cls)
        except INSPECT_EXCEPTIONS:
            src_file = None
        methods = sorted({name for name in dir(adapter) if 'hwnd' in name.lower()})
        logger.debug(
            '[VideoController] Adapter debug: class=%s.%s file=%s hwnd_methods=%s',
            getattr(cls, '__module__', '?'),
            getattr(cls, '__name__', '?'),
            src_file,
            methods,
        )
    except INSPECT_EXCEPTIONS:
        logger.debug('[VideoController] Adapter debug failed', exc_info=True)



def _resolve_bind_method(adapter: Any) -> Optional[Callable[[int], Any]]:
    for name in ('set_hwnd', 'bind_hwnd', 'set_target_hwnd', 'set_video_window'):
        method = getattr(adapter, name, None)
        if callable(method):
            return method
    return None


def _build_stopped_event_payload(
    self,
    *,
    path: Optional[str],
    hwnd: Optional[int],
    duration_hint: float,
) -> dict[str, Any]:
    """Return a deterministic stop payload for the session being torn down.

    Edge cases:
        1. Fast video switches can close the previous adapter while a new session is already starting, so the payload must snapshot the old path/HWND before state reset.
        2. Legacy shutdown paths can have missing path/HWND values and must still publish a stable bounded payload.
        3. Duration hints can be malformed during partial startup and must coerce to a non-negative float.
    """
    try:
        normalized_duration_hint = max(0.0, float(duration_hint or 0.0))
    except (TypeError, ValueError):
        normalized_duration_hint = 0.0

    payload: dict[str, Any] = {'duration_hint': normalized_duration_hint}
    if path:
        payload['path'] = str(path)
    try:
        hwnd_int = int(hwnd) if hwnd is not None else 0
    except (TypeError, ValueError):
        hwnd_int = 0
    if hwnd_int > 0:
        payload['hwnd'] = hwnd_int
    return payload



def ensure_video_adapter(self, hwnd: int, loop_enabled: bool) -> bool:
    """Garantisce che esista un adapter pronto a riprodurre e collegato all'HWND della UI."""
    if not self._validate_hwnd(hwnd):
        return False

    if self._shutting_down:
        logger.debug('[VideoController] ensure_video_adapter ignored (shutting down)')
        return False

    self._loop_enabled = bool(loop_enabled)
    logger.debug(
        '[VideoController] ensure_video_adapter(hwnd=%s, loop_enabled=%s)',
        hwnd,
        self._loop_enabled,
    )

    need_new_adapter = self._adapter is None or self._state == VideoState.ERROR

    if need_new_adapter:
        if self._adapter:
            self._close_adapter_internal()

        try:
            self._adapter = create_imf_media_engine_adapter(event_bus=self._event_bus)
            self._current_hwnd = None
            self._last_error_info = None
            if self._state == VideoState.ERROR:
                self._update_state(VideoState.STOPPED)
            logger.info(
                '[VideoController] Created new video adapter (pending bind) for HWND=%s',
                hwnd,
            )
        except ADAPTER_EXCEPTIONS as exc:
            logger.error('[VideoController] Failed creating video adapter: %s', exc, exc_info=True)
            self._adapter = None
            self._update_state(VideoState.ERROR)
            return False

    try:
        if self._adapter and self._current_hwnd != hwnd:
            logger.debug('[VideoController] Binding adapter to HWND=%s', hwnd)
            bind_fn = _resolve_bind_method(self._adapter)
            if bind_fn is None:
                logger.error('[VideoController] Adapter non supporta binding HWND (metodo mancante)')
                self._debug_adapter_methods()
                raise AttributeError('Video adapter missing HWND binding method')
            self._safe_adapter_call(bind_fn, hwnd)
            if self._state == VideoState.ERROR:
                return False

            self._current_hwnd = hwnd
            self._last_surface_signature = None
            self._publish_event(AudioEventType.VIDEO_PLAYBACK_STARTING, {'hwnd': hwnd})
    except ADAPTER_EXCEPTIONS as exc:
        logger.error('[VideoController] Failed binding hwnd to adapter: %s', exc, exc_info=True)
        self._update_state(VideoState.ERROR)
        return False

    if self._adapter:
        self._safe_adapter_call(self._adapter.set_loop, self._loop_enabled)
        if self._state == VideoState.ERROR:
            return False
        self._update_state(VideoState.READY)
        return True

    return False



def _on_video_playback_ready(self, data=None):
    if not data or not isinstance(data, dict):
        return
    try:
        hwnd = int(data.get('hwnd') or 0)
    except HWND_EXCEPTIONS:
        hwnd = 0
    path = data.get('path')
    loop = bool(data.get('loop', self._loop_enabled))

    if hwnd and path:
        ok = self.ensure_video_adapter(hwnd, loop)
        if ok:
            self.play_media(path)



def set_loop(self, loop_enabled: bool) -> None:
    """Abilita/disabilita il loop sul video."""
    self._loop_enabled = bool(loop_enabled)
    if self._adapter:
        self._safe_adapter_call(self._adapter.set_loop, self._loop_enabled)



def _close_adapter_internal(self) -> None:
    """Close the current adapter exactly once for the active video session.

    Edge cases:
        1. Rapid video switches can call this method twice, so a second close on an already cleared session must be a no-op.
        2. Partial startup can leave an adapter without path/HWND metadata, so stop events must not be emitted with anonymous payloads.
        3. Shutdown can race with UI-driven stop requests, so the close guard must stay deterministic and bounded.
    """
    with self._close_lock:
        if self._closing:
            return
        self._closing = True

        adapter = self._adapter
        current_path = self._current_path
        current_hwnd = self._current_hwnd
        current_duration_hint = self._current_duration_hint
        had_active_session = bool(adapter) or bool(current_path) or bool(current_hwnd)
        stopped_payload = _build_stopped_event_payload(
            self,
            path=current_path,
            hwnd=current_hwnd,
            duration_hint=current_duration_hint,
        )
        self._adapter = None
        self._current_hwnd = None
        self._current_path = None
        self._current_duration_hint = 0.0
        self._last_surface_signature = None
        self._last_error_info = None

    try:
        logger.debug(
            '[VideoController] _close_adapter_internal() payload=%s had_active_session=%s',
            stopped_payload,
            had_active_session,
        )

        if not had_active_session:
            return

        if adapter:
            try:
                if hasattr(adapter, 'stop'):
                    adapter.stop()
            except ADAPTER_EXCEPTIONS as exc:
                logger.debug('[VideoController] Adapter stop before close failed: %s', exc, exc_info=True)

            try:
                if hasattr(adapter, 'close'):
                    adapter.close()
                elif hasattr(adapter, 'shutdown'):
                    adapter.shutdown()
            except ADAPTER_EXCEPTIONS as exc:
                logger.debug('[VideoController] Adapter cleanup failed: %s', exc, exc_info=True)

        self._update_state(VideoState.STOPPED)
        self._publish_event(
            AudioEventType.VIDEO_PLAYBACK_STOPPED,
            stopped_payload,
            require_ui_thread=True,
        )
    finally:
        with self._close_lock:
            self._closing = False



def shutdown(self) -> None:
    """Chiude il controller e rilascia tutte le risorse (idempotente)."""
    logger.debug('[VideoController] shutdown() called')
    self._shutting_down = True
    self._close_adapter_internal()



_VIDEO_CONTROLLER_CORE_METHODS: tuple[tuple[str, Callable[..., Any]], ...] = (
    ("_setup_event_subscriptions", _setup_event_subscriptions),
    ("_publish_event", _publish_event),
    ("_update_state", _update_state),
    ("_validate_hwnd", _validate_hwnd),
    ("_safe_adapter_call", _safe_adapter_call),
    ("_debug_adapter_methods", _debug_adapter_methods),
    ("ensure_video_adapter", ensure_video_adapter),
    ("_on_video_playback_ready", _on_video_playback_ready),
    ("set_loop", set_loop),
    ("_close_adapter_internal", _close_adapter_internal),
    ("shutdown", shutdown),
)


def install_video_controller_core_behavior(controller_cls: type) -> None:
    """Install video controller core behavior on the central coordinator.

    Edge cases:
        1. A binding name is empty and would mutate an unintended class attribute.
        2. A split module exports a non-callable binding and breaks controller wiring.
        3. Duplicate binding names silently shadow an earlier core controller method.
    """
    if getattr(controller_cls, '_video_controller_core_behavior_attached', False):
        return

    seen_names: set[str] = set()
    for attribute_name, method in _VIDEO_CONTROLLER_CORE_METHODS:
        if not attribute_name:
            raise TypeError('Video controller core binding name cannot be empty')
        if attribute_name in seen_names:
            raise TypeError(f'Duplicate video controller core binding: {attribute_name}')
        if not callable(method):
            raise TypeError(f'Invalid video controller core binding: {attribute_name}')
        setattr(controller_cls, attribute_name, method)
        seen_names.add(attribute_name)

    setattr(controller_cls, '_video_controller_core_behavior_attached', True)


def attach_video_controller_core_behavior(controller_cls: type) -> None:
    """Compatibility shim for historical attach_* imports.

    Prefer install_video_controller_core_behavior() from the central coordinator path.
    """
    install_video_controller_core_behavior(controller_cls)
