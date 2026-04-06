-- WaveHelm - playlist & items schema (final & idempotent)
-- Provides a flexible items table that supports either media_id FK or external reference.
-- Safe to run multiple times.

PRAGMA foreign_keys=ON;
BEGIN;

-- Ensure playlists table exists (basic shape; adjust if you already have a richer table)
CREATE TABLE IF NOT EXISTS playlists (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Items referencing either an internal media table OR an external resource (path/url)
CREATE TABLE IF NOT EXISTS playlist_items (
    id          INTEGER PRIMARY KEY,
    playlist_id INTEGER NOT NULL,
    media_id    INTEGER,                    -- optional FK to media.id (if your 'media' table exists)
    external_id TEXT,                       -- path/URL when media_id is not available
source_type TEXT NOT NULL DEFAULT 'local',  -- e.g., 'local','stream'
    position    INTEGER NOT NULL DEFAULT 0,
    added_at    TEXT NOT NULL DEFAULT (datetime('now')),

    -- constraints
    FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE,
    -- If you have a 'media' table, you can enable the FK below by uncommenting:
    -- FOREIGN KEY (media_id) REFERENCES media(id) ON DELETE SET NULL,

    CHECK (media_id IS NOT NULL OR external_id IS NOT NULL)
);

-- Uniqueness: avoid duplicates inside the same playlist.
-- (Allows either media_id or external_id uniqueness, depending on which is used)
CREATE UNIQUE INDEX IF NOT EXISTS ux_playlist_items_media
    ON playlist_items (playlist_id, media_id)
    WHERE media_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS ux_playlist_items_external
    ON playlist_items (playlist_id, external_id)
    WHERE external_id IS NOT NULL;

-- Helpful lookups & ordering
CREATE INDEX IF NOT EXISTS idx_playlist_items_playlist ON playlist_items (playlist_id);
CREATE INDEX IF NOT EXISTS idx_playlist_items_position ON playlist_items (playlist_id, position);

COMMIT;
