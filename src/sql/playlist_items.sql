CREATE TABLE IF NOT EXISTS playlist_items (
    playlist_id INTEGER NOT NULL,
    media_path TEXT NOT NULL,
    position INTEGER NOT NULL,
    added_at TEXT NOT NULL,
    PRIMARY KEY (playlist_id, media_path),
    FOREIGN KEY (playlist_id) REFERENCES playlists (id) ON DELETE CASCADE
);
