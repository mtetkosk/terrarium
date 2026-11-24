"""Unsupervised offense / defense style types (research only; not used by the picks model).

Each team gets one offensive type and one defensive type from k-means on
style stats: *how* a team plays, not how well.

* **Choice stats only.** Features are things a coaching staff picks (pace,
  shot mix, ball movement, crashing the glass, pressure, fouling, where the
  defense gives up shots). Outcome-heavy stats such as turnover rate are
  left out.
* **Quality removed.** Within each season (or live snapshot) every feature
  is z-scored and then regressed on the team's AdjO and AdjD; only the
  residual is clustered. A type therefore means "plays this way *for a team
  of its quality*", and good teams spread across all types (AdjEM explains
  ~0.2% of the variance between offensive types and ~1.4% between defensive
  types, 2023-2026).
* Centroids are fit once on full-season profiles (``fit``) and saved to
  ``models/style_clusters.json`` with hand-written names; daily snapshots are
  then assigned to the nearest centroid (``assign``). Early in a season a
  team's stats rest on a handful of games, so assignments move around; the
  stored ``*_margin`` (runner-up distance minus assigned distance) flags
  borderline teams.
* Styles are a continuum (silhouette ~0.13), and several cuts of it fit about
  equally well, so types are useful labels for regions, not hard categories.

``game_style_matchups`` (a view) tags every game with both teams' types as of
the morning of the game, alongside the model's line, the closing line and the
result, so matchup data accumulates for later study.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import duckdb
import numpy as np

from src.db import PROJECT_ROOT

MODEL_VERSION = "style-v2"
MODEL_PATH = PROJECT_ROOT / "models" / "style_clusters.json"
N_CLUSTERS = 5
FIT_SEASONS = (2023, 2024, 2025, 2026)
SEED = 0

# Offense: off_2pdist = average 2-point shot distance (rim vs mid-range),
# off_or = offensive rebounding (crash vs get back), off_ftr = FT rate
# (attacking contact). Defense uses opponents' stats except def_stl / def_blk
# (own steal and block rates): def_3pa high = concedes threes, def_2pdist
# high = forces mid-range twos, def_ftr high = fouls a lot, def_or high =
# gives up offensive rebounds. Turnover rates are left out (mostly outcome);
# def_stl stands in for pressure (0.88 correlated with turnovers forced).
FEATURES = {
    "off": ["tempo", "off_3pa", "off_2pdist", "off_ast", "off_ftr", "off_or"],
    "def": ["def_3pa", "def_2pdist", "def_ast", "def_stl", "def_ftr", "def_blk", "def_or"],
}
FEATURE_LABELS = {
    "tempo": "pace", "off_3pa": "3PA rate", "off_ftr": "FT rate", "off_or": "off. rebounding",
    "off_to": "turnovers", "off_ast": "assist rate", "off_2pdist": "2P distance",
    "def_3pa": "opp 3PA rate", "def_ftr": "opp FT rate (fouling)", "def_or": "opp off. rebounding",
    "def_to": "turnovers forced", "def_stl": "steal rate", "def_blk": "block rate", "def_2pdist": "opp 2P distance",
    "def_ast": "opp assist rate",
}


@dataclass
class StyleModel:
    version: str
    features: dict[str, list[str]]
    centroids: dict[str, np.ndarray]          # side -> (k, n_features) in z units
    names: dict[str, list[str]]
    blurbs: dict[str, list[str]]
    fit_seasons: list[int]

    def to_json(self) -> dict:
        return {
            "version": self.version, "fit_seasons": self.fit_seasons, "features": self.features,
            "clusters": {
                side: [{"id": j, "name": self.names[side][j], "blurb": self.blurbs[side][j],
                        "centroid": [round(float(v), 4) for v in self.centroids[side][j]]}
                       for j in range(len(self.centroids[side]))]
                for side in self.centroids
            },
        }

    @classmethod
    def from_json(cls, data: dict) -> StyleModel:
        cl = data["clusters"]
        return cls(
            version=data["version"], features=data["features"], fit_seasons=data["fit_seasons"],
            centroids={s: np.array([c["centroid"] for c in cl[s]]) for s in cl},
            names={s: [c["name"] for c in cl[s]] for s in cl},
            blurbs={s: [c.get("blurb", "") for c in cl[s]] for s in cl},
        )


def load_model(path: Path = MODEL_PATH) -> StyleModel:
    return StyleModel.from_json(json.loads(path.read_text()))


def save_model(model: StyleModel, path: Path = MODEL_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(model.to_json(), indent=2) + "\n")


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------

def _standardize(X: np.ndarray) -> np.ndarray:
    sd = X.std(axis=0)
    return (X - X.mean(axis=0)) / np.where(sd > 0, sd, 1.0)


def remove_quality(X: np.ndarray, quality: np.ndarray) -> np.ndarray:
    """Residuals of standardized features after regressing on standardized
    quality (AdjO, AdjD), re-standardized. Constant quality columns are skipped."""
    Q = _standardize(quality)
    Q = Q[:, quality.std(axis=0) > 0]
    A = np.column_stack([np.ones(len(X)), Q])
    beta, *_ = np.linalg.lstsq(A, _standardize(X), rcond=None)
    return _standardize(_standardize(X) - A @ beta)


def style_matrix(con: duckdb.DuckDBPyConnection, cols: list[str], where: str,
                 params: list | None = None) -> tuple[list[tuple], np.ndarray]:
    """[(snapshot_date, season, team)] and quality-neutral style features,
    standardized and residualized within each snapshot."""
    rows = con.execute(
        f"SELECT snapshot_date, season, team, adj_o, adj_d, {', '.join(cols)} FROM team_style "
        f"WHERE {where} ORDER BY snapshot_date, team", params or []).fetchall()
    keys = [r[:3] for r in rows]
    X = np.zeros((len(rows), len(cols)))
    snaps = np.array([r[0] for r in rows], dtype=object)
    for snap in dict.fromkeys(snaps):
        m = snaps == snap
        block = np.array([r[3:] for r, keep in zip(rows, m) if keep], dtype=float)
        X[m] = remove_quality(block[:, 2:], block[:, :2])
    return keys, X


def season_final_where(seasons) -> tuple[str, list]:
    dates = [date(s, 4, 30) for s in seasons]
    return f"snapshot_date IN ({', '.join('?' * len(dates))})", dates


# --------------------------------------------------------------------------
# Fit / assign
# --------------------------------------------------------------------------

def describe(centroid: np.ndarray, cols: list[str], top: int = 3) -> str:
    order = np.argsort(-np.abs(centroid))[:top]
    return ", ".join(f"{'high' if centroid[i] > 0 else 'low'} {FEATURE_LABELS[cols[i]]}" for i in order)


def fit(con: duckdb.DuckDBPyConnection, seasons=FIT_SEASONS, k: int = N_CLUSTERS,
        seed: int = SEED) -> StyleModel:
    """k-means per side on season-final profiles. Names default to the
    centroid's strongest traits; edit them in the saved JSON."""
    from sklearn.cluster import KMeans

    where, params = season_final_where(seasons)
    centroids, names, blurbs = {}, {}, {}
    for side, cols in FEATURES.items():
        _, X = style_matrix(con, cols, where, params)
        if len(X) < k * 20:
            raise ValueError(f"only {len(X)} team-seasons for {side}; ingest season finals first")
        km = KMeans(k, n_init=50, random_state=seed).fit(X)
        centroids[side] = km.cluster_centers_
        names[side] = [f"{side.upper()} {j}" for j in range(k)]
        blurbs[side] = [describe(c, cols) for c in km.cluster_centers_]
    return StyleModel(MODEL_VERSION, dict(FEATURES), centroids, names, blurbs, list(seasons))


