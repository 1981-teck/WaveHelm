-- WaveHelm - history schema (final & idempotent)
-- Creates normalized history/event_types tables if missing.
-- Safe to run multiple times.

PRAGMA foreign_keys=ON;
BEGIN;

-- Event types
CREATE TABLE IF NOT EXISTS event_types (
    id    INTEGER PRIMARY KEY,
    name  TEXT NOT NULL UNIQUE
);

-- Seed common types
INSERT OR IGNORE INTO event_types (id, name) VALUES
    (1, 'playback'),
    (2, 'pause'),
    (3, 'stop'),
    (4, 'seek'),
    (5, 'open'),
    (6, 'download');

-- Final history table
CREATE TABLE IF NOT EXISTS history (
    id         INTEGER PRIMARY KEY,
    title      TEXT,
    path       TEXT,                         -- file path or external URL
    event_type INTEGER NOT NULL DEFAULT 1,   -- FK -> event_types(id); default 'playback'
    duration   INTEGER,                      -- seconds
    timestamp  TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (event_type) REFERENCES event_types(id) ON UPDATE CASCADE ON DELETE RESTRICT
);

-- Helpful indexes
CREATE INDEX IF NOT EXISTS idx_history_timestamp ON history(timestamp);
CREATE INDEX IF NOT EXISTS idx_history_event_type ON history(event_type);
CREATE INDEX IF NOT EXISTS idx_history_path ON history(path);

COMMIT;