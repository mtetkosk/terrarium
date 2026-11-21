"""DuckDB storage layer for the CBB prediction engine.

Owns the embedded warehouse at ``data/cbb_engine.duckdb``: the three
Lean V1.0 tables plus ``model_predictions`` (Milestone 4) and
``scheduled_games`` / ``odds_snapshots`` / ``power_rankings`` (Milestone 6). All other
modules should obtain connections through :func:`get_db_connection` so the
schema is guaranteed to exist.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "cbb_engine.duckdb"

# Table name -> CREATE statement. Order matters only for readability; there
# are no foreign keys between tables.
SCHEMA: dict[str, str] = {
    "kenpom_snapshots": """
        CREATE TABLE IF NOT EXISTS kenpom_snapshots (
            snapshot_date DATE,
            team VARCHAR,
            adj_em DOUBLE,
            adj_o DOUBLE,
            adj_d DOUBLE,
            adj_t DOUBLE,
            three_pa_rate DOUBLE,
            PRIMARY KEY (snapshot_date, team)
        )
    """,
    "historical_games": """
        CREATE TABLE IF NOT EXISTS historical_games (
            game_id VARCHAR PRIMARY KEY,
            game_date DATE,
            team_a VARCHAR,
            team_b VARCHAR,
            actual_score_a INT,
            actual_score_b INT,
            venue VARCHAR,
            hca_eff DOUBLE
        )
    """,
    "vegas_closing_odds": """
        CREATE TABLE IF NOT EXISTS vegas_closing_odds (
            game_id VARCHAR PRIMARY KEY,
            closing_spread_a DOUBLE,
            vegas_ml_a DOUBLE,
            vegas_ml_b DOUBLE
        )
    """,
    # Milestone 4: simulator output per game, keyed by model_version so
    # configurations (e.g. HCA values) can be backtested side by side.
    "model_predictions": """
        CREATE TABLE IF NOT EXISTS model_predictions (
            game_id VARCHAR,
            model_version VARCHAR,
            as_of DATE,
            snapshot_date DATE,
            pred_score_a DOUBLE,
            pred_score_b DOUBLE,
            pred_spread_a DOUBLE,
            pred_total DOUBLE,
            win_prob_a DOUBLE,
            cover_prob_a DOUBLE,
            hca_eff DOUBLE,
            n_sims INTEGER,
            created_at TIMESTAMP DEFAULT current_timestamp,
            prob_a_by_5 DOUBLE,
            prob_a_by_10 DOUBLE,
            prob_b_by_5 DOUBLE,
            prob_b_by_10 DOUBLE,
            PRIMARY KEY (game_id, model_version)
        )
    """,
    # Milestone 6: live slate from The Odds API. commence_time is UTC.
    "scheduled_games": """
        CREATE TABLE IF NOT EXISTS scheduled_games (
            game_id VARCHAR PRIMARY KEY,
            source VARCHAR,
            source_event_id VARCHAR,
            commence_time TIMESTAMP,
            team_a VARCHAR,
            team_b VARCHAR
        )
    """,
    # Every pre-tipoff odds pull, per book, from team A's perspective. The
    # earliest row per game is the pick-time line, the latest the closing line.
    "odds_snapshots": """
        CREATE TABLE IF NOT EXISTS odds_snapshots (
            game_id VARCHAR,
            fetched_at TIMESTAMP,
            book VARCHAR,
            spread_a DOUBLE,
            ml_a DOUBLE,
            ml_b DOUBLE,
            total DOUBLE,
            PRIMARY KEY (game_id, fetched_at, book)
        )
    """,
    # Milestone 6: daily neutral-court power rankings.
    "power_rankings": """
        CREATE TABLE IF NOT EXISTS power_rankings (
            ranking_date DATE,
            team VARCHAR,
            rank INTEGER,
            ndi DOUBLE,
            adj_em DOUBLE,
            snapshot_date DATE,
            PRIMARY KEY (ranking_date, team)
        )
    """,
    # Venue + neutral-site flag per scheduled game (src/venues.py, ESPN scoreboard).
    "game_venues": """
        CREATE TABLE IF NOT EXISTS game_venues (
            game_id VARCHAR PRIMARY KEY,
            neutral BOOLEAN,
            venue VARCHAR,
            city VARCHAR,
            source VARCHAR,
            fetched_at TIMESTAMP
        )
    """,
    # Pre-lock / pre-publish sanity checks (src/sanity.py); rewritten per date + stage.
    "sanity_issues": """
        CREATE TABLE IF NOT EXISTS sanity_issues (
            check_date DATE,
            stage VARCHAR,
            severity VARCHAR,
            check_name VARCHAR,
            game_id VARCHAR,
            message VARCHAR,
            created_at TIMESTAMP
        )
    """,
}


# Additive migrations for databases created before a column existed.
MIGRATIONS = [
    "ALTER TABLE model_predictions ADD COLUMN IF NOT EXISTS prob_a_by_5 DOUBLE",
    "ALTER TABLE model_predictions ADD COLUMN IF NOT EXISTS prob_a_by_10 DOUBLE",
    "ALTER TABLE model_predictions ADD COLUMN IF NOT EXISTS prob_b_by_5 DOUBLE",
    "ALTER TABLE model_predictions ADD COLUMN IF NOT EXISTS prob_b_by_10 DOUBLE",
]


def init_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Create all tables and apply migrations on a read-write connection (idempotent)."""
    for ddl in SCHEMA.values():
        con.execute(ddl)
    for ddl in MIGRATIONS:
        con.execute(ddl)


def init_db(db_path: str | Path | None = None) -> Path:
    """Create the database file (and parent dirs) and all tables if missing.

    Returns the resolved database path.
    """
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(path)) as con:
        init_schema(con)
    return path


def get_db_connection(
    read_only: bool = False, db_path: str | Path | None = None
) -> duckdb.DuckDBPyConnection:
    """Open a connection to the engine database.

    Read-write connections create the file and ensure the schema exists.
    Read-only connections require an existing database and never mutate it;
    a missing file raises ``FileNotFoundError``.

    Note: DuckDB forbids opening the same file in one process with differing
    ``read_only`` settings at the same time, so close one before opening the
    other.
    """
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH

    if read_only:
        if not path.exists():
            raise FileNotFoundError(
                f"Database not found at {path}; run init_db() or open a "
                "read-write connection first."
            )
        return duckdb.connect(str(path), read_only=True)

    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    init_schema(con)
    return con


if __name__ == "__main__":
    print(f"Initialized DuckDB warehouse at {init_db()}")
