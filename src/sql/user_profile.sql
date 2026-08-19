CREATE TABLE IF NOT EXISTS user_profile (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_name TEXT NOT NULL,
    avatar_path TEXT,  -- Aggiungi questa linea
    stats_data TEXT,
    eq_settings_data TEXT,
    effects_settings_data TEXT,
    ambient_presets TEXT, -- Aggiunto per i preset ambientali
    created_at TEXT NOT NULL,
    last_updated TEXT NOT NULL
);
