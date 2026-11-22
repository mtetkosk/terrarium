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
    # Consensus = median across books in a pull (DK -4.5, FD -5.0), not rounded.
    first = oa.market_line(con, gid, earliest=True)
    assert first["spread_a"] == -4.75 and first["total"] == 148.75
    assert (first["ml_a"], first["ml_b"]) == (-200.0, 170.0)          # first book in BOOKS order
    assert set(first["lines"]) == {"draftkings", "fanduel"}
    assert oa.market_line(con, gid, latest_at=FETCHED)["spread_a"] == -4.75
    assert oa.market_line(con, gid)["spread_a"] == -6.0               # latest pull: DK only
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



def _game(gid, eid, a, b):
    return oa.ScheduledGame(gid, eid, datetime(2026, 11, 20, 20), date(2026, 11, 20), a, b)


@pytest.fixture
def stored(tmp_path):
    with db.get_db_connection(db_path=tmp_path / "x.duckdb") as con:
        oa.store_games(con, [_game("2026-11-20_virginia-tech_northwestern", "EV1", "Virginia Tech", "Northwestern")])
        yield con


def test_final_with_home_and_away_swapped_grades_the_stored_game(stored):
    # scores feed lists the same event as Virginia Tech @ Northwestern (home = Northwestern 70, VT 87)
    flipped = _game("2026-11-20_northwestern_virginia-tech", "EV1", "Northwestern", "Virginia Tech")
    oa.store_finals(stored, [(flipped, 70, 87)])
    rows = stored.execute("SELECT game_id, actual_score_a, actual_score_b FROM historical_games").fetchall()
    assert rows == [("2026-11-20_virginia-tech_northwestern", 87, 70)]                # VT's 87 as team A
    assert stored.execute("SELECT count(*) FROM scheduled_games").fetchone()[0] == 1   # no phantom game


def test_odds_with_home_and_away_swapped_are_flipped(stored):
    flipped = _game("2026-11-20_northwestern_virginia-tech", "EV1", "Northwestern", "Virginia Tech")
    snap = oa.OddsSnapshot(flipped.game_id, datetime(2026, 11, 20, 14), "draftkings", 3.5, 140, -165, 150.5)
    games, snaps = oa.reconcile(stored, [flipped], [snap])
    assert games == []
    assert snaps == [oa.OddsSnapshot("2026-11-20_virginia-tech_northwestern", datetime(2026, 11, 20, 14),
                                     "draftkings", -3.5, -165, 140, 150.5)]


def test_new_events_are_untouched(stored):
    other = _game("2026-11-20_duke_kansas", "EV2", "Duke", "Kansas")
    snap = oa.OddsSnapshot(other.game_id, datetime(2026, 11, 20, 14), "draftkings", -2.0, -130, 110, 140.0)
    assert oa.reconcile(stored, [other], [snap]) == ([other], [snap])


# --- line shopping ----------------------------------------------------------------

LINES = {"draftkings": (-4.5, -110.0, -110.0), "fanduel": (-5.0, -105.0, -115.0),
         "betmgm": (-3.5, -135.0, 105.0), "betrivers": (-4.5, -105.0, -115.0)}


def test_best_line_for_each_side():
    # Home (A) wants the highest spread_a: -3.5 is priced -135 (too juiced), so -4.5;
    # tie between DK and BetRivers at -4.5 goes to the better price (-105).
    assert oa.best_line(LINES, "A") == (-4.5, "betrivers", -105.0)
    # Away (B) wants the lowest spread_a (+5.0 beats +4.5).
    assert oa.best_line(LINES, "B") == (-5.0, "fanduel", -115.0)


def test_best_line_ties_fall_back_to_book_order_and_missing_prices():
    lines = {"fanduel": (2.5, None, None), "draftkings": (2.5, None, None)}
    assert oa.best_line(lines, "A") == (2.5, "draftkings", None)


def test_best_line_without_side_or_books_uses_fallback():
    assert oa.best_line(LINES, None, fallback=-4.75) == (-4.75, None, None)
    assert oa.best_line({}, "A", fallback=-4.75) == (-4.75, None, None)
    assert oa.best_line({"betmgm": (-3.5, -140.0, 120.0)}, "A", fallback=-4.0) == (-4.0, None, None)


def test_spread_prices_are_parsed_and_stored(con):
    games, snaps, _ = oa.parse_odds(EVENTS, MAPPER, FETCHED)
    oa.store_games(con, games)
    oa.store_snapshots(con, snaps)
    rows = con.execute("SELECT book, spread_price_a, spread_price_b FROM odds_snapshots "
                       "WHERE game_id = ? ORDER BY book", [games[0].game_id]).fetchall()
    assert all(r[1] is not None and r[2] is not None for r in rows)


def test_odds_only_pull_skips_scores(con, tmp_path):
    session = FakeSession()
    st = oa.update(con, session, key="k", cache_dir=tmp_path / "cache", now=FETCHED, scores=False)
    assert {u.rsplit("/", 1)[1] for u, _ in session.calls} == {"odds"}
    assert st.finals == 0 and st.snapshots == 2


def test_odds_request_names_at_most_ten_books():
    session = FakeSession()
    oa.fetch_odds(session, "k", oa.Quota())
    books = session.calls[0][1]["bookmakers"].split(",")
    assert len(books) <= 10 and books[:2] == ["draftkings", "fanduel"]
