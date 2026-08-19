from __future__ import annotations

from collections.abc import Iterable, Mapping
import logging
import os
import re
import sys
import unicodedata
from pathlib import Path
from typing import Union

from src.config.app_metadata import get_app_name

logger = logging.getLogger(__name__)

_WINDOWS_MAX_FILENAME_UNITS = 255
_DEFAULT_COMPOSABLE_FILENAME_UNITS = 123
_MAX_FILENAME_INPUT_CODEPOINTS = 4_096
_MAX_FILENAME_COLLISION_CANDIDATES = 65_536
_MAX_FILENAME_COLLISION_ATTEMPTS = 100_000
_WINDOWS_INVALID_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_FILENAME_WHITESPACE_RE = re.compile(r'\s+')
_FILENAME_UNDERSCORE_RE = re.compile(r'_+')
_WINDOWS_RESERVED_FILENAME_STEMS = frozenset(
    {
        'aux',
        'clock$',
        'con',
        'conin$',
        'conout$',
        'nul',
        'prn',
        *(f'com{index}' for index in range(1, 10)),
        *(f'lpt{index}' for index in range(1, 10)),
    }
)


def format_duration(seconds: Union[int, float]) -> str:
    """
    Formatta una durata in secondi in una stringa nel formato HH:MM:SS o MM:SS.
    """
    try:
        total_seconds = int(seconds)
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)

        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"
    except (TypeError, ValueError) as e:
        logger.error("Errore formattazione durata: %s", e, exc_info=True)
        return "00:00"


def _validate_filename_limit(max_length: int) -> None:
    if isinstance(max_length, bool) or not isinstance(max_length, int):
        raise TypeError('max_length must be an integer')
    if not 1 <= max_length <= _WINDOWS_MAX_FILENAME_UNITS:
        raise ValueError(
            f'max_length must be between 1 and {_WINDOWS_MAX_FILENAME_UNITS}'
        )


def _replace_unpaired_surrogates(text: str) -> str:
    return ''.join(
        '_' if 0xD800 <= ord(character) <= 0xDFFF else character
        for character in text
    )


def _utf16_units(text: str) -> int:
    return sum(2 if ord(character) > 0xFFFF else 1 for character in text)


def _truncate_utf16(text: str, max_units: int) -> str:
    used_units = 0
    result: list[str] = []
    for character in text:
        character_units = 2 if ord(character) > 0xFFFF else 1
        if used_units + character_units > max_units:
            break
        result.append(character)
        used_units += character_units
    return ''.join(result)


def _normalize_filename_candidate(text: str) -> str:
    bounded_text = text[:_MAX_FILENAME_INPUT_CODEPOINTS]
    normalized = unicodedata.normalize('NFKC', _replace_unpaired_surrogates(bounded_text))
    normalized = normalized.strip().strip(' .')
    normalized = _WINDOWS_INVALID_FILENAME_RE.sub('_', normalized)
    normalized = _FILENAME_WHITESPACE_RE.sub('_', normalized)
    normalized = _FILENAME_UNDERSCORE_RE.sub('_', normalized)
    candidate = normalized.rstrip(' .')
    return '' if not candidate.strip('_') else candidate


def _is_windows_reserved_filename(candidate: str) -> bool:
    stem = candidate.split('.', 1)[0].rstrip(' .').casefold()
    return stem in _WINDOWS_RESERVED_FILENAME_STEMS


def _protect_reserved_filename(candidate: str, max_length: int) -> str:
    if not _is_windows_reserved_filename(candidate):
        return candidate
    return _truncate_utf16(f'_{candidate}', max_length).rstrip(' .')


def _sanitize_filename_component(
    text: str,
    *,
    fallback: str,
    max_length: int,
) -> str:
    if not isinstance(text, str):
        raise TypeError('text must be a string')
    if not isinstance(fallback, str):
        raise TypeError('fallback must be a string')

    candidate = _truncate_utf16(_normalize_filename_candidate(text), max_length)
    candidate = candidate.rstrip(' .')
    if not candidate:
        candidate = _truncate_utf16(_normalize_filename_candidate(fallback), max_length)
        candidate = candidate.rstrip(' .')
    if not candidate:
        candidate = _truncate_utf16('untitled', max_length)
    return _protect_reserved_filename(candidate, max_length)


