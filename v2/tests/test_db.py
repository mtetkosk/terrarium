"""Milestone 1 gate: DuckDB storage & schema."""

from datetime import date

import duckdb
import pytest

from src import db

EXPECTED_COLUMNS = {
    "kenpom_snapshots": [
        ("snapshot_date", "DATE"),
        ("team", "VARCHAR"),
        ("adj_em", "DOUBLE"),
        ("adj_o", "DOUBLE"),
        ("adj_d", "DOUBLE"),
        ("adj_t", "DOUBLE"),
        ("three_pa_rate", "DOUBLE"),
    ],
    "historical_games": [
        ("game_id", "VARCHAR"),
        ("game_date", "DATE"),
        ("team_a", "VARCHAR"),
        ("team_b", "VARCHAR"),
        ("actual_score_a", "INTEGER"),
        ("actual_score_b", "INTEGER"),
        ("venue", "VARCHAR"),
        ("hca_eff", "DOUBLE"),
    ],
    "vegas_closing_odds": [
        ("game_id", "VARCHAR"),
        ("closing_spread_a", "DOUBLE"),
        ("vegas_ml_a", "DOUBLE"),
        ("vegas_ml_b", "DOUBLE"),
    ],
    "model_predictions": [
        ("game_id", "VARCHAR"),
        ("model_version", "VARCHAR"),
        ("as_of", "DATE"),
        ("snapshot_date", "DATE"),
        ("pred_score_a", "DOUBLE"),
        ("pred_score_b", "DOUBLE"),
        ("pred_spread_a", "DOUBLE"),
        ("pred_total", "DOUBLE"),
        ("win_prob_a", "DOUBLE"),
        ("cover_prob_a", "DOUBLE"),
        ("hca_eff", "DOUBLE"),
        ("n_sims", "INTEGER"),
        ("created_at", "TIMESTAMP"),
        ("prob_a_by_5", "DOUBLE"),
        ("prob_a_by_10", "DOUBLE"),
        ("prob_b_by_5", "DOUBLE"),
        ("prob_b_by_10", "DOUBLE"),
    ],
    "scheduled_games": [
        ("game_id", "VARCHAR"),
        ("source", "VARCHAR"),
        ("source_event_id", "VARCHAR"),
        ("commence_time", "TIMESTAMP"),
        ("team_a", "VARCHAR"),
        ("team_b", "VARCHAR"),
    ],
    "odds_snapshots": [
        ("game_id", "VARCHAR"),
        ("fetched_at", "TIMESTAMP"),
        ("book", "VARCHAR"),
        ("spread_a", "DOUBLE"),
        ("ml_a", "DOUBLE"),
        ("ml_b", "DOUBLE"),
        ("total", "DOUBLE"),
        ("spread_price_a", "DOUBLE"),
        ("spread_price_b", "DOUBLE"),
    ],
    "vegas_closing_totals": [
        ("game_id", "VARCHAR"),
        ("closing_total", "DOUBLE"),
    ],
    "pick_lines": [
        ("game_id", "VARCHAR"),
        ("model_version", "VARCHAR"),
        ("locked_at", "TIMESTAMP"),
        ("side", "VARCHAR"),
        ("line_a", "DOUBLE"),
        ("book", "VARCHAR"),
        ("price", "DOUBLE"),
        ("market_line_a", "DOUBLE"),
        ("market_total", "DOUBLE"),
        ("ml_a", "DOUBLE"),
        ("ml_b", "DOUBLE"),
    ],
    "power_rankings": [
        ("ranking_date", "DATE"),
        ("team", "VARCHAR"),
        ("rank", "INTEGER"),
        ("ndi", "DOUBLE"),
        ("adj_em", "DOUBLE"),
        ("snapshot_date", "DATE"),
    ],
    "game_venues": [
        ("game_id", "VARCHAR"),
        ("neutral", "BOOLEAN"),
        ("venue", "VARCHAR"),
        ("city", "VARCHAR"),
        ("source", "VARCHAR"),
        ("fetched_at", "TIMESTAMP"),
    ],
    "team_style": [
        ("snapshot_date", "DATE"),
        ("season", "INTEGER"),
        ("team", "VARCHAR"),
        ("tempo", "DOUBLE"),
        ("adj_o", "DOUBLE"),
        ("adj_d", "DOUBLE"),
        ("off_efg", "DOUBLE"),
        ("off_to", "DOUBLE"),
        ("off_or", "DOUBLE"),
        ("off_ftr", "DOUBLE"),
        ("def_efg", "DOUBLE"),
        ("def_to", "DOUBLE"),
        ("def_or", "DOUBLE"),
        ("def_ftr", "DOUBLE"),
        ("off_3p", "DOUBLE"),
        ("off_2p", "DOUBLE"),
        ("off_ft", "DOUBLE"),
        ("off_blk", "DOUBLE"),
        ("off_stl", "DOUBLE"),
        ("off_nst", "DOUBLE"),
        ("off_2pdist", "DOUBLE"),
        ("off_ast", "DOUBLE"),
        ("off_3pa", "DOUBLE"),
        ("def_3p", "DOUBLE"),
        ("def_2p", "DOUBLE"),
        ("def_ft", "DOUBLE"),
        ("def_blk", "DOUBLE"),
        ("def_stl", "DOUBLE"),
        ("def_nst", "DOUBLE"),
        ("def_2pdist", "DOUBLE"),
        ("def_ast", "DOUBLE"),
        ("def_3pa", "DOUBLE"),
    ],
    "team_style_clusters": [
        ("snapshot_date", "DATE"),
        ("team", "VARCHAR"),
        ("model_version", "VARCHAR"),
        ("off_cluster", "INTEGER"),
        ("off_margin", "DOUBLE"),
        ("def_cluster", "INTEGER"),
        ("def_margin", "DOUBLE"),
        ("off_second", "INTEGER"),
        ("def_second", "INTEGER"),
    ],
    "sanity_issues": [
        ("check_date", "DATE"),
        ("stage", "VARCHAR"),
        ("severity", "VARCHAR"),
        ("check_name", "VARCHAR"),
        ("game_id", "VARCHAR"),
        ("message", "VARCHAR"),
        ("created_at", "TIMESTAMP"),
    ],
}

