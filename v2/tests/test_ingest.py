"""Milestone 2 gate: KenPom parsing, caching, leakage guard, and upsert.

All tests run offline against HTML fixtures and a fake HTTP session.
"""

from datetime import date
from pathlib import Path

import pytest

from src import db
from src import ingest_kenpom as ik

FIXTURES = Path(__file__).parent / "fixtures"
RATINGS_HTML = (FIXTURES / "kenpom_ratings.html").read_text()
TEAMSTATS_HTML = (FIXTURES / "kenpom_teamstats.html").read_text()
LOGIN_WALL_HTML = "<html><body><form action='handlers/login_handler.php'></form></body></html>"

TODAY = date(2026, 11, 20)


ARCHIVE_HTML = (FIXTURES / "kenpom_archive.html").read_text()
ARCHIVE_EMPTY_HTML = (FIXTURES / "kenpom_archive_empty.html").read_text()


class FakeResponse:
    def __init__(self, text: str = "", status_code: int = 200, headers: dict | None = None):
        self.text = text
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise ik.requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    """Pages map URL -> HTML string, or a list of FakeResponse/Exception
    consumed one per request (the last entry repeats)."""

    def __init__(self, pages: dict | None = None):
        self.pages = pages if pages is not None else {
            ik.RATINGS_URL: RATINGS_HTML,
            ik.TEAMSTATS_URL: TEAMSTATS_HTML,
        }
        self.calls: list[tuple[str, str, dict | None]] = []

    def get(self, url, timeout=None):
        self.calls.append(("GET", url, None))
        page = self.pages[url]
        if isinstance(page, str):
            return FakeResponse(page)
        item = page.pop(0) if len(page) > 1 else page[0]
        if isinstance(item, Exception):
            raise item
        return item

    def post(self, url, data=None, timeout=None):
        self.calls.append(("POST", url, data))
        return FakeResponse("ok")


@pytest.fixture
def creds(monkeypatch):
    for var in ("KENPOM_USER", "KENPOM_PASS", "KENPOM_EMAIL", "KENPOM_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("KENPOM_USER", "me@example.com")
    monkeypatch.setenv("KENPOM_PASS", "hunter2")


@pytest.fixture
def cache_dir(tmp_path):
    return tmp_path / "raw_cache"


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "cbb_engine.duckdb"


def seed_cache(cache_dir: Path, snapshot_date: date) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    ik.ratings_cache_path(cache_dir, snapshot_date).write_text(RATINGS_HTML)
    ik.teamstats_cache_path(cache_dir, snapshot_date).write_text(TEAMSTATS_HTML)


# --- parsing ---------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Duke", "Duke"),
        ("Duke 1", "Duke"),
        ("St. John's 3*", "St. John's"),
        ("Michigan St.*", "Michigan St."),
        ("  Texas A&M 16 ", "Texas A&M"),
    ],
)
def test_clean_team_name(raw, expected):
    assert ik.clean_team_name(raw) == expected


def test_parse_ratings_maps_first_occurrence_columns():
    ratings = ik.parse_ratings(RATINGS_HTML)
    assert set(ratings) == {"Duke", "Michigan St.", "St. John's", "Texas A&M"}
    # Must pick main ratings, not the SOS NetRtg/ORtg/DRtg or rank columns.
    assert ratings["Duke"] == {"adj_em": 29.69, "adj_o": 123.2, "adj_d": 93.5, "adj_t": 71.1}
    assert ratings["Texas A&M"] == {"adj_em": 16.68, "adj_o": 112.6, "adj_d": 95.9, "adj_t": 64.8}


def test_parse_ratings_skips_repeated_header_rows():
    assert len(ik.parse_ratings(RATINGS_HTML)) == 4


def test_parse_ratings_detects_column_drift():
    broken = RATINGS_HTML.replace("<td>+29.69</td>", "<td>+12.00</td>")
    with pytest.raises(ik.KenPomParseError, match="AdjEM"):
        ik.parse_ratings(broken)


def test_parse_ratings_requires_table():
    with pytest.raises(ik.KenPomParseError):
        ik.parse_ratings(LOGIN_WALL_HTML)


def test_parse_three_pa_rate_returns_fractions():
    rates = ik.parse_three_pa_rate(TEAMSTATS_HTML)
    assert rates["Duke"] == pytest.approx(0.382)
    assert rates["Michigan St."] == pytest.approx(0.315)
    assert rates["St. John's"] == pytest.approx(0.350)
    assert all(0.0 <= r <= 1.0 for r in rates.values())