def windows_filename_collision_key(name: str) -> str:
    """Return a conservative Windows filename-equivalence key.

    Edge cases:
        1. Canonically equivalent Unicode spellings must share one key.
        2. Case-only differences must collide on case-insensitive filesystems.
        3. Trailing spaces or dots must not create a distinct apparent name.
    """
    if not isinstance(name, str):
        raise TypeError('name must be a string')
    candidate = _truncate_utf16(
        _normalize_filename_candidate(name),
        _WINDOWS_MAX_FILENAME_UNITS,
    ).rstrip(' .')
    return unicodedata.normalize('NFKC', candidate).casefold()


def _split_filename_extension(candidate: str) -> tuple[str, str]:
    stem, separator, suffix = candidate.rpartition('.')
    if not separator or not stem:
        return candidate, ''
    return stem, f'.{suffix}'


def _fit_collision_candidate(candidate: str, index: int, max_length: int) -> str:
    suffix = f'_{index}'
    stem, extension = _split_filename_extension(candidate)
    suffix_units = _utf16_units(suffix)
    extension_units = _utf16_units(extension)

    if suffix_units >= max_length:
        return _truncate_utf16(str(index), max_length)
    if extension_units + suffix_units >= max_length:
        extension = ''
        extension_units = 0

    stem_budget = max_length - suffix_units - extension_units
    fitted_stem = _truncate_utf16(stem, stem_budget).rstrip(' .')
    fitted = f'{fitted_stem}{suffix}{extension}'
    return _protect_reserved_filename(fitted, max_length)


def _collect_filename_collision_keys(existing_names: Iterable[str]) -> set[str]:
    if isinstance(existing_names, (str, bytes)):
        raise TypeError('existing_names must be an iterable of filename strings')

    keys: set[str] = set()
    for index, existing_name in enumerate(existing_names, start=1):
        if index > _MAX_FILENAME_COLLISION_CANDIDATES:
            raise ValueError(
                'existing_names exceeds the bounded collision-candidate limit'
            )
        keys.add(windows_filename_collision_key(existing_name))
    return keys


def safe_filename(
    text: str,
    max_length: int = _DEFAULT_COMPOSABLE_FILENAME_UNITS,
    *,
    fallback: str = 'untitled',
    existing_names: Iterable[str] = (),
) -> str:
    """Create a deterministic filename component valid on Windows.

    The limit is measured in UTF-16 code units, matching the Windows filename
    component model. The conservative default leaves room for WaveHelm's
    composed export suffixes; callers owning the complete component may pass
    a larger explicit limit up to 255. When ``existing_names`` is supplied,
    collisions are resolved conservatively using NFKC normalization, case folding, and
    Windows trailing-dot/space semantics.

    Edge cases:
        1. Reserved device names such as CON, AUX, COM1, or LPT9 are prefixed.
        2. Empty, invalid-only, or unpaired-surrogate input uses a safe fallback.
        3. Unicode/case-insensitive collisions receive a bounded numeric suffix.
        4. Supplementary Unicode characters are never split across UTF-16 units.
        5. Pathological input is bounded before Unicode normalization.
    """
    _validate_filename_limit(max_length)
    candidate = _sanitize_filename_component(
        text,
        fallback=fallback,
        max_length=max_length,
    )
    used_keys = _collect_filename_collision_keys(existing_names)
    if windows_filename_collision_key(candidate) not in used_keys:
        return candidate

    for index in range(2, _MAX_FILENAME_COLLISION_ATTEMPTS + 2):
        alternative = _fit_collision_candidate(candidate, index, max_length)
        if windows_filename_collision_key(alternative) not in used_keys:
            return alternative
    raise ValueError('unable to allocate a unique bounded Windows filename')


def format_file_size(size_bytes: int) -> str:
    """
    Formatta una dimensione in byte in una stringa leggibile (KB, MB, GB).
    """
    if size_bytes < 1024:
        return f"{size_bytes} B"

    value = float(size_bytes)
    for unit in ["KB", "MB", "GB"]:
        value /= 1024.0
        if value < 1024:
            return f"{value:.2f} {unit}"

    return f"{value:.2f} TB"


def is_audio_file(file_path: str) -> bool:
    """
    Verifica se un file è un file audio supportato in base all'estensione.
    """
    audio_extensions = {
        ".mp3",
        ".wav",
        ".flac",
        ".ogg",
        ".m4a",
        ".aac",
        ".opus",
        ".wma",
    }
    ext = os.path.splitext(file_path)[1].lower()
    return ext in audio_extensions


