"""
playback_status – adapter helpers to read position/duration/seek
from either AudioEngine or VideoController using only a PlayerController instance.
"""

from __future__ import annotations

import logging
from typing import Optional

from src.model.media_file import MediaType

logger = logging.getLogger(__name__)

LOOKUP_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError)
FORMAT_EXCEPTIONS = (TypeError, ValueError)
ENGINE_EXCEPTIONS = (AttributeError, RuntimeError, TypeError, ValueError, OSError)



def _get_current_track(player_controller):
    """Resolve the current track from the facade or its underlying components."""
    try:
        track = getattr(player_controller, 'current_track', None)
        if track is not None:
            return track
    except LOOKUP_EXCEPTIONS:
        logger.debug('_get_current_track: current_track property failed', exc_info=True)

    try:
        queue_manager = getattr(player_controller, 'queue_manager', None)
        track = getattr(queue_manager, 'current_track', None)
        if track is not None:
            return track
    except LOOKUP_EXCEPTIONS:
        logger.debug('_get_current_track: queue_manager.current_track failed', exc_info=True)

    return getattr(player_controller, '_current_track', None)



def _get_video_controller(player_controller):
    """Resolve the active VideoController from PlayerController or EngineController."""
    direct = getattr(player_controller, 'video_controller', None)
    if direct is not None:
        return direct

    engine_controller = getattr(player_controller, 'engine_controller', None)
    if engine_controller is not None:
        return getattr(engine_controller, 'video_controller', None)

    return None



def _get_audio_engine(player_controller):
    """Resolve the active AudioEngine from PlayerController or EngineController."""
    direct = getattr(player_controller, 'audio_engine', None)
    if direct is not None:
        return direct

    engine_controller = getattr(player_controller, 'engine_controller', None)
    if engine_controller is not None:
        return getattr(engine_controller, 'audio_engine', None)

    return None



def is_video_current(player_controller) -> bool:
    """Return True if the PlayerController current track is a VIDEO item."""
    try:
        track = _get_current_track(player_controller)
        if track is None:
            return False
        media_type = getattr(track, 'media_type', None)
        return media_type == MediaType.VIDEO
    except LOOKUP_EXCEPTIONS:
        logger.debug('is_video_current failed', exc_info=True)
        return False



def get_duration(player_controller) -> Optional[float]:
    """Return total duration (seconds) for the current media item."""
    try:
        track = _get_current_track(player_controller)
        if track is not None:
            dur_attr = getattr(track, 'duration', None)
            if isinstance(dur_attr, (int, float)) and dur_attr > 0:
                return float(dur_attr)

        pc_get_duration = getattr(player_controller, 'get_duration', None)
        if callable(pc_get_duration):
            value = pc_get_duration()
            if isinstance(value, (int, float)) and value > 0:
                return float(value)

        if is_video_current(player_controller):
            video_controller = _get_video_controller(player_controller)
            if video_controller and hasattr(video_controller, 'get_duration'):
                try:
                    value = video_controller.get_duration()
                    if isinstance(value, (int, float)) and value > 0:
                        return float(value)
                except ENGINE_EXCEPTIONS:
                    logger.debug('get_duration: video_controller.get_duration failed', exc_info=True)

        audio_engine = _get_audio_engine(player_controller)
        if audio_engine is not None:
            for name in ('get_length', 'get_duration', 'length'):
                fn = getattr(audio_engine, name, None)
                if callable(fn):
                    try:
                        value = fn()
                    except ENGINE_EXCEPTIONS:
                        logger.debug('get_duration: AudioEngine.%s failed', name, exc_info=True)
                        continue
                    if isinstance(value, (int, float)) and value > 0:
                        return float(value)
        return None
    except LOOKUP_EXCEPTIONS:
        logger.exception('get_duration failed')
        return None



def get_position(player_controller) -> Optional[float]:
    """Return current playback position (seconds) for the current media item."""
    try:
        pc_get_position = getattr(player_controller, 'get_position', None)
        if callable(pc_get_position):
            value = pc_get_position()
            if isinstance(value, (int, float)):
                return float(value)

        if is_video_current(player_controller):
            video_controller = _get_video_controller(player_controller)
            if video_controller and hasattr(video_controller, 'get_position'):
                value = video_controller.get_position()
                if isinstance(value, (int, float)):
                    return float(value)

        audio_engine = _get_audio_engine(player_controller)
        if audio_engine is not None:
            for name in ('get_pos', 'get_position', 'tell'):
                fn = getattr(audio_engine, name, None)
                if callable(fn):
                    value = fn()
                    if isinstance(value, (int, float)):
                        return float(value)
        return None
    except (LOOKUP_EXCEPTIONS + ENGINE_EXCEPTIONS):
        logger.exception('get_position failed')
        return None



def seek_to(player_controller, seconds: float) -> bool:
    """Seek current media to absolute 'seconds'. Returns True if performed."""
    try:
        seconds = float(seconds)
    except FORMAT_EXCEPTIONS:
        logger.exception('seek_to failed')
        return False

    if seconds < 0:
        seconds = 0.0

    try:
        if is_video_current(player_controller):
            video_controller = _get_video_controller(player_controller)
            if video_controller is not None:
                fn = getattr(video_controller, 'seek', None)
                if callable(fn):
                    try:
                        return bool(fn(seconds))
                    except ENGINE_EXCEPTIONS:
                        logger.debug('VideoController.seek failed', exc_info=True)
            return False

        audio_engine = _get_audio_engine(player_controller)
        if audio_engine is not None:
            for name in ('seek', 'set_position', 'set_pos'):
                fn = getattr(audio_engine, name, None)
                if callable(fn):
                    try:
                        fn(float(seconds))
                        return True
                    except ENGINE_EXCEPTIONS:
                        logger.debug('AudioEngine.%s failed', name, exc_info=True)
        return False
    except LOOKUP_EXCEPTIONS:
        logger.exception('seek_to failed')
        return False
