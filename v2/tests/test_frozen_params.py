"""lean-v1.3 is frozen for the 2026-27 season (docs/how_picks_work.md, "Season
protocol"). The live record is only an out-of-sample test if the model that
makes the picks stays fixed, so every fitted parameter is pinned here.

Changing any of these is a NEW model version: bump DEFAULT_MODEL_VERSION,
regenerate the backtest, and update this file and the papers in the same
commit. Locked picks are never re-made (each keeps the version that made it).
"""

from datetime import date

import pytest

from src import odds_api as oa
from src import simulate_game as sg
from src import validate_model as vm
from src.live_picks import LIVE_MODEL_VERSION

FROZEN = {
    "DEFAULT_HCA_EFF": 1.9,
    "EARLY_STRETCH_K": 0.48,
    "EARLY_STRETCH_DAYS": 70,
    "EARLY_STRETCH_MAX": 1.25,
    "SIGMA_TEMPO": 3.5,
    "SIGMA_EFF_BASE": 14.1,
    "SIGMA_EFF_3PA_SLOPE": 0.0,
    "EFF_CORRELATION": 0.30,
    "TOTAL_TEMPO_SHRINK": 0.85,
    "TOTAL_EFF_SHRINK": 0.67,
    "DEFAULT_N_SIMS": 10_000,
}


def test_model_version_is_the_frozen_one():
    assert vm.DEFAULT_MODEL_VERSION == "lean-v1.3"
    assert LIVE_MODEL_VERSION == "live-lean-v1.3"


@pytest.mark.parametrize("name, value", sorted(FROZEN.items()))
def test_fitted_parameter_is_frozen(name, value):
    assert getattr(sg, name) == value


def test_line_rules_are_frozen():
    assert oa.WORST_SPREAD_PRICE == -125.0
    assert len(oa.BOOKS) <= 10 and oa.BOOKS[:2] == ("draftkings", "fanduel")


def test_stretch_schedule_is_frozen():
    assert sg.early_season_stretch(date(2026, 11, 23)) == 1.25
    assert sg.early_season_stretch(date(2027, 1, 15)) == 1.0