def is_video_file(file_path: str) -> bool:
    """
    Verifica se un file è un file video supportato in base all'estensione.
    """
    video_extensions = {
        ".mp4",
        ".avi",
        ".mov",
        ".mkv",
        ".webm",
        ".flv",
        ".m4v",
        ".asf",
        ".qt",
        ".ts",
        ".m2ts",
        ".mts",
        ".f4v",
        ".3gp",
        ".3g2",
        ".wmv",
        ".mpeg",
    }
    ext = os.path.splitext(file_path)[1].lower()
    return ext in video_extensions


def get_system_downloads_path() -> Path:
    """
    Restituisce il percorso della cartella "Downloads" dell'utente, in modo cross-platform.
    Crea la cartella se non esiste.
    """
    downloads_path = Path.home() / "Downloads"
    try:
        downloads_path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.error("Impossibile creare la cartella Downloads in %s: %s", downloads_path, e, exc_info=True)
    return downloads_path


def resolve_user_config_root(
    *,
    os_name: str | None = None,
    sys_platform: str | None = None,
    env: Mapping[str, str] | None = None,
    home_dir: Path | None = None,
) -> Path:
    """
    Restituisce la directory base di configurazione utente in modo iniettabile e testabile.
    """
    effective_os_name = os.name if os_name is None else os_name
    effective_sys_platform = sys.platform if sys_platform is None else sys_platform
    effective_env = os.environ if env is None else env
    effective_home = Path.home() if home_dir is None else Path(home_dir)

    if effective_os_name == "nt":
        appdata = effective_env.get("APPDATA")
        return Path(appdata) if appdata else effective_home / "AppData" / "Roaming"
    if effective_sys_platform == "darwin":
        return effective_home / "Library" / "Application Support"

    xdg_config_home = effective_env.get("XDG_CONFIG_HOME")
    return Path(xdg_config_home) if xdg_config_home else effective_home / ".config"


def get_user_data_dir(
    *,
    os_name: str | None = None,
    sys_platform: str | None = None,
    env: Mapping[str, str] | None = None,
    home_dir: Path | None = None,
    app_name: str | None = None,
) -> Path:
    """
    Restituisce il percorso della cartella dati dell'applicazione, specifica per l'utente e il sistema operativo.
    Crea la cartella se non esiste.

    - Windows: %APPDATA%/<AppName>
    - macOS: ~/Library/Application Support/<AppName>
    - Linux: ~/.config/<AppName> (fallback) o XDG_CONFIG_HOME/<AppName>
    """
    resolved_app_name = app_name or get_app_name()
    base_dir = resolve_user_config_root(
        os_name=os_name,
        sys_platform=sys_platform,
        env=env,
        home_dir=home_dir,
    )
    resolved_home = Path.home() if home_dir is None else Path(home_dir)

    app_data_dir = base_dir / resolved_app_name

    try:
        app_data_dir.mkdir(parents=True, exist_ok=True)
        return app_data_dir
    except OSError as e:
        logger.error("Impossibile creare la directory dati app in %s: %s", app_data_dir, e, exc_info=True)
        fallback_dir = resolved_home / f".{resolved_app_name.lower()}_data"
        try:
            fallback_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            # ultima spiaggia: home
            return resolved_home
        return fallback_dir


def get_app_data_path(*parts: str, create: bool = True) -> Path:
    """
    API attesa dal resto dell'app: ritorna un Path sotto la directory dati utente dell'app.

    Esempi:
        get_app_data_path("themes") -> <app_data>/themes
        get_app_data_path("config", "ui.json") -> <app_data>/config/ui.json

    Args:
        *parts: sotto-percorsi da concatenare
        create: se True, crea la directory (o la parent directory se l'ultimo elemento sembra un file)

    Returns:
        Path risultante.
    """
    base = get_user_data_dir()
    path = base.joinpath(*parts) if parts else base

    if create:
        try:
            # se sembra un file (ha suffisso), crea la parent; altrimenti crea la directory
            if path.suffix:
                path.parent.mkdir(parents=True, exist_ok=True)
            else:
                path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.error("Impossibile creare il path dati app %s: %s", path, e, exc_info=True)

    return path

def get_downloads_path() -> Path:
    """
    Alias retro-compatibile: alcune parti dell'app si aspettano get_downloads_path().
    Manteniamo come default la cartella Downloads di sistema.
    """
    return get_system_downloads_path()
