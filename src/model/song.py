from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from datetime import timedelta
import os


@dataclass
class Song:
    """
    Classe che rappresenta una canzone nel sistema.
    """

    id: str
    title: str
    artist: str
    album: str
    duration: timedelta
    file_path: str
    genre: Optional[str] = None
    year: Optional[int] = None
    track_number: Optional[int] = None
    artwork_url: Optional[str] = None
    tags: Optional[List[str]] = None

    @property
    def file_name(self) -> str:
        """Restituisce il nome del file senza percorso."""
        return os.path.basename(self.file_path)

    @property
    def formatted_duration(self) -> str:
        """Restituisce la durata formattata come stringa MM:SS."""
        total_seconds = int(self.duration.total_seconds())
        minutes, seconds = divmod(total_seconds, 60)
        return f"{minutes:02d}:{seconds:02d}"

    def to_dict(self) -> Dict[str, Any]:
        """Converte l'oggetto in un dizionario per la serializzazione."""
        return {
            "id": self.id,
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "duration": self.duration.total_seconds(),
            "file_path": self.file_path,
            "genre": self.genre,
            "year": self.year,
            "track_number": self.track_number,
            "artwork_url": self.artwork_url,
            "tags": self.tags or [],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Song:
        """Crea un'istanza di Song da un dizionario."""
        return cls(
            id=data.get("id", ""),
            title=data["title"],
            artist=data["artist"],
            album=data["album"],
            duration=timedelta(seconds=data["duration"]),
            file_path=data["file_path"],
            genre=data.get("genre"),
            year=data.get("year"),
            track_number=data.get("track_number"),
            artwork_url=data.get("artwork_url"),
            tags=data.get("tags"),
        )

    def __str__(self) -> str:
        return f"{self.title} - {self.artist} ({self.formatted_duration})"
