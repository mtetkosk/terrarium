"""The Odds API parsing/storage (offline)."""

from datetime import date, datetime

import pytest

from src import db
from src import odds_api as oa
from src.team_names import TeamNameMapper

MAPPER = TeamNameMapper(["Duke", "Michigan St.", "Kansas", "Houston"])
FETCHED = datetime(2026, 11, 20, 16, 0)


def book(key, home, away, spread=None, ml=None, total=None):
    markets = []
    if spread is not None:
        markets.append({"key": "spreads", "outcomes": [{"name": home, "price": -110, "point": spread},
                                                       {"name": away, "price": -110, "point": -spread}]})
    if ml is not None:
        markets.append({"key": "h2h", "outcomes": [{"name": home, "price": ml[0]}, {"name": away, "price": ml[1]}]})
    if total is not None:
        markets.append({"key": "totals", "outcomes": [{"name": "Over", "price": -110, "point": total},
                                                      {"name": "Under", "price": -110, "point": total}]})
    return {"key": key, "markets": markets}


EVENTS = [
    {"id": "e1", "commence_time": "2026-11-21T01:00:00Z", "home_team": "Duke Blue Devils",
     "away_team": "Michigan State Spartans",
     "bookmakers": [book("draftkings", "Duke Blue Devils", "Michigan State Spartans", -4.5, (-200, 170), 148.5),
                    book("fanduel", "Duke Blue Devils", "Michigan State Spartans", -5.0, (-210, 175), 149.0)]},
    {"id": "e2", "commence_time": "2026-11-20T15:00:00Z", "home_team": "Kansas Jayhawks",   # already started
     "away_team": "Houston Cougars",
     "bookmakers": [book("draftkings", "Kansas Jayhawks", "Houston Cougars", 6.5, (220, -270), 130.5)]},
    {"id": "e3", "commence_time": "2026-11-21T00:00:00Z", "home_team": "Kansas Jayhawks",
     "away_team": "Adrian Bulldogs", "bookmakers": []},
]


def test_eastern_date_handles_late_west_coast_tips():
    assert oa.eastern_date(oa.parse_utc("2026-11-21T04:00:00Z")) == date(2026, 11, 20)   # 11 pm ET
    assert oa.eastern_date(oa.parse_utc("2026-11-21T05:30:00Z")) == date(2026, 11, 21)


def test_parse_odds_maps_names_and_skips_in_play():
    games, snaps, unmatched = oa.parse_odds(EVENTS, MAPPER, FETCHED)
    assert [(g.team_a, g.team_b, g.game_date) for g in games] == [
        ("Duke", "Michigan St.", date(2026, 11, 20)), ("Kansas", "Houston", date(2026, 11, 20))]
    assert games[0].game_id == "2026-11-20_duke_michigan-st"
    assert {(s.game_id, s.book) for s in snaps} == {(games[0].game_id, "draftkings"), (games[0].game_id, "fanduel")}
    dk = next(s for s in snaps if s.book == "draftkings")
    assert (dk.spread_a, dk.ml_a, dk.ml_b, dk.total) == (-4.5, -200, 170, 148.5)
    assert unmatched == {"Adrian Bulldogs"}


def test_mismatched_spread_sides_are_dropped():
    ev = [dict(EVENTS[0], bookmakers=[{"key": "draftkings", "markets": [{"key": "spreads", "outcomes": [
        {"name": "Duke Blue Devils", "price": -110, "point": -4.5},
        {"name": "Michigan State Spartans", "price": -110, "point": 3.5}]}]}])]
    _, snaps, _ = oa.parse_odds(ev, MAPPER, FETCHED)
    assert snaps == []


@pytest.fixture
def con(tmp_path):
    with db.get_db_connection(db_path=tmp_path / "x.duckdb") as c:
        c.executemany("INSERT INTO kenpom_snapshots (snapshot_date, team) VALUES (?, ?)",
                      [(date(2026, 11, 20), t) for t in MAPPER.kenpom_names])
        yield c


