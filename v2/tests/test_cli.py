"""Milestone 5: Typer CLI (offline, temporary databases)."""

from datetime import date

import pytest
from rich.console import Console
from typer.testing import CliRunner

import cli
from src import db
from src import ingest_kenpom as ik
from src import validate_model as vm
from tests.test_ingest import RATINGS_HTML, TEAMSTATS_HTML
from tests.test_validation import seed_slate

runner = CliRunner()
TODAY = date.today()


@pytest.fixture(autouse=True)
def wide_console(monkeypatch):
    """Avoid rich wrapping long lines at 80 columns inside panels."""
    monkeypatch.setattr(cli, "console", Console(width=200))


def invoke(*args):
    return runner.invoke(cli.app, [str(a) for a in args])


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "cbb.duckdb"
    with db.get_db_connection(db_path=path) as con:
        con.executemany("INSERT INTO kenpom_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)", [
            (TODAY, "Duke", 26.0, 120.0, 94.0, 70.0, 0.38),
            (TODAY, "Michigan St.", 21.0, 116.0, 95.0, 67.0, None),
        ])
        con.executemany("INSERT INTO historical_games VALUES (?, ?, 'Duke', ?, 80, 70, ?, 1.9)", [
            (f"g{i}", date(2026, 1, i + 1), opp, "Cameron Indoor Stadium")
            for i, opp in enumerate(["A", "B", "C"])
        ])
    return path


def test_help_lists_commands():
    out = invoke("--help").output
    for command in ("ingest", "simulate", "audit", "predict", "import-v1"):
        assert command in out


def test_simulate_neutral_card(db_path):
    res = invoke("simulate", "--team1", "Duke", "--team2", "Michigan St.", "--neutral",
                 "--seed", 1, "--sims", 2000, "--db-path", db_path)
    assert res.exit_code == 0, res.output
    assert "Duke vs Michigan St." in res.output
    assert "neutral, HCA +0.0" in res.output
    assert "3PA imputed: Michigan St." in res.output


def test_simulate_venue_arena_resolves_home(db_path):
    res = invoke("simulate", "--team1", "Michigan St.", "--team2", "Duke", "--venue",
                 "cameron indoor stadium", "--seed", 1, "--sims", 2000, "--db-path", db_path)
    assert res.exit_code == 0, res.output
    assert "Duke home (Cameron Indoor Stadium), HCA -1.9" in res.output


def test_simulate_unknown_team_fails(db_path):
    res = invoke("simulate", "--team1", "Duke", "--team2", "Michigan State", "--db-path", db_path)
    assert res.exit_code == 1
    assert "Michigan St." in res.output                      # suggestion


def test_simulate_bad_date(db_path):
    res = invoke("simulate", "--team1", "Duke", "--team2", "Michigan St.", "--date", "11/20",
                 "--db-path", db_path)
    assert res.exit_code != 0


def test_ingest_from_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(ik, "load_env_files", lambda paths: None)
    cache = tmp_path / "raw_cache"
    cache.mkdir()
    ik.ratings_cache_path(cache, TODAY).write_text(RATINGS_HTML)
    ik.teamstats_cache_path(cache, TODAY).write_text(TEAMSTATS_HTML)
    res = invoke("ingest", "--cache-dir", cache, "--db-path", tmp_path / "x.duckdb")
    assert res.exit_code == 0, res.output
    assert "Upserted 4 teams" in res.output and "1 missing 3PA" in res.output


def test_ingest_uncached_past_date_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(ik, "load_env_files", lambda paths: None)
    res = invoke("ingest", "--date", "2020-01-01", "--cache-dir", tmp_path / "c",
                 "--db-path", tmp_path / "x.duckdb")
    assert res.exit_code == 1
    assert "only serves current ratings" in res.output


def test_audit_renders_tables(tmp_path):
    path = tmp_path / "audit.duckdb"
    with db.get_db_connection(db_path=path) as con:
        seed_slate(con, model_version=vm.DEFAULT_MODEL_VERSION)
    res = invoke("audit", "--season", 2026, "--db-path", path)
    assert res.exit_code == 0, res.output
    for text in (f"Backtest {vm.DEFAULT_MODEL_VERSION}", "Brier Skill Score", "Heavy favorites", "HCA check"):
        assert text in res.output


def test_predict_writes_predictions(db_path):
    with db.get_db_connection(db_path=db_path) as con:
        con.execute("INSERT INTO historical_games VALUES "
                    "('today', ?, 'Duke', 'Michigan St.', 75, 70, 'Cameron Indoor Stadium', 1.9)",
                    [TODAY])
    res = invoke("predict", "--start", TODAY, "--end", TODAY, "--sims", 1000, "--db-path", db_path)
    assert res.exit_code == 0, res.output
    assert "wrote 1 predictions" in res.output
