from pathlib import Path
from src.utils.helpers import get_user_data_dir

def get_app_data_dir() -> Path:
    r"""Restituisce la cartella dati utente di WaveHelm."""
    return get_user_data_dir()
