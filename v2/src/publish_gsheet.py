"""Publish picks, ledger, scorecard and power rankings to a tabbed Google Sheet.

Setup (service account; it has no Drive storage, so it can't own the file):
  1. Enable the Google Sheets API (and Drive API, for --make-public) in the
     key's GCP project.
  2. Create an empty Google Sheet yourself and share it as Editor with the
     service account's ``client_email``.
  3. In .env: ``GOOGLE_SHEET_ID=<id from the sheet URL>`` and
     ``GOOGLE_SERVICE_ACCOUNT_FILE=<path to key JSON>`` (falls back to
     ``GOOGLE_SHEETS_CREDENTIALS_PATH``). Keep the key outside ~/Desktop --
     macOS privacy protection blocks background jobs from reading it there.

The spreadsheet gets four designed tabs (sheets), created if missing and
matched by title; a fresh file's lone default "Sheet1" is renamed and tabs
you add yourself are left alone. Every tab follows one rule: a cell holds
exactly one value (numbers typed as numbers with display formats, so columns
sort and filter). Each is built as a ``GridTab`` -- rows of CellData plus
merges, frozen panes, column widths and an optional header filter -- and
rewritten in a single batchUpdate: unmerge, resize the grid to fit exactly
(dropping stale rows), write every cell (fields="*" also clears old formats
and notes), merge, size rows/columns, set or clear the filter.

Sharing stays private unless ``make_public=True`` (anyone with the link can
view; only the owner and the service account can edit).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb

from src import live_picks as lp
from src import power_rankings as pr
from src import scorecard as sc
from src import validate_model as vm
from src.odds_api import EASTERN

TAB_TITLES = ("Daily Picks", "Historical Picks", "Model Performance & Stats",
              "Daily Power Rankings 1-N")
SCOPES = ("https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive")
DEFAULT_ROW_PX = 21


class PublishConfigError(RuntimeError):
    pass


def season_of(d: date) -> int:
    return d.year + 1 if d.month >= 7 else d.year


def build_tabs(con: duckdb.DuckDBPyConnection, today: date,
               generated_at: datetime | None = None) -> list["GridTab"]:
    """The four published tabs, in order."""
    return [build_daily_grid(con, today, generated_at=generated_at),
            build_ledger_grid(con, today, generated_at=generated_at),
            build_performance_grid(con, today, generated_at=generated_at),
            build_rankings_grid(con, today, generated_at=generated_at)]


def cell_text(cell: dict) -> str:
    """Plain-text rendering of a CellData (for previews and tests)."""
    v = cell.get("userEnteredValue", {})
    if "stringValue" in v:
        return v["stringValue"]
    if "formulaValue" in v:
        return "[chart]"
    if "numberValue" not in v:
        return ""
    x = v["numberValue"]
    pattern = cell.get("userEnteredFormat", {}).get("numberFormat", {}).get("pattern", "")
    if pattern.startswith('"▲'):
        return f"▲ {x:.0f}" if x > 0 else f"▼ {-x:.0f}" if x < 0 else "–"
    if "AM/PM" in pattern:
        minutes = round(x * 24 * 60)
        h, m = divmod(minutes, 60)
        return f"{(h - 1) % 12 + 1}:{m:02d} {'AM' if h < 12 else 'PM'}"
    decimals = len(pattern.split(";")[0].split(".")[1].rstrip("%")) if "." in pattern else 0
    if "%" in pattern:
        return f"{x * 100:.{decimals}f}%"
    if x == 0 and "PK" in pattern:
        return "PK"
    return f"{x:+.{decimals}f}" if pattern.startswith("+") else f"{x:.{decimals}f}"


def _render_grid(layout: SheetLayout) -> list[str]:
    out = []
    for row in layout.rows:
        cells = [cell_text(c).replace("|", "/") for c in row]
        if any(cells):
            out.append("| " + " | ".join(cells) + " |")
    return out


def render_markdown(tabs: list[GridTab], generated_at: datetime | None = None) -> str:
    stamp = (generated_at or datetime.now(EASTERN)).strftime("%Y-%m-%d %H:%M %Z")
    out = [f"<!-- publish preview generated {stamp} -->"]
    for tab in tabs:
        out.append(f"\n# {tab.title}\n")
        out.extend(_render_grid(tab.layout))
    return "\n".join(out)


# --------------------------------------------------------------------------
# Sheet layout (pure; no API calls)
# --------------------------------------------------------------------------



@dataclass
class SheetLayout:
    rows: list[list[dict]]        # CellData, every row padded to n_cols
    n_cols: int
    frozen_rows: int
    col_widths: list[int]         # pixels
    frozen_cols: int = 0
    merges: list[tuple[int, int, int, int]] = field(default_factory=list)   # (row0, row1, col0, col1), end-exclusive
    row_heights: dict[int, int] = field(default_factory=dict)              # row index -> pixels
    default_row_px: int = DEFAULT_ROW_PX
    filter_row: int | None = None      # header row of a filterable table (to the last row)


@dataclass
class GridTab:
    """A tab laid out cell by cell."""
    title: str
    layout: SheetLayout


# --------------------------------------------------------------------------
# Daily Picks: designed grid (one value per cell)
# --------------------------------------------------------------------------


def _hex(color: str) -> dict:
    color = color.lstrip("#")
    return {k: int(color[i:i + 2], 16) / 255 for k, i in (("red", 0), ("green", 2), ("blue", 4))}


def _tint(color: str, t: float) -> dict:
    """``color`` blended with white; t=0 white, t=1 full color."""
    c = _hex(color)
    return {k: 1 - (1 - v) * t for k, v in c.items()}


WHITE, INK, MUTED = _hex("#FFFFFF"), _hex("#1F2937"), _hex("#6B7280")
NAVY = "#1F2A44"
ZEBRA = _hex("#F6F8FB")
DIVIDER = _hex("#CBD5E1")
AWAY_COLOR, HOME_COLOR = "#D9822B", "#2F6DB5"
PICK_STRONG, PICK_LEAN = "#1E8E3E", "#D4EDDA"
EDGE_STRONG, EDGE_LEAN = 3.0, 1.5
SPREAD_FMT = '+0.0;-0.0;"PK"'
SEARCH_HINT = "🔍 Find a team: ⌘F / Ctrl+F, or ▾ on a header to filter"

# (group label, color, [(column header, width px, hover note)])
DAILY_GROUPS = [
    ("GAME", "#475569", [
        ("#", 34, "Game number, in tip-off order."),
        ("Tip (ET)", 70, "Scheduled tip-off, Eastern time."),
        ("Away", 150, "Visiting team (bold = model favorite)."),
        ("Home", 150, "Home team (bold = model favorite). The odds feed has no neutral-site flag, "
                      "so every game is modeled with home-court for this team."),
    ]),
    ("PROJECTED SCORE", "#2F5D8A", [
        ("Away", 54, "Model's projected points for the away team (mean of 10,000 simulations)."),
        ("Home", 54, "Model's projected points for the home team."),
        ("Total", 58, "Model's projected combined score."),
        ("Mkt Total", 66, "Sportsbook over/under (DraftKings, else FanDuel)."),
    ]),
    ("SPREAD (HOME TEAM)", "#B7791F", [
        ("Model", 60, "Model's spread for the HOME team: -4.5 = home favored by 4.5, "
                      "+4.5 = home is a 4.5-point underdog."),
        ("Market", 60, "Sportsbook spread for the HOME team (DraftKings, else FanDuel)."),
        ("Edge", 54, "Points between the model and market spreads, in favor of the Pick."),
        ("Pick", 150, "Side the model likes against the market spread. Solid green = edge 3+ pts, "
                      "light green = 1.5-3, gray = under 1.5."),
    ]),
    ("WIN PROBABILITY", "#0F766E", [
        ("Away", 52, "Model's chance the away team wins."),
        ("Split", 110, "Win probability split: orange = away, blue = home."),
        ("Home", 52, "Model's chance the home team wins."),
        ("Mkt Home", 66, "Sportsbook moneyline converted to a win chance for the home team, "
                         "with the bookmaker's margin removed."),
    ]),
    ("MARGIN OF VICTORY", "#5B4B8A", [
        ("Away 10+", 62, "Chance the away team wins by 10 or more."),
        ("Away 5–9", 62, "Chance the away team wins by 5 to 9."),
        ("Away 1–4", 62, "Chance the away team wins by 1 to 4."),
        ("Home 1–4", 62, "Chance the home team wins by 1 to 4."),
        ("Home 5–9", 62, "Chance the home team wins by 5 to 9."),
        ("Home 10+", 62, "Chance the home team wins by 10 or more. The six margin columns add up to 100%."),
    ]),
]


def _fmt(bg=None, fg=None, bold=False, size=None, italic=False, align=None, pattern=None,
         pattern_type="NUMBER", left_border=False, wrap=None) -> dict:
    fmt: dict = {"verticalAlignment": "MIDDLE", "textFormat": {"bold": bold, "fontSize": size or 10,
                                                               "foregroundColor": fg or INK}}
    if italic:
        fmt["textFormat"]["italic"] = True
    if bg:
        fmt["backgroundColor"] = bg
    if align:
        fmt["horizontalAlignment"] = align
    if pattern:
        fmt["numberFormat"] = {"type": pattern_type, "pattern": pattern}
    if left_border:
        fmt["borders"] = {"left": {"style": "SOLID", "color": DIVIDER}}
    if wrap:
        fmt["wrapStrategy"] = wrap
    return fmt


def _numcell(value, fmt: dict) -> dict:
    if value is None:
        return {"userEnteredValue": {"stringValue": "—"}, "userEnteredFormat": {**fmt, "horizontalAlignment": "CENTER"}}
    return {"userEnteredValue": {"numberValue": float(value)}, "userEnteredFormat": fmt}


def _text(value: str, fmt: dict) -> dict:
    return {"userEnteredValue": {"stringValue": value}, "userEnteredFormat": fmt}


def _split_bar(away: float, home: float) -> dict:
    formula = (f'=SPARKLINE({{{away:.4f},{home:.4f}}},{{"charttype","bar";"max",1;'
               f'"color1","{AWAY_COLOR}";"color2","{HOME_COLOR}"}})')
    return {"userEnteredValue": {"formulaValue": formula}}


def build_daily_grid(con, today: date, title: str = TAB_TITLES[0],
                     generated_at: datetime | None = None) -> GridTab:
    games = lp.slate_games(con, today)
    stamp = (generated_at or datetime.now(EASTERN)).astimezone(EASTERN)
    columns = [(group, color, col) for group, color, cols in DAILY_GROUPS for col in cols]
    n_cols = len(columns)
    group_start = {}
    for c, (group, _, _) in enumerate(columns):
        group_start.setdefault(group, c)
    first_of_group = set(group_start.values())
    frozen_cols = len(DAILY_GROUPS[0][2])

    # Row 0: title bar.  Row 1: subtitle + key.  Row 2: group bands.  Row 3: headers.
    bar = _fmt(bg=_hex(NAVY), fg=WHITE, bold=True, size=15)
    title_row = [_text(f"Daily Picks · {today:%A, %B %-d, %Y}", {**bar, "wrapStrategy": "OVERFLOW_CELL"})]
    title_row += [{"userEnteredFormat": bar} for _ in range(n_cols - 1)]

    sub = _fmt(bg=_hex("#EEF2F7"), fg=MUTED, size=9, italic=True, wrap="OVERFLOW_CELL")
    sub_row = [{"userEnteredFormat": sub} for _ in range(n_cols)]
    sub_row[0] = _text(f"{len(games)} games · updated {stamp:%-I:%M %p} ET", sub)
    sub_row[frozen_cols] = _text(
        f"{SEARCH_HINT} · Spreads are the home team's (−4.5 = home favored by 4.5) · Edge = model vs. "
        "market, toward the Pick · Hover any column header for details", sub)

    band_row, header_row, merges = [], [], []
    for c, (group, color, (label, _, note)) in enumerate(columns):
        band_fmt = _fmt(bg=_hex(color), fg=WHITE, bold=True, size=9, align="CENTER",
                        left_border=c in first_of_group)
        band_row.append(_text(group, band_fmt) if c in first_of_group else {"userEnteredFormat": band_fmt})
        head_fmt = _fmt(bg=_tint(color, 0.16), bold=True, size=9, align="CENTER", wrap="WRAP",
                        left_border=c in first_of_group)
        head_fmt["borders"] = {**head_fmt.get("borders", {}),
                               "bottom": {"style": "SOLID_MEDIUM", "color": _hex(color)}}
        header_row.append({**_text(label, head_fmt), "note": note})
    for group, start in group_start.items():
        width = sum(1 for g, _, _ in columns if g == group)
        if width > 1:
            merges.append((2, 3, start, start + width))

    rows = [title_row, sub_row, band_row, header_row]
    for i, g in enumerate(games):
        bg = ZEBRA if i % 2 else WHITE

        def f(c, **kw):
            return _fmt(bg=bg, left_border=c in first_of_group, **kw)

        fav = g.model_fav
        tip = (g.tip_et.hour * 3600 + g.tip_et.minute * 60) / 86400
        cells = [
            _numcell(i + 1, f(0, fg=MUTED, align="CENTER", pattern="0")),
            _numcell(tip, f(1, align="CENTER", pattern="h:mm AM/PM", pattern_type="TIME")),
            _text(g.away, f(2, bold=fav == g.away)),
            _text(g.home, f(3, bold=fav == g.home)),
            _numcell(round(g.away_pts), f(4, align="CENTER", pattern="0")),
            _numcell(round(g.home_pts), f(5, align="CENTER", pattern="0")),
            _numcell(g.total, f(6, align="CENTER", pattern="0.0")),
            _numcell(g.market_total, f(7, align="CENTER", pattern="0.0", fg=MUTED)),
            _numcell(g.model_line, f(8, align="CENTER", pattern=SPREAD_FMT, bold=True)),
            _numcell(g.market_line, f(9, align="CENTER", pattern=SPREAD_FMT, fg=MUTED)),
            _numcell(g.edge, f(10, align="CENTER", pattern="0.0")),
        ]
        if g.pick is None:
            cells.append(_text("—", f(11, fg=MUTED, align="CENTER")))
        elif g.edge >= EDGE_STRONG:
            cells.append(_text(g.pick, _fmt(bg=_hex(PICK_STRONG), fg=WHITE, bold=True, left_border=False)))
        elif g.edge >= EDGE_LEAN:
            cells.append(_text(g.pick, _fmt(bg=_hex(PICK_LEAN), fg=_hex("#14532D"), bold=True)))
        else:
            cells.append(_text(g.pick, f(11, fg=MUTED)))
        cells += [
            _numcell(g.away_win, f(12, align="CENTER", pattern="0%", pattern_type="PERCENT",
                               bold=fav == g.away)),
            {**_split_bar(g.away_win, g.home_win), "userEnteredFormat": f(13)},
            _numcell(g.home_win, f(14, align="CENTER", pattern="0%", pattern_type="PERCENT",
                               bold=fav == g.home)),
            _numcell(g.market_home_win, f(15, align="CENTER", pattern="0%", pattern_type="PERCENT", fg=MUTED)),
        ]
        for k in range(6):
            c = 16 + k
            if g.buckets is None:
                cells.append(_text("—", f(c, fg=MUTED, align="CENTER")))
                continue
            p = g.buckets[k]
            t = min(1.0, p / 0.5) * 0.9
            color = AWAY_COLOR if k < 3 else HOME_COLOR
            cells.append(_numcell(p, _fmt(bg=_tint(color, t), fg=WHITE if t > 0.55 else INK,
                                      bold=t > 0.55, align="CENTER", pattern="0%", pattern_type="PERCENT",
                                      left_border=c in first_of_group)))
        rows.append(cells)
    if not games:
        rows.append([_text("No locked picks for today yet.", _fmt(fg=MUTED, italic=True))]
                    + [{} for _ in range(n_cols - 1)])

    widths = [w for _, _, (_, w, _) in columns]
    return GridTab(title, SheetLayout(rows, n_cols, frozen_rows=4, col_widths=widths, frozen_cols=frozen_cols,
                                      merges=merges, row_heights={0: 36, 1: 22, 2: 22, 3: 34},
                                      default_row_px=24, filter_row=3 if games else None))


# --------------------------------------------------------------------------
# Power Rankings: designed grid (one value per cell, rank history + trend)
# --------------------------------------------------------------------------

RANK_HISTORY_DAYS = 30
TOP_N_DIVIDER = 25
TREND_UP, TREND_DOWN, TREND_FLAT = "#1E8E3E", "#C5221F", "#9AA0A6"
CHANGE_FMT = '"▲ "0;"▼ "0;"–"'
TEAM_COLOR, HISTORY_COLOR = "#1F2A44", "#475569"


def _trend_cell(ranks_oldest_first: list[int]) -> dict:
    if len(ranks_oldest_first) < 2:
        return {}
    first, last = ranks_oldest_first[0], ranks_oldest_first[-1]
    color = TREND_UP if last < first else TREND_DOWN if last > first else TREND_FLAT
    values = ",".join(str(-r) for r in ranks_oldest_first)      # negated: up = better rank
    return {"userEnteredValue": {"formulaValue":
            f'=SPARKLINE({{{values}}},{{"charttype","line";"linewidth",2;"color","{color}"}})'}}


def build_rankings_grid(con, today: date, title: str = TAB_TITLES[3],
                        generated_at: datetime | None = None, recompute_today: bool = False,
                        history_days: int = RANK_HISTORY_DAYS) -> GridTab:
    current = pr.ensure_rankings(con, today, recompute=recompute_today)
    history = pr.rank_history(con, today, history_days)             # newest first
    by_day = dict(history)
    week_ago = by_day.get(today - timedelta(days=7))
    # Until the season is 30 days old, L30D compares against opening day (the oldest history).
    month_day = today - timedelta(days=30)
    if month_day not in by_day and history:
        month_day = history[-1][0]
    month_ago = by_day.get(month_day)
    if month_ago is None or month_day == today - timedelta(days=30):
        l30_note = "Places gained (▲) or lost (▼) since 30 days ago."
    else:
        l30_note = (f"Places gained (▲) or lost (▼) since {month_day:%B %-d}, the first day with rankings "
                    f"this season; becomes a true 30-day change on {month_day + timedelta(days=30):%B %-d}.")
    stamp = (generated_at or datetime.now(EASTERN)).astimezone(EASTERN)

    fixed = [("Rank", 46, "Today's position, 1 = best."),
             ("Team", 170, "Team name (KenPom spelling)."),
             ("NDI", 62, "Neutral Dominance Index: the team's average chance to beat every other D-I "
                         "team on a neutral floor, from the calibrated simulator. Rankings sort by this."),
             ("L7D Change", 74, "Places gained (▲) or lost (▼) since 7 days ago."),
             ("L30D Change", 78, l30_note),
             ("Trend", 120, f"Daily rank over the last {len(history) + 1} days, oldest to newest; "
                            "up = moving up the rankings. Green = better than at the start, red = worse.")]
    dated = [(f"{d.month}/{d.day}", 46, f"Rank on the morning of {d:%A, %B %-d}.") for d, _ in history]
    columns = fixed + dated
    n_cols, n_fixed = len(columns), len(fixed)

    bar = _fmt(bg=_hex(NAVY), fg=WHITE, bold=True, size=15)
    title_row = [_text(f"Power Rankings · {today:%A, %B %-d, %Y}", {**bar, "wrapStrategy": "OVERFLOW_CELL"})]
    title_row += [{"userEnteredFormat": bar} for _ in range(n_cols - 1)]
    sub = _fmt(bg=_hex("#EEF2F7"), fg=MUTED, size=9, italic=True, wrap="OVERFLOW_CELL")
    sub_row = [{"userEnteredFormat": sub} for _ in range(n_cols)]
    sub_row[0] = _text(f"{len(current)} teams · updated {stamp:%-I:%M %p} ET", sub)
    if n_cols > n_fixed:
        sub_row[n_fixed] = _text(f"{SEARCH_HINT} · Ranked by Neutral Dominance Index (NDI): average chance to "
                                 "beat every other D-I team on a neutral floor · Hover any column header for "
                                 "details", sub)

    band_row, header_row, merges = [], [], [(2, 3, 0, n_fixed)]
    for c, (label, _, note) in enumerate(columns):
        color = TEAM_COLOR if c < n_fixed else HISTORY_COLOR
        first = c in (0, n_fixed)
        band_fmt = _fmt(bg=_hex(color), fg=WHITE, bold=True, size=9, align="CENTER", left_border=first)
        band_label = "TODAY" if c == 0 else "DAILY RANK HISTORY" if c == n_fixed else None
        band_row.append(_text(band_label, band_fmt) if band_label else {"userEnteredFormat": band_fmt})
        head_fmt = _fmt(bg=_tint(color, 0.16), bold=True, size=9, align="CENTER", wrap="WRAP", left_border=first)
        head_fmt["borders"] = {**head_fmt.get("borders", {}),
                               "bottom": {"style": "SOLID_MEDIUM", "color": _hex(color)}}
        header_row.append({**_text(label, head_fmt), "note": note})
    if n_cols - n_fixed > 1:
        merges.append((2, 3, n_fixed, n_cols))

    rows = [title_row, sub_row, band_row, header_row]
    for i, r in enumerate(current):
        bg = ZEBRA if i % 2 else WHITE
        divider = r.rank == TOP_N_DIVIDER and len(current) > TOP_N_DIVIDER

        def f(c, **kw):
            fmt = _fmt(bg=kw.pop("bg", bg), left_border=c == n_fixed, **kw)
            if divider:
                fmt["borders"] = {**fmt.get("borders", {}), "bottom": {"style": "SOLID_MEDIUM", "color": _hex(NAVY)}}
            return fmt

        cells = [
            _numcell(r.rank, f(0, bold=True, align="CENTER", pattern="0")),
            _text(r.team, f(1, bold=r.rank <= TOP_N_DIVIDER)),
            _numcell(r.ndi, f(2, bg=_tint("#0F766E", 0.55 * r.ndi ** 2), align="CENTER", pattern="0.0%",
                              pattern_type="PERCENT")),
        ]
        for c, baseline in ((3, week_ago), (4, month_ago)):
            if baseline is None or r.team not in baseline:
                cells.append(_text("new" if baseline is not None else "—", f(c, fg=MUTED, align="CENTER")))
                continue
            change = baseline[r.team] - r.rank
            fg = _hex(TREND_UP) if change > 0 else _hex(TREND_DOWN) if change < 0 else MUTED
            cells.append(_numcell(change, f(c, fg=fg, bold=change != 0, align="CENTER", pattern=CHANGE_FMT)))
        trend = [ranks[r.team] for _, ranks in reversed(history) if r.team in ranks] + [r.rank]
        cells.append({**_trend_cell(trend), "userEnteredFormat": f(5)})
        for k, (_, ranks) in enumerate(history):
            c = n_fixed + k
            cells.append(_numcell(ranks[r.team], f(c, fg=MUTED, align="CENTER", pattern="0"))
                         if r.team in ranks else {"userEnteredFormat": f(c)})
        rows.append(cells)
    if not current:
        rows.append([_text("No KenPom snapshot available yet.", _fmt(fg=MUTED, italic=True))]
                    + [{} for _ in range(n_cols - 1)])

    widths = [w for _, w, _ in columns]
    return GridTab(title, SheetLayout(rows, n_cols, frozen_rows=4, col_widths=widths, frozen_cols=n_fixed,
                                      merges=merges, row_heights={0: 36, 1: 22, 2: 22, 3: 34},
                                      default_row_px=24, filter_row=3 if current else None))


# --------------------------------------------------------------------------
# Model Performance: designed scorecard vs. Vegas
# --------------------------------------------------------------------------

STATUS_STYLE = {   # status -> (label, fill, text)
    "good": ("Good", "#1E8E3E", "#FFFFFF"),
    "ok": ("OK", "#F9AB00", "#3C2A00"),
    "poor": ("Poor", "#C5221F", "#FFFFFF"),
    "early": ("Too early", "#E8EAED", "#5F6368"),
}
DIRECTION_STYLE = {"up": ("▲ Improving", TREND_UP), "flat": ("● Steady", TREND_FLAT),
                   "down": ("▼ Slipping", TREND_DOWN)}
PERF_COLORS = {"live": "#0F766E", "backtest": "#2F5D8A"}
PERF_WIDTHS = [250, 84, 84, 96, 84, 108, 150, 70, 200]
UNIT_FMT = {"pct": ("0.0%", "PERCENT"), "pts": ("0.00", "NUMBER"), "brier": ("0.000", "NUMBER"),
            "bss": ("+0.000;-0.000;0.000", "NUMBER"), "signed_pts": ("+0.00;-0.00;0.00", "NUMBER")}
DIFF_FMT = {"pct": ('+0.0%;-0.0%;0.0%', "PERCENT"), "pts": ("+0.00;-0.00;0.00", "NUMBER"),
            "brier": ("+0.000;-0.000;0.000", "NUMBER"), "bss": ("+0.000;-0.000;0.000", "NUMBER"),
            "signed_pts": ("+0.00;-0.00;0.00", "NUMBER")}
GRADE_TINT = {"good": _tint("#1E8E3E", 0.18), "ok": _tint("#F9AB00", 0.22), "poor": _tint("#C5221F", 0.18)}


def _weekly_chart(m: "sc.Metric", values: list[float], trend: str | None) -> dict:
    if len(values) < 2:
        return {}
    nums = ",".join(f"{v:.5f}" for v in values)
    if m.benchmark is not None:   # bars: each week's edge over the benchmark
        opts = f'"charttype","column";"color","{TREND_UP}";"negcolor","{TREND_DOWN}";"axis",true'
    else:
        color = DIRECTION_STYLE.get(trend, ("", TREND_FLAT))[1]
        opts = f'"charttype","line";"linewidth",2;"color","{color}"'
    return {"userEnteredValue": {"formulaValue": f"=SPARKLINE({{{nums}}},{{{opts}}})"}}


def _chip(label: str, fill: str, text: str) -> dict:
    return _text(label, _fmt(bg=_hex(fill), fg=_hex(text), bold=True, size=9, align="CENTER"))


def _grade(adv: float | None, good: float, ok: float) -> str | None:
    if adv is None:
        return None
    return "good" if adv >= -good else "ok" if adv >= -ok else "poor"


class _Sheet:
    """Row accumulator for free-form designed tabs."""

    def __init__(self, n_cols: int):
        self.n_cols, self.rows, self.merges, self.heights = n_cols, [], [], {}

    def add(self, cells: list[dict], height: int | None = None) -> int:
        self.rows.append(cells + [{} for _ in range(self.n_cols - len(cells))])
        if height:
            self.heights[len(self.rows) - 1] = height
        return len(self.rows) - 1

    def band(self, label: str, color: str) -> None:
        fmt = _fmt(bg=_hex(color), fg=WHITE, bold=True, size=11)
        r = self.add([_text(label, fmt)] + [{"userEnteredFormat": fmt} for _ in range(self.n_cols - 1)], 30)
        self.merges.append((r, r + 1, 0, self.n_cols))

    def subheading(self, label: str, color: str) -> None:
        self.add([_text(label, _fmt(fg=_hex(color), bold=True, size=10, wrap="OVERFLOW_CELL"))], 26)

    def header(self, labels_notes: list[tuple[str, str]], color: str) -> None:
        cells = []
        for label, note in labels_notes:
            fmt = _fmt(bg=_tint(color, 0.16), bold=True, size=9, align="CENTER", wrap="WRAP")
            fmt["borders"] = {"bottom": {"style": "SOLID_MEDIUM", "color": _hex(color)}}
            cells.append({**_text(label, fmt), "note": note})
        self.add(cells, 34)

    def note(self, text: str) -> None:
        self.add([_text(text, _fmt(fg=MUTED, italic=True, size=9, wrap="OVERFLOW_CELL"))])

    def blank(self) -> None:
        self.add([])


def _scorecard_block(sh: _Sheet, rows: list[tuple], color: str) -> None:
    season = sc.scorecard(rows)
    weeks = sc.weekly(rows)
    recent_sc = sc.scorecard(sc.recent(rows))
    sh.header([("Metric", "What is being measured. Hover a metric name for its definition."),
               ("Model", "The model's value."),
               ("Benchmark", "What the model is measured against (see the last column)."),
               ("vs. Benchmark", "Model minus benchmark. Green = the model is ahead, red = behind."),
               ("Status", f"Good = at or ahead of the benchmark; OK = slightly behind; Poor = clearly "
                          f"behind. 'Too early' until {sc.MIN_SAMPLE} games are graded."),
               ("Direction", f"Last {sc.RECENT_DAYS} days vs. the whole sample: Improving, Steady or "
                             "Slipping."),
               ("Weekly trend", "Bars: each week's margin over the benchmark (green = model ahead, "
                                "red = behind). Line: the weekly value, for metrics without a benchmark."),
               ("Games", "Games behind this metric."),
               ("Benchmark is", "Where the benchmark number comes from.")], color)
    for i, m in enumerate(sc.METRICS):
        bg = ZEBRA if i % 2 else WHITE
        value, bench = m.value(season), (m.benchmark(season) if m.benchmark else None)
        pattern, ptype = UNIT_FMT[m.unit]
        cells = [{**_text(m.label, _fmt(bg=bg, bold=True)), "note": m.note},
                 _numcell(value if sc._finite(value) else None,
                          _fmt(bg=bg, bold=True, align="CENTER", pattern=pattern, pattern_type=ptype)),
                 _numcell(bench if sc._finite(bench) else None,
                          _fmt(bg=bg, fg=MUTED, align="CENTER", pattern=pattern, pattern_type=ptype))]
        adv = sc.advantage(m, season)
        if m.benchmark is not None and adv is not None:
            dpat, dtype = DIFF_FMT[m.unit]
            fg = _hex(TREND_UP) if adv > 0 else _hex(TREND_DOWN) if adv < 0 else MUTED
            cells.append(_numcell(value - bench, _fmt(bg=bg, fg=fg, bold=True, align="CENTER",
                                                      pattern=dpat, pattern_type=dtype)))
        else:
            cells.append(_text("—", _fmt(bg=bg, fg=MUTED, align="CENTER")))
        st = sc.status(m, season)
        cells.append(_chip(*STATUS_STYLE[st]) if st else _text("—", _fmt(bg=bg, fg=MUTED, align="CENTER")))
        trend = sc.direction(m, season, recent_sc, len(weeks))
        if trend:
            label, tcolor = DIRECTION_STYLE[trend]
            cells.append(_text(label, _fmt(bg=bg, fg=_hex(tcolor), bold=True, size=9, align="CENTER")))
        else:
            cells.append(_text("—", _fmt(bg=bg, fg=MUTED, align="CENTER")))
        cells.append({**_weekly_chart(m, sc.weekly_series(m, weeks), trend), "userEnteredFormat": _fmt(bg=bg)})
        cells.append(_numcell(m.sample(season), _fmt(bg=bg, fg=MUTED, align="CENTER", pattern="#,##0")))
        cells.append(_text(m.benchmark_label or "No benchmark", _fmt(bg=bg, fg=MUTED, size=9)))
        sh.add(cells, 28)


def _tranche_block(sh: _Sheet, rows: list[tuple], color: str) -> None:
    report = vm.audit_rows([r[1:] for r in rows], "", date.min, date.max)
    sh.subheading("By closing spread", color)
    sh.header([("Spread range", "Games grouped by the favorite's closing spread."),
               ("Games", "Games with a closing spread in this range."),
               ("Model SU", "Model picks the winner (games with a moneyline)."),
               ("Vegas SU", "Vegas favorite wins (same games)."),
               ("ATS", "Model's side covers the closing spread (pushes excluded); 52.4% breaks even."),
               ("Model Brier", "Win-probability error, lower is better."),
               ("Vegas Brier", "Same for the de-vigged closing moneyline."),
               ("BSS", "Brier Skill Score vs. Vegas; above 0 = model better.")], color)
    for i, t in enumerate(vm.TRANCHES):
        tm, bg = report.tranches[t], ZEBRA if i % 2 else WHITE
        mk = tm.market
        su_grade = _grade(None if not sc._finite(mk.su_model) else mk.su_model - mk.su_vegas, 0.0, 0.02)
        ats_grade = _grade(None if not sc._finite(tm.ats) else tm.ats - sc.ATS_BREAK_EVEN, 0.0, 0.024)
        bss_grade = _grade(mk.bss if sc._finite(mk.bss) else None, 0.0, 0.02)

        def graded(x, grade, pattern, ptype="NUMBER"):
            x = x if sc._finite(x) else None
            return _numcell(x, _fmt(bg=GRADE_TINT.get(grade, bg), bold=grade is not None, align="CENTER",
                                    pattern=pattern, pattern_type=ptype))

        sh.add([_text(vm.TRANCHE_LABELS[t], _fmt(bg=bg, bold=True)),
                _numcell(tm.n_spread, _fmt(bg=bg, fg=MUTED, align="CENTER", pattern="#,##0")),
                graded(mk.su_model, su_grade, "0.0%", "PERCENT"),
                graded(mk.su_vegas, None, "0.0%", "PERCENT"),
                graded(tm.ats, ats_grade, "0.0%", "PERCENT"),
                graded(mk.brier_model, None, "0.000"),
                graded(mk.brier_vegas, None, "0.000"),
                graded(mk.bss, bss_grade, "+0.000;-0.000;0.000")], 26)


def _calibration_block(sh: _Sheet, rows: list[tuple], color: str) -> None:
    cal = sc.calibration(rows)
    if not cal:
        return
    sh.subheading("Calibration: when the model gives the favorite X%, how often does it win?", color)
    sh.header([("Model's win chance", "Games bucketed by the model's win chance for its favorite."),
               ("Games", "Games in this bucket with a closing moneyline."),
               ("Model said", "Average model win chance for its favorite."),
               ("Vegas said", "Average de-vigged Vegas win chance for the same team."),
               ("Actually won", "How often that team won."),
               ("Model gap", "Actual minus model. Near 0 = well calibrated; negative = overconfident."),
               ("Vegas gap", "Actual minus Vegas, on the same games.")], color)
    for i, c in enumerate(cal):
        bg = ZEBRA if i % 2 else WHITE
        label = f"{c.lo:.0%}–{c.hi:.0%}"

        def gap(x):
            grade = _grade(-abs(x), 0.02, 0.05)
            return _numcell(x, _fmt(bg=GRADE_TINT[grade], bold=True, align="CENTER",
                                    pattern="+0.0%;-0.0%;0.0%", pattern_type="PERCENT"))

        pct = lambda x, **kw: _numcell(x, _fmt(bg=bg, align="CENTER", pattern="0.0%",  # noqa: E731
                                                pattern_type="PERCENT", **kw))
        sh.add([_text(label, _fmt(bg=bg, bold=True)),
                _numcell(c.n, _fmt(bg=bg, fg=MUTED, align="CENTER", pattern="#,##0")),
                pct(c.model_said), pct(c.vegas_said, fg=MUTED), pct(c.actual, bold=True),
                gap(c.actual - c.model_said), gap(c.actual - c.vegas_said)], 26)


def build_performance_grid(con, today: date, title: str = TAB_TITLES[2],
                           generated_at: datetime | None = None) -> GridTab:
    stamp = (generated_at or datetime.now(EASTERN)).astimezone(EASTERN)
    live_season = season_of(today)
    seasons = [("live", f"THIS SEASON · LIVE PICKS · {live_season - 1}-{str(live_season)[2:]}",
                sc.graded_rows(con, live_season, lp.LIVE_MODEL_VERSION),
                "Picks locked before tip-off and graded after the final."),
               ("backtest", f"LAST SEASON · BACKTEST · {live_season - 2}-{str(live_season - 1)[2:]}",
                sc.graded_rows(con, live_season - 1, vm.DEFAULT_MODEL_VERSION),
                f"Every regular-season game replayed with the {vm.DEFAULT_MODEL_VERSION} model, using only "
                "ratings available the morning of the game.")]
    sh = _Sheet(len(PERF_WIDTHS))
    bar = _fmt(bg=_hex(NAVY), fg=WHITE, bold=True, size=15)
    sh.add([_text(f"Model Performance · {today:%A, %B %-d, %Y}", {**bar, "wrapStrategy": "OVERFLOW_CELL"})]
           + [{"userEnteredFormat": bar} for _ in range(sh.n_cols - 1)], 36)
    sub = _fmt(bg=_hex("#EEF2F7"), fg=MUTED, size=9, italic=True, wrap="OVERFLOW_CELL")
    sh.add([_text(f"Updated {stamp:%-I:%M %p} ET · Vegas = closing lines (DraftKings, else FanDuel), "
                  "bookmaker's margin removed · Green = model ahead of the benchmark, red = behind · "
                  "Hover any header or metric for details", sub)]
           + [{"userEnteredFormat": sub} for _ in range(sh.n_cols - 1)], 22)
    for key, label, rows, blurb in seasons:
        color = PERF_COLORS[key]
        sh.blank()
        sh.band(label, color)
        sh.note(f"{blurb} {len(rows):,} graded game{'' if len(rows) == 1 else 's'}.")
        if not rows:
            sh.note("No graded games yet: the first results land after tonight's games go final.")
            continue
        _scorecard_block(sh, rows, color)
        sh.blank()
        _tranche_block(sh, rows, color)
        if len(rows) >= sc.MIN_SAMPLE:
            sh.blank()
            _calibration_block(sh, rows, color)
    return GridTab(title, SheetLayout(sh.rows, sh.n_cols, frozen_rows=2, col_widths=list(PERF_WIDTHS),
                                      merges=sh.merges, row_heights=sh.heights, default_row_px=24))


# --------------------------------------------------------------------------
# Historical Picks: record strip + one-value-per-cell ledger
# --------------------------------------------------------------------------

RESULT_STYLE = {"W": ("#1E8E3E", "#FFFFFF"), "L": ("#C5221F", "#FFFFFF"),
                "Cover": ("#1E8E3E", "#FFFFFF"), "Loss": ("#C5221F", "#FFFFFF"), "Push": ("#E8EAED", "#5F6368")}
UNITS_WIN, UNITS_LOSS = 1.0, -1.1          # flat 1-unit bets at -110

# (group label, color, [(column header, width px, hover note)])
LEDGER_GROUPS = [
    ("GAME", "#475569", [
        ("Date", 84, "Game date (Eastern)."),
        ("Away", 150, "Visiting team."),
        ("Home", 150, "Home team (the odds feed has no neutral-site flag)."),
    ]),
    ("MODEL PICK", "#B7791F", [
        ("Model Margin", 72, "Model's projected HOME margin when the pick was locked: +6.0 = home by 6."),
        ("Pick", 150, "Side the model took against the spread at pick time."),
        ("Pick Line", 66, "HOME team's spread when the pick was locked (-4.5 = home favored by 4.5)."),
    ]),
    ("MARKET", "#0F766E", [
        ("Close Line", 66, "HOME team's spread at tip-off."),
        ("CLV", 56, "Closing line value: points the line moved toward the Pick between pick time and "
                    "tip-off. Positive = the market moved our way, a sign of a sharp pick."),
    ]),
    ("RESULT", "#2F5D8A", [
        ("Away Pts", 56, "Away team's final score."),
        ("Home Pts", 56, "Home team's final score."),
        ("Final Margin", 66, "HOME team's final margin: +8 = home won by 8."),
        ("SU", 48, "Did the model's projected winner win? W / L."),
        ("ATS", 64, "Did the Pick cover the pick-time spread? Cover / Loss / Push."),
    ]),
]


def _record(games: list) -> dict:
    ats = [g.ats for g in games if g.ats]
    covers, losses, pushes = ats.count("Cover"), ats.count("Loss"), ats.count("Push")
    su_w = sum(g.su == "W" for g in games)
    clvs = [g.clv for g in games if g.clv is not None]
    units, running = 0.0, []
    for g in reversed(games):                       # oldest first
        if g.ats == "Cover":
            units += UNITS_WIN
        elif g.ats == "Loss":
            units += UNITS_LOSS
        if g.ats:
            running.append(units)
    return {"n": len(games), "su_w": su_w, "su_l": len(games) - su_w,
            "su_pct": su_w / len(games) if games else None,
            "covers": covers, "losses": losses, "pushes": pushes,
            "ats_pct": covers / (covers + losses) if covers + losses else None,
            "units": units if running else None, "running": running,
            "clv": sum(clvs) / len(clvs) if clvs else None,
            "beat_close": sum(c > 0 for c in clvs) / len(clvs) if clvs else None}


def _chip_cell(label: str | None, bg) -> dict:
    if not label:
        return _text("—", _fmt(bg=bg, fg=MUTED, align="CENTER"))
    fill, text = RESULT_STYLE[label]
    return _text(label, _fmt(bg=_hex(fill), fg=_hex(text), bold=True, size=9, align="CENTER"))


def build_ledger_grid(con, today: date, title: str = TAB_TITLES[1],
                      generated_at: datetime | None = None) -> GridTab:
    games = lp.ledger_games(con)
    rec = _record(games)
    stamp = (generated_at or datetime.now(EASTERN)).astimezone(EASTERN)
    columns = [(group, color, col) for group, color, cols in LEDGER_GROUPS for col in cols]
    n_cols = len(columns)
    group_start: dict[str, int] = {}
    for c, (group, _, _) in enumerate(columns):
        group_start.setdefault(group, c)
    first_of_group = set(group_start.values())
    frozen_cols = len(LEDGER_GROUPS[0][2])

    bar = _fmt(bg=_hex(NAVY), fg=WHITE, bold=True, size=15)
    rows = [[_text("Historical Picks · walk-forward ledger", {**bar, "wrapStrategy": "OVERFLOW_CELL"})]
            + [{"userEnteredFormat": bar} for _ in range(n_cols - 1)]]
    sub = _fmt(bg=_hex("#EEF2F7"), fg=MUTED, size=9, italic=True, wrap="OVERFLOW_CELL")
    sub_row = [{"userEnteredFormat": sub} for _ in range(n_cols)]
    sub_row[0] = _text(f"{rec['n']:,} graded picks · updated {stamp:%-I:%M %p} ET", sub)
    sub_row[frozen_cols] = _text(
        f"{SEARCH_HINT} · Every pick is locked before tip-off and never changed · Spreads and margins are the "
        "home team's · Hover any header for details", sub)
    rows.append(sub_row)

    # Record strip: label row + value row. (label, column, value, number pattern, type, grade)
    def grade(x, bench, ok):
        return None if x is None else ("good" if x >= bench else "ok" if x >= bench - ok else "poor")

    kpis = [
        ("GRADED", 0, rec["n"], "#,##0", "NUMBER", None),
        ("STRAIGHT UP", 1, rec["su_pct"], "0.0%", "PERCENT", None),
        ("ATS", 2, rec["ats_pct"], "0.0%", "PERCENT", grade(rec["ats_pct"], sc.ATS_BREAK_EVEN, 0.024)),
        ("COVERS", 3, rec["covers"], "0", "NUMBER", None),
        ("LOSSES", 4, rec["losses"], "0", "NUMBER", None),
        ("PUSHES", 5, rec["pushes"], "0", "NUMBER", None),
        ("UNITS @ −110", 6, rec["units"], "+0.0;-0.0;0.0", "NUMBER", grade(rec["units"], 0.0, 3.0)),
        ("AVG CLV", 11, rec["clv"], "+0.00;-0.00;0.00", "NUMBER", grade(rec["clv"], 0.0, 0.25)),
        ("BEAT CLOSE", 12, rec["beat_close"], "0%", "PERCENT", grade(rec["beat_close"], 0.5, 0.05)),
    ]
    kpi_bg = _hex("#F8FAFC")
    labels = [{"userEnteredFormat": _fmt(bg=kpi_bg)} for _ in range(n_cols)]
    values = [{"userEnteredFormat": _fmt(bg=kpi_bg)} for _ in range(n_cols)]
    notes = {"ATS": "Covers / (covers + losses). 52.4% breaks even at -110.",
             "UNITS @ −110": "Profit betting 1 unit on every pick at -110 (+1 per cover, -1.1 per loss).",
             "AVG CLV": "Average points the line moved toward our pick by tip-off.",
             "BEAT CLOSE": "Share of picks where the line moved toward our side by tip-off."}
    for label, c, value, pattern, ptype, g in kpis:
        labels[c] = {**_text(label, _fmt(bg=kpi_bg, fg=MUTED, bold=True, size=8, align="CENTER")),
                     **({"note": notes[label]} if label in notes else {})}
        fmt = _fmt(bg=GRADE_TINT.get(g, kpi_bg), bold=True, size=14, align="CENTER", pattern=pattern,
                   pattern_type=ptype)
        values[c] = _numcell(value, fmt)
    labels[7] = _text("UNITS TREND", _fmt(bg=kpi_bg, fg=MUTED, bold=True, size=8, align="CENTER"))
    if len(rec["running"]) >= 2:
        color = TREND_UP if rec["running"][-1] >= 0 else TREND_DOWN
        nums = ",".join(f"{u:.1f}" for u in rec["running"])
        values[7] = {"userEnteredValue": {"formulaValue":
                     f'=SPARKLINE({{{nums}}},{{"charttype","line";"linewidth",2;"color","{color}"}})'},
                     "userEnteredFormat": _fmt(bg=kpi_bg)}
    rows += [labels, values, []]
    merges = [(2, 3, 7, 11), (3, 4, 7, 11)]

    band_row, header_row = [], []
    for c, (group, color, (label, _, note)) in enumerate(columns):
        band_fmt = _fmt(bg=_hex(color), fg=WHITE, bold=True, size=9, align="CENTER",
                        left_border=c in first_of_group)
        band_row.append(_text(group, band_fmt) if c in first_of_group else {"userEnteredFormat": band_fmt})
        head_fmt = _fmt(bg=_tint(color, 0.16), bold=True, size=9, align="CENTER", wrap="WRAP",
                        left_border=c in first_of_group)
        head_fmt["borders"] = {**head_fmt.get("borders", {}),
                               "bottom": {"style": "SOLID_MEDIUM", "color": _hex(color)}}
        header_row.append({**_text(label, head_fmt), "note": note})
    band_at = len(rows)
    for group, start in group_start.items():
        width = sum(1 for g, _, _ in columns if g == group)
        if width > 1:
            merges.append((band_at, band_at + 1, start, start + width))
    rows += [band_row, header_row]
    header_at = band_at + 1

    for i, g in enumerate(games):
        bg = ZEBRA if i % 2 else WHITE

        def f(c, **kw):
            return _fmt(bg=bg, left_border=c in first_of_group, **kw)

        clv_fg = MUTED if not g.clv else _hex(TREND_UP) if g.clv > 0 else _hex(TREND_DOWN)
        rows.append([
            _text(f"{g.game_date:%Y-%m-%d}", f(0, fg=MUTED, align="CENTER")),
            _text(g.away, f(1, bold=g.pred_margin < 0)),
            _text(g.home, f(2, bold=g.pred_margin >= 0)),
            _numcell(round(g.pred_margin, 1), f(3, align="CENTER", pattern="+0.0;-0.0;0.0")),
            _text(g.pick, f(4, bold=True)) if g.pick else _text("—", f(4, fg=MUTED, align="CENTER")),
            _numcell(g.pick_line, f(5, align="CENTER", pattern=SPREAD_FMT)),
            _numcell(g.closing_line, f(6, fg=MUTED, align="CENTER", pattern=SPREAD_FMT)),
            _numcell(g.clv, f(7, fg=clv_fg, bold=bool(g.clv), align="CENTER", pattern="+0.0;-0.0;0.0")),
            _numcell(g.away_pts, f(8, align="CENTER", pattern="0")),
            _numcell(g.home_pts, f(9, align="CENTER", pattern="0")),
            _numcell(g.final_margin, f(10, bold=True, align="CENTER", pattern="+0;-0;0")),
            _chip_cell(g.su, bg),
            _chip_cell(g.ats, bg),
        ])
    if not games:
        rows.append([_text("No graded picks yet: tonight's games are graded once they go final.",
                           _fmt(fg=MUTED, italic=True))] + [{} for _ in range(n_cols - 1)])
    rows = [r + [{} for _ in range(n_cols - len(r))] for r in rows]
    widths = [w for _, _, (_, w, _) in columns]
    return GridTab(title, SheetLayout(rows, n_cols, frozen_rows=header_at + 1, col_widths=widths,
                                      frozen_cols=frozen_cols, merges=merges,
                                      row_heights={0: 36, 1: 22, 2: 20, 3: 34, 4: 10, band_at: 22, header_at: 34},
                                      default_row_px=24, filter_row=header_at if games else None))


# --------------------------------------------------------------------------
# Google Sheets / Drive
# --------------------------------------------------------------------------


def config_from_env() -> tuple[str, Path]:
    sheet_id = os.getenv("GOOGLE_SHEET_ID")
    key = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE") or os.getenv("GOOGLE_SHEETS_CREDENTIALS_PATH")
    if not sheet_id or not key:
        raise PublishConfigError(
            "Set GOOGLE_SHEET_ID and GOOGLE_SERVICE_ACCOUNT_FILE in .env (see src/publish_gsheet.py)")
    path = Path(key).expanduser()
    if "Desktop" in path.parts:
        raise PublishConfigError(
            f"Service-account key is under ~/Desktop ({path}); macOS blocks background access there. "
            "Move it (e.g. ~/.config/terrarium/) and update GOOGLE_SERVICE_ACCOUNT_FILE.")
    if not path.is_file():
        raise PublishConfigError(f"Service-account key not found: {path}")
    return sheet_id, path


def build_services(key_path: Path):
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    creds = service_account.Credentials.from_service_account_file(str(key_path), scopes=list(SCOPES))
    return (build("sheets", "v4", credentials=creds, cache_discovery=False),
            build("drive", "v3", credentials=creds, cache_discovery=False))


class SheetsPublisher:
    def __init__(self, sheets, drive, spreadsheet_id: str):
        self.sheets, self.drive, self.spreadsheet_id = sheets, drive, spreadsheet_id
        self._filtered: set[int] | None = None   # sheetIds that currently have a basic filter

    def _properties(self) -> list[dict]:
        meta = self.sheets.spreadsheets().get(
            spreadsheetId=self.spreadsheet_id, fields="sheets(properties,basicFilter.range)").execute()
        sheets = meta.get("sheets", [])
        self._filtered = {s["properties"]["sheetId"] for s in sheets if s.get("basicFilter")}
        return [s["properties"] for s in sheets]

    def _batch(self, requests: list) -> dict:
        if not requests:
            return {}
        return self.sheets.spreadsheets().batchUpdate(
            spreadsheetId=self.spreadsheet_id, body={"requests": requests}).execute()

    def ensure_tabs(self, titles=TAB_TITLES) -> dict[str, int]:
        """Title -> sheetId, adding tabs as needed. A fresh file's lone default
        tab ("Sheet1") is renamed to the first title instead of left behind."""
        props = self._properties()
        by_title = {p["title"]: p["sheetId"] for p in props}
        if len(props) == 1 and props[0]["title"] not in titles:
            self._batch([{"updateSheetProperties": {
                "properties": {"sheetId": props[0]["sheetId"], "title": titles[0]}, "fields": "title"}}])
            by_title = {titles[0]: props[0]["sheetId"]}
        missing = [(i, t) for i, t in enumerate(titles) if t not in by_title]
        if missing:
            reply = self._batch([{"addSheet": {"properties": {"title": t, "index": i}}} for i, t in missing])
            for (_, t), r in zip(missing, reply["replies"]):
                by_title[t] = r["addSheet"]["properties"]["sheetId"]
        return {t: by_title[t] for t in titles}

    def write_tab(self, sheet_id: int, content: GridTab) -> None:
        lay = content.layout
        n_rows = len(lay.rows) + 1   # one spare row: a sheet can't freeze every row
        whole = {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": n_rows,
                 "startColumnIndex": 0, "endColumnIndex": lay.n_cols}
        if self._filtered is None:
            self._properties()
        requests = [
            # Unmerge first: freezing across an old merge is rejected.
            {"unmergeCells": {"range": {"sheetId": sheet_id}}},
            {"updateSheetProperties": {
                "properties": {"sheetId": sheet_id, "gridProperties": {
                    "rowCount": n_rows, "columnCount": lay.n_cols,
                    "frozenRowCount": lay.frozen_rows, "frozenColumnCount": lay.frozen_cols}},
                "fields": "gridProperties(rowCount,columnCount,frozenRowCount,frozenColumnCount)"}},
            # fields="*" on every cell in range also wipes the previous publish's values/formats/notes.
            {"updateCells": {"range": whole, "rows": [{"values": r} for r in lay.rows], "fields": "*"}},
            *[{"mergeCells": {"range": {"sheetId": sheet_id, "startRowIndex": r0, "endRowIndex": r1,
                                        "startColumnIndex": c0, "endColumnIndex": c1},
                              "mergeType": "MERGE_ALL"}} for r0, r1, c0, c1 in lay.merges],
            *[{"updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": c, "endIndex": c + 1},
                "properties": {"pixelSize": px}, "fields": "pixelSize"}}
              for c, px in enumerate(lay.col_widths)],
            {"updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": 0, "endIndex": n_rows},
                "properties": {"pixelSize": lay.default_row_px}, "fields": "pixelSize"}},
            *[{"updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": r, "endIndex": r + 1},
                "properties": {"pixelSize": px}, "fields": "pixelSize"}}
              for r, px in sorted(lay.row_heights.items())],
        ]
        if lay.filter_row is not None:
            # Header filter buttons (▾) over the table; replaces any previous filter range.
            requests.append({"setBasicFilter": {"filter": {"range": {
                "sheetId": sheet_id, "startRowIndex": lay.filter_row, "endRowIndex": len(lay.rows),
                "startColumnIndex": 0, "endColumnIndex": lay.n_cols}}}})
            self._filtered.add(sheet_id)
        elif sheet_id in self._filtered:
            requests.insert(0, {"clearBasicFilter": {"sheetId": sheet_id}})
            self._filtered.discard(sheet_id)
        self._batch(requests)

    def make_public(self) -> None:
        self.drive.permissions().create(fileId=self.spreadsheet_id, body={"type": "anyone", "role": "reader"},
                                        fields="id").execute()

    def publish(self, tabs: list[GridTab], make_public: bool = False) -> str:
        ids = self.ensure_tabs([t.title for t in tabs])
        for tab in tabs:
            self.write_tab(ids[tab.title], tab)
        if make_public:
            self.make_public()
        return f"https://docs.google.com/spreadsheets/d/{self.spreadsheet_id}/edit"
