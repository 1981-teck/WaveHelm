from __future__ import annotations

import logging
from typing import Any, Dict

from src.audio.audio_events import AudioEventType

logger = logging.getLogger("src.controller.settings_controller")

"""Runtime update helpers extracted from settings_controller.

Edge cases handled by this module:
- Volume updates may target engines/players that expose different setter names or only a
  writable ``volume`` attribute.
- Runtime refresh requests may carry malformed payloads or non-numeric values after import,
  reset, or UI-driven updates.
- Language, theme, and video runtime sync can fail independently from persistence, leaving the
  stored setting valid while the live runtime remains partially stale.

Mitigations:
- Probe a bounded list of setter names and fall back to a plain attribute assignment.
- Normalize inbound payloads defensively and use deterministic defaults for invalid volume data.
- Report runtime sync failures explicitly through the controller error channel instead of
  silently swallowing partial refresh errors.
"""

_VOLUME_SETTER_CANDIDATES = (
    "set_volume",
    "set_master_volume",
    "set_output_volume",
    "set_gain",
)
_VIDEO_RUNTIME_KEYS = {
    "video_hw_accel_enabled",
    "video_hw_device",
    "video_drop_late_frames",
    "video_frame_queue_size",
    "video_decode_threads",
    "video_fast_seek",
    "video_resize_quality_high",
}


def _handle_volume_runtime_update(self, value: Any, key: str) -> None:
    try:
        volume = float(value) / 100.0
    except (TypeError, ValueError):
        volume = 0.7

    audio_updated = self._set_engine_volume_safe(volume)
    video_updated = self._set_video_volume_safe(volume)
    if not audio_updated:
        self._report_runtime_sync_failure(
            key,
            "audio engine volume update could not be applied",
        )
    if self.video_player is not None and not video_updated:
        self._report_runtime_sync_failure(
            key,
            "video player volume update could not be applied",
        )


def _handle_language_runtime_update(self, value: Any, key: str) -> None:
    try:
        self.localization_manager.set_language(str(value))
        self._publish_language_changed()
    except self.RUNTIME_EXCEPTIONS as error:
        self._handle_error(error, "error_setting_value", key=key)


def _handle_theme_runtime_update(self, key: str) -> None:
    try:
        theme = self.settings_manager.get_setting("theme", "System")
        primary = self.settings_manager.get_setting("primary_color", "blue")
        self.theme_manager.set_theme(theme, primary)
    except (self.SETTINGS_MANAGER_EXCEPTIONS + self.RUNTIME_EXCEPTIONS) as error:
        self._handle_error(error, "error_setting_value", key=key)


def _handle_video_runtime_update(self, key: str) -> None:
    try:
        self._apply_video_runtime_from_settings(self.get_all_settings())
    except (self.SETTINGS_MANAGER_EXCEPTIONS + self.RUNTIME_EXCEPTIONS) as error:
        self._handle_error(error, "error_setting_value", key=key)


def _on_settings_updated(self, data: Dict[str, Any]) -> None:
    """Reagisce agli aggiornamenti per-chiave applicando l'effetto lato runtime."""
    payload = data if isinstance(data, dict) else {}
    key = payload.get("key")
    value = payload.get("value")
    if key is None:
        return

    if key == "volume":
        _handle_volume_runtime_update(self, value, str(key))
        return
    if key == "language":
        _handle_language_runtime_update(self, value, str(key))
        return
    if key in ("theme", "primary_color"):
        _handle_theme_runtime_update(self, str(key))
        return
    if key in _VIDEO_RUNTIME_KEYS:
        _handle_video_runtime_update(self, str(key))


def _report_runtime_sync_failure(self, key: str, detail: str) -> None:
    message = self._get_localized_text("error_setting_value", key=key)
    logger.error("%s: %s", message, detail)
    self._publish_event(
        AudioEventType.ERROR,
        {"message": f"{message}: {detail}"},
    )


def _set_object_volume_safe(self, target: object | None, target_label: str, volume_01: float) -> bool:
    if target is None:
        return True

    for name in _VOLUME_SETTER_CANDIDATES:
        if not hasattr(target, name):
            continue
        try:
            getattr(target, name)(float(volume_01))
            return True
        except self.RUNTIME_EXCEPTIONS:
            logger.debug(
                "%s volume update via %s failed.",
                target_label,
                name,
                exc_info=True,
            )

    try:
        if hasattr(target, "volume"):
            target.volume = float(volume_01)
            return True
    except self.RUNTIME_EXCEPTIONS:
        logger.debug(
            "%s volume attribute update failed.",
            target_label,
            exc_info=True,
        )

    logger.warning(
        "Unable to apply runtime %s volume update for %s.",
        target_label.lower(),
        type(target).__name__,
    )
    return False


def _set_engine_volume_safe(self, volume_01: float) -> bool:
    return _set_object_volume_safe(self, self.audio_engine, "Audio engine", volume_01)


def _set_video_volume_safe(self, volume_01: float) -> bool:
    return _set_object_volume_safe(self, self.video_player, "Video player", volume_01)


SETTINGS_CONTROLLER_RUNTIME_BINDINGS = (
    ("_on_settings_updated", _on_settings_updated),
    ("_report_runtime_sync_failure", _report_runtime_sync_failure),
    ("_set_engine_volume_safe", _set_engine_volume_safe),
    ("_set_video_volume_safe", _set_video_volume_safe),
)
