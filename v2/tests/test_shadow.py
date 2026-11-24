"""Shadow models (src/shadow.py): locking, scale matching, grading."""

from datetime import date, timedelta

import numpy as np
import pytest

from src import db
from src import shadow as sh

D1, D2 = date(2026, 11, 23), date(2026, 11, 24)


@pytest.fixture
def con(tmp_path):
    with db.get_db_connection(db_path=tmp_path / "w.duckdb") as c:
        yield c


def add_game(con, gid, day, a, b, line_a, score=None):
    sa, sb = score if score else (None, None)
    con.execute("INSERT INTO historical_games VALUES (?, ?, ?, ?, ?, ?, NULL, 1.9)", [gid, day, a, b, sa, sb])
    con.execute("INSERT INTO pick_lines (game_id, model_version, locked_at, side, line_a, market_line_a) "
                "VALUES (?, 'live-x', ?, ?, ?, ?)", [gid, day, a, line_a, line_a])


class Fixed(sh.ShadowModel):
    def __init__(self, name, margins):
        super().__init__(name, "test", D1)
        self._m = margins

    def margins(self, con, day, games):
        return {g.game_id: self._m[g.game_id] for g in games if g.game_id in self._m}


def test_edge_side_and_tier():
    assert sh.edge_and_side(5.0, -3.0) == (2.0, "a")       # A by 5 vs A -3: A covers by 2
    assert sh.edge_and_side(1.0, -3.0) == (2.0, "b")
    assert sh.edge_and_side(3.0, -3.0) == (0.0, None)
    assert [sh.tier(x) for x in (3.0, 2.9, 1.5, 1.4)] == ["Strong", "Lean", "Lean", "None"]


def test_lock_never_overwrites_and_flags_backfill(con):
    add_game(con, "g1", D1, "A", "B", -3.0)
    m = Fixed("m", {"g1": 7.0})
    run = sh.lock(con, D1, [m], backfill=True)
    assert run.locked == {"m": 1}
    m._m["g1"] = -7.0
    assert sh.lock(con, D1, [m]).locked == {"m": 0}       # already locked: untouched
    side, edge, bf = con.execute("SELECT side, edge, backfill FROM shadow_picks").fetchone()
    assert (side, edge, bf) == ("a", 4.0, True)


def test_report_grades_at_the_locked_line(con):
    # g1: A -3, A wins by 10 -> A covers.   g2: A +4 (dog), A loses by 4 -> push.
    # g3: A -10, A wins by 2 -> B covers.   g4: not played yet.
    add_game(con, "g1", D1, "A", "B", -3.0, (80, 70))
    add_game(con, "g2", D1, "C", "D", 4.0, (60, 64))
    add_game(con, "g3", D1, "E", "F", -10.0, (70, 68))
    add_game(con, "g4", D2, "G", "H", -1.0)
    con.execute("INSERT INTO vegas_closing_odds VALUES ('g1', -4.5, NULL, NULL)")
    sh.lock(con, D1, [Fixed("m", {"g1": 8.0, "g2": 0.0, "g3": 5.0})])   # A, C, F: Strong, Strong, Strong
    sh.lock(con, D2, [Fixed("m", {"g4": 0.0})])
    r, = sh.report(con)
    assert (r.picks, r.pending) == (4, 1)
    # g1: A covers. g2: push. g3: model likes F +10 (E by 5 < 10), E wins by 2 -> F covers.
    assert (r.all.covers, r.all.losses, r.all.pushes) == (2, 0, 1)
    assert (r.strong_fav.covers, r.strong_dog.covers) == (1, 1)
    assert r.strong.clv == [pytest.approx(1.5)]          # laid 3, closed -4.5


def test_report_hides_backfill_unless_asked(con):
    add_game(con, "g1", D1, "A", "B", -3.0, (80, 70))
    sh.lock(con, D1, [Fixed("m", {"g1": 8.0})], backfill=True)
    assert sh.report(con) == []
    assert sh.report(con, include_backfill=True)[0].all.covers == 1


def test_scale_matched_removes_a_pure_scale_disagreement(con, monkeypatch):
    rng = np.random.default_rng(0)
    model = {}
    for i in range(30):
        m = float(rng.uniform(-20, 20))
        gid = f"g{i}"
        model[gid] = m
        market = 1.2 * m + 0.5 + (6.0 if i == 0 else 0.0)        # one game-specific disagreement
        add_game(con, gid, D2 - timedelta(days=i % 3), f"A{i}", f"B{i}", -market)
    monkeypatch.setattr(sh, "closed_form_margin", lambda con, g, stretch=1.0: model[g.game_id])
    sm = sh.ScaleMatched("s", "t", D2, window=7)
    today = sh.locked_slate(con, D2, D2)
    out = sm.margins(con, D2, today)
    edges = {g.game_id: sh.edge_and_side(out[g.game_id], g.market_line_a)[0] for g in today}
    raw = {g.game_id: sh.edge_and_side(model[g.game_id], g.market_line_a)[0] for g in today}
    assert max(raw.values()) > 3                         # raw edges are mostly the 1.2x scale gap
    assert edges["g0"] == max(edges.values()) and edges["g0"] > 4
    assert sorted(edges.values())[-2] < 1.0              # the rest vanish once scale is matched


def test_scale_matched_falls_back_with_too_few_games(con, monkeypatch):
    add_game(con, "g1", D2, "A", "B", -3.0)
    monkeypatch.setattr(sh, "closed_form_margin", lambda con, g, stretch=1.0: 9.0)
    assert sh.ScaleMatched("s", "t", D2).margins(con, D2, sh.locked_slate(con, D2, D2)) == {"g1": 9.0}


def test_fixed_stretch_schedule():
    m = sh.FixedStretch("x", "t", D1, cap=1.25)
    assert m.stretch(date(2026, 11, 24)) == 1.25 and m.stretch(date(2027, 1, 20)) == 1.0


def test_registry_names_unique():
    names = [m.name for m in sh.REGISTRY]
    assert len(names) == len(set(names)) and "baseline-v1.4" in names
