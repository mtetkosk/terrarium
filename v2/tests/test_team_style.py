"""Team style ingest (src/team_style.py) and style types (src/style_clusters.py).

Offline: pages are generated here in KenPom's layout (value + rank column per
stat), including the unclosed rank <td> the live four-factors page has.
"""

from datetime import date

import numpy as np
import pytest

from src import db
from src import ingest_kenpom as ik
from src import style_clusters as sc
from src import team_style as ts

TODAY = date(2026, 11, 23)
FF_HEADERS = ["AdjTempo", "AdjOE", "eFG%", "TO%", "OR%", "FTRate", "AdjDE", "eFG%", "TO%", "OR%", "FTRate"]
TS_HEADERS = ["3P%", "2P%", "FT%", "Blk%", "Stl%", "NST%", "2P Dist", "A%", "3PA%"]


def page(headers: list[str], rows: dict[str, list[float]], broken_rank: int | None = None) -> str:
    th = "".join(f'<th colspan="2">{h}</th>' for h in headers)
    body = []
    for team, values in rows.items():
        cells = []
        for k, v in enumerate(values):
            rank = f"<td>{k + 1}" if k == broken_rank else f"<td>{k + 1}</td>"   # unclosed, as live
            cells.append(f"<td>{v}</td>{rank}")
        body.append(f'<tr><td><a href="team.php">{team}</a> <span>3</span></td><td>B12</td>{"".join(cells)}</tr>')
    return (f'<html><table id="ratings-table"><thead><tr><th>Team</th><th>Conf</th>{th}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></html>')


def ff_row(i=0.0):
    return [68.0 + i, 115.0 + i, 52.0, 16.0, 30.0, 33.0, 98.0 - i, 48.0, 18.0, 28.0, 31.0]


def ts_row(i=0.0, adj=115.0):
    return [35.0, 53.0, 72.0, 9.0, 8.0, 7.0, 6.0 + i / 10, 52.0, 40.0 + i, adj]


def pages(teams=("Houston", "Duke"), broken=None):
    ff = page(FF_HEADERS, {t: ff_row(i) for i, t in enumerate(teams)}, broken)
    off = page(TS_HEADERS + ["AdjOE"], {t: ts_row(i) for i, t in enumerate(teams)})
    dfn = page(TS_HEADERS + ["AdjDE"], {t: ts_row(-i, 98.0) for i, t in enumerate(teams)})
    return ff, off, dfn


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

def test_four_factors_split_offense_and_defense():
    rows = ts.parse_four_factors(pages()[0])
    assert rows["Duke"]["tempo"] == 69.0 and rows["Duke"]["adj_d"] == 97.0
    assert rows["Houston"]["off_to"] == 16.0 and rows["Houston"]["def_to"] == 18.0


def test_unclosed_rank_cell_does_not_shift_values():
    rows = ts.parse_four_factors(pages(broken=3)[0])     # TO% rank <td> left open
    assert rows["Houston"]["off_or"] == 30.0 and rows["Houston"]["def_ftr"] == 31.0


def test_teamstats_sides_are_checked():
    _, off, dfn = pages()
    assert ts.parse_teamstats(off, "off")["Duke"]["off_3pa"] == 41.0
    assert ts.parse_teamstats(dfn, "def")["Duke"]["def_2pdist"] == pytest.approx(5.9)
    with pytest.raises(ik.KenPomParseError):
        ts.parse_teamstats(off, "def")       # offense page handed in as defense


def test_build_rows_merges_pages_and_range_checks():
    rows = ts.build_style_rows(*pages())
    assert set(rows) == {"Houston", "Duke"} and set(rows["Duke"]) == set(ts.STYLE_COLUMNS)
    ff, off, dfn = pages()
    with pytest.raises(ik.KenPomParseError, match="outside"):
        ts.build_style_rows(ff.replace("<td>52.0</td>", "<td>520.0</td>"), off, dfn)


def test_season_labels():
    assert ts.season_for(date(2026, 11, 23)) == 2027
    assert ts.season_for(date(2027, 3, 1)) == 2027
    assert ts.final_snapshot_date(2026) == date(2026, 4, 30)


# --------------------------------------------------------------------------
# Fetching + leakage guards
# --------------------------------------------------------------------------

class FakeSession:
    def __init__(self, by_url):
        self.by_url, self.calls = by_url, []

    def get(self, url, timeout=None):
        self.calls.append(url)
        return type("R", (), {"text": self.by_url[url], "status_code": 200, "headers": {},
                              "raise_for_status": lambda self: None})()

    def post(self, url, data=None, timeout=None):
        return type("R", (), {"raise_for_status": lambda self: None})()


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setenv("KENPOM_USER", "me@example.com")
    monkeypatch.setenv("KENPOM_PASS", "x")


def _session(season=None):
    ff, off, dfn = pages()
    return FakeSession(dict(zip(ts._urls(season), (ff, off, dfn))))


def test_live_fetch_caches_then_reuses(tmp_path, creds):
    s = _session()
    ts.load_style_pages(TODAY, cache_dir=tmp_path, session=s, today=TODAY, sleep=lambda _: None)
    assert s.calls == list(ts._urls(None))
    assert (tmp_path / "kenpom_teamstats_def_2026-11-23.html").exists()
    s2 = _session()
    ts.load_style_pages(TODAY, cache_dir=tmp_path, session=s2, today=TODAY)
    assert s2.calls == []


def test_past_date_needs_cache(tmp_path, creds):
    with pytest.raises(ik.LeakageError):
        ts.load_style_pages(date(2026, 11, 20), cache_dir=tmp_path, session=_session(), today=TODAY)