EXPECTED_PRIMARY_KEYS = {
    "kenpom_snapshots": ["snapshot_date", "team"],
    "historical_games": ["game_id"],
    "vegas_closing_odds": ["game_id"],
    "model_predictions": ["game_id", "model_version"],
    "scheduled_games": ["game_id"],
    "odds_snapshots": ["game_id", "fetched_at", "book"],
    "power_rankings": ["ranking_date", "team"],
    "game_venues": ["game_id"],
    "team_style": ["snapshot_date", "team"],
    "team_style_clusters": ["snapshot_date", "team"],
}


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "nested" / "cbb_engine.duckdb"


@pytest.fixture
def con(db_path):
    connection = db.get_db_connection(db_path=db_path)
    yield connection
    connection.close()


def test_default_path_is_project_data_dir():
    assert db.DEFAULT_DB_PATH == db.PROJECT_ROOT / "data" / "cbb_engine.duckdb"
    assert (db.PROJECT_ROOT / "src" / "db.py").exists()


def test_init_db_creates_file_and_parent_dirs(db_path):
    assert not db_path.parent.exists()
    returned = db.init_db(db_path)
    assert returned == db_path
    assert db_path.exists()


def test_get_db_connection_creates_database(db_path):
    with db.get_db_connection(db_path=db_path) as connection:
        assert db_path.exists()
        assert connection.execute("SELECT 1").fetchone() == (1,)


def test_all_tables_created(con):
    tables = {
        row[0]
        for row in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchall()
    }
    assert tables == set(EXPECTED_COLUMNS)


