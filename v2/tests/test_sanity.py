"""Sanity checks: objective errors are caught, normal games pass."""

from dataclasses import dataclass
from datetime import date, datetime

import pytest

from src import sanity
from src.power_rankings import Ranking

DAY = date(2026, 11, 21)


@dataclass
class Sim:
    expected_score_a: float = 75.0
    expected_score_b: float = 70.0
    win_prob_a: float = 0.66
    prob_a_by_5: float = 0.49
    prob_a_by_10: float = 0.31
    prob_b_by_5: float = 0.20
    prob_b_by_10: float = 0.09

    @property
    def spread_a(self):
        return -(self.expected_score_a - self.expected_score_b)


def checks(sim=None, market=-4.5, ranks=None, dup=None):
    return {i.check for i in sanity.game_issues("g1", "Home U", "Away St.", sim or Sim(), market, ranks, dup)}


def test_normal_game_passes():
    assert checks() == set()
    assert checks(market=None) == set()


def test_favorite_flip_and_big_gap():
    assert checks(market=+6.5) == {"favorite_flip"}            # market: away by 6.5, model: home by 5
    assert checks(market=+3.5) == set()                        # small disagreement is allowed
    assert checks(Sim(expected_score_a=95, win_prob_a=0.97, prob_a_by_5=0.9, prob_a_by_10=0.75,
                      prob_b_by_5=0.01, prob_b_by_10=0.0), market=-7.0) == {"market_gap"}  # -25 vs -7


def test_rank_inversion_in_market():
    ranks = {"Home U": 12, "Away St.": 240}
    # #12 at home but the feed says they are 9-point underdogs to #240
    assert "rank_inversion" in checks(market=+9.0, ranks=ranks)
    assert "rank_inversion" not in checks(market=-12.0, ranks=ranks)


def test_number_checks():
    assert "non_finite" in checks(Sim(expected_score_a=float("nan")))
    assert "score_range" in checks(Sim(expected_score_a=150, expected_score_b=140), market=None)
    assert "probabilities" in checks(Sim(prob_a_by_10=0.6))                       # 10+ more likely than 5+
    assert "favorite_mismatch" in checks(Sim(win_prob_a=0.3, prob_a_by_5=0.2, prob_a_by_10=0.1), market=None)


def test_duplicate_team():
    slate = [("g1", None, "Home U", "Away St.", 1.9), ("g2", None, "Other", "Away St.", 1.9)]
    dup = sanity.duplicate_teams(slate)
    assert dup == {"Away St.": ["g1", "g2"]}
    assert "duplicate_team" in checks(dup=dup)


def _ranking(n=365):
    ndi = [1 - (i + 0.5) / n for i in range(n)]
    return [Ranking(i + 1, f"T{i}", ndi[i], 0.0, DAY) for i in range(n)]


def test_rankings_checks():
    assert sanity.rankings_issues(_ranking()) == []
    assert sanity.rankings_issues(_ranking(120))[0].message.startswith("only 120")
    bad = _ranking()
    bad[5] = Ranking(6, "T0", bad[5].ndi, 0.0, DAY)
    assert "more than once" in sanity.rankings_issues(bad)[0].message
    unsorted = _ranking()
    unsorted[0], unsorted[1] = (Ranking(1, "T0", unsorted[1].ndi, 0, DAY), Ranking(2, "T1", unsorted[0].ndi, 0, DAY))
    assert "not sorted" in sanity.rankings_issues(unsorted)[0].message


def test_bad_cells_are_caught():
    from src.publish_gsheet import GridTab, SheetLayout
    tab = GridTab("T", SheetLayout([[{"userEnteredValue": {"numberValue": float("nan")}},
                                     {"userEnteredValue": {"stringValue": "nan"}},
                                     {"userEnteredValue": {"numberValue": 3.0}}]], 3, 0, [10, 10, 10]))
    assert len(sanity.tab_issues([tab])) == 2


def test_stale_ratings(tmp_path):
    from src import db
    with db.get_db_connection(db_path=tmp_path / "x.duckdb") as con:
        assert sanity.snapshot_issues(con, DAY)[0].check == "stale_ratings"
        con.execute("INSERT INTO kenpom_snapshots VALUES (?, 'A', 1, 100, 99, 68, 0.4)", [date(2026, 11, 15)])
        assert "6 days old" in sanity.snapshot_issues(con, DAY)[0].message
        con.execute("INSERT INTO kenpom_snapshots VALUES (?, 'A', 1, 100, 99, 68, 0.4)", [DAY])
        assert sanity.snapshot_issues(con, DAY) == []


def test_unmatched_names_from_cached_feed(tmp_path):
    import json
    from src import db
    (tmp_path / "odds_2026-11-21T120000Z.json").write_text(json.dumps([
        {"commence_time": "2026-11-21T17:00:00Z", "home_team": "Duke Blue Devils", "away_team": "Nowhere Tech Owls"},
        {"commence_time": "2026-11-23T17:00:00Z", "home_team": "Other Future", "away_team": "Duke Blue Devils"}]))
    with db.get_db_connection(db_path=tmp_path / "x.duckdb") as con:
        con.execute("INSERT INTO kenpom_snapshots VALUES (?, 'Duke', 1, 100, 99, 68, 0.4)", [DAY])
        assert sanity.unmatched_names(con, DAY, tmp_path) == ["Nowhere Tech Owls"]   # other day ignored
        issues = sanity.feed_issues(con, DAY, tmp_path, now=datetime(2026, 11, 21, 15))
    assert {i.check for i in issues} == {"unmatched_names", "stale_odds"}


def test_store_replaces_stage(tmp_path):
    from src import db
    with db.get_db_connection(db_path=tmp_path / "x.duckdb") as con:
        sanity.store(con, DAY, "picks", [sanity.Issue("error", "market_gap", "A @ B: x", "g1")])
        sanity.store(con, DAY, "publish", [sanity.Issue("warning", "stale_odds", "y")])
        sanity.store(con, DAY, "picks", [])
        assert [i.check for i in sanity.load(con, DAY)] == ["stale_odds"]


def test_stale_team_ratings():
    sim = Sim()
    sim.snapshot_date_a, sim.snapshot_date_b = DAY, date(2026, 3, 1)
    found = sanity.game_issues("g1", "Home U", "Away St.", sim, -4.5, day=DAY)
    assert [i.check for i in found] == ["stale_team_ratings"] and "Away St." in found[0].message
    sim.snapshot_date_b = date(2026, 11, 18)
    assert sanity.game_issues("g1", "Home U", "Away St.", sim, -4.5, day=DAY) == []
