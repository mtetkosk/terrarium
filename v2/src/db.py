"""DuckDB storage layer for the CBB prediction engine.

Owns the embedded warehouse at ``data/cbb_engine.duckdb``: the three
Lean V1.0 tables plus ``model_predictions`` (Milestone 4). All other
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
            PRIMARY KEY (game_id, model_version)
        )
    """,
}


def init_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Create all tables on an open read-write connection (idempotent)."""
    for ddl in SCHEMA.values():
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