@pytest.mark.parametrize("table", sorted(EXPECTED_COLUMNS))
def test_table_columns_and_types(con, table):
    rows = con.execute(
        """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'main' AND table_name = ?
        ORDER BY ordinal_position
        """,
        [table],
    ).fetchall()
    assert rows == EXPECTED_COLUMNS[table]


@pytest.mark.parametrize("table", sorted(EXPECTED_PRIMARY_KEYS))
def test_primary_keys(con, table):
    rows = con.execute(
        """
        SELECT constraint_column_names
        FROM duckdb_constraints()
        WHERE table_name = ? AND constraint_type = 'PRIMARY KEY'
        """,
        [table],
    ).fetchall()
    assert len(rows) == 1
    assert list(rows[0][0]) == EXPECTED_PRIMARY_KEYS[table]


def test_init_is_idempotent_and_preserves_data(db_path):
    with db.get_db_connection(db_path=db_path) as connection:
        connection.execute(
            "INSERT INTO kenpom_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
            [date(2026, 11, 11), "Duke", 28.1, 122.0, 93.9, 68.2, 0.38],
        )
    db.init_db(db_path)
    with db.get_db_connection(db_path=db_path) as connection:
        count = connection.execute("SELECT count(*) FROM kenpom_snapshots").fetchone()[0]
    assert count == 1


def test_kenpom_composite_primary_key_enforced(con):
    row = [date(2026, 11, 11), "Duke", 28.1, 122.0, 93.9, 68.2, 0.38]
    con.execute("INSERT INTO kenpom_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)", row)
    with pytest.raises(duckdb.ConstraintException):
        con.execute("INSERT INTO kenpom_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)", row)

    # Same team on a different date is a distinct snapshot.
    next_day = [date(2026, 11, 12), *row[1:]]
    con.execute("INSERT INTO kenpom_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)", next_day)
    assert con.execute("SELECT count(*) FROM kenpom_snapshots").fetchone()[0] == 2


@pytest.mark.parametrize(
    "table, row",
    [
        (
            "historical_games",
            ["g1", date(2026, 11, 11), "Duke", "Michigan St.", 78, 71, "neutral", 0.0],
        ),
        ("vegas_closing_odds", ["g1", -6.5, -260.0, 210.0]),
    ],
)
def test_game_id_primary_key_enforced(con, table, row):
    placeholders = ", ".join("?" for _ in row)
    con.execute(f"INSERT INTO {table} VALUES ({placeholders})", row)
    with pytest.raises(duckdb.ConstraintException):
        con.execute(f"INSERT INTO {table} VALUES ({placeholders})", row)


def test_read_only_connection_reads_but_rejects_writes(db_path):
    with db.get_db_connection(db_path=db_path) as connection:
        connection.execute(
            "INSERT INTO vegas_closing_odds VALUES ('g1', -6.5, -260.0, 210.0)"
        )

    with db.get_db_connection(read_only=True, db_path=db_path) as ro:
        assert ro.execute("SELECT closing_spread_a FROM vegas_closing_odds").fetchone() == (-6.5,)
        with pytest.raises(duckdb.Error):
            ro.execute("INSERT INTO vegas_closing_odds VALUES ('g2', 3.0, 120.0, -140.0)")


def test_read_only_missing_database_raises(db_path):
    with pytest.raises(FileNotFoundError):
        db.get_db_connection(read_only=True, db_path=db_path)
    assert not db_path.exists()


def test_migration_adds_columns_to_old_table(db_path):
    db_path.parent.mkdir(parents=True)
    with duckdb.connect(str(db_path)) as raw:
        raw.execute("CREATE TABLE model_predictions (game_id VARCHAR, model_version VARCHAR, "
                    "PRIMARY KEY (game_id, model_version))")
    with db.get_db_connection(db_path=db_path) as con:
        cols = [r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'model_predictions'").fetchall()]
    assert {"prob_a_by_5", "prob_b_by_10"} <= set(cols)
