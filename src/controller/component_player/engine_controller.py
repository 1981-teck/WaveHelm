# -*- coding: utf-8 -*-
"""engine_controller.py
Wrapper per l'interazione diretta con gli engine audio e video.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

from src.model.media_file import MediaFile, MediaType

from .playback_state_manager import PlaybackStateManager, PlayerState

logger = logging.getLogger(__name__)

ENGINE_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError, OSError)
STATE_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)


class EngineController:
    """Controlla direttamente gli engine audio e video."""

    def __init__(
        self,
        state_manager: PlaybackStateManager,
        audio_engine: Any,
        video_controller: Any,
        video_controller_factory: Optional[Callable[[], Any]] = None,
    ):
        self.state_manager = state_manager
        self.audio_engine = audio_engine
        self.video_controller = video_controller
        self._video_controller_factory = video_controller_factory

        self._shutdown_lock = threading.RLock()
        self._is_shutting_down: bool = False

        logger.debug('[EngineController] Initialized')

    def set_video_controller_factory(self, factory: Optional[Callable[[], Any]]):
        self._video_controller_factory = factory

    def shutdown(self) -> None:
        with self._shutdown_lock:
            if self._is_shutting_down:
                return
            self._is_shutting_down = True

        logger.debug('[EngineController] shutdown() requested')
        try:
            self.stop()
        except ENGINE_EXCEPTIONS:
            logger.debug('[EngineController] stop() failed during shutdown (best effort).', exc_info=True)

    def _ensure_video_controller(self) -> bool:
        if self.video_controller:
            return True

        factory = self._video_controller_factory
        if not callable(factory):
            logger.debug('[EngineController] No video controller factory configured.')
            return False

        logger.debug('[EngineController] Creating VideoController JIT via factory...')
        try:
            self.video_controller = factory()
            if not self.video_controller:
                logger.error('[EngineController] VideoController factory returned None.')
                return False
            return True
        except ENGINE_EXCEPTIONS as exc:
            logger.error('[EngineController] Error creating VideoController JIT: %s', exc, exc_info=True)
            self.state_manager.update_state(PlayerState.ERROR, {'message': str(exc)})
            return False

    def _read_audio_engine_bool(self, attr_name: str) -> Optional[bool]:
        attr = getattr(self.audio_engine, attr_name, None)
        if attr is None:
            return None
        try:
            value = attr() if callable(attr) else attr
        except ENGINE_EXCEPTIONS:
            logger.debug('[EngineController] audio_engine.%s probe failed.', attr_name, exc_info=True)
            return None
        return bool(value)

    def _audio_engine_loaded_path_matches(self, path: str) -> Optional[bool]:
        current_file = getattr(self.audio_engine, 'current_file', None)
        if current_file is not None:
            return str(current_file) == path
        return self._read_audio_engine_bool('is_loaded')

    def _invoke_audio_engine_playback(self, path: str) -> Optional[bool]:
        """Start audio playback and return an explicit engine result when available.

        Edge cases handled:
        - same-file resume paths should prefer resume() over reloading the media
        - engines exposing set_file() must fail closed if loading the file returns False
        - legacy engines exposing only play_file()/play() keep compatibility with best-effort probing
        """
        if self._audio_engine_loaded_path_matches(path) and self._read_audio_engine_bool('is_paused'):
            resume = getattr(self.audio_engine, 'resume', None)
            if callable(resume):
                resume()
                return True

        set_file = getattr(self.audio_engine, 'set_file', None)
        play = getattr(self.audio_engine, 'play', None)
        if callable(set_file) and callable(play):
            if not bool(set_file(path)):
                return False
            play()
            return None

        play_file = getattr(self.audio_engine, 'play_file', None)
        if callable(play_file):
            return play_file(path)
        if callable(play):
            play(path)
            return None
        raise RuntimeError('AudioEngine non espone play_file()/play().')

    def _audio_start_confirmed(self, path: str, explicit_result: Optional[bool]) -> bool:
        if explicit_result is False:
            return False

        loaded_match = self._audio_engine_loaded_path_matches(path)
        is_playing = self._read_audio_engine_bool('is_playing')
        is_paused = self._read_audio_engine_bool('is_paused')
        playback_state_known = is_playing is not None or is_paused is not None
        if playback_state_known:
            return bool(is_playing) or bool(is_paused)

        if explicit_result is True:
            return True
        if loaded_match is not None:
            return bool(loaded_match)
        return True

    def _play_audio(self, path: str, loop: bool) -> bool:
        try:
            if hasattr(self.audio_engine, 'set_loop'):
                try:
                    self.audio_engine.set_loop(bool(loop))
                except ENGINE_EXCEPTIONS:
                    logger.debug('[EngineController] audio_engine.set_loop failed (ignored)', exc_info=True)

            play_result = self._invoke_audio_engine_playback(path)
            if not self._audio_start_confirmed(path, play_result):
                raise RuntimeError('Audio playback did not start correctly.')

            self.state_manager.update_state(PlayerState.PLAYING_AUDIO)
            return True
        except ENGINE_EXCEPTIONS as exc:
            logger.error('[EngineController] Errore avviando la riproduzione audio: %s', exc, exc_info=True)
            self.state_manager.update_state(PlayerState.ERROR, {'message': str(exc)})
            return False

    def _prepare_video(self, loop: bool) -> bool:
        if not self._ensure_video_controller():
            logger.error('[EngineController] VideoController non disponibile per la riproduzione.')
            if self.state_manager.state != PlayerState.ERROR:
                self.state_manager.update_state(PlayerState.ERROR, {'message': 'VideoController not available'})
            return False

        try:
            self.set_loop(bool(loop))
        except ENGINE_EXCEPTIONS:
            logger.debug('[EngineController] set_loop failed while preparing video', exc_info=True)

        self.state_manager.update_state(PlayerState.LOADING)
        return True

    def play(self, media: MediaFile, loop: bool) -> bool:
        if self._is_shutting_down:
            logger.debug('[EngineController] play() ignored: shutting down.')
            return False

        self.stop()

        path = getattr(media, 'path', None)
        if not path:
            logger.error('[EngineController] Il media file non ha un percorso.')
            return False

        media_type = getattr(media, 'media_type', None)

        if media_type == MediaType.AUDIO:
            logger.info('[EngineController] Playing AUDIO: %s', path)
            return self._play_audio(path, loop)

        if media_type == MediaType.VIDEO:
            logger.info('[EngineController] Preparing VIDEO: %s', path)
            return self._prepare_video(loop)

        logger.warning('[EngineController] Tipo di media non supportato: %r', media_type)
        return False

    def pause(self) -> None:
        if self._is_shutting_down:
            logger.debug('[EngineController] pause() ignored: shutting down.')
            return

        state = self.state_manager.state
        try:
            if state == PlayerState.PLAYING_AUDIO:
                if hasattr(self.audio_engine, 'pause'):
                    self.audio_engine.pause()
                self.state_manager.update_state(PlayerState.PAUSED_AUDIO)
            elif state == PlayerState.PLAYING_VIDEO:
                if self.video_controller and hasattr(self.video_controller, 'pause'):
                    self.video_controller.pause()
                self.state_manager.update_state(PlayerState.PAUSED_VIDEO)
        except ENGINE_EXCEPTIONS as exc:
            logger.error('[EngineController] Errore durante la pausa: %s', exc, exc_info=True)

    def resume(self) -> None:
        if self._is_shutting_down:
            logger.debug('[EngineController] resume() ignored: shutting down.')
            return

        state = self.state_manager.state
        try:
            if state == PlayerState.PAUSED_AUDIO:
                if hasattr(self.audio_engine, 'resume'):
                    self.audio_engine.resume()
                elif hasattr(self.audio_engine, 'play'):
                    self.audio_engine.play()
                self.state_manager.update_state(PlayerState.PLAYING_AUDIO)
            elif state == PlayerState.PAUSED_VIDEO:
                if self.video_controller and hasattr(self.video_controller, 'resume'):
                    self.video_controller.resume()
                self.state_manager.update_state(PlayerState.PLAYING_VIDEO)
        except ENGINE_EXCEPTIONS as exc:
            logger.error('[EngineController] Errore durante la ripresa: %s', exc, exc_info=True)

    def _should_stop_video(self) -> bool:
        """Return True only when a real video session may still need teardown.

        Edge cases:
            1. During fast video-to-video switches the next track can already be a video while the player state is STOPPED, so using state_manager.is_video() alone would trigger a duplicate close on the old session.
            2. Legacy callers can leave the playback state stale while a video adapter or current path is still present; those cases must still stop the controller.
            3. Dummy/fake video controllers used in tests may not expose private session fields and must fail closed without raising.
        """
        state = self.state_manager.state
        if state in (PlayerState.PLAYING_VIDEO, PlayerState.PAUSED_VIDEO, PlayerState.LOADING):
            return True

        controller = self.video_controller
        if controller is None:
            return False

        try:
            if getattr(controller, '_adapter', None) is not None:
                return True
            if bool(getattr(controller, '_current_path', None)):
                return True
            if getattr(controller, '_current_hwnd', None):
                return True
        except STATE_EXCEPTIONS:
            return False
        return False

    def stop(self) -> None:
        try:
            if hasattr(self.audio_engine, 'stop'):
                self.audio_engine.stop()
        except ENGINE_EXCEPTIONS as exc:
            logger.warning("[EngineController] Errore fermando l'audio engine: %s", exc)

        if self.video_controller and self._should_stop_video():
            try:
                if hasattr(self.video_controller, 'stop'):
                    self.video_controller.stop(close_adapter=True)
                elif hasattr(self.video_controller, 'shutdown'):
                    self.video_controller.shutdown()
                elif hasattr(self.video_controller, 'close'):
                    self.video_controller.close()
            except ENGINE_EXCEPTIONS as exc:
                logger.warning('[EngineController] Errore fermando il video controller: %s', exc)

        if not self.state_manager.is_stopped():
            self.state_manager.update_state(PlayerState.STOPPED)

    def seek(self, position_sec: float) -> None:
        if self._is_shutting_down:
            logger.debug('[EngineController] seek() ignored: shutting down.')
            return

        state = self.state_manager.state
        if state == PlayerState.LOADING:
            logger.debug('[EngineController] seek() ignored: state=LOADING.')
            return

        try:
            if state in (PlayerState.PLAYING_AUDIO, PlayerState.PAUSED_AUDIO):
                if hasattr(self.audio_engine, 'seek'):
                    self.audio_engine.seek(position_sec)
            elif state in (PlayerState.PLAYING_VIDEO, PlayerState.PAUSED_VIDEO):
                if self.video_controller and hasattr(self.video_controller, 'seek'):
                    self.video_controller.seek(position_sec)
        except ENGINE_EXCEPTIONS as exc:
            logger.error('[EngineController] Errore durante il seek: %s', exc, exc_info=True)

    def set_volume(self, volume: float) -> None:
        if self._is_shutting_down:
            logger.debug('[EngineController] set_volume() ignored: shutting down.')
            return

        try:
            volume_f = float(volume)
        except (TypeError, ValueError):
            volume_f = 1.0

        volume_f = max(0.0, min(1.0, volume_f))

        try:
            if hasattr(self.audio_engine, 'set_volume'):
                self.audio_engine.set_volume(volume_f)

            if self.video_controller and self.state_manager.is_video():
                if hasattr(self.video_controller, 'set_volume'):
                    self.video_controller.set_volume(volume_f)
        except ENGINE_EXCEPTIONS as exc:
            logger.error('[EngineController] Errore impostando il volume: %s', exc, exc_info=True)

    def set_loop(self, loop: bool) -> None:
        if self._is_shutting_down:
            logger.debug('[EngineController] set_loop() ignored: shutting down.')
            return

        loop_b = bool(loop)
        state = self.state_manager.state
        try:
            if state in (PlayerState.PLAYING_AUDIO, PlayerState.PAUSED_AUDIO):
                if hasattr(self.audio_engine, 'set_loop'):
                    self.audio_engine.set_loop(loop_b)
            elif state in (PlayerState.PLAYING_VIDEO, PlayerState.PAUSED_VIDEO, PlayerState.LOADING):
                if self.video_controller and hasattr(self.video_controller, 'set_loop'):
                    self.video_controller.set_loop(loop_b)
        except ENGINE_EXCEPTIONS as exc:
            logger.error('[EngineController] Errore impostando il loop: %s', exc, exc_info=True)

    def should_repeat_current_track(self) -> bool:
        """Indica se il playback corrente deve ripetersi a fine traccia.

        Per l'audio il valore e' congelato all'avvio della riproduzione corrente,
        cosi' il toggle loop non altera il brano gia' in corso.
        """
        state = self.state_manager.state
        try:
            if state in (PlayerState.PLAYING_AUDIO, PlayerState.PAUSED_AUDIO):
                return bool(getattr(self.audio_engine, '_current_play_uses_native_loop', False))
            if state in (PlayerState.PLAYING_VIDEO, PlayerState.PAUSED_VIDEO, PlayerState.LOADING):
                if self.video_controller and hasattr(self.video_controller, 'loop_enabled'):
                    return bool(getattr(self.video_controller, 'loop_enabled'))
                return bool(getattr(self.state_manager, 'loop_enabled', False))
        except (ENGINE_EXCEPTIONS + STATE_EXCEPTIONS):
            logger.debug('[EngineController] should_repeat_current_track() fallback failed.', exc_info=True)
        return False
