"""Static charts for the style-type section of the picks paper (docs/images/).

    python cli.py style --charts      # regenerate after a refit

Light-mode PNGs for a Google Doc / GitHub page (no hover layer; the paper's
tables are the table view). Colors follow the reference data-viz palette:
diverging blue <-> red around a neutral gray, one highlight blue on gray for
the small multiples.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import numpy as np

from src import style_clusters as sc
from src.db import PROJECT_ROOT

IMAGES_DIR = PROJECT_ROOT / "docs" / "images"

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
HIGHLIGHT = "#2a78d6"
CONTEXT = "#d9d8d2"
# Diverging: blue arm (sequential blue steps) - gray midpoint - red arm.
DIVERGING = ["#184f95", "#3987e5", "#9ec5f4", "#f0efec", "#f3b1ad", "#e34948", "#a32a2a"]

SHORT = {
    "tempo": "Pace", "off_3pa": "3-pt\nattempts", "off_2pdist": "2-pt shot\ndistance", "off_ast": "Assists",
    "off_ftr": "Free\nthrows", "off_or": "Off.\nrebounds",
    "def_3pa": "Opp 3-pt\nattempts", "def_2pdist": "Opp 2-pt\ndistance", "def_ast": "Opp\nassists",
    "def_stl": "Steals", "def_ftr": "Fouls\n(opp FTs)", "def_blk": "Blocks", "def_or": "Opp off.\nrebounds",
}
# Style-map axes: the two main directions of variation (principal components)
# of each side's quality-neutral features, named by their loadings.
AXES = {
    "off": (("Inside & crash the glass", "Perimeter & share the ball"),
            ("Half-court, mid-range", "Fast, rim & free throws")),
    "def": (("Conservative", "Aggressive (steals, blocks)"),
            ("Protect the arc", "Protect the paint")),
}
MAP_SEASON = 2026


def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 9, "text.color": INK, "axes.labelcolor": INK_2,
        "axes.edgecolor": GRID, "xtick.color": INK_2, "ytick.color": INK_2,
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    })
    return plt


def _cmap():
    from matplotlib.colors import LinearSegmentedColormap
    return LinearSegmentedColormap.from_list("style_div", DIVERGING)


def _cell_ink(value: float, vmax: float) -> str:
    return "#ffffff" if abs(value) / vmax > 0.6 else INK


def fingerprints(model: sc.StyleModel, path: Path) -> Path:
    """Heatmap of each type's centroid: how far above/below the league it sits
    on each feature (quality-neutral standard deviations)."""
    plt = _plt()
    fig, axes = plt.subplots(2, 1, figsize=(8.0, 7.0), gridspec_kw={"hspace": 0.75})
    fig.subplots_adjust(top=0.80)
    vmax = 1.2
    for ax, side, title in zip(axes, ("off", "def"), ("Offensive types", "Defensive types")):
        M = model.centroids[side]
        im = ax.imshow(M, cmap=_cmap(), vmin=-vmax, vmax=vmax, aspect="auto")
        ax.set_xticks(range(M.shape[1]), [SHORT[f] for f in model.features[side]], fontsize=8.5)
        ax.xaxis.tick_top()
        ax.set_yticks(range(M.shape[0]), model.names[side], fontsize=9.5)
        ax.tick_params(length=0)
        for s in ax.spines.values():
            s.set_visible(False)
        ax.set_xticks(np.arange(-0.5, M.shape[1]), minor=True)
        ax.set_yticks(np.arange(-0.5, M.shape[0]), minor=True)
        ax.grid(which="minor", color=SURFACE, linewidth=2)
        ax.tick_params(which="minor", length=0)
        for i in range(M.shape[0]):
            for j in range(M.shape[1]):
                v = M[i, j]
                if abs(v) >= 0.5:                       # label only the defining traits
                    ax.text(j, i, f"{v:+.1f}", ha="center", va="center", fontsize=8.5,
                            color=_cell_ink(v, vmax), fontweight="bold")
        ax.set_title(title, loc="left", fontsize=11, fontweight="bold", pad=34)
    cbar = fig.colorbar(im, ax=axes, orientation="horizontal", fraction=0.035, pad=0.06, aspect=40)
    cbar.set_ticks([-1, 0, 1], labels=["−1 sd: much less than\nteams of its quality", "League\ntypical",
                                       "+1 sd: much more than\nteams of its quality"])
    cbar.outline.set_visible(False)
    cbar.ax.tick_params(length=0, labelsize=8, colors=INK_2)
    fig.suptitle("What defines each style type", x=0.02, ha="left", fontsize=13, fontweight="bold", y=0.985)
    fig.text(0.02, 0.945, "Average of each type, in standard deviations from teams of the same quality. "
             "Numbers shown where the gap is 0.5+.", fontsize=8.5, color=INK_2)
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return path


def _pca(X: np.ndarray, sign_ref: dict[int, int]) -> tuple[np.ndarray, np.ndarray]:
    Xc = X - X.mean(0)
    _, _, Vt = np.linalg.svd(Xc, full_matrices=False)
    V = Vt[:2].copy()
    for k, j in sign_ref.items():                 # fix sign so the axis names read left->right
        if V[k, j] < 0:
            V[k] *= -1
    return Xc @ V.T, V


# Feature whose loading must be positive on each axis (keeps the AXES names true).
SIGN_REF = {"off": {0: 1, 1: 0}, "def": {0: 3, 1: 0}}


def style_map(con: duckdb.DuckDBPyConnection, model: sc.StyleModel, side: str, path: Path) -> Path:
    """Small multiples: every team of the map season in gray, one type
    highlighted per panel, on the side's two main style axes."""
    plt = _plt()
    where, params = sc.season_final_where(sc.FIT_SEASONS)
    keys, X = sc.style_matrix(con, model.features[side], where, params)
    proj_all, V = _pca(X, SIGN_REF[side])
    season = np.array([k[1] for k in keys])
    teams = np.array([k[2] for k in keys])
    m = season == MAP_SEASON
    P, T = proj_all[m], teams[m]
    idx, _, _ = sc.nearest(X[m], model.centroids[side])
    em = dict(con.execute("SELECT team, adj_o - adj_d FROM team_style WHERE snapshot_date = ?",
                          [date(MAP_SEASON, 4, 30)]).fetchall())
    fig, axes = plt.subplots(2, 3, figsize=(8.0, 5.9), sharex=True, sharey=True,
                             gridspec_kw={"hspace": 0.32, "wspace": 0.16})
    lim = np.quantile(np.abs(P), 0.995) * 1.08
    for k, ax in enumerate(axes.flat):
        ax.set_xlim(-lim, lim)
        ax.set_ylim(-lim, lim)
        ax.tick_params(length=0, labelbottom=False, labelleft=False)
        for s in ax.spines.values():
            s.set_color(GRID)
        if k != 5:
            ax.axhline(0, color=GRID, linewidth=0.8, zorder=0)
            ax.axvline(0, color=GRID, linewidth=0.8, zorder=0)
        if k == 5:
            ax.axis("off")
            (xl, xr), (yb, yt) = AXES[side]
            ax.text(0.0, 0.92, "How to read", fontsize=9.5, fontweight="bold", transform=ax.transAxes)
            ax.text(0.0, 0.80, f"Each dot is a team (2025-26).\nBlue = teams of that type;\ngray = everyone else.\n\n"
                               f"→  {xr}\n←  {xl}\n↑  {yt}\n↓  {yb}\n\nTypes overlap: styles are a\ncontinuum, "
                               "and these two\naxes show about half of it.",
                    fontsize=8, color=INK_2, va="top", transform=ax.transAxes, linespacing=1.35)
            continue
        sel = idx == k
        ax.scatter(P[~sel, 0], P[~sel, 1], s=9, color=CONTEXT, linewidths=0, zorder=1)
        ax.scatter(P[sel, 0], P[sel, 1], s=14, color=HIGHLIGHT, edgecolors=SURFACE, linewidths=0.6, zorder=2)
        ax.set_title(f"{model.names[side][k]} · {sel.sum()}", fontsize=8.5, fontweight="bold", loc="left")
        best = sorted(np.where(sel)[0], key=lambda i: -em.get(T[i], -99))[:2]
        placed = []
        for i in best:
            # Second label goes below-left if it would sit on top of the first.
            near = any(np.hypot(*(P[i] - P[j])) < lim * 0.35 for j in placed)
            offset, ha, va = ((-5, -5), "right", "top") if near else ((5, 4), "left", "bottom")
            from matplotlib import patheffects
            ax.annotate(T[i], P[i], xytext=offset, textcoords="offset points", fontsize=7.5, color=INK,
                        ha=ha, va=va, zorder=3,
                        path_effects=[patheffects.withStroke(linewidth=2.5, foreground=SURFACE)])
            placed.append(i)
    (xl, xr), (yb, yt) = AXES[side]
    fig.supxlabel(f"←  {xl}          {xr}  →", fontsize=8.5, color=INK_2, y=0.035)
    fig.supylabel(f"←  {yb}          {yt}  →", fontsize=8.5, color=INK_2, x=0.075)
    kind = "Offensive" if side == "off" else "Defensive"
    fig.suptitle(f"{kind} style map, 2025-26", x=0.125, ha="left", fontsize=13, fontweight="bold", y=0.985)
    fig.text(0.125, 0.935, "Labeled: the two highest-rated teams of each type. Positions remove team quality, "
             "so elite and weak teams mix.", fontsize=8.5, color=INK_2)
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return path


