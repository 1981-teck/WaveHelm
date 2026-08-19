# -*- coding: utf-8 -*-
from __future__ import annotations

import inspect
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List

from src.utils.app_paths import get_app_data_dir

# Fallback di modulo: NON usare `self` qui
SRC_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCALES_DIR = SRC_DIR / "locales"  # solo come default non vincolante
logger = logging.getLogger(__name__)

LOCALIZATION_FORMAT_EXCEPTIONS = (
    IndexError,
    KeyError,
    TypeError,
    ValueError,
)
LOCALIZATION_LOAD_EXCEPTIONS = (
    OSError,
    TypeError,
    ValueError,
    json.JSONDecodeError,
)
LOCALIZATION_CALLBACK_EXCEPTIONS = (
    AttributeError,
    RuntimeError,
    TypeError,
    ValueError,
)
LOCALES_ENV_VAR = "WAVEHELM_LOCALES"


def _callback_requires_language_arg(callback: Callable[..., None]) -> bool:
    try:
        signature = inspect.signature(callback)
    except (TypeError, ValueError):
        return False

    required_positional = 0
    positional_capacity = 0
    has_varargs = False

    for parameter in signature.parameters.values():
        if parameter.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            positional_capacity += 1
            if parameter.default is inspect.Signature.empty:
                required_positional += 1
        elif parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            has_varargs = True

    return required_positional == 1 and (positional_capacity >= 1 or has_varargs)


def _resolve_locales_dir(explicit: str | Path | None = None) -> Path:
    """
    Risolve la cartella locales in modo robusto.
    Ordine:
    1) parametro esplicito
    2) env WAVEHELM_LOCALES
    3) sys._MEIPASS (PyInstaller)
    4) src/locales del progetto
    5) project_root/locales
    6) CWD/src/locales oppure CWD/locales
    7) ~/.wavehelm/locales
    """
    if explicit:
        p = Path(explicit).expanduser().resolve()
        if p.exists():
            return p

    envp = os.environ.get(LOCALES_ENV_VAR)
    if envp:
        p = Path(envp).expanduser().resolve()
        if p.exists():
            return p

    base = getattr(sys, "_MEIPASS", None)
    if base:
        pyinstaller_candidates = [
            Path(base) / "locales",
            Path(base) / "src" / "locales",
        ]
        for p in pyinstaller_candidates:
            if p.exists():
                return p.resolve()

    here = Path(__file__).resolve()
    candidates = [
        here.parents[1] / "locales",
        here.parents[2] / "locales",
        Path.cwd() / "src" / "locales",
        Path.cwd() / "locales",
        get_app_data_dir() / "locales",
    ]
    for p in candidates:
        if p.exists():
            return p.resolve()

    return candidates[0]


class LocalizationManager:
    def __init__(
        self, locales_dir: str | Path | None = None, fallback_language: str = "en"
    ) -> None:
        self.locales_dir: Path = _resolve_locales_dir(locales_dir)
        self.fallback_language = (fallback_language or "en").lower()
        self.current_language = self.fallback_language
        self._strings: Dict[str, str] = {}
        self._fallback_strings: Dict[str, str] = {}
        self._language_change_callbacks: List[Callable[..., None]] = []

        self._fallback_strings = self._load_language_file(self.fallback_language)
        self._strings = dict(self._fallback_strings)

    def set_language(self, language: str) -> None:
        language = (language or "").strip().lower() or self.fallback_language
        if language == self.current_language and self._strings:
            return
        strings = self._load_language_file(language)
        if strings:
            self._strings = {**self._fallback_strings, **strings}
            self.current_language = language
        else:
            self._strings = dict(self._fallback_strings)
            self.current_language = self.fallback_language
        self._notify_language_change_callbacks()

    def get_current_language(self) -> str:
        """Return the currently active language code (compat helper for UI)."""
        return getattr(self, "current_language", self.fallback_language)

    def get_language(self) -> str:
        """Alias for get_current_language (compat)."""
        return self.get_current_language()

    def get_text(self, key: str, default: str | None = None, **kwargs: Any) -> str:
        if not key:
            return default or ""
        text = (
            self._strings.get(key)
            or self._fallback_strings.get(key)
            or (default or key)
        )
        if kwargs:
            try:
                text = str(text).format(**kwargs)
            except LOCALIZATION_FORMAT_EXCEPTIONS as error:
                logger.debug(
                    "Localization formatting failed for key %s: %s",
                    key,
                    error,
                    exc_info=True,
                )
        return str(text)

    def register_language_change_callback(self, callback: Callable[..., None]) -> None:
        if not callable(callback):
            return
        if callback not in self._language_change_callbacks:
            self._language_change_callbacks.append(callback)

    def unregister_language_change_callback(self, callback: Callable[..., None]) -> None:
        try:
            self._language_change_callbacks.remove(callback)
        except ValueError as error:
            logger.debug(
                "Language change callback already absent during unregister: %r (%s)",
                callback,
                error,
                exc_info=True,
            )

    def _load_language_file(self, language: str) -> Dict[str, str]:
        try:
            p = self.locales_dir / f"{language}.json"
            if not p.exists():
                return {}
            with p.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {
                    str(k): str(v)
                    for k, v in data.items()
                    if isinstance(v, (str, int, float))
                }
        except LOCALIZATION_LOAD_EXCEPTIONS as error:
            logger.debug(
                "Failed to load locale file for language %s: %s",
                language,
                error,
                exc_info=True,
            )
        return {}

    def _notify_language_change_callbacks(self) -> None:
        for callback in list(self._language_change_callbacks):
            try:
                if _callback_requires_language_arg(callback):
                    callback(self.current_language)
                else:
                    callback()
            except LOCALIZATION_CALLBACK_EXCEPTIONS:
                logger.debug(
                    "Language change callback failed: %r",
                    callback,
                    exc_info=True,
                )
                continue

    def get_locales_dir(self) -> Path:
        return self.locales_dir

    def get_available_languages(self) -> Dict[str, str]:
        """Scans the locales directory and returns a dict of available languages."""
        languages = {}
        if not self.locales_dir.is_dir():
            return languages

        fallback_names = {
            "en": "English",
            "it": "Italiano",
        }

        for file in self.locales_dir.glob("*.json"):
            try:
                lang_code = file.stem
                with file.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                lang_name = data.get(
                    "_language_name",
                    data.get(
                        "_language_name_",
                        fallback_names.get(lang_code, lang_code.capitalize()),
                    ),
                )
                languages[lang_code] = lang_name
            except (json.JSONDecodeError, IOError):
                continue
        return dict(sorted(languages.items()))