def nearest(X: np.ndarray, centroids: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(cluster, runner-up cluster, margin) per row; margin = runner-up minus nearest distance."""
    d = np.sqrt(((X[:, None, :] - centroids[None, :, :]) ** 2).sum(-1))
    order = np.argsort(d, axis=1)
    rows = np.arange(len(X))
    return order[:, 0], order[:, 1], d[rows, order[:, 1]] - d[rows, order[:, 0]]


def assign(con: duckdb.DuckDBPyConnection, snapshot_date: date, model: StyleModel | None = None) -> int:
    """Assign every team in one style snapshot to its offense / defense type."""
    model = model or load_model()
    out = {}
    for side in ("off", "def"):
        keys, X = style_matrix(con, model.features[side], "snapshot_date = ?", [snapshot_date])
        if not keys:
            return 0
        idx, second, margin = nearest(X, model.centroids[side])
        for (_, _, team), j, j2, m in zip(keys, idx, second, margin):
            out.setdefault(team, {})[side] = (int(j), float(m), int(j2))
    con.execute("DELETE FROM team_style_clusters WHERE snapshot_date = ?", [snapshot_date])
    con.executemany(
        "INSERT INTO team_style_clusters (snapshot_date, team, model_version, off_cluster, off_margin, "
        "off_second, def_cluster, def_margin, def_second) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [[snapshot_date, t, model.version, *v["off"], *v["def"]] for t, v in sorted(out.items())])
    return len(out)


BORDERLINE_MARGIN = 0.2      # ~ the closest quarter of teams to a second type


@dataclass(frozen=True)
class TeamType:
    side: str
    cluster: int
    name: str
    blurb: str
    runner_up: str
    margin: float

    @property
    def borderline(self) -> bool:
        return self.margin < BORDERLINE_MARGIN


def team_types(con: duckdb.DuckDBPyConnection, day: date, max_staleness_days: int = 7,
               model: StyleModel | None = None) -> dict[str, dict[str, TeamType]]:
    """{team: {"off": TeamType, "def": TeamType}} from the latest assignment on
    or before ``day`` (at most ``max_staleness_days`` old). Empty when there is
    no model or no recent snapshot, so callers can always fall back to "—"."""
    try:
        model = model or load_model()
    except (OSError, ValueError, KeyError):
        return {}
    snap = con.execute(
        "SELECT max(snapshot_date) FROM team_style_clusters WHERE snapshot_date <= ? AND snapshot_date >= ?",
        [day, day - timedelta(days=max_staleness_days)]).fetchone()[0]
    if snap is None:
        return {}
    out: dict[str, dict[str, TeamType]] = {}
    for team, oc, om, os_, dc, dm, ds in con.execute(
            "SELECT team, off_cluster, off_margin, off_second, def_cluster, def_margin, def_second "
            "FROM team_style_clusters WHERE snapshot_date = ?", [snap]).fetchall():
        types = {}
        for side, c, m, c2 in (("off", oc, om, os_), ("def", dc, dm, ds)):
            names = model.names[side]
            if c is None or c >= len(names):
                continue
            types[side] = TeamType(side, c, names[c], model.blurbs[side][c],
                                   names[c2] if c2 is not None and c2 < len(names) else "", m or 0.0)
        out[team] = types
    return out


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

@dataclass
class ClusterSummary:
    side: str
    cluster: int
    name: str
    blurb: str
    n: int
    adj_o: float
    adj_d: float
    traits: dict[str, float]         # feature -> mean quality-neutral z
    examples: list[str]              # best-rated teams in the type


def summarize(con: duckdb.DuckDBPyConnection, snapshot_date: date, model: StyleModel | None = None,
              n_examples: int = 5) -> list[ClusterSummary]:
    model = model or load_model()
    out = []
    for side in ("off", "def"):
        cols = model.features[side]
        keys, X = style_matrix(con, cols, "snapshot_date = ?", [snapshot_date])
        teams = [k[2] for k in keys]
        rows = dict(zip(teams, X))
        clusters = dict(con.execute(
            f"SELECT team, {side}_cluster FROM team_style_clusters WHERE snapshot_date = ?",
            [snapshot_date]).fetchall())
        eff = {t: (o, d) for t, o, d in con.execute(
            "SELECT team, adj_o, adj_d FROM team_style WHERE snapshot_date = ?", [snapshot_date]).fetchall()}
        for j, name in enumerate(model.names[side]):
            members = [t for t in teams if clusters.get(t) == j]
            if not members:
                continue
            key = (lambda t: -eff[t][0]) if side == "off" else (lambda t: eff[t][1])
            out.append(ClusterSummary(
                side, j, name, model.blurbs[side][j], len(members),
                float(np.mean([eff[t][0] for t in members])), float(np.mean([eff[t][1] for t in members])),
                dict(zip(cols, np.mean([rows[t] for t in members], axis=0).round(2).tolist())),
                sorted(members, key=key)[:n_examples]))
    return out


MATCHUP_VIEW = """
CREATE OR REPLACE VIEW game_style_matchups AS
WITH g AS (
    SELECT h.*, p.model_version, mp.pred_spread_a, v.closing_spread_a
    FROM historical_games h
    LEFT JOIN pick_lines p USING (game_id)
    LEFT JOIN model_predictions mp ON mp.game_id = h.game_id AND mp.model_version = p.model_version
    LEFT JOIN vegas_closing_odds v ON v.game_id = h.game_id
)
SELECT g.game_id, g.game_date, g.team_a, g.team_b, g.hca_eff,
       ca.snapshot_date AS style_date_a, cb.snapshot_date AS style_date_b,
       ca.off_cluster AS a_off, ca.def_cluster AS a_def,
       cb.off_cluster AS b_off, cb.def_cluster AS b_def,
       g.model_version, g.pred_spread_a, g.closing_spread_a,
       g.actual_score_a, g.actual_score_b,
       g.actual_score_a - g.actual_score_b AS margin_a
FROM g
ASOF LEFT JOIN team_style_clusters ca ON ca.team = g.team_a AND g.game_date >= ca.snapshot_date
ASOF LEFT JOIN team_style_clusters cb ON cb.team = g.team_b AND g.game_date >= cb.snapshot_date
"""


def ensure_matchup_view(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(MATCHUP_VIEW)


def matchup_rows(con: duckdb.DuckDBPyConnection, start: date, end: date,
                 max_staleness_days: int = 7) -> list[tuple]:
    """Games in [start, end] whose both teams have a same-season style type
    from at most ``max_staleness_days`` before the game (no look-ahead)."""
    ensure_matchup_view(con)
    return con.execute(
        "SELECT * FROM game_style_matchups WHERE game_date BETWEEN ? AND ? "
        "AND game_date - style_date_a <= ? AND game_date - style_date_b <= ? ORDER BY game_date, game_id",
        [start, end, max_staleness_days, max_staleness_days]).fetchall()