def pairing_lift(con: duckdb.DuckDBPyConnection, model: sc.StyleModel) -> tuple[np.ndarray, np.ndarray]:
    """(counts, lift) of offense x defense types over the fit seasons' finals."""
    dates = [date(s, 4, 30) for s in model.fit_seasons]
    rows = con.execute(
        f"SELECT off_cluster, def_cluster FROM team_style_clusters WHERE snapshot_date IN "
        f"({', '.join('?' * len(dates))})", dates).fetchall()
    k_off, k_def = len(model.names["off"]), len(model.names["def"])
    counts = np.zeros((k_off, k_def))
    for o, d in rows:
        counts[o, d] += 1
    expected = counts.sum(1, keepdims=True) * counts.sum(0, keepdims=True) / counts.sum()
    return counts, counts / expected


def pairings(con: duckdb.DuckDBPyConnection, model: sc.StyleModel, path: Path) -> Path:
    plt = _plt()
    counts, lift = pairing_lift(con, model)
    L = np.log2(lift)
    vmax = 0.8
    fig, ax = plt.subplots(figsize=(8.0, 5.0))
    fig.subplots_adjust(top=0.72)
    ax.imshow(L, cmap=_cmap(), vmin=-vmax, vmax=vmax, aspect="auto")
    ax.set_xticks(range(L.shape[1]), [n.replace(" ", "\n", 1) if len(n) > 14 else n
                                      for n in model.names["def"]], fontsize=8.5)
    ax.xaxis.tick_top()
    ax.set_yticks(range(L.shape[0]), model.names["off"], fontsize=9.5)
    ax.tick_params(length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_xticks(np.arange(-0.5, L.shape[1]), minor=True)
    ax.set_yticks(np.arange(-0.5, L.shape[0]), minor=True)
    ax.grid(which="minor", color=SURFACE, linewidth=2)
    ax.tick_params(which="minor", length=0)
    for i in range(L.shape[0]):
        for j in range(L.shape[1]):
            shown = round(lift[i, j], 1)
            ax.text(j, i, f"{shown:.1f}×", ha="center", va="center", fontsize=9,
                    color=_cell_ink(L[i, j], vmax), fontweight="bold" if shown >= 1.3 or shown <= 0.7 else "normal")
    ax.set_ylabel("Offense  (rows)", fontsize=9, color=INK_2)
    n = int(counts.sum())
    first, last = model.fit_seasons[0], model.fit_seasons[-1]
    fig.suptitle("Which offenses pair with which defenses", x=0.02, ha="left", fontsize=13,
                 fontweight="bold", y=0.995)
    fig.text(0.02, 0.925, f"Defense types across the top, offense types down the side.\nHow often each pairing occurs vs. chance, {n:,} team-seasons ({first - 1}-{str(first)[2:]} "
             f"to {last - 1}-{str(last)[2:]}). \nRed = more common than chance, blue = rarer.",
             fontsize=8.5, color=INK_2, va="top", linespacing=1.4)
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return path


def render_all(con: duckdb.DuckDBPyConnection, out_dir: Path = IMAGES_DIR,
               model: sc.StyleModel | None = None) -> list[Path]:
    model = model or sc.load_model()
    out_dir.mkdir(parents=True, exist_ok=True)
    return [
        fingerprints(model, out_dir / "style_fingerprints.png"),
        style_map(con, model, "off", out_dir / "style_map_offense.png"),
        style_map(con, model, "def", out_dir / "style_map_defense.png"),
        pairings(con, model, out_dir / "style_pairings.png"),
    ]
