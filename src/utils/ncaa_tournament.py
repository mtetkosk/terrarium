"""NCAA Tournament date detection for slate context.

Used to tag runs during March Madness so agents can reason about
tournament-specific factors (neutral site, single-elimination, variance).
"""

from datetime import date
from typing import Optional

# Default window: First Four through Championship (approximate; adjust per year)
DEFAULT_START_MONTH = 3
DEFAULT_START_DAY = 17
DEFAULT_END_MONTH = 4
DEFAULT_END_DAY = 8

# Round labels by approximate date ranges (month, day) - inclusive start, exclusive end
# Order matters: first match wins. Format: (start_md, end_md, label).
# Using (month, day) for start; end is exclusive (next range starts there).
_ROUND_RANGES: list[tuple[tuple[int, int], tuple[int, int], str]] = [
    ((3, 18), (3, 20), "Round of 64"),
    ((3, 20), (3, 22), "Round of 32"),
    ((3, 25), (3, 29), "Sweet 16"),
    ((3, 29), (3, 31), "Elite Eight"),
    ((4, 4), (4, 6), "Final Four"),
    ((4, 6), (4, 9), "Championship"),
]


def _get_config() -> dict:
    """Get ncaa_tournament config section. Lazy import to avoid circular deps."""
    try:
        from src.utils.config import config
        return config.get("ncaa_tournament", {}) or {}
    except Exception:
        return {}


def _date_in_range(d: date, start_month: int, start_day: int, end_month: int, end_day: int) -> bool:
    """True if d is in [start_month/start_day, end_month/end_day) (inclusive start, exclusive end)."""
    start = date(d.year, start_month, start_day)
    end = date(d.year, end_month, end_day)
    return start <= d < end


def is_ncaa_tournament_date(d: date) -> bool:
    """Return True if d falls within the NCAA tournament window."""
    cfg = _get_config()
    if cfg.get("enabled") is False:
        return False
    start_md = cfg.get("start_month_day", "03-18")
    end_md = cfg.get("end_month_day", "04-08")
    try:
        start_month = int(start_md[:2])
        start_day = int(start_md[3:5])
        end_month = int(end_md[:2])
        end_day = int(end_md[3:5])
    except (ValueError, IndexError):
        start_month, start_day = DEFAULT_START_MONTH, DEFAULT_START_DAY
        end_month, end_day = DEFAULT_END_MONTH, DEFAULT_END_DAY
    return _date_in_range(d, start_month, start_day, end_month, end_day)


def get_ncaa_tournament_round(d: date) -> Optional[str]:
    """Return a short round label for the date, or None if outside tournament window."""
    if not is_ncaa_tournament_date(d):
        return None
    for (sm, sd), (em, ed), label in _ROUND_RANGES:
        if _date_in_range(d, sm, sd, em, ed):
            return label
    return "NCAA Tournament"


def get_slate_context(target_date: date) -> Optional[str]:
    """Return slate context string for prompts (e.g. 'NCAA Tournament — Round of 64'), or None."""
    if not is_ncaa_tournament_date(target_date):
        return None
    round_name = get_ncaa_tournament_round(target_date)
    if round_name:
        return f"NCAA Tournament — {round_name}"
    return "NCAA Tournament"
