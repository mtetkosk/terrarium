"""Warehouse <-> CSV round trip for the cloud daily job."""

from datetime import date, datetime

import pytest
from typer.testing import CliRunner

import cli
from src import db
from src import state


def _fill(con):
    con.execute("INSERT INTO kenpom_snapshots VALUES ('2026-11-21', 'Duke', 26.123456789012, 120.1, 94.0, 70.0, NULL)")
    con.execute("INSERT INTO kenpom_snapshots VALUES ('2026-11-20', 'Iowa St.', 22.5, 118.0, 95.5, 67.25, 0.41)")
    con.execute("INSERT INTO historical_games VALUES ('g1', '2026-11-21', 'Duke', 'Iowa St.', NULL, NULL, NULL, 1.9)")
    con.execute("INSERT INTO odds_snapshots VALUES ('g1', ?, 'draftkings', -3.5, -170, 145, 148.5)",
                [datetime(2026, 11, 21, 12, 4, 16)])
    con.execute("INSERT INTO sanity_issues VALUES (?, 'picks', 'error', 'market_gap', 'g1', 'A, \"B\" @ C: x', ?)",
                [date(2026, 11, 21), datetime(2026, 11, 21, 12, 5)])


def _dump(path):
    with db.get_db_connection(read_only=True, db_path=path) as con:
        return {t: sorted(con.execute(f"SELECT * FROM {t}").fetchall(), key=repr) for t in state.TABLES}


def test_round_trip_is_exact(tmp_path):
    src = tmp_path / "a.duckdb"
    with db.get_db_connection(db_path=src) as con:
        _fill(con)
        counts = state.export_state(con, tmp_path / "state")
    assert counts["kenpom_snapshots"] == 2 and counts["sanity_issues"] == 1
    state.import_state(tmp_path / "state", tmp_path / "b.duckdb")
    assert _dump(src) == _dump(tmp_path / "b.duckdb")                      # values, NULLs, quotes, timestamps


def test_export_is_sorted_and_stable(tmp_path):
    with db.get_db_connection(db_path=tmp_path / "a.duckdb") as con:
        _fill(con)
        state.export_state(con, tmp_path / "s1")
        state.export_state(con, tmp_path / "s2")
    first = (tmp_path / "s1" / "kenpom_snapshots.csv").read_text()
    assert first == (tmp_path / "s2" / "kenpom_snapshots.csv").read_text()
    lines = first.splitlines()
    assert lines[0].startswith("snapshot_date,team") and lines[1].startswith("2026-11-20")


def test_refuses_to_export_empty_or_import_nothing(tmp_path):
    with db.get_db_connection(db_path=tmp_path / "a.duckdb") as con:
        with pytest.raises(state.StateError, match="empty"):
            state.export_state(con, tmp_path / "s")
    with pytest.raises(state.StateError, match="no saved state"):
        state.import_state(tmp_path / "nothing", tmp_path / "b.duckdb")


def test_import_replaces_existing_database(tmp_path):
    with db.get_db_connection(db_path=tmp_path / "a.duckdb") as con:
        _fill(con)
        state.export_state(con, tmp_path / "s")
    target = tmp_path / "b.duckdb"
    with db.get_db_connection(db_path=target) as con:
        con.execute("INSERT INTO kenpom_snapshots VALUES ('2020-01-01', 'Stale', 0, 100, 100, 68, NULL)")
    state.import_state(tmp_path / "s", target)
    assert "Stale" not in str(_dump(target)["kenpom_snapshots"])


def test_cli_state_commands(tmp_path):
    src = tmp_path / "a.duckdb"
    with db.get_db_connection(db_path=src) as con:
        _fill(con)
    r = CliRunner().invoke(cli.app, ["state", "export", str(tmp_path / "s"), "--db-path", str(src)])
    assert r.exit_code == 0, r.output
    r = CliRunner().invoke(cli.app, ["state", "import", str(tmp_path / "s"), "--db-path", str(tmp_path / "b.duckdb")])
    assert r.exit_code == 0, r.output
    assert _dump(src) == _dump(tmp_path / "b.duckdb")
