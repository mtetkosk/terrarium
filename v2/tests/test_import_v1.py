"""v1 SQLite -> historical_games / vegas_closing_odds importer."""

import json
import sqlite3
from datetime import date, datetime

import pytest

from src import db
from src import import_v1 as iv
from src.simulate_game import DEFAULT_HCA_EFF

KENPOM = ["Duke", "Michigan St.", "Kansas", "Houston"]


def make_v1(path, games, lines):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE games (id INTEGER PRIMARY KEY, team1_id INT, team2_id INT, date DATE, "
                "venue VARCHAR, status VARCHAR, result JSON, game_time_est DATETIME)")
    con.execute("CREATE TABLE betting_lines (id INTEGER PRIMARY KEY, game_id INT, book VARCHAR, "
                "bet_type VARCHAR, line FLOAT, odds INT, timestamp DATETIME, team VARCHAR)")
    for gid, d, venue, status, home, away, hs, as_, tip in games:
        result = json.dumps({"home_team": home, "away_team": away, "home_score": hs,
                             "away_score": as_}) if hs is not None else "null"
        con.execute("INSERT INTO games VALUES (?, 1, 2, ?, ?, ?, ?, ?)",
                    [gid, d, venue, status, result, tip])
    con.executemany("INSERT INTO betting_lines (game_id, book, bet_type, line, odds, timestamp, team) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)", lines)
    con.commit()
    con.close()


TIP = "2026-01-10 19:00:00.000000"
GAMES = [
    # id, date, venue, status, home, away, home_score, away_score, tipoff
    (1, "2026-01-10", "Cameron Indoor", "FINAL", "Duke Blue Devils", "Michigan State Spartans", 80, 72, TIP),
    (2, "2026-01-03", "Cameron Indoor", "FINAL", "Duke Blue Devils", "Kansas Jayhawks", 70, 75, None),
    (3, "2026-01-05", "Cameron Indoor", "FINAL", "Duke Blue Devils", "Houston Cougars", 66, 60, None),
    (4, "2025-11-28", "Las Vegas Arena", "FINAL", "Duke Blue Devils", "Houston Cougars", 71, 69, None),
    (5, "2026-01-12", "Allen Fieldhouse", "FINAL", "Kansas Jayhawks", "Adrian Bulldogs", 99, 50, None),
    (6, "2026-01-10", "Cameron Indoor", "FINAL", "Duke Blue Devils", "Michigan State Spartans", 80, 72, TIP),
    (7, "2026-01-20", "Allen Fieldhouse", "SCHEDULED", "Kansas Jayhawks", "Houston Cougars", None, None, None),
]
LINES = [
    # game_id, book, bet_type, line, odds, timestamp, team
    (1, "draftkings", "SPREAD", -4.5, -110, "2026-01-10 08:00:00.000001", "Duke Blue Devils"),
    (1, "draftkings", "SPREAD", 4.5, -110, "2026-01-10 08:00:00.000009", "Michigan State Spartans"),
    (1, "draftkings", "SPREAD", -6.0, -110, "2026-01-10 18:00:00.000001", "Duke Blue Devils"),   # closing
    (1, "draftkings", "SPREAD", 6.0, -110, "2026-01-10 18:00:00.000009", "Michigan State Spartans"),
    (1, "draftkings", "SPREAD", -12.5, -110, "2026-01-10 20:00:00.000001", "Duke Blue Devils"),  # in-game
    (1, "draftkings", "SPREAD", 12.5, -110, "2026-01-10 20:00:00.000009", "Michigan State Spartans"),
    (1, "draftkings", "SPREAD", -30.0, -110, "2026-01-10 18:30:00", "Kansas Jayhawks"),          # mislinked
    (1, "draftkings", "SPREAD", 30.0, -110, "2026-01-10 18:30:00", "Houston Cougars"),
    (1, "draftkings", "MONEYLINE", 0.0, -250, "2026-01-10 18:00:00.000002", "Duke Blue Devils"),
    (1, "draftkings", "MONEYLINE", 0.0, 205, "2026-01-10 18:00:00.000003", "Michigan State Spartans"),
    (1, "draftkings", "MONEYLINE", 0.0, -900, "2026-01-10 17:00:00", ""),                       # unlabeled
]


