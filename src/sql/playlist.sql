CREATE TABLE IF NOT EXISTS playlists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    cover_art TEXT,
    creation_date TEXT NOT NULL,
    last_modified TEXT NOT NULL
);
