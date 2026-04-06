from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from datetime import datetime
import os
import json
import logging
from src.utils.helpers import get_app_data_path
from .song import Song

logger = logging.getLogger(__name__)


@dataclass
class Playlist:
    """
    Classe che rappresenta una playlist di brani musicali.
    """

    id: str
    name: str
    songs: List[Song] = field(default_factory=list)
    creation_date: datetime = field(default_factory=datetime.now)
    last_modified: datetime = field(default_factory=datetime.now)
    description: Optional[str] = None
    cover_art: Optional[str] = None  # Percorso o URL dell'immagine di copertina

    def get_song_by_id(self, song_id: str) -> Optional[Song]:
        """Restituisce una canzone dalla playlist tramite il suo ID."""
        return next((s for s in self.songs if s.id == song_id), None)

    def get_song_index(self, song_id: str) -> Optional[int]:
        """Restituisce l'indice di una canzone nella playlist tramite il suo ID."""
        try:
            return next(i for i, s in enumerate(self.songs) if s.id == song_id)
        except StopIteration:
            return None

    def contains_song(self, song_id: str) -> bool:
        """Verifica se una canzone è presente nella playlist."""
        return any(s.id == song_id for s in self.songs)

    def add_song(self, song: Song):
        """Aggiunge una canzone alla playlist."""
        if not self.contains_song(song.id):
            self.songs.append(song)
            self.last_modified = datetime.now()
            logger.debug("Aggiunta canzone %s alla playlist %s", song.title, self.name)
        else:
            logger.warning(
                "Canzone %s già presente nella playlist %s", song.title, self.name
            )

    def remove_song(self, song_id: str):
        """Rimuove una canzone dalla playlist."""
        self.songs = [s for s in self.songs if s.id != song_id]
        self.last_modified = datetime.now()
        logger.debug("Rimossa canzone %s dalla playlist %s", song_id, self.name)

    def move_song(self, song_id: str, new_position: int):
        """Sposta una canzone a una nuova posizione nella playlist."""
        song_index = next(
            (i for i, s in enumerate(self.songs) if s.id == song_id), None
        )
        if song_index is not None:
            song = self.songs.pop(song_index)
            self.songs.insert(new_position, song)
            self.last_modified = datetime.now()
            logger.debug(
                "Spostata canzone %s alla posizione %d", song.title, new_position
            )

    def get_duration(self) -> float:
        """Calcola la durata totale della playlist in secondi."""
        return sum(s.duration.total_seconds() for s in self.songs)

    def formatted_duration(self) -> str:
        """Restituisce la durata totale formattata come HH:MM:SS."""
        total_seconds = int(self.get_duration())
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def to_dict(self) -> Dict[str, Any]:
        """Converte la playlist in un dizionario per la serializzazione."""
        return {
            "id": self.id,
            "name": self.name,
            "songs": [song.to_dict() for song in self.songs],
            "creation_date": self.creation_date.isoformat(),
            "last_modified": self.last_modified.isoformat(),
            "description": self.description,
            "cover_art": self.cover_art,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Playlist:
        """Crea una playlist da un dizionario."""
        playlist = cls(
            id=data["id"],
            name=data["name"],
            description=data.get("description"),
            cover_art=data.get("cover_art"),
        )

        # Converti le stringhe ISO in datetime
        playlist.creation_date = datetime.fromisoformat(data["creation_date"])
        playlist.last_modified = datetime.fromisoformat(data["last_modified"])

        # Ricostruisci le canzoni
        playlist.songs = [
            Song.from_dict(song_data) for song_data in data.get("songs", [])
        ]

        return playlist

    def save_to_file(self, directory: str):
        """Salva la playlist in un file JSON."""
        os.makedirs(directory, exist_ok=True)
        file_path = os.path.join(directory, f"{self.id}.json")

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

        logger.info("Playlist %s salvata in %s", self.name, file_path)

    @classmethod
    def load_from_file(cls, file_path: str) -> Playlist:
        """Carica una playlist da un file JSON."""
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        playlist = cls.from_dict(data)
        logger.info("Playlist %s caricata da %s", playlist.name, file_path)
        return playlist

    def __len__(self) -> int:
        return len(self.songs)

    def __str__(self) -> str:
        return f"{self.name} ({len(self)} brani, {self.formatted_duration()})"