@pytest.fixture
def paths(tmp_path):
    v1 = tmp_path / "v1.db"
    make_v1(v1, GAMES, LINES)
    target = tmp_path / "cbb.duckdb"
    with db.get_db_connection(db_path=target) as con:
        con.executemany("INSERT INTO kenpom_snapshots (snapshot_date, team) VALUES (?, ?)",
                        [(date(2026, 1, 1), t) for t in KENPOM])
    return v1, target


def test_import_end_to_end(paths):
    v1, target = paths
    stats = iv.import_v1(v1, target)
    assert stats.final_games == 6
    assert stats.non_d1_skipped == 1 and "Adrian Bulldogs" in stats.unmatched_names
    assert stats.duplicates == 1
    assert stats.imported == 4
    assert (stats.with_spread, stats.with_moneyline) == (1, 1)

    with db.get_db_connection(read_only=True, db_path=target) as con:
        games = {r[0]: r for r in con.execute("SELECT * FROM historical_games").fetchall()}
        odds = con.execute("SELECT * FROM vegas_closing_odds").fetchall()

    gid = iv.game_id_for(date(2026, 1, 10), "Duke", "Michigan St.")
    assert gid == "2026-01-10_duke_michigan-st"
    assert games[gid][1:] == (date(2026, 1, 10), "Duke", "Michigan St.", 80, 72,
                              "Cameron Indoor", DEFAULT_HCA_EFF)
    # Closing = last pre-tipoff pair; in-game, mislinked and unlabeled lines ignored.
    assert odds == [(gid, -6.0, -250.0, 205.0)]


def test_neutral_site_detected_from_modal_home_venue(paths):
    v1, target = paths
    iv.import_v1(v1, target)
    with db.get_db_connection(read_only=True, db_path=target) as con:
        hca = dict(con.execute("SELECT venue, hca_eff FROM historical_games").fetchall())
    assert hca["Las Vegas Arena"] == 0.0
    assert hca["Cameron Indoor"] == DEFAULT_HCA_EFF


def test_import_date_window_and_idempotency(paths):
    v1, target = paths
    iv.import_v1(v1, target, start=date(2026, 1, 1), end=date(2026, 1, 6))
    iv.import_v1(v1, target, start=date(2026, 1, 1), end=date(2026, 1, 6))
    with db.get_db_connection(read_only=True, db_path=target) as con:
        assert con.execute("SELECT count(*) FROM historical_games").fetchone()[0] == 2


def test_requires_kenpom_names(tmp_path):
    v1 = tmp_path / "v1.db"
    make_v1(v1, GAMES, LINES)
    with pytest.raises(RuntimeError, match="kenpom_snapshots is empty"):
        iv.import_v1(v1, tmp_path / "empty.duckdb")


def test_missing_v1_db(tmp_path):
    with pytest.raises(FileNotFoundError):
        iv.import_v1(tmp_path / "nope.db", tmp_path / "x.duckdb")


def L(team, line, odds, ts, bet_type="SPREAD", book="draftkings"):
    return iv.Line(book, bet_type, line, odds, datetime.fromisoformat(ts), team)


TIPOFF = datetime(2026, 1, 10, 19)


def test_closing_odds_requires_mirrored_same_snapshot_spreads():
    lines = [L("Duke", -6.0, -110, "2026-01-10 18:00:00"), L("Michigan St.", 5.5, -110, "2026-01-10 18:00:00")]
    assert iv.closing_odds(lines, "Duke", "Michigan St.", TIPOFF)[0] is None
    skewed = [L("Duke", -6.0, -110, "2026-01-10 18:00:00"), L("Michigan St.", 6.0, -110, "2026-01-10 12:00:00")]
    assert iv.closing_odds(skewed, "Duke", "Michigan St.", TIPOFF)[0] is None


def test_closing_odds_falls_back_to_second_book():
    lines = [
        L("Duke", 0, -200, "2026-01-10 18:00:00", "MONEYLINE", "fanduel"),
        L("Michigan St.", 0, 170, "2026-01-10 18:00:00", "MONEYLINE", "fanduel"),
        L("Duke", -5.0, -110, "2026-01-10 18:00:00"),
        L("Michigan St.", 5.0, -110, "2026-01-10 18:00:00"),
    ]
    assert iv.closing_odds(lines, "Duke", "Michigan St.", TIPOFF) == (-5.0, -200, 170)
