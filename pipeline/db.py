import sqlite3
from contextlib import contextmanager
from .config import DB_PATH


@contextmanager
def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS formats (
                format_id TEXT PRIMARY KEY,
                generation INTEGER DEFAULT 9,
                tier TEXT,
                name TEXT
            );

            CREATE TABLE IF NOT EXISTS months (
                month TEXT PRIMARY KEY,
                total_battles INTEGER
            );

            CREATE TABLE IF NOT EXISTS elo_tiers (
                elo_tier INTEGER PRIMARY KEY
            );

            CREATE TABLE IF NOT EXISTS discovered_sources (
                month TEXT NOT NULL,
                format_id TEXT NOT NULL,
                elo_tier INTEGER NOT NULL,
                source_type TEXT NOT NULL DEFAULT 'usage',
                PRIMARY KEY (month, format_id, elo_tier, source_type)
            );

            CREATE TABLE IF NOT EXISTS usage_stats (
                month TEXT NOT NULL,
                format_id TEXT NOT NULL,
                elo_tier INTEGER NOT NULL,
                pokemon TEXT NOT NULL,
                rank INTEGER,
                usage_pct REAL,
                raw_count INTEGER,
                raw_pct REAL,
                real_count INTEGER,
                real_pct REAL,
                PRIMARY KEY (month, format_id, elo_tier, pokemon)
            );

            CREATE TABLE IF NOT EXISTS pokemon_details (
                month TEXT NOT NULL,
                format_id TEXT NOT NULL,
                elo_tier INTEGER NOT NULL,
                pokemon TEXT NOT NULL,
                raw_count INTEGER,
                avg_weight REAL,
                viability_ceiling INTEGER,
                PRIMARY KEY (month, format_id, elo_tier, pokemon)
            );

            CREATE TABLE IF NOT EXISTS abilities (
                month TEXT NOT NULL,
                format_id TEXT NOT NULL,
                elo_tier INTEGER NOT NULL,
                pokemon TEXT NOT NULL,
                ability TEXT NOT NULL,
                usage_pct REAL,
                PRIMARY KEY (month, format_id, elo_tier, pokemon, ability)
            );

            CREATE TABLE IF NOT EXISTS items (
                month TEXT NOT NULL,
                format_id TEXT NOT NULL,
                elo_tier INTEGER NOT NULL,
                pokemon TEXT NOT NULL,
                item TEXT NOT NULL,
                usage_pct REAL,
                PRIMARY KEY (month, format_id, elo_tier, pokemon, item)
            );

            CREATE TABLE IF NOT EXISTS moves (
                month TEXT NOT NULL,
                format_id TEXT NOT NULL,
                elo_tier INTEGER NOT NULL,
                pokemon TEXT NOT NULL,
                move TEXT NOT NULL,
                usage_pct REAL,
                PRIMARY KEY (month, format_id, elo_tier, pokemon, move)
            );

            CREATE TABLE IF NOT EXISTS spreads (
                month TEXT NOT NULL,
                format_id TEXT NOT NULL,
                elo_tier INTEGER NOT NULL,
                pokemon TEXT NOT NULL,
                nature TEXT,
                hp INTEGER,
                atk INTEGER,
                def INTEGER,
                spa INTEGER,
                spd INTEGER,
                spe INTEGER,
                spread_str TEXT NOT NULL,
                usage_pct REAL,
                PRIMARY KEY (month, format_id, elo_tier, pokemon, spread_str)
            );

            CREATE TABLE IF NOT EXISTS tera_types (
                month TEXT NOT NULL,
                format_id TEXT NOT NULL,
                elo_tier INTEGER NOT NULL,
                pokemon TEXT NOT NULL,
                tera_type TEXT NOT NULL,
                usage_pct REAL,
                PRIMARY KEY (month, format_id, elo_tier, pokemon, tera_type)
            );

            CREATE TABLE IF NOT EXISTS teammates (
                month TEXT NOT NULL,
                format_id TEXT NOT NULL,
                elo_tier INTEGER NOT NULL,
                pokemon1 TEXT NOT NULL,
                pokemon2 TEXT NOT NULL,
                usage_pct REAL,
                PRIMARY KEY (month, format_id, elo_tier, pokemon1, pokemon2)
            );

            CREATE TABLE IF NOT EXISTS checks_counters (
                month TEXT NOT NULL,
                format_id TEXT NOT NULL,
                elo_tier INTEGER NOT NULL,
                pokemon TEXT NOT NULL,
                counter_pokemon TEXT NOT NULL,
                score REAL,
                ko_pct REAL,
                switch_pct REAL,
                PRIMARY KEY (month, format_id, elo_tier, pokemon, counter_pokemon)
            );

            CREATE TABLE IF NOT EXISTS leads (
                month TEXT NOT NULL,
                format_id TEXT NOT NULL,
                elo_tier INTEGER NOT NULL,
                pokemon TEXT NOT NULL,
                rank INTEGER,
                usage_pct REAL,
                raw_count INTEGER,
                PRIMARY KEY (month, format_id, elo_tier, pokemon)
            );

            CREATE TABLE IF NOT EXISTS metagame (
                month TEXT NOT NULL,
                format_id TEXT NOT NULL,
                elo_tier INTEGER NOT NULL,
                playstyle TEXT NOT NULL,
                usage_pct REAL,
                PRIMARY KEY (month, format_id, elo_tier, playstyle)
            );

            CREATE TABLE IF NOT EXISTS replays (
                replay_id TEXT PRIMARY KEY,
                format_id TEXT,
                rating INTEGER,
                player1 TEXT,
                player2 TEXT,
                uploadtime INTEGER,
                month TEXT
            );

            CREATE TABLE IF NOT EXISTS replay_teams (
                replay_id TEXT NOT NULL,
                side TEXT NOT NULL,
                pokemon TEXT NOT NULL,
                won INTEGER,
                PRIMARY KEY (replay_id, side, pokemon)
            );

            CREATE INDEX IF NOT EXISTS idx_usage_month_fmt ON usage_stats(month, format_id);
            CREATE INDEX IF NOT EXISTS idx_chaos_month_fmt ON teammates(month, format_id);
            CREATE INDEX IF NOT EXISTS idx_replays_format ON replays(format_id);
            CREATE INDEX IF NOT EXISTS idx_replay_teams_rid ON replay_teams(replay_id);
        """)