def test_store_and_closing_line_prefers_latest_draftkings(con):
    games, snaps, _ = oa.parse_odds(EVENTS, MAPPER, FETCHED)
    oa.store_games(con, games)
    oa.store_snapshots(con, snaps)
    later = datetime(2026, 11, 20, 23, 0)
    _, snaps2, _ = oa.parse_odds([dict(EVENTS[0], bookmakers=[book(
        "draftkings", "Duke Blue Devils", "Michigan State Spartans", -6.0, (-260, 215), 150.5)])], MAPPER, later)
    oa.store_snapshots(con, snaps2)
    gid = games[0].game_id
    assert oa.refresh_closing_odds(con, [gid]) == 1
    assert con.execute("SELECT * FROM vegas_closing_odds").fetchall() == [(gid, -6.0, -260.0, 215.0)]
    assert oa.market_line(con, gid, earliest=True)["spread_a"] == -4.5
    assert oa.market_line(con, gid, latest_at=FETCHED)["spread_a"] == -4.5
    # historical_games row created with NULL scores and default HCA
    assert con.execute("SELECT actual_score_a, hca_eff FROM historical_games WHERE game_id = ?",
                       [gid]).fetchone() == (None, oa.DEFAULT_HCA_EFF)


def test_scores_fill_finals(con):
    scores = [
        {"id": "e2", "commence_time": "2026-11-20T15:00:00Z", "completed": True,
         "home_team": "Kansas Jayhawks", "away_team": "Houston Cougars",
         "scores": [{"name": "Kansas Jayhawks", "score": "71"}, {"name": "Houston Cougars", "score": "68"}]},
        {"id": "e1", "commence_time": "2026-11-21T01:00:00Z", "completed": False,
         "home_team": "Duke Blue Devils", "away_team": "Michigan State Spartans", "scores": None},
    ]
    finals, _ = oa.parse_scores(scores, MAPPER)
    assert len(finals) == 1
    assert oa.store_finals(con, finals) == 1
    assert con.execute("SELECT team_a, actual_score_a, actual_score_b FROM historical_games").fetchall() == [
        ("Kansas", 71, 68)]


class FakeResp:
    def __init__(self, payload, status=200):
        self.payload, self.status_code = payload, status
        self.headers = {"x-requests-remaining": "490", "x-requests-used": "10", "x-requests-last": "3"}

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise oa.requests.HTTPError(str(self.status_code))


class FakeSession:
    def __init__(self, status=200):
        self.status, self.calls = status, []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return FakeResp(EVENTS if url.endswith("/odds") else [], self.status)


def test_update_end_to_end(con, tmp_path):
    session = FakeSession()
    st = oa.update(con, session, key="k", cache_dir=tmp_path / "cache", now=FETCHED)
    assert (st.events, st.games, st.snapshots, st.closing_lines) == (3, 2, 2, 1)
    assert st.quota.remaining == "490"
    assert {u.rsplit("/", 1)[1] for u, _ in session.calls} == {"odds", "scores"}
    assert len(list((tmp_path / "cache").glob("*.json"))) == 2


def test_update_bad_key(con, tmp_path):
    with pytest.raises(oa.OddsApiError, match="401"):
        oa.update(con, FakeSession(status=401), key="bad", cache_dir=tmp_path, now=FETCHED)


def test_current_teams_ignore_retired_kenpom_names(tmp_path):
    from datetime import date
    from src import db
    from src.team_names import TeamNameMapper
    with db.get_db_connection(db_path=tmp_path / "x.duckdb") as con:
        con.execute("INSERT INTO kenpom_snapshots VALUES ('2026-03-01', 'New Orleans', 0, 100, 100, 68, 0.4)")
        con.execute("INSERT INTO kenpom_snapshots VALUES ('2026-11-21', 'LSU New Orleans', 0, 100, 100, 68, 0.4)")
        teams = oa.current_teams(con, date(2026, 11, 21))
    assert teams == ["LSU New Orleans"]
    assert TeamNameMapper(teams).to_kenpom("New Orleans Privateers") == "LSU New Orleans"
