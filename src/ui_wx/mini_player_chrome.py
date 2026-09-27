"""Existing wx appearance/native-window methods, extracted without behavior changes.

This is the dynamic wx/Win32 boundary, not the typed playback model. Ownership and
native-call behavior are retained; progress observation lives in the main controller.
"""
from __future__ import annotations

import logging
from typing import Any

from src.controller.playback_status import is_video_current
from src.ui_wx.common import (
    WX_CALLBACK_EXCEPTIONS,
    apply_colors,
    create_flow_sizer,
    get_localized_text,
    get_theme_colors,
    register_callback,
    set_label_text,
)

logger = logging.getLogger(__name__)



class MiniPlayerChrome:
    """Cohesive inherited view boundary; original public owner class is retained."""

    def _build_ui(self) -> None:
        wx = self._wx
        root = wx.BoxSizer(wx.VERTICAL)
        self.track_label = wx.StaticText(self.panel, label='WaveHelm')
        self.state_label = wx.StaticText(self.panel, label='--')
        info_row = wx.BoxSizer(wx.HORIZONTAL)
        info_row.Add(self.track_label, 1, wx.ALL | wx.EXPAND, 6)
        info_row.Add(self.state_label, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 6)
        self.shuffle_button = wx.Button(self.panel, label='Shuffle')
        self.loop_button = wx.Button(self.panel, label='Loop')
        self.prev_button = wx.Button(self.panel, label='Prev')
        self.play_button = wx.Button(self.panel, label='Play')
        self.pause_button = wx.Button(self.panel, label='Pause')
        self.next_button = wx.Button(self.panel, label='Next')
        self.stop_button = wx.Button(self.panel, label='Stop')
        self.volume_caption = wx.StaticText(self.panel, label='Volume')
        self.volume_slider = wx.Slider(self.panel, value=100, minValue=0, maxValue=100)
        self.mute_button = wx.Button(self.panel, label='Mute')
        controls_row = create_flow_sizer(wx)
        for widget in (
            self.shuffle_button,
            self.loop_button,
            self.prev_button,
            self.play_button,
            self.pause_button,
            self.next_button,
            self.stop_button,
            self.volume_caption,
            self.volume_slider,
            self.mute_button,
        ):
            controls_row.Add(widget, 0 if widget is not self.volume_slider else 1, wx.ALL | wx.EXPAND, 4)
        self.time_left_label = wx.StaticText(self.panel, label='--:--')
        self.progress_slider = wx.Slider(self.panel, value=0, minValue=0, maxValue=1000)
        self.time_right_label = wx.StaticText(self.panel, label='--:--')
        progress_row = wx.BoxSizer(wx.HORIZONTAL)
        progress_row.Add(self.time_left_label, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 6)
        progress_row.Add(self.progress_slider, 1, wx.ALL | wx.EXPAND, 4)
        progress_row.Add(self.time_right_label, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 6)
        root.Add(info_row, 0, wx.ALL | wx.EXPAND, 0)
        root.Add(controls_row, 0, wx.ALL | wx.EXPAND, 0)
        root.Add(progress_row, 0, wx.ALL | wx.EXPAND, 0)
        self.panel.SetSizer(root)


    def _bind_controls(self) -> None:
        wx = self._wx
        self.shuffle_button.Bind(wx.EVT_BUTTON, lambda _event: self._defer_player_action('toggle_shuffle'))
        self.loop_button.Bind(wx.EVT_BUTTON, lambda _event: self._defer_player_action('toggle_loop'))
        self.prev_button.Bind(wx.EVT_BUTTON, lambda _event: self._defer_player_action('previous'))
        self.play_button.Bind(wx.EVT_BUTTON, lambda _event: self._defer_player_action('play_action'))
        self.pause_button.Bind(wx.EVT_BUTTON, lambda _event: self._defer_player_action('pause'))
        self.next_button.Bind(wx.EVT_BUTTON, lambda _event: self._defer_player_action('next'))
        self.stop_button.Bind(wx.EVT_BUTTON, lambda _event: self._defer_player_action('stop'))
        self.mute_button.Bind(wx.EVT_BUTTON, self._on_mute_clicked)
        slider_event = getattr(wx, 'EVT_SLIDER', wx.EVT_BUTTON)
        self.volume_slider.Bind(slider_event, self._on_volume_slider_changed)
        self._bind_progress_gesture()


    def _register_callbacks(self) -> None:
        register_callback(
            self.localization_manager,
            'register_language_change_callback',
            self.update_localization,
            logger=logger,
            message='Unable to register wx MiniPlayer language callback.',
        )
        register_callback(
            self.theme_manager,
            'register_theme_change_callback',
            self.update_theme_colors,
            logger=logger,
            message='Unable to register wx MiniPlayer theme callback.',
        )


    def _read_initial_volume(self) -> float:
        """Read initial slider state without changing the audio backend.

        Edge cases: zero is a valid reading; missing or invalid readings retain
        the legacy 1.0 display default; out-of-range readings remain clamped.
        The legacy clamp maps NaN/+inf to 1.0 and -inf to 0.0. This is a one-shot
        GUI bootstrap, not a playback/timer path or an audio-volume setter.
        """
        getter = getattr(self.audio_engine, 'get_volume', None)
        if not callable(getter):
            return 1.0
        try:
            raw_volume = getter()
            volume = float(1.0 if raw_volume is None else raw_volume)
        except (OverflowError, TypeError, ValueError) + WX_CALLBACK_EXCEPTIONS:
            logger.debug('wx MiniPlayer volume bootstrap failed.', exc_info=True)
            return 1.0
        return max(0.0, min(1.0, volume))


    def _apply_volume_payload(self, payload: dict[str, Any]) -> None:
        volume = payload.get('volume')
        if volume is None:
            return
        try:
            value = max(0.0, min(1.0, float(volume)))
        except (OverflowError, TypeError, ValueError):
            return
        self._set_volume_slider(value)


    def _set_volume_slider(self, value: float) -> None:
        """Render volume and restore the caller's synchronization state.

        Widget/conversion errors propagate; nested writes retain an active outer
        guard; zero retains the last nonzero volume. No backend write occurs here.
        """
        if value > 0.0:
            self._last_nonzero_volume = value
        previous_sync = self._volume_sync
        self._volume_sync = True
        try:
            self.volume_slider.SetValue(int(round(value * 100)))
        finally:
            self._volume_sync = previous_sync


    def _apply_mute_payload(self, payload: dict[str, Any]) -> None:
        self._muted = bool(payload.get('muted', False))
        self._refresh_mute_label()


    def _apply_shuffle_payload(self, payload: dict[str, Any]) -> None:
        self._shuffle_enabled = bool(payload.get('shuffle_enabled', payload.get('shuffled', self._shuffle_enabled)))
        self._refresh_toggle_labels()


    def _apply_loop_payload(self, payload: dict[str, Any]) -> None:
        self._loop_enabled = bool(payload.get('loop_enabled', self._loop_enabled))
        self._refresh_toggle_labels()


    def _refresh_toggle_labels(self) -> None:
        shuffle = get_localized_text(self.localization_manager, 'btn_shuffle', 'Shuffle')
        loop = get_localized_text(self.localization_manager, 'btn_loop', 'Loop')
        set_label_text(self.shuffle_button, f'{shuffle} ✓' if self._shuffle_enabled else shuffle)
        set_label_text(self.loop_button, f'{loop} ✓' if self._loop_enabled else loop)


    def _refresh_mute_label(self) -> None:
        mute = get_localized_text(self.localization_manager, 'tooltip_mute', 'Mute/Unmute')
        suffix = ' ✓' if self._muted else ''
        set_label_text(self.mute_button, f'{mute}{suffix}')


    def _on_volume_slider_changed(self, _event: Any | None = None) -> None:
        if self._volume_sync:
            return
        value = max(0.0, min(1.0, float(self.volume_slider.GetValue()) / 100.0))
        if value > 0.0:
            self._last_nonzero_volume = value
        setter = getattr(self.player_controller, 'set_volume', None)
        if callable(setter):
            setter(value)
        if self._muted and value > 0.0:
            mute_setter = getattr(self.audio_engine, 'set_mute', None)
            if callable(mute_setter):
                mute_setter(False)
            self._muted = False
            self._refresh_mute_label()


    def _on_mute_clicked(self, _event: Any | None = None) -> None:
        new_muted = not self._muted
        if is_video_current(self.player_controller):
            target = 0.0 if new_muted else self._last_nonzero_volume
            setter = getattr(self.player_controller, 'set_volume', None)
            if callable(setter):
                setter(target)
            self._set_volume_slider(target)
            self._muted = new_muted
            self._refresh_mute_label()
            return
        mute_setter = getattr(self.audio_engine, 'set_mute', None)
        if callable(mute_setter):
            mute_setter(new_muted)
        self._muted = new_muted
        self._refresh_mute_label()


    def update_localization(self, *_: Any) -> None:
        set_label_text(self.volume_caption, get_localized_text(self.localization_manager, 'btn_volume', 'Volume'))
        set_label_text(self.prev_button, get_localized_text(self.localization_manager, 'btn_prev', 'Previous'))
        set_label_text(self.play_button, get_localized_text(self.localization_manager, 'btn_play', 'Play'))
        set_label_text(self.pause_button, get_localized_text(self.localization_manager, 'btn_pause', 'Pause'))
        set_label_text(self.next_button, get_localized_text(self.localization_manager, 'btn_next', 'Next'))
        set_label_text(self.stop_button, get_localized_text(self.localization_manager, 'btn_stop', 'Stop'))
        self._refresh_toggle_labels()
        self._refresh_mute_label()
        self._refresh_track_and_state_text()


    def update_theme_colors(self, *_: Any) -> None:
        colors = get_theme_colors(self.theme_manager)
        background = colors.get('footer_bg') or colors.get('panel_bg') or colors.get('bg_color')
        foreground = colors.get('text_color')
        accent = colors.get('button_color') or background
        widgets = [
            self.panel,
            self.track_label,
            self.state_label,
            self.volume_caption,
            self.time_left_label,
            self.progress_slider,
            self.time_right_label,
            self.volume_slider,
        ]
        for widget in widgets:
            apply_colors(widget, background=background, foreground=foreground)
        for button in (
            self.shuffle_button,
            self.loop_button,
            self.prev_button,
            self.play_button,
            self.pause_button,
            self.next_button,
            self.stop_button,
            self.mute_button,
        ):
            apply_colors(button, background=accent, foreground=foreground)


    def _set_enabled(self, widget: Any, enabled: bool) -> None:
        setter = getattr(widget, 'Enable', None)
        if callable(setter):
            setter(bool(enabled))


    def _refresh_track_and_state_text(self, payload: dict[str, Any] | None = None) -> None:
        title = self._current_track_title or get_localized_text(
            self.localization_manager,
            'mini_player_no_media',
            'No media loaded in MiniPlayer.',
        )
        set_label_text(self.track_label, title)
        if (payload and payload.get('playing')) or self._is_playing:
            state = get_localized_text(self.localization_manager, 'mini_player_state_playing', 'MiniPlayer state: playing.')
        else:
            state = get_localized_text(self.localization_manager, 'mini_player_state_paused_stopped', 'MiniPlayer state: paused/stopped.')
        set_label_text(self.state_label, state)
