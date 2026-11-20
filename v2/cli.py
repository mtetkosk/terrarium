"""Unified command-line interface for the CBB prediction engine.

    python cli.py ingest [--date D | --start D [--end D]]
    python cli.py simulate --team1 "Duke" --team2 "Michigan St." [--neutral | --venue V]
    python cli.py import-v1
    python cli.py predict --season 2026 [--start D --end D] [--hca X]
    python cli.py audit --season 2026 [--start D --end D] [--model-version V]
"""

from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src import ingest_kenpom as ik
from src import validate_model as vm
from src.db import PROJECT_ROOT, get_db_connection
from src.import_v1 import DEFAULT_V1_DB, import_v1
from src.simulate_game import (
    DEFAULT_HCA_EFF,
    DEFAULT_N_SIMS,
    SimulationError,
    SimulationResult,
    resolve_venue_hca,
    simulate_game,
)

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="NCAA D-I basketball prediction engine (Lean V1.0).")
console = Console()

DbPath = typer.Option(None, "--db-path", help="DuckDB file (default: data/cbb_engine.duckdb)")


def _date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter(f"expected YYYY-MM-DD, got {value!r}") from exc


def _fail(message: str) -> None:
    console.print(f"[bold red]Error:[/] {message}")
    raise typer.Exit(1)


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING,
                        format="%(levelname)s %(message)s")


# --------------------------------------------------------------------------
# ingest
# --------------------------------------------------------------------------