def test_build_snapshot_merges_and_nulls_missing_rate():
    rows = {r.team: r for r in ik.build_snapshot(TODAY, RATINGS_HTML, TEAMSTATS_HTML)}
    assert set(rows) == {"Duke", "Michigan St.", "St. John's", "Texas A&M"}  # Gonzaga not rated
    assert rows["Duke"].three_pa_rate == pytest.approx(0.382)
    assert rows["Duke"].snapshot_date == TODAY
    assert rows["Texas A&M"].three_pa_rate is None


# --- credentials & login ----------------------------------------------------


def test_credentials_missing_raises(monkeypatch):
    for var in ("KENPOM_USER", "KENPOM_PASS", "KENPOM_EMAIL", "KENPOM_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ik.KenPomAuthError):
        ik.get_credentials()


def test_credentials_fall_back_to_v1_names(monkeypatch):
    for var in ("KENPOM_USER", "KENPOM_PASS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("KENPOM_EMAIL", "a@b.com")
    monkeypatch.setenv("KENPOM_PASSWORD", "pw")
    assert ik.get_credentials() == ("a@b.com", "pw")


def test_login_posts_to_handler(creds):
    session = FakeSession()
    ik.login(session)
    method, url, data = session.calls[0]
    assert (method, url) == ("POST", ik.LOGIN_URL)
    assert data["email"] == "me@example.com"
    assert data["password"] == "hunter2"


# --- cache & leakage guard ---------------------------------------------------


def test_fresh_fetch_logs_in_and_writes_cache(creds, cache_dir):
    session = FakeSession()
    ratings, teamstats = ik.load_pages(TODAY, cache_dir, session, today=TODAY)

    assert ratings == RATINGS_HTML and teamstats == TEAMSTATS_HTML
    assert [(m, u) for m, u, _ in session.calls] == [
        ("GET", ik.RATINGS_URL),
        ("POST", ik.LOGIN_URL),
        ("GET", ik.TEAMSTATS_URL),
    ]
    assert ik.ratings_cache_path(cache_dir, TODAY).name == "kenpom_2026-11-20.html"
    assert ik.ratings_cache_path(cache_dir, TODAY).read_text() == RATINGS_HTML
    assert ik.teamstats_cache_path(cache_dir, TODAY).read_text() == TEAMSTATS_HTML


def test_cache_hit_skips_network(cache_dir):
    seed_cache(cache_dir, TODAY)
    session = FakeSession(pages={})  # any request would KeyError
    ratings, teamstats = ik.load_pages(TODAY, cache_dir, session, today=TODAY)
    assert session.calls == []
    assert ratings == RATINGS_HTML and teamstats == TEAMSTATS_HTML


def test_past_date_without_cache_refuses_to_fetch(creds, cache_dir):
    session = FakeSession()
    with pytest.raises(ik.LeakageError):
        ik.load_pages(date(2026, 11, 11), cache_dir, session, today=TODAY)
    assert session.calls == []


def test_past_date_with_cache_is_allowed(cache_dir):
    past = date(2026, 11, 11)
    seed_cache(cache_dir, past)
    ik.load_pages(past, cache_dir, FakeSession(pages={}), today=TODAY)


def test_failed_login_raises_and_does_not_poison_cache(creds, cache_dir):
    session = FakeSession(pages={ik.RATINGS_URL: RATINGS_HTML, ik.TEAMSTATS_URL: LOGIN_WALL_HTML})
    with pytest.raises(ik.KenPomAuthError):
        ik.load_pages(TODAY, cache_dir, session, today=TODAY)
    assert not ik.ratings_cache_path(cache_dir, TODAY).exists()
    assert not ik.teamstats_cache_path(cache_dir, TODAY).exists()


# --- persistence --------------------------------------------------------------


def fetch_snapshot(db_path, snapshot_date):
    with db.get_db_connection(read_only=True, db_path=db_path) as con:
        return con.execute(
            "SELECT team, adj_em, adj_o, adj_d, adj_t, three_pa_rate "
            "FROM kenpom_snapshots WHERE snapshot_date = ? ORDER BY team",
            [snapshot_date],
        ).fetchall()


def test_ingest_end_to_end_from_cache(cache_dir, db_path):
    seed_cache(cache_dir, TODAY)
    rows = ik.ingest(TODAY, db_path, cache_dir, FakeSession(pages={}), today=TODAY)
    assert len(rows) == 4

    stored = {r[0]: r for r in fetch_snapshot(db_path, TODAY)}
    assert stored["Duke"] == ("Duke", 29.69, 123.2, 93.5, 71.1, pytest.approx(0.382))
    assert stored["Texas A&M"][5] is None


def test_upsert_replaces_same_date_and_keeps_other_dates(db_path):
    d1, d2 = date(2026, 11, 19), TODAY
    first = ik.build_snapshot(d1, RATINGS_HTML, TEAMSTATS_HTML)
    ik.upsert_snapshot(first, db_path)
    ik.upsert_snapshot(ik.build_snapshot(d2, RATINGS_HTML, TEAMSTATS_HTML), db_path)

    revised = [
        ik.SnapshotRow(r.snapshot_date, r.team, r.adj_em + 1, r.adj_o + 1, r.adj_d,
                       r.adj_t, r.three_pa_rate)
        for r in first
    ]
    ik.upsert_snapshot(revised, db_path)

    assert len(fetch_snapshot(db_path, d1)) == 4
    assert len(fetch_snapshot(db_path, d2)) == 4
    duke_d1 = next(r for r in fetch_snapshot(db_path, d1) if r[0] == "Duke")
    duke_d2 = next(r for r in fetch_snapshot(db_path, d2) if r[0] == "Duke")
    assert duke_d1[1] == pytest.approx(30.69)
    assert duke_d2[1] == pytest.approx(29.69)


def test_cli_rejects_uncached_past_date(cache_dir, db_path, monkeypatch, creds):
    monkeypatch.setattr(ik, "load_env_files", lambda paths: None)
    code = ik.main(["--date", "2020-01-01", "--cache-dir", str(cache_dir),
                    "--db-path", str(db_path)])
    assert code == 1
    assert not db_path.exists()


def test_load_env_files_does_not_override(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comment\nKP_TEST_A=from_file\nexport KP_TEST_B='quoted'\n")
    monkeypatch.setenv("KP_TEST_A", "from_env")
    monkeypatch.delenv("KP_TEST_B", raising=False)
    ik.load_env_files([env, tmp_path / "missing.env"])
    assert ik.os.environ["KP_TEST_A"] == "from_env"
    assert ik.os.environ["KP_TEST_B"] == "quoted"


# --- HTTP metering ------------------------------------------------------------

URL = "https://kenpom.com/x"


def test_get_retries_429_honoring_retry_after():
    session = FakeSession({URL: [FakeResponse(status_code=429, headers={"Retry-After": "7"}),
                                 FakeResponse("ok")]})
    waits = []
    assert ik._get(session, URL, sleep=waits.append) == "ok"
    assert waits == [7.0]


def test_get_exponential_backoff_then_gives_up():
    session = FakeSession({URL: [FakeResponse(status_code=503)]})
    waits = []
    with pytest.raises(ik.requests.HTTPError):
        ik._get(session, URL, sleep=waits.append)
    assert waits == [ik.RETRY_BACKOFF * 2**i for i in range(ik.MAX_RETRIES)]
    assert len(session.calls) == ik.MAX_RETRIES + 1


def test_get_retries_connection_errors():
    session = FakeSession({URL: [ik.requests.ConnectionError("reset"), FakeResponse("ok")]})
    waits = []
    assert ik._get(session, URL, sleep=waits.append) == "ok"
    assert waits == [ik.RETRY_BACKOFF]


def test_get_does_not_retry_plain_500():
    session = FakeSession({URL: [FakeResponse(status_code=500)]})
    waits = []
    with pytest.raises(ik.requests.HTTPError):
        ik._get(session, URL, sleep=waits.append)
    assert waits == [] and len(session.calls) == 1


# --- archive ------------------------------------------------------------------

D1, D2, D3 = date(2026, 11, 16), date(2026, 11, 17), date(2026, 11, 18)


def archive_url(d: date) -> str:
    return f"{ik.ARCHIVE_URL}?d={d.isoformat()}"


def test_parse_archive_uses_as_of_block_not_current():
    ratings = ik.parse_ratings(ARCHIVE_HTML)
    assert ratings["Florida"] == {"adj_em": 33.20, "adj_o": 126.1, "adj_d": 92.9, "adj_t": 70.0}
    # Seed/finish spans in the team cell don't leak into the name.
    assert ratings["Duke"]["adj_em"] == 29.62


def test_build_archive_snapshot_has_null_three_pa():
    rows = ik.build_archive_snapshot(D3, ARCHIVE_HTML)
    assert {r.team for r in rows} == {"Florida", "Duke"}
    assert all(r.three_pa_rate is None and r.snapshot_date == D3 for r in rows)


def test_load_archive_page_fetches_and_caches(creds, cache_dir):
    session = FakeSession({archive_url(D3): ARCHIVE_HTML})
    assert ik.load_archive_page(D3, cache_dir, session, today=TODAY) == ARCHIVE_HTML
    assert [(m, u) for m, u, _ in session.calls] == [("POST", ik.LOGIN_URL), ("GET", archive_url(D3))]
    assert ik.archive_cache_path(cache_dir, D3).name == "kenpom_archive_2026-11-18.html"

    again = FakeSession(pages={})
    assert ik.load_archive_page(D3, cache_dir, again, today=TODAY) == ARCHIVE_HTML
    assert again.calls == []


def test_load_archive_page_off_day_returns_none_and_skips_cache(creds, cache_dir):
    session = FakeSession({archive_url(D2): ARCHIVE_EMPTY_HTML})
    assert ik.load_archive_page(D2, cache_dir, session, today=TODAY) is None
    assert not ik.archive_cache_path(cache_dir, D2).exists()


def test_load_archive_page_rejects_today_and_future(cache_dir):
    for d in (TODAY, date(2026, 12, 1)):
        with pytest.raises(ik.KenPomError):
            ik.load_archive_page(d, cache_dir, FakeSession(pages={}), today=TODAY)


def test_load_archive_page_login_wall_is_auth_error(creds, cache_dir):
    session = FakeSession({archive_url(D3): LOGIN_WALL_HTML})
    with pytest.raises(ik.KenPomAuthError):
        ik.load_archive_page(D3, cache_dir, session, today=TODAY)


def test_backfill_ingests_skips_off_days_and_meters(creds, cache_dir, db_path):
    session = FakeSession({
        archive_url(D1): ARCHIVE_HTML,
        archive_url(D2): ARCHIVE_EMPTY_HTML,
        archive_url(D3): ARCHIVE_HTML,
    })
    waits = []
    res = ik.backfill(D1, D3, db_path, cache_dir, session, today=TODAY, delay=3.0,
                      sleep=waits.append)

    assert res.ingested == {D1: 2, D3: 2}
    assert res.no_data == [D2]
    assert res.failed == {}
    assert sum(1 for m, *_ in session.calls if m == "POST") == 1  # logged in once
    # One jittered pause before each fetch after the first.
    assert len(waits) == 2
    assert all(3.0 <= w <= 3.0 * (1 + ik.BACKFILL_JITTER) for w in waits)

    rows = fetch_snapshot(db_path, D3)
    assert ("Florida", 33.20, 126.1, 92.9, 70.0, None) in rows

    # Rerun: cached dates aren't refetched and don't wait. The off-day is retried.
    rerun_session = FakeSession({archive_url(D2): ARCHIVE_EMPTY_HTML})
    rerun_waits = []
    ik.backfill(D1, D3, db_path, cache_dir, rerun_session, today=TODAY, sleep=rerun_waits.append)
    assert [u for m, u, _ in rerun_session.calls if m == "GET"] == [archive_url(D2)]
    assert rerun_waits == []


def test_backfill_aborts_after_consecutive_failures(creds, cache_dir, db_path):
    start = date(2026, 11, 10)
    days = [start + ik.timedelta(days=i) for i in range(5)]
    session = FakeSession({archive_url(d): [FakeResponse(status_code=500)] for d in days})
    with pytest.raises(ik.KenPomError, match="consecutive failures"):
        ik.backfill(days[0], days[-1], db_path, cache_dir, session, today=TODAY,
                    sleep=lambda s: None)
    gets = [u for m, u, _ in session.calls if m == "GET"]
    assert len(gets) == ik.MAX_CONSECUTIVE_FAILURES


def test_backfill_validates_range(cache_dir, db_path):
    with pytest.raises(ik.KenPomError):
        ik.backfill(D3, D1, db_path, cache_dir, FakeSession(pages={}), today=TODAY)
    with pytest.raises(ik.KenPomError):
        ik.backfill(D1, TODAY, db_path, cache_dir, FakeSession(pages={}), today=TODAY)


def test_archive_upsert_does_not_erase_known_three_pa(db_path):
    live = ik.build_snapshot(D3, RATINGS_HTML, TEAMSTATS_HTML)
    ik.upsert_snapshot(live, db_path)
    archive_row = ik.SnapshotRow(D3, "Duke", 30.0, 122.0, 92.0, 70.0, None)
    ik.upsert_snapshot([archive_row], db_path)

    duke = next(r for r in fetch_snapshot(db_path, D3) if r[0] == "Duke")
    assert duke[1] == 30.0                       # ratings updated
    assert duke[5] == pytest.approx(0.382)       # 3PA preserved
