"""Live pick locking, ATS grading and CLV."""

from datetime import date, datetime

import pytest

from src import db
from src import live_picks as lp

DAY = date(2026, 1, 20)


@pytest.mark.parametrize("pred, line, side", [(5.0, -3.0, "A"), (2.0, -3.0, "B"), (3.0, -3.0, None), (1.0, None, None)])
def test_model_side(pred, line, side):
    assert lp.model_side(pred, line) == side


@pytest.mark.parametrize("side, actual, line, result", [
    ("A", 5, -3.0, "Cover"), ("A", 2, -3.0, "Loss"), ("B", 2, -3.0, "Cover"), ("A", 3, -3.0, "Push"),
    (None, 10, -3.0, "—")])
def test_ats_result(side, actual, line, result):
    assert lp.ats_result(side, actual, line) == result


def test_clv_sign():
    assert lp.clv("A", -3.0, -4.5) == 1.5      # took fav -3, closed -4.5: good
    assert lp.clv("B", -3.0, -4.5) == -1.5     # took dog +3, closed +4.5: bad
    assert lp.clv("A", None, -4.5) is None


@pytest.fixture
def con(tmp_path):
    with db.get_db_connection(db_path=tmp_path / "x.duckdb") as c:
        c.executemany("INSERT INTO kenpom_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)", [
            (DAY, "Duke", 26.0, 120.0, 94.0, 70.0, 0.38), (DAY, "Kansas", 20.0, 116.0, 96.0, 68.0, 0.35),
            (DAY, "Baylor", 18.0, 114.0, 96.0, 67.0, 0.36), (DAY, "Houston", 16.0, 113.0, 97.0, 64.0, 0.30)])
        for gid, tip, home, away in (("early", datetime(2026, 1, 20, 17), "Baylor", "Houston"),
                                     ("late", datetime(2026, 1, 21, 1), "Duke", "Kansas")):
            c.execute("INSERT INTO scheduled_games VALUES (?, 'x', ?, ?, ?, ?)", [gid, gid, tip, home, away])
            c.execute("INSERT INTO historical_games VALUES (?, ?, ?, ?, NULL, NULL, NULL, 1.9)", [gid, DAY, home, away])
            c.execute("INSERT INTO odds_snapshots VALUES (?, ?, 'draftkings', -3.0, -150, 130, 145.5)",
                      [gid, datetime(2026, 1, 20, 14)])
        yield c


def test_make_picks_locks_only_unstarted_games(con):
    run = lp.make_picks(con, DAY, now=datetime(2026, 1, 20, 18), n_sims=2000)
    assert (run.created, run.started) == (1, 1)
    created = con.execute("SELECT game_id, created_at, cover_prob_a FROM model_predictions").fetchall()
    assert [r[0] for r in created] == ["late"] and created[0][1] == datetime(2026, 1, 20, 18)
    # Re-running later never overwrites the locked pick.
    again = lp.make_picks(con, DAY, now=datetime(2026, 1, 20, 20), n_sims=500, seed=99)
    assert (again.created, again.already_locked) == (0, 1)
    assert con.execute("SELECT created_at, cover_prob_a FROM model_predictions").fetchall() == [created[0][1:]]


def test_ledger_grades_at_pick_line(con):
    lp.make_picks(con, DAY, now=datetime(2026, 1, 20, 15), n_sims=2000)
    con.execute("INSERT INTO odds_snapshots VALUES ('late', ?, 'draftkings', -5.0, -220, 180, 146.5)",
                [datetime(2026, 1, 20, 23)])
    con.execute("UPDATE historical_games SET actual_score_a = 75, actual_score_b = 71 WHERE game_id = 'late'")
    rows = lp.ledger(con)
    assert len(rows) == 1
    r = rows[0]
    assert (r.pick_line, r.closing_line, r.final) == ("Duke -3.0", "Duke -5.0", "Duke 75, Kansas 71")
    pred = float(r.pred_margin.split(" by ")[1])
    assert r.ats == ("Cover (Duke)" if pred > 3 else "Loss (Kansas)")
    assert r.clv == ("+2.0" if pred > 3 else "-2.0")


def test_daily_picks_rows(con):
    lp.make_picks(con, DAY, now=datetime(2026, 1, 20, 15), n_sims=2000)
    rows = lp.daily_picks(con, DAY)
    assert [r.tip_et for r in rows] == ["12:00 PM", "8:00 PM"]
    assert rows[1].matchup == "Kansas @ Duke" and rows[1].market_line == "Duke -3.0"
    assert rows[1].market_win.startswith("Duke ")


def test_make_picks_holds_back_suspicious_games(con):
    # The feed's line says Kansas is a 9-point favorite at Duke: model and market flip.
    con.execute("UPDATE odds_snapshots SET spread_a = 9.0 WHERE game_id = 'late'")
    run = lp.make_picks(con, DAY, now=datetime(2026, 1, 20, 15), n_sims=2000)
    assert run.created == 1 and {i.game_id for i in run.held_back} == {"late"}
    check = run.held_back[0].check
    assert check in ("favorite_flip", "market_gap")
    locked = [r[0] for r in con.execute("SELECT game_id FROM model_predictions").fetchall()]
    assert locked == ["early"]
    stored = con.execute("SELECT stage, check_name, game_id FROM sanity_issues").fetchall()
    assert stored == [("picks", check, "late")]
    # Once the line is fixed, the next run locks it.
    con.execute("UPDATE odds_snapshots SET spread_a = -3.0 WHERE game_id = 'late'")
    again = lp.make_picks(con, DAY, now=datetime(2026, 1, 20, 16), n_sims=2000)
    assert again.created == 1 and again.held_back == []
    assert con.execute("SELECT count(*) FROM sanity_issues").fetchone()[0] == 0



def test_unlock_releases_only_unstarted_games(con):
    lp.make_picks(con, DAY, now=datetime(2026, 1, 20, 15), n_sims=2000)          # both games locked
    released = lp.unlock_unstarted(con, DAY, now=datetime(2026, 1, 20, 18))      # "early" tipped at 17:00
    assert released == ["late"]
    assert [r[0] for r in con.execute("SELECT game_id FROM model_predictions").fetchall()] == ["early"]
    again = lp.make_picks(con, DAY, now=datetime(2026, 1, 20, 18), n_sims=2000)
    assert (again.created, again.already_locked, again.started) == (1, 1, 0)