def test_season_finals_only_for_finished_seasons(tmp_path, creds):
    with pytest.raises(ik.KenPomError):
        ts.load_style_pages(season=2027, cache_dir=tmp_path, session=_session(2027), today=TODAY)
    assert ts._urls(2026)[2].endswith("teamstats.php?y=2026&od=d")


def test_ingest_season_final_stamped_after_season(tmp_path, creds):
    with db.get_db_connection(db_path=tmp_path / "w.duckdb") as con:
        snap, n = ts.ingest_style(con, season=2026, cache_dir=tmp_path, session=_session(2026), today=TODAY)
        assert (snap, n) == (date(2026, 4, 30), 2)
        assert con.execute("SELECT DISTINCT season FROM team_style").fetchall() == [(2026,)]


# --------------------------------------------------------------------------
# Clusters
# --------------------------------------------------------------------------

def _seed_styles(con, snapshot_date, season, n=120, seed=0, shift=0.0):
    rng = np.random.default_rng(seed)
    cols = ts.STYLE_COLUMNS
    rows = {}
    for i in range(n):
        group = i % 3                                  # three well-separated styles
        base = {c: 30.0 + rng.normal(0, 1) for c in cols}
        base.update(tempo=68 + rng.normal(0, 1), adj_o=110.0, adj_d=100.0 + shift)
        for j, c in enumerate(sc.FEATURES["off"] + sc.FEATURES["def"]):
            base[c] += 6.0 * ((group + j) % 3 - 1)
        rows[f"T{i}"] = base
    ts.store_style(con, snapshot_date, season, rows)


@pytest.fixture
def con(tmp_path):
    with db.get_db_connection(db_path=tmp_path / "w.duckdb") as c:
        yield c


def test_fit_assign_and_json_roundtrip(con, tmp_path):
    for y in (2025, 2026):
        _seed_styles(con, date(y, 4, 30), y, seed=y)
    model = sc.fit(con, seasons=(2025, 2026), k=3)
    path = tmp_path / "m.json"
    sc.save_model(model, path)
    loaded = sc.load_model(path)
    np.testing.assert_allclose(loaded.centroids["off"], model.centroids["off"], atol=1e-4)
    assert sc.assign(con, date(2026, 4, 30), loaded) == 120
    counts = con.execute("SELECT off_cluster, count(*) FROM team_style_clusters GROUP BY 1").fetchall()
    assert sorted(n for _, n in counts) == [40, 40, 40]
    assert con.execute("SELECT min(off_margin) FROM team_style_clusters").fetchone()[0] > 0


def test_nearest_margin():
    idx, second, margin = sc.nearest(np.array([[0.0, 0.0], [2.9, 0.0]]), np.array([[0.0, 0.0], [3.0, 0.0]]))
    assert idx.tolist() == [0, 1] and second.tolist() == [1, 0]
    np.testing.assert_allclose(margin, [3.0, 2.8])


def test_matchups_never_use_later_styles(con):
    _seed_styles(con, date(2026, 11, 23), 2027)
    for y in (2025, 2026):
        _seed_styles(con, date(y, 4, 30), y, seed=y)
    model = sc.fit(con, seasons=(2025, 2026), k=3)
    sc.assign(con, date(2026, 11, 23), model)
    sc.assign(con, date(2026, 4, 30), model)
    con.executemany("INSERT INTO historical_games VALUES (?, ?, ?, ?, ?, ?, NULL, 0)", [
        ["g1", date(2026, 11, 22), "T0", "T1", 70, 60],    # day before the first live snapshot
        ["g2", date(2026, 11, 23), "T0", "T1", 70, 60],
        ["g3", date(2026, 12, 5), "T0", "T1", 70, 60],     # 12 days stale
    ])
    rows = sc.matchup_rows(con, date(2026, 11, 1), date(2026, 12, 31))
    assert [r[0] for r in rows] == ["g2"]
    # g1 would only match last April's final profile: excluded as stale, never a later one.
    g1 = con.execute("SELECT style_date_a FROM game_style_matchups WHERE game_id = 'g1'").fetchone()
    assert g1[0] == date(2026, 4, 30)


def test_committed_model_matches_features():
    model = sc.load_model()
    assert model.version == sc.MODEL_VERSION
    for side in ("off", "def"):
        assert model.features[side] == sc.FEATURES[side]
        assert model.centroids[side].shape == (sc.N_CLUSTERS, len(sc.FEATURES[side]))
        assert len(set(model.names[side])) == sc.N_CLUSTERS
        assert all(not n.startswith(side.upper() + " ") for n in model.names[side])


def test_remove_quality_strips_linear_quality_signal():
    rng = np.random.default_rng(3)
    quality = rng.normal(size=(300, 2))
    style = rng.normal(size=(300, 1))
    X = np.column_stack([2 * quality[:, 0] + style[:, 0], quality[:, 1] - 0.5 * style[:, 0]])
    R = sc.remove_quality(X, quality)
    for j in range(2):
        for q in range(2):
            assert abs(np.corrcoef(R[:, j], quality[:, q])[0, 1]) < 1e-8
        assert abs(np.corrcoef(R[:, j], style[:, 0])[0, 1]) > 0.9   # the style part survives
    np.testing.assert_allclose(R.std(axis=0), 1.0)


def test_remove_quality_ignores_constant_quality():
    X = np.arange(12, dtype=float).reshape(6, 2)
    R = sc.remove_quality(X, np.ones((6, 2)))
    np.testing.assert_allclose(R, (X - X.mean(0)) / X.std(0))


def test_outcome_stats_are_not_features():
    for side in ("off", "def"):
        assert not {"off_to", "def_to", "off_efg", "def_efg", "adj_o", "adj_d"} & set(sc.FEATURES[side])
