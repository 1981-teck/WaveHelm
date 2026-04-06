from __future__ import annotations

from collections.abc import Mapping
import logging
import os
import re
import sys
from pathlib import Path
from typing import Union

from src.config.app_metadata import get_app_name

logger = logging.getLogger(__name__)


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


def safe_filename(text: str, max_length: int = 255) -> str:
    """
    Crea un nome file sicuro rimuovendo caratteri non validi e troncando se necessario.
    """
    safe_text = re.sub(r'[<>:"/\\|?*]', "", text)
    safe_text = safe_text.strip().strip(".")
    safe_text = safe_text.replace(" ", "_")

    if len(safe_text) > max_length:
        safe_text = safe_text[:max_length]

    return safe_text


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