@app.command()
def ingest(
    date_: str = typer.Option(None, "--date", help="Live snapshot date (default today; past dates must be cached)"),
    start: str = typer.Option(None, help="Archive backfill start date (enables backfill)"),
    end: str = typer.Option(None, help="Archive backfill end date, inclusive (default yesterday)"),
    delay: float = typer.Option(ik.DEFAULT_BACKFILL_DELAY, help="Seconds between archive fetches"),
    db_path: Path = DbPath,
    cache_dir: Path = typer.Option(ik.DEFAULT_CACHE_DIR, help="Raw HTML cache directory"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Ingest today's KenPom snapshot, or backfill archived ratings."""
    _setup_logging(verbose)
    ik.load_env_files([PROJECT_ROOT / ".env", PROJECT_ROOT.parent / ".env"])
    snap, lo, hi = _date(date_), _date(start), _date(end)
    try:
        if lo is not None:
            if snap is not None:
                raise typer.BadParameter("--date can't be combined with --start/--end")
            hi = hi or date.today() - timedelta(days=1)
            with console.status(f"Backfilling KenPom archive {lo} → {hi}..."):
                res = ik.backfill(lo, hi, db_path, cache_dir, delay=delay)
            console.print(f"[green]Backfilled {len(res.ingested)} dates[/] "
                          f"({sum(res.ingested.values())} rows); {len(res.no_data)} off-days; "
                          f"{len(res.failed)} failed")
            for d, err in res.failed.items():
                console.print(f"  [red]{d}[/]: {err}")
            if res.failed:
                raise typer.Exit(1)
            return
        rows = ik.ingest(snap, db_path, cache_dir)
    except ik.KenPomError as exc:
        _fail(str(exc))
    missing = sum(r.three_pa_rate is None for r in rows)
    console.print(f"[green]Upserted {len(rows)} teams[/] for {rows[0].snapshot_date} "
                  f"({missing} missing 3PA rate)")


# --------------------------------------------------------------------------
# simulate
# --------------------------------------------------------------------------


def _fmt_ml(ml: float) -> str:
    return f"{ml:+.0f}" if math.isfinite(ml) else "n/a"


def matchup_card(r: SimulationResult, venue_desc: str) -> Panel:
    fav = r.team_a if r.spread_a <= 0 else r.team_b
    teams = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    for col, justify in (("Team", "left"), ("Proj.", "right"), ("Win", "right"),
                         ("Fair ML", "right"), ("By 5+", "right"), ("By 10+", "right")):
        teams.add_column(col, justify=justify)
    teams.add_row(r.team_a, f"{r.expected_score_a:.1f}", f"{r.win_prob_a:.1%}", _fmt_ml(r.fair_ml_a),
                  f"{r.prob_a_by_5:.1%}", f"{r.prob_a_by_10:.1%}")
    teams.add_row(r.team_b, f"{r.expected_score_b:.1f}", f"{r.win_prob_b:.1%}", _fmt_ml(r.fair_ml_b),
                  f"{r.prob_b_by_5:.1%}", f"{r.prob_b_by_10:.1%}")

    details = Table.grid(padding=(0, 2))
    details.add_column(style="dim")
    details.add_column()
    details.add_row("Line", f"[bold]{fav} {-abs(r.spread_a):.1f}[/]   Total {r.expected_total:.1f}   "
                            f"OT {r.ot_prob:.1%}")
    details.add_row("Venue", f"{venue_desc}, HCA {r.hca_eff:+.1f} per 100 poss")
    imputed = [t for t, flag in ((r.team_a, r.three_pa_imputed_a), (r.team_b, r.three_pa_imputed_b)) if flag]
    details.add_row("Ratings", f"{r.team_a} {r.snapshot_date_a}, {r.team_b} {r.snapshot_date_b}"
                    + (f"  [yellow](3PA imputed: {', '.join(imputed)})[/]" if imputed else ""))
    details.add_row("Model", f"{r.expected_possessions:.1f} poss; efficiency {r.expected_eff_a:.1f} "
                             f"(σ {r.sigma_eff_a:.1f}) / {r.expected_eff_b:.1f} (σ {r.sigma_eff_b:.1f}); "
                             f"league tempo {r.league_tempo:.1f}, eff {r.league_efficiency:.1f}"
                             + (f"; early-season stretch ×{r.margin_stretch:.2f}"
                                if r.margin_stretch != 1.0 else ""))
    details.add_row("Sims", f"{r.n_sims:,}")

    body = Table.grid()
    body.add_row(teams)
    body.add_row("")
    body.add_row(details)
    return Panel(body, title=f"[bold]{r.team_a} vs {r.team_b}[/]", expand=False)


@app.command()
def simulate(
    team1: str = typer.Option(..., help="Team A (KenPom spelling, e.g. 'Michigan St.')"),
    team2: str = typer.Option(..., help="Team B"),
    venue: str = typer.Option("home", help="home (team1 hosts), away, neutral, or an arena name"),
    neutral: bool = typer.Option(False, "--neutral", help="Shortcut for --venue neutral"),
    hca: float = typer.Option(DEFAULT_HCA_EFF, help="Home-court advantage, pts per 100 possessions"),
    date_: str = typer.Option(None, "--date", help="Game date; uses ratings on or before it (default today)"),
    sims: int = typer.Option(DEFAULT_N_SIMS, help="Monte Carlo trials"),
    seed: int = typer.Option(None, help="RNG seed for reproducible output"),
    no_early_stretch: bool = typer.Option(False, "--no-early-stretch",
                                          help="Disable the early-season margin stretch"),
    db_path: Path = DbPath,
):
    """Simulate one game and print a matchup card."""
    as_of = _date(date_) or date.today()
    try:
        with get_db_connection(read_only=True, db_path=db_path) as con:
            hca_a, venue_desc = resolve_venue_hca(con, team1, team2, "neutral" if neutral else venue, hca)
            result = simulate_game(team1, team2, as_of, hca_a, sims, seed, con=con,
                                   early_stretch=not no_early_stretch)
    except (SimulationError, FileNotFoundError) as exc:
        _fail(str(exc))
    console.print(matchup_card(result, venue_desc))


# --------------------------------------------------------------------------
# backtesting
# --------------------------------------------------------------------------


@app.command("import-v1")
def import_v1_cmd(
    v1_db: Path = typer.Option(DEFAULT_V1_DB, help="v1 SQLite database"),
    start: str = typer.Option(None), end: str = typer.Option(None),
    db_path: Path = DbPath,
):
    """Import finished games and closing odds from the v1 database."""
    try:
        s = import_v1(v1_db, db_path, _date(start), _date(end))
    except (FileNotFoundError, RuntimeError) as exc:
        _fail(str(exc))
    console.print(f"v1 FINAL games {s.final_games}; skipped non-D-I {s.non_d1_skipped}; "
                  f"duplicates {s.duplicates}")
    console.print(f"[green]Imported {s.imported} games[/] ({s.neutral} neutral-site); closing spread "
                  f"{s.with_spread}, moneyline {s.with_moneyline}")


@app.command()
def predict(
    season: int = typer.Option(None, help="Season end year (2026 = 2025-26)"),
    start: str = typer.Option(None), end: str = typer.Option(None),
    model_version: str = typer.Option(None, help="Defaults to lean-v1.1[-hcaX][-nostretch]"),
    hca: float = typer.Option(None, help="Override HCA for non-neutral games"),
    sims: int = typer.Option(DEFAULT_N_SIMS), seed: int = typer.Option(0),
    max_staleness: int = typer.Option(vm.DEFAULT_MAX_STALENESS_DAYS,
                                      help="Skip games whose ratings snapshot is older (days)"),
    no_early_stretch: bool = typer.Option(False, "--no-early-stretch"),
    db_path: Path = DbPath,
):
    """Simulate historical games with point-in-time ratings into model_predictions."""
    try:
        lo, hi = vm.resolve_window(season, _date(start), _date(end))
    except ValueError as exc:
        _fail(str(exc))
    with get_db_connection(db_path=db_path) as con, console.status("Simulating games..."):
        run = vm.generate_predictions(con, lo, hi, model_version, sims, seed, hca, max_staleness,
                                      early_stretch=not no_early_stretch)
    console.print(f"[green]{run.model_version}[/]: wrote {run.written} predictions for {lo} → {hi}; "
                  f"skipped {len(run.skipped_no_ratings)} without ratings, "
                  f"{len(run.skipped_stale)} stale")


def _pct(x: float) -> str:
    return "n/a" if math.isnan(x) else f"{x:.1%}"


def _num(x: float, fmt: str = ".3f") -> str:
    return "n/a" if math.isnan(x) else format(x, fmt)


@app.command()
def audit(
    season: int = typer.Option(None, help="Season end year (2026 = 2025-26)"),
    start: str = typer.Option(None), end: str = typer.Option(None),
    model_version: str = typer.Option(vm.DEFAULT_MODEL_VERSION),
    db_path: Path = DbPath,
):
    """Score stored predictions vs results and de-vigged closing lines."""
    try:
        r = vm.run_backtest_audit(season, model_version, _date(start), _date(end), db_path=db_path)
    except (ValueError, FileNotFoundError) as exc:
        _fail(str(exc))

    console.print(f"[bold]Backtest {r.model_version}[/]  {r.start} → {r.end}  ({r.n_games} games)")
    summary = Table(show_header=True, header_style="bold")
    summary.add_column("Metric")
    summary.add_column("Model", justify="right")
    summary.add_column("Vegas", justify="right")
    summary.add_column("n", justify="right")
    m = r.market
    summary.add_row("SU accuracy (all games)", _pct(r.su_model), "", str(r.n_games))
    summary.add_row("Team score RMSE", _num(r.rmse_team, ".2f"), "", str(r.n_games))
    summary.add_row("Game total RMSE", _num(r.rmse_total, ".2f"), "", str(r.n_games))
    summary.add_row("ATS accuracy (breakeven 52.4%)", _pct(r.ats), "", str(r.ats_n))
    summary.add_row("SU accuracy (ML games)", _pct(m.su_model), _pct(m.su_vegas), str(m.n))
    summary.add_row("Brier score", _num(m.brier_model), _num(m.brier_vegas), str(m.n))
    summary.add_row("Brier Skill Score", _num(m.bss, "+.3f"), "ref", str(m.n))
    console.print(summary)

    tranches = Table(title="By closing-spread tranche", show_header=True, header_style="bold")
    for col in ("Tranche", "Games", "ATS", "ATS n", "Brier", "Vegas", "BSS"):
        tranches.add_column(col, justify="left" if col == "Tranche" else "right")
    for t in vm.TRANCHES:
        tm = r.tranches[t]
        tranches.add_row(vm.TRANCHE_LABELS[t], str(tm.n_spread), _pct(tm.ats), str(tm.ats_n),
                         _num(tm.market.brier_model), _num(tm.market.brier_vegas),
                         _num(tm.market.bss, "+.3f"))
    console.print(tranches)
    console.print(f"HCA check (actual − predicted margin): home {_num(r.margin_residual_home, '+.2f')} "
                  f"(n={r.n_home}), neutral {_num(r.margin_residual_neutral, '+.2f')} (n={r.n_neutral})")


if __name__ == "__main__":
    app()
