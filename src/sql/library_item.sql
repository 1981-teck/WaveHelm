CREATE TABLE IF NOT EXISTS library_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    media_type TEXT NOT NULL,
    duration REAL NOT NULL,
    metadata TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);