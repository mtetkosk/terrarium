"""Milestone 6 gate: four designed Google Sheet tabs (Google APIs mocked)."""

from datetime import date, datetime, timedelta

import pytest
from typer.testing import CliRunner

import cli
from src import db
from src import live_picks as lp
from src import publish_gsheet as pg

TODAY = date(2026, 11, 20)
YESTERDAY = TODAY - timedelta(days=1)


# --- a small fake of the Sheets API that enforces grid bounds ----------------------------------


class _Call:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class FakeSheets:
    """Tabs with a grid size and a cell map; writes outside the grid, or
    freezing every row, raise like the real API does."""

    def __init__(self, titles=("Sheet1",)):
        self.tabs = [self._new(i, t) for i, t in enumerate(titles)]
        self.batches = []

    @staticmethod
    def _new(sheet_id, title, rows=1000, cols=26):
        return {"sheetId": sheet_id, "title": title, "rows": rows, "cols": cols, "frozen": 0,
                "frozen_cols": 0, "cells": {}, "widths": {}, "heights": {}, "merges": [], "filter": None}

    def spreadsheets(self):
        return self

    def get(self, spreadsheetId, fields=None):
        return _Call(lambda: {"sheets": [
            {"properties": {"sheetId": t["sheetId"], "title": t["title"], "index": i},
             **({"basicFilter": {"range": t["filter"]}} if t["filter"] else {})}
            for i, t in enumerate(self.tabs)]})

    def batchUpdate(self, spreadsheetId, body):
        return _Call(lambda: self._apply(body["requests"]))

    def _tab(self, sheet_id):
        return next(t for t in self.tabs if t["sheetId"] == sheet_id)

    def _apply(self, requests):
        self.batches.append(requests)
        replies = []
        for req in requests:
            (kind, args), = req.items()
            reply = {}
            if kind == "addSheet":
                props = args["properties"]
                tab = self._new(100 + len(self.tabs), props["title"])
                self.tabs.insert(props.get("index", len(self.tabs)), tab)
                reply = {"addSheet": {"properties": {"sheetId": tab["sheetId"], "title": tab["title"]}}}
            elif kind == "updateSheetProperties":
                props, tab = args["properties"], self._tab(args["properties"]["sheetId"])
                if "title" in props:
                    tab["title"] = props["title"]
                if "index" in props:
                    self.tabs.remove(tab)
                    self.tabs.insert(props["index"], tab)
                if "tabColorStyle" in props:
                    tab["color"] = props["tabColorStyle"]["rgbColor"]
                grid = props.get("gridProperties")
                if grid:
                    tab["rows"], tab["cols"] = grid["rowCount"], grid["columnCount"]
                    tab["frozen"] = grid.get("frozenRowCount", 0)
                    tab["frozen_cols"] = grid.get("frozenColumnCount", 0)
                    assert tab["frozen"] < tab["rows"], "can't freeze all rows"
                    for r0, r1, c0, c1 in tab["merges"]:
                        assert not (c0 < tab["frozen_cols"] < c1), "merge crosses frozen columns"
                    tab["cells"] = {k: v for k, v in tab["cells"].items()
                                    if k[0] < tab["rows"] and k[1] < tab["cols"]}
            elif kind == "updateCells":
                rng, tab = args["range"], self._tab(args["range"]["sheetId"])
                assert rng["endRowIndex"] <= tab["rows"] and rng["endColumnIndex"] <= tab["cols"], \
                    "updateCells outside grid"
                assert args["fields"] == "*"
                for r in range(rng["startRowIndex"], rng["endRowIndex"]):
                    for c in range(rng["startColumnIndex"], rng["endColumnIndex"]):
                        tab["cells"].pop((r, c), None)
                for r, row in enumerate(args["rows"], start=rng["startRowIndex"]):
                    assert len(row["values"]) <= tab["cols"], "row wider than grid"
                    for c, cell in enumerate(row["values"], start=rng["startColumnIndex"]):
                        if cell:
                            tab["cells"][(r, c)] = cell
            elif kind == "updateDimensionProperties":
                rng, tab = args["range"], self._tab(args["range"]["sheetId"])
                if rng["dimension"] == "COLUMNS":
                    assert rng["endIndex"] <= tab["cols"], "column outside grid"
                    tab["widths"][rng["startIndex"]] = args["properties"]["pixelSize"]
                else:
                    assert rng["endIndex"] <= tab["rows"], "row outside grid"
                    for r in range(rng["startIndex"], rng["endIndex"]):
                        tab["heights"][r] = args["properties"]["pixelSize"]
            elif kind == "setBasicFilter":
                rng = args["filter"]["range"]
                tab = self._tab(rng["sheetId"])
                assert rng["endRowIndex"] <= tab["rows"] and rng["endColumnIndex"] <= tab["cols"], \
                    "filter outside grid"
                tab["filter"] = rng
            elif kind == "clearBasicFilter":
                tab = self._tab(args["sheetId"])
                assert tab["filter"] is not None, "no filter to clear"
                tab["filter"] = None
            elif kind == "unmergeCells":
                rng = args["range"]
                assert set(rng) == {"sheetId"}, "expected a whole-sheet unmerge"
                self._tab(rng["sheetId"])["merges"] = []
            elif kind == "mergeCells":
                rng, tab = args["range"], self._tab(args["range"]["sheetId"])
                box = (rng["startRowIndex"], rng["endRowIndex"], rng["startColumnIndex"], rng["endColumnIndex"])
                assert box[1] <= tab["rows"] and box[3] <= tab["cols"], "merge outside grid"
                assert not (box[2] < tab["frozen_cols"] < box[3]), "merge crosses frozen columns"
                assert all(box[1] <= m[0] or m[1] <= box[0] or box[3] <= m[2] or m[3] <= box[2]
                           for m in tab["merges"]), "overlapping merge"
                tab["merges"].append(box)
            else:
                raise AssertionError(f"unexpected request {kind}")
            replies.append(reply)
        return {"replies": replies}

    # helpers for assertions
    def by_title(self, title):
        return next(t for t in self.tabs if t["title"] == title)

    def display(self, title):
        """Grid of raw values (numbers as floats, text as str, empty as '')."""
        tab = self.by_title(title)
        out = [["" for _ in range(tab["cols"])] for _ in range(tab["rows"])]
        for (r, c), cell in tab["cells"].items():
            v = cell.get("userEnteredValue", {})
            out[r][c] = v.get("numberValue", v.get("stringValue", v.get("formulaValue", "")))
        return out


class FakeDrive:
    def __init__(self):
        self.permissions_created = []

    def permissions(self):
        return self

    def create(self, fileId, body, fields=None):
        return _Call(lambda: self.permissions_created.append((fileId, body)))


# --- synthetic warehouse --------------------------------------------------------------------

TEAMS = {"Duke": (120.0, 94.0, 70.0, 0.38), "Michigan St.": (116.0, 95.0, 67.0, 0.33),
         "Kansas": (118.0, 93.0, 69.0, 0.36), "Houston": (119.0, 90.0, 64.0, 0.30)}


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "cbb.duckdb"
    with db.get_db_connection(db_path=path) as con:
        for day in (TODAY - timedelta(days=7), YESTERDAY, TODAY):
            con.executemany("INSERT INTO kenpom_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
                            [(day, t, o - d, o, d, tempo, r) for t, (o, d, tempo, r) in TEAMS.items()])
        games = [  # game_id, date, home, away, commence (UTC)
            ("y1", YESTERDAY, "Duke", "Michigan St.", datetime(2026, 11, 20, 0, 0)),
            ("t1", TODAY, "Kansas", "Houston", datetime(2026, 11, 21, 1, 0)),
        ]
        for gid, d, a, b, tip in games:
            con.execute("INSERT INTO scheduled_games VALUES (?, 'the-odds-api', ?, ?, ?, ?)", [gid, gid, tip, a, b])
            con.execute("INSERT INTO historical_games VALUES (?, ?, ?, ?, NULL, NULL, NULL, 1.9)", [gid, d, a, b])
        con.executemany("INSERT INTO odds_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)", [
            ("y1", datetime(2026, 11, 19, 14), "draftkings", -3.0, -150, 130, 148.5),   # pick-time line
            ("y1", datetime(2026, 11, 19, 23), "draftkings", -4.5, -190, 160, 149.5),   # closing line
            ("t1", datetime(2026, 11, 20, 14), "draftkings", 1.5, 105, -125, 139.5),
        ])
        lp.make_picks(con, YESTERDAY, now=datetime(2026, 11, 19, 15), n_sims=2000)
        lp.make_picks(con, TODAY, now=datetime(2026, 11, 20, 15), n_sims=2000)
        con.execute("UPDATE historical_games SET actual_score_a = 80, actual_score_b = 72 WHERE game_id = 'y1'")
    return path


STAMP = datetime(2026, 11, 20, 9, 0, tzinfo=pg.EASTERN)


@pytest.fixture
def tabs(db_path):
    with db.get_db_connection(db_path=db_path) as con:
        return pg.build_tabs(con, TODAY, generated_at=STAMP)


def plain_tab(title):
    """A minimal tab with no merges, frozen panes or filter."""
    return pg.GridTab(title, pg.SheetLayout([[pg._text("x", {})]], 1, frozen_rows=0, col_widths=[100]))


# --- tabs --------------------------------------------------------------------------------------


def test_build_tabs_desktop_then_phone_tabs(tabs):
    assert [t.title for t in tabs] == [*pg.TAB_TITLES, *pg.MOBILE_TITLES]
    assert [t.layout.tab_color for t in tabs] == [None] * 4 + [pg.MOBILE_TAB_COLOR] * 3
    assert all(isinstance(t, pg.GridTab) for t in tabs)
    assert all(pg.cell_text(t.layout.rows[0][0]) for t in tabs)            # every tab has a title bar


def test_render_markdown(tabs):
    md = pg.render_markdown(tabs)
    for title in pg.TAB_TITLES:
        assert f"# {title}" in md
    assert "| Rank | Team | NDI |" in md


# --- publishing ----------------------------------------------------------------------------------


def test_publish_creates_tabs_and_writes_cells(tabs):
    sheets, drive = FakeSheets(), FakeDrive()
    url = pg.SheetsPublisher(sheets, drive, "SHEET").publish(tabs)
    assert url == "https://docs.google.com/spreadsheets/d/SHEET/edit"
    assert [t["title"] for t in sheets.tabs] == [*pg.TAB_TITLES, *pg.MOBILE_TITLES]   # "Sheet1" renamed, rest added
    for tab in tabs:
        grid, lay = sheets.display(tab.title), tab.layout
        assert len(grid) == len(lay.rows) + 1                           # exact fit + spare row
        for r, row in enumerate(lay.rows):
            for c, cell in enumerate(row):
                v = cell.get("userEnteredValue", {})
                assert grid[r][c] == v.get("numberValue", v.get("stringValue", v.get("formulaValue", ""))), \
                    (tab.title, r, c)
    assert drive.permissions_created == []                               # private by default


def test_republish_shrinks_and_clears(tabs):
    sheets = FakeSheets(titles=pg.TAB_TITLES)
    publisher = pg.SheetsPublisher(sheets, FakeDrive(), "SHEET")
    stale = sheets.by_title("Daily Picks")
    stale["cells"][(500, 3)] = {"userEnteredValue": {"stringValue": "old"}}
    stale["cells"][(1, 1)] = {"userEnteredValue": {"stringValue": "old"}}
    publisher.publish(tabs)
    publisher.publish(tabs)
    assert len(sheets.tabs) == 7
    flat = [v for row in sheets.display("Daily Picks") for v in row]
    assert "old" not in flat
    assert stale["rows"] == len(tabs[0].layout.rows) + 1


def test_existing_tabs_are_reused_and_extra_tabs_kept(tabs):
    sheets = FakeSheets(titles=(*pg.TAB_TITLES, "My notes", *pg.MOBILE_TITLES))
    pg.SheetsPublisher(sheets, FakeDrive(), "SHEET").publish(tabs)
    assert [t["title"] for t in sheets.tabs] == [*[*pg.TAB_TITLES, *pg.MOBILE_TITLES], "My notes"]      # ours first, in order
    assert sheets.by_title("My notes")["cells"] == {}
    assert not any("addSheet" in r for b in sheets.batches for r in b)


def test_tabs_are_reordered_only_when_needed(tabs):
    sheets = FakeSheets(titles=(pg.MOBILE_TITLES[0], *pg.TAB_TITLES))
    publisher = pg.SheetsPublisher(sheets, FakeDrive(), "SHEET")
    publisher.publish(tabs)
    assert [t["title"] for t in sheets.tabs] == [*pg.TAB_TITLES, *pg.MOBILE_TITLES]
    assert sheets.by_title(pg.MOBILE_TITLES[0])["color"] == pg._hex(pg.MOBILE_TAB_COLOR)
    before = len(sheets.batches)
    publisher.publish(tabs)
    assert len(sheets.batches) - before == len(tabs)                       # no reorder batch second time


def test_one_batch_per_tab(tabs):
    sheets = FakeSheets(titles=[*pg.TAB_TITLES, *pg.MOBILE_TITLES])
    pg.SheetsPublisher(sheets, FakeDrive(), "SHEET").publish(tabs)
    assert len(sheets.batches) == 7
    assert all([next(iter(r)) for r in b[:2]] == ["unmergeCells", "updateSheetProperties"]
               for b in sheets.batches)                                   # unmerge, then resize


def test_fake_rejects_writes_outside_grid():
    sheets = FakeSheets()
    with pytest.raises(AssertionError, match="outside grid"):
        sheets._apply([{"updateCells": {"range": {"sheetId": 0, "startRowIndex": 0, "endRowIndex": 2000,
                                                  "startColumnIndex": 0, "endColumnIndex": 1},
                                        "rows": [], "fields": "*"}}])


def test_make_public_sets_anyone_reader(tabs):
    drive = FakeDrive()
    pg.SheetsPublisher(FakeSheets(), drive, "SHEET").publish(tabs, make_public=True)
    assert drive.permissions_created == [("SHEET", {"type": "anyone", "role": "reader"})]


# --- designed Historical Picks grid ----------------------------------------------------------------


@pytest.fixture
def ledger_grid(tabs):
    return tabs[1]


def test_ledger_games_are_raw_values(db_path):
    with db.get_db_connection(db_path=db_path) as con:
        g, = lp.ledger_games(con)
    assert (g.away, g.home, g.away_pts, g.home_pts, g.final_margin) == ("Michigan St.", "Duke", 72, 80, 8)
    assert (g.pick, g.pick_line, g.closing_line, g.su, g.ats) == ("Duke", -3.0, -4.5, "W", "Cover")
    assert g.model_version == "lean-v1.1"
    assert g.clv == pytest.approx(1.5)


def test_ledger_grid_record_strip(ledger_grid):
    lay = ledger_grid.layout
    labels = [pg.cell_text(c) for c in lay.rows[2]]
    values = [pg.cell_text(c) for c in lay.rows[3]]
    strip = dict(zip(labels, values))
    assert strip["GRADED"] == "1" and strip["ATS"] == "100.0%" and strip["COVERS"] == "1"
    assert strip["UNITS @ −110"] == "+1.0" and strip["AVG CLV"] == "+1.50" and strip["BEAT CLOSE"] == "100%"
    # ATS above break-even is tinted green
    ats_cell = lay.rows[3][labels.index("ATS")]
    assert ats_cell["userEnteredFormat"]["backgroundColor"] == pg.GRADE_TINT["good"]


def test_ledger_grid_rows_one_value_per_cell(ledger_grid):
    lay = ledger_grid.layout
    header = [pg.cell_text(c) for c in lay.rows[6]]
    assert header == ["Date", "Away", "Home", "Site", "Venue", "City", "Model", "Model Margin", "Pick", "Pick Line",
                      "Close Line", "CLV", "Away Pts", "Home Pts", "Final Margin", "SU", "ATS"]
    row = [pg.cell_text(c) for c in lay.rows[7]]
    assert row == ["2026-11-19", "Michigan St.", "Duke", "—", "—", "—", "lean-v1.1", row[7], "Duke", "-3.0", "-4.5",
                   "+1.5", "72", "80", "+8", "W", "Cover"]
    assert lay.rows[7][16]["userEnteredFormat"]["backgroundColor"] == pg._hex(pg.RESULT_STYLE["Cover"][0])
    assert (lay.frozen_rows, lay.frozen_cols, lay.filter_row) == (7, 3, 6)
    assert all(c.get("note") for c in lay.rows[6])


def test_ledger_grid_empty(db_path):
    with db.get_db_connection(db_path=db_path) as con:
        con.execute("UPDATE historical_games SET actual_score_a = NULL, actual_score_b = NULL")
        grid = pg.build_ledger_grid(con, TODAY)
    assert pg.cell_text(grid.layout.rows[-1][0]).startswith("No graded picks yet")
    assert grid.layout.filter_row is None


def test_units_and_running_total():
    G = lp.LedgerGame
    base = dict(game_id="g", game_date=TODAY, away="A", home="H", neutral=False, pred_margin=3.0, pick="H",
                pick_line=-2.0, closing_line=-2.5, away_pts=60, home_pts=70, su="W", clv=0.5)
    games = [G(**{**base, "ats": a}) for a in ("Loss", "Push", "Cover", "Cover")]   # newest first
    rec = pg._record(games)
    assert rec["running"] == pytest.approx([1.0, 2.0, 2.0, 0.9])                   # oldest first
    assert rec["units"] == pytest.approx(0.9) and rec["ats_pct"] == pytest.approx(2 / 3)


# --- configuration & CLI ------------------------------------------------------------------------


@pytest.fixture
def clean_env(monkeypatch, tmp_path_factory):
    for var in ("GOOGLE_SHEET_ID", "GOOGLE_SERVICE_ACCOUNT_FILE", "GOOGLE_SHEETS_CREDENTIALS_PATH"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(cli.ik, "load_env_files", lambda paths: None)
    # The synthetic warehouse ranks 4 teams and has no odds cache; relax only those two checks.
    monkeypatch.setattr(cli.sanity, "MIN_RANKED_TEAMS", 1)
    monkeypatch.setattr(cli.sanity, "CACHE_DIR", tmp_path_factory.mktemp("cache"))
    return monkeypatch


def test_config_requires_sheet_and_key(clean_env, tmp_path):
    with pytest.raises(pg.PublishConfigError, match="GOOGLE_SHEET_ID"):
        pg.config_from_env()
    clean_env.setenv("GOOGLE_SHEET_ID", "SHEET")
    clean_env.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", str(tmp_path / "missing.json"))
    with pytest.raises(pg.PublishConfigError, match="not found"):
        pg.config_from_env()
    clean_env.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", "/Users/someone/Desktop/key.json")
    with pytest.raises(pg.PublishConfigError, match="Desktop"):
        pg.config_from_env()
    key = tmp_path / "key.json"
    key.write_text("{}")
    clean_env.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", str(key))
    assert pg.config_from_env() == ("SHEET", key)


def test_cli_publish_dry_run_and_unconfigured(db_path, tmp_path, clean_env):
    runner = CliRunner()
    preview = tmp_path / "preview.md"
    res = runner.invoke(cli.app, ["publish", "--dry-run", "--date", str(TODAY), "--preview-path",
                                  str(preview), "--db-path", str(db_path)])
    assert res.exit_code == 0, res.output
    assert "# Daily Power Rankings" in preview.read_text()

    res = runner.invoke(cli.app, ["publish", "--date", str(TODAY), "--db-path", str(db_path)])
    assert res.exit_code == 1 and "GOOGLE_SHEET_ID" in res.output
    res = runner.invoke(cli.app, ["publish", "--skip-if-unconfigured", "--date", str(TODAY),
                                  "--db-path", str(db_path)])
    assert res.exit_code == 0 and "Skipping publish" in res.output


def test_cli_publish_uses_google_services(db_path, clean_env, tmp_path):
    key = tmp_path / "key.json"
    key.write_text("{}")
    clean_env.setenv("GOOGLE_SHEET_ID", "SHEET")
    clean_env.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", str(key))
    sheets, drive = FakeSheets(), FakeDrive()
    clean_env.setattr(pg, "build_services", lambda path: (sheets, drive))
    res = CliRunner().invoke(cli.app, ["publish", "--make-public", "--date", str(TODAY),
                                       "--db-path", str(db_path)])
    assert res.exit_code == 0, res.output
    assert [t["title"] for t in sheets.tabs] == [*pg.TAB_TITLES, *pg.MOBILE_TITLES]
    assert drive.permissions_created == [("SHEET", {"type": "anyone", "role": "reader"})]


# --- designed Daily Picks grid ---------------------------------------------------------------


@pytest.fixture
def grid(db_path):
    with db.get_db_connection(db_path=db_path) as con:
        return pg.build_daily_grid(con, TODAY, generated_at=datetime(2026, 11, 20, 14, 5,
                                                                   tzinfo=pg.EASTERN))


def test_slate_games_are_numbers(db_path):
    with db.get_db_connection(db_path=db_path) as con:
        g, = lp.slate_games(con, TODAY)
    assert (g.away, g.home, g.market_line, g.market_total) == ("Houston", "Kansas", 1.5, 139.5)
    assert sum(g.buckets) == pytest.approx(1.0, abs=1e-9) and all(b >= 0 for b in g.buckets)
    assert g.edge == pytest.approx(abs(1.5 - g.model_line), abs=0.06)
    assert g.pick in ("Houston", "Kansas") and g.tip_et.hour == 20
    assert g.away_win + g.home_win == pytest.approx(1.0)


def test_buckets_from_tail_probs():
    b = lp._buckets(0.66, 0.49, 0.32, 0.20, 0.09)
    assert b == pytest.approx((0.09, 0.11, 0.14, 0.17, 0.17, 0.32))
    assert lp._buckets(0.5, None, 0.1, 0.1, 0.1) is None


def test_daily_grid_one_value_per_cell(grid):
    lay = grid.layout
    header = [pg.cell_text(c) for c in lay.rows[3]]
    assert header[:7] == ["#", "Tip (ET)", "Away", "Home", "Site", "Venue", "City"] and header[-1] == "Home 10+"
    assert len(header) == lay.n_cols == 25
    assert all(c.get("note") for c in lay.rows[3])                    # every header has a hover note
    row = lay.rows[4]
    texts = [pg.cell_text(c) for c in row]
    assert texts[:7] == ["1", "8:00 PM", "Houston", "Kansas", "—", "—", "—"]       # no venue data in fixture
    assert texts[12] == "+1.5" and texts[16] == "[chart]"
    # numbers are numbers, names are single strings: no cell mixes a team name with a value
    for c, cell in enumerate(row):
        v = cell.get("userEnteredValue", {})
        if c in (2, 3, 14):
            assert v["stringValue"] in ("Houston", "Kansas")
        elif c in (4, 5, 6):
            assert v["stringValue"] == "—"
        elif c == 16:
            assert v["formulaValue"].startswith("=SPARKLINE(")
        else:
            assert "numberValue" in v, (c, v)
    assert sum(row[c]["userEnteredValue"]["numberValue"] for c in range(19, 25)) == pytest.approx(1.0)


def test_daily_grid_structure(grid):
    lay = grid.layout
    assert (lay.frozen_rows, lay.frozen_cols) == (4, 4)
    assert lay.merges == [(2, 3, 0, 4), (2, 3, 4, 7), (2, 3, 7, 11), (2, 3, 11, 15), (2, 3, 15, 19), (2, 3, 19, 25)]
    assert all(len(r) == lay.n_cols for r in lay.rows)
    assert pg.cell_text(lay.rows[1][0]) == "1 games · updated 2:05 PM ET"


def test_pick_cell_shading_by_edge(grid):
    pick = grid.layout.rows[4][14]
    edge = grid.layout.rows[4][13]["userEnteredValue"]["numberValue"]
    bg = pick["userEnteredFormat"].get("backgroundColor")
    if edge >= pg.EDGE_STRONG:
        assert bg == pg._hex(pg.PICK_STRONG)
    elif edge >= pg.EDGE_LEAN:
        assert bg == pg._hex(pg.PICK_LEAN)
    else:
        assert pick["userEnteredFormat"]["textFormat"]["foregroundColor"] == pg.MUTED


def test_daily_grid_publishes_and_republishes(grid, tabs):
    sheets = FakeSheets()
    publisher = pg.SheetsPublisher(sheets, FakeDrive(), "SHEET")
    publisher.publish([grid, *tabs[1:]])
    publisher.publish([grid, *tabs[1:]])                              # re-merge after unmerge works
    tab = sheets.by_title("Daily Picks")
    assert (tab["frozen"], tab["frozen_cols"]) == (4, 4) and len(tab["merges"]) == 6
    assert tab["heights"][0] == 36 and tab["heights"][4] == 24
    assert sheets.display("Daily Picks")[4][2] == "Houston"
    publisher.publish([plain_tab("Daily Picks"), *tabs[1:]])          # merges cleared
    assert sheets.by_title("Daily Picks")["merges"] == []


def test_fake_rejects_merge_across_frozen_columns():
    sheets = FakeSheets()
    sheets._apply([{"updateSheetProperties": {"properties": {"sheetId": 0, "gridProperties": {
        "rowCount": 10, "columnCount": 10, "frozenRowCount": 1, "frozenColumnCount": 2}}, "fields": "x"}}])
    with pytest.raises(AssertionError, match="frozen columns"):
        sheets._apply([{"mergeCells": {"range": {"sheetId": 0, "startRowIndex": 0, "endRowIndex": 1,
                                                 "startColumnIndex": 0, "endColumnIndex": 5}}}])


def test_markdown_preview_renders_grid(grid):
    md = pg.render_markdown([grid])
    assert "| # | Tip (ET) | Away | Home |" in md and "| 1 | 8:00 PM | Houston | Kansas |" in md


# --- designed Power Rankings grid ----------------------------------------------------------------


@pytest.fixture
def rank_grid(db_path):
    with db.get_db_connection(db_path=db_path) as con:
        return pg.build_rankings_grid(con, TODAY, generated_at=datetime(2026, 11, 20, 9, 0, tzinfo=pg.EASTERN))


def test_rankings_grid_columns_and_history(rank_grid):
    lay = rank_grid.layout
    header = [pg.cell_text(c) for c in lay.rows[3]]
    # Snapshots on 11/13, 11/19, 11/20 -> history 11/19 back to 11/13 (11/14-18 reuse 11/13's ratings).
    assert header == ["Rank", "Team", "NDI", "L7D Change", "L30D Change", "Trend",
                      "11/19", "11/18", "11/17", "11/16", "11/15", "11/14", "11/13"]
    assert (lay.frozen_rows, lay.frozen_cols) == (4, 6)
    assert lay.merges == [(2, 3, 0, 6), (2, 3, 6, 13)]
    assert all(c.get("note") for c in lay.rows[3])
    assert len(lay.rows) == 4 + len(TEAMS)
    ranks = [r[0]["userEnteredValue"]["numberValue"] for r in lay.rows[4:]]
    assert ranks == [1, 2, 3, 4]


def test_rankings_grid_one_value_per_cell(rank_grid):
    for row in rank_grid.layout.rows[4:]:
        v = [c.get("userEnteredValue", {}) for c in row]
        assert set(v[1]) == {"stringValue"} and v[1]["stringValue"] in TEAMS
        assert all("numberValue" in v[c] for c in (0, 2, 3, 4))
        assert all("numberValue" in x for x in v[6:])
        assert 0 < v[2]["numberValue"] < 1


def test_rankings_change_and_trend(rank_grid):
    for row in rank_grid.layout.rows[4:]:
        rank = row[0]["userEnteredValue"]["numberValue"]
        week_ago = row[-1]["userEnteredValue"]["numberValue"]              # 11/13 column
        change = row[3]["userEnteredValue"]["numberValue"]
        assert change == week_ago - rank
        # History starts 11/13 (< 30 days), so L30D falls back to that first day too.
        assert row[4]["userEnteredValue"]["numberValue"] == week_ago - rank
        formula = row[5]["userEnteredValue"]["formulaValue"]
        values = [int(x) for x in formula[formula.index("{") + 1:formula.index("}")].split(",")]
        assert values[-1] == -rank and values[0] == -week_ago           # oldest -> today, negated
        color = pg.TREND_UP if rank < week_ago else pg.TREND_DOWN if rank > week_ago else pg.TREND_FLAT
        assert color in formula


@pytest.mark.parametrize("value, text", [(5, "▲ 5"), (-3, "▼ 3"), (0, "–")])
def test_change_format_renders_arrows(value, text):
    assert pg.cell_text(pg._numcell(value, pg._fmt(pattern=pg.CHANGE_FMT))) == text


def test_trend_needs_two_points():
    assert pg._trend_cell([4]) == {}
    assert pg.TREND_FLAT in pg._trend_cell([4, 4])["userEnteredValue"]["formulaValue"]


def test_rankings_grid_publishes(rank_grid, tabs):
    sheets = FakeSheets()
    publisher = pg.SheetsPublisher(sheets, FakeDrive(), "SHEET")
    publisher.publish([*tabs[:3], rank_grid])
    publisher.publish([*tabs[:3], rank_grid])
    tab = sheets.by_title("Daily Power Rankings")
    assert (tab["frozen"], tab["frozen_cols"]) == (4, 6) and len(tab["merges"]) == 2
    assert sheets.display("Daily Power Rankings")[4][1] in TEAMS


def test_l30d_note_explains_early_season_fallback(rank_grid):
    note = rank_grid.layout.rows[3][4]["note"]
    assert "November 13" in note and "December 13" in note


def test_l30d_uses_true_30_day_baseline_when_available(db_path):
    later = date(2026, 12, 4)                     # 30 days back = Nov 4, inside the season
    with db.get_db_connection(db_path=db_path) as con:
        con.execute("INSERT INTO kenpom_snapshots SELECT ?, team, adj_em, adj_o + "
                    "CASE team WHEN 'Duke' THEN -30 ELSE 0 END, adj_d, adj_t, three_pa_rate "
                    "FROM kenpom_snapshots WHERE snapshot_date = ?", [later - timedelta(days=30), TODAY])
        grid = pg.build_rankings_grid(con, later)
    row = next(r for r in grid.layout.rows[4:] if r[1]["userEnteredValue"]["stringValue"] == "Duke")
    assert grid.layout.rows[3][4]["note"] == "Places gained (▲) or lost (▼) since 30 days ago."
    assert row[4]["userEnteredValue"]["numberValue"] == 4 - row[0]["userEnteredValue"]["numberValue"]


# --- designed Model Performance grid ---------------------------------------------------------------


def _backtest(con, n_weeks=4, per_week=40):
    """Graded last-season predictions so the backtest section renders fully."""
    from src import validate_model as vm
    rows, start = [], date(2025, 11, 10)
    for w in range(n_weeks):
        for k in range(per_week):
            d = start + timedelta(weeks=w, days=k % 5)
            gid = f"bt{w}_{k}"
            win_a = (k % 3) != 0
            con.execute("INSERT INTO historical_games VALUES (?, ?, 'Duke', 'Kansas', ?, ?, NULL, 1.9)",
                        [gid, d, 75 if win_a else 65, 68])
            con.execute("INSERT INTO vegas_closing_odds VALUES (?, -4.5, -200, 170)", [gid])
            rows.append((gid, vm.DEFAULT_MODEL_VERSION, d, d, 74.0, 69.0, -5.0, 143.0, 0.68, 0.5, 1.9, 1000,
                         None, 0.4, 0.2, 0.1, 0.03))
    con.executemany("INSERT INTO model_predictions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    rows)


@pytest.fixture
def perf_grid(db_path):
    with db.get_db_connection(db_path=db_path) as con:
        _backtest(con)
        return pg.build_performance_grid(con, TODAY, generated_at=datetime(2026, 11, 20, 9, tzinfo=pg.EASTERN))


def _rows_text(grid):
    return [[pg.cell_text(c) for c in r] for r in grid.layout.rows]


def test_performance_grid_sections(perf_grid):
    text = _rows_text(perf_grid)
    firsts = [r[0] for r in text]
    assert "THIS SEASON · LIVE PICKS · 2026-27" in firsts and "LAST SEASON · BACKTEST · 2025-26" in firsts
    assert any("1 graded game." in f for f in firsts) and any("160 graded games." in f for f in firsts)
    labels = [m.label for m in pg.sc.METRICS]
    assert all(lbl in firsts for lbl in labels)
    assert any(f.startswith("Calibration") for f in firsts) and "By closing spread" in firsts
    merges = perf_grid.layout.merges
    assert all(m[2:] == (0, perf_grid.layout.n_cols) for m in merges) and len(merges) == 2


def test_performance_metric_rows_are_single_values(perf_grid):
    rows = perf_grid.layout.rows
    start = next(i for i, r in enumerate(rows) if pg.cell_text(r[0]).startswith("LAST SEASON"))
    labels = {m.label: m for m in pg.sc.METRICS}
    seen = 0
    for r in rows[start:]:
        label = pg.cell_text(r[0])
        if label not in labels:
            continue
        assert r[0].get("note")                                   # definition on hover
        v = [c.get("userEnteredValue", {}) for c in r]
        assert "numberValue" in v[1]                              # model value
        assert set(v[2]) == {"stringValue"}                        # status chip, right after the value
        assert v[2]["stringValue"] in ("Good", "OK", "Poor", "Too early", "—")
        assert "numberValue" in v[7]                               # games
        if labels[label].benchmark is not None:
            assert "numberValue" in v[3] and "numberValue" in v[4]
            assert v[6]["formulaValue"].startswith("=SPARKLINE(") and '"column"' in v[6]["formulaValue"]
        seen += 1
    assert seen == len(labels)


def test_performance_grid_before_first_live_final(db_path):
    with db.get_db_connection(db_path=db_path) as con:
        con.execute("UPDATE historical_games SET actual_score_a = NULL, actual_score_b = NULL")
        grid = pg.build_performance_grid(con, TODAY)
    firsts = [r[0] for r in _rows_text(grid)]
    assert sum(f.startswith("No graded games yet") for f in firsts) == 2      # live and backtest
    assert not any(f in {m.label for m in pg.sc.METRICS} for f in firsts)


def test_status_chip_colors(perf_grid):
    for r in perf_grid.layout.rows:
        chip = r[2].get("userEnteredValue", {}).get("stringValue")
        for key, (label, fill, _) in pg.STATUS_STYLE.items():
            if chip == label and pg.cell_text(r[0]) in {m.label for m in pg.sc.METRICS}:
                assert r[2]["userEnteredFormat"]["backgroundColor"] == pg._hex(fill)


def test_performance_grid_publishes(perf_grid, tabs):
    sheets = FakeSheets()
    publisher = pg.SheetsPublisher(sheets, FakeDrive(), "SHEET")
    publisher.publish([tabs[0], tabs[1], perf_grid, tabs[3]])
    publisher.publish([tabs[0], tabs[1], perf_grid, tabs[3]])
    tab = sheets.by_title("Model Performance & Stats")
    assert tab["frozen"] == 2 and len(tab["merges"]) == 2


# --- header filters (search) ---------------------------------------------------------------------


def test_lookup_tabs_get_header_filters(grid, rank_grid, tabs):
    sheets = FakeSheets()
    publisher = pg.SheetsPublisher(sheets, FakeDrive(), "SHEET")
    publisher.publish([grid, tabs[1], tabs[2], rank_grid])
    picks = sheets.by_title("Daily Picks")["filter"]
    ranks = sheets.by_title("Daily Power Rankings")["filter"]
    assert (picks["startRowIndex"], picks["endRowIndex"], picks["endColumnIndex"]) == (3, 5, 25)
    assert (ranks["startRowIndex"], ranks["endRowIndex"]) == (3, 4 + len(TEAMS))
    assert sheets.by_title("Model Performance & Stats")["filter"] is None
    assert pg.SEARCH_HINT in pg.cell_text(grid.layout.rows[1][4])


def test_filter_is_cleared_when_tab_loses_it(grid, tabs):
    sheets = FakeSheets()
    pg.SheetsPublisher(sheets, FakeDrive(), "SHEET").publish([grid, *tabs[1:]])
    # A fresh publisher (next day's run) learns about the filter from the spreadsheet itself.
    plain = [plain_tab("Daily Picks"), *tabs[1:]]
    pg.SheetsPublisher(sheets, FakeDrive(), "SHEET").publish(plain)
    assert sheets.by_title("Daily Picks")["filter"] is None
    pg.SheetsPublisher(sheets, FakeDrive(), "SHEET").publish(plain)     # no filter: nothing to clear


# --- phone layout ---------------------------------------------------------------------------------


@pytest.fixture
def mobile_grid(db_path):
    with db.get_db_connection(db_path=db_path) as con:
        return pg.build_mobile_grid(con, TODAY, generated_at=STAMP)


def test_mobile_grid_fits_a_phone(mobile_grid):
    lay = mobile_grid.layout
    assert lay.n_cols == 3 and sum(lay.col_widths) <= 340 and lay.frozen_cols == 0
    assert all(len(r) == 3 for r in lay.rows)


def test_mobile_card_rows(mobile_grid):
    text = {pg.cell_text(r[0]): [pg.cell_text(c) for c in r[1:]] for r in mobile_grid.layout.rows}
    assert text["Away · Home"] == ["Houston", "Kansas"]
    assert text["8:00 PM · Game 1"] == ["", ""]
    assert text["Market line"] == ["-1.5", "+1.5"]                  # each team's own spread
    away, home = (float(x.rstrip("%")) for x in text["Win chance"])
    assert away + home == pytest.approx(100, abs=1)
    for label in ("Wins by 10+", "Wins by 5–9", "Wins by 1–4", "Pick", "Edge (pts)", "Proj. total"):
        assert label in text
    ladder = sum(float(x.rstrip("%")) for k in ("Wins by 10+", "Wins by 5–9", "Wins by 1–4") for x in text[k])
    assert ladder == pytest.approx(100, abs=3)                         # whole-percent rounding


def test_mobile_grid_publishes(mobile_grid, tabs):
    sheets = FakeSheets()
    pg.SheetsPublisher(sheets, FakeDrive(), "SHEET").publish(tabs)          # phone tab is part of build_tabs
    tab = sheets.by_title(pg.MOBILE_TITLE)
    assert tab["cols"] == 3 and tab["frozen"] == 1 and tab["color"] == pg._hex(pg.MOBILE_TAB_COLOR)


@pytest.fixture
def mobile_ranks(db_path):
    with db.get_db_connection(db_path=db_path) as con:
        return pg.build_mobile_rankings_grid(con, TODAY, generated_at=STAMP)


def test_mobile_rankings_list(mobile_ranks, rank_grid):
    lay = mobile_ranks.layout
    assert sum(lay.col_widths) <= 340 and lay.frozen_cols == 0
    assert [pg.cell_text(c) for c in lay.rows[2]] == ["#", "Team", "NDI", "7D", "Trend"]
    assert lay.frozen_rows == 3 and lay.filter_row == 2
    mobile = [[pg.cell_text(c) for c in r[:4]] for r in lay.rows[3:]]
    desktop = [[pg.cell_text(r[i]) for i in (0, 1, 2, 3)] for r in rank_grid.layout.rows[4:]]
    assert mobile == desktop                                                # same ranks, NDI, 7-day change
    assert all(r[4]["userEnteredValue"]["formulaValue"].startswith("=SPARKLINE(") for r in lay.rows[3:])


@pytest.fixture
def mobile_ledger(db_path):
    with db.get_db_connection(db_path=db_path) as con:
        return pg.build_mobile_ledger_grid(con, TODAY, generated_at=STAMP)


def test_mobile_ledger(mobile_ledger):
    lay = mobile_ledger.layout
    assert sum(lay.col_widths) <= 345
    text = [[pg.cell_text(c) for c in r] for r in lay.rows]
    stats = {r[0]: r[3] for r in text if r[0] and r[3]}
    assert stats["Against the spread"] == "100.0%" and stats["Units @ −110"] == "+1.0"
    assert stats["Avg closing-line value"] == "+1.50"
    header = next(i for i, r in enumerate(text) if r[0] == "Date")
    assert text[header + 1] == ["11/19", "Duke", "Michigan St.", "-3.0", "Cover"]   # Duke's own line
    assert lay.filter_row == header


def test_mobile_ledger_caps_rows(db_path):
    with db.get_db_connection(db_path=db_path) as con:
        grid = pg.build_mobile_ledger_grid(con, TODAY, max_rows=0)
    assert "last 0 below" in pg.cell_text(grid.layout.rows[1][0])


def test_performance_status_fits_on_a_phone(perf_grid):
    assert sum(pg.PERF_WIDTHS[:3]) <= 340
    assert pg.cell_text(next(r for r in perf_grid.layout.rows if pg.cell_text(r[0]) == "Metric")[2]) == "Status"


def test_cli_publish_blocked_by_sanity_errors(db_path, clean_env, tmp_path):
    key = tmp_path / "key.json"
    key.write_text("{}")
    clean_env.setenv("GOOGLE_SHEET_ID", "SHEET")
    clean_env.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", str(key))
    clean_env.setattr(cli.sanity, "MIN_RANKED_TEAMS", 300)          # 4-team warehouse fails the rankings check
    sheets = FakeSheets()
    clean_env.setattr(pg, "build_services", lambda path: (sheets, FakeDrive()))
    res = CliRunner().invoke(cli.app, ["publish", "--date", str(TODAY), "--db-path", str(db_path)])
    assert res.exit_code == 1 and "blocked" in res.output and sheets.batches == []
    res = CliRunner().invoke(cli.app, ["publish", "--force", "--date", str(TODAY), "--db-path", str(db_path)])
    assert res.exit_code == 0 and sheets.batches


def test_held_back_games_are_noted_on_daily_tabs(db_path):
    from src import sanity
    with db.get_db_connection(db_path=db_path) as con:
        sanity.store(con, TODAY, "picks", [sanity.Issue("error", "favorite_flip", "Rice @ Duke: x", "g9")])
        daily, mobile = pg.build_daily_grid(con, TODAY), pg.build_mobile_grid(con, TODAY)
    assert "1 game held back for review: Rice @ Duke" in pg.cell_text(daily.layout.rows[1][4])
    assert "held back for review: Rice @ Duke" in pg.cell_text(mobile.layout.rows[1][0])



# --- column fitting -------------------------------------------------------------------------------


def test_text_px_scales_with_length_size_and_weight():
    assert pg.text_px("Home 10+") > pg.text_px("Home")
    assert pg.text_px("Home 10+", 10) > pg.text_px("Home 10+", 9)
    assert pg.text_px("Home 10+", bold=True) > pg.text_px("Home 10+")
    assert pg.text_px("WWW") > pg.text_px("iii")


def _single_line_cells(lay):
    merged = {(r, c) for r0, r1, c0, c1 in lay.merges for r in range(r0, r1) for c in range(c0, c1)}
    for r, row in enumerate(lay.rows):
        labeled = [cell for cell in row if pg.cell_text(cell)]
        header = len(labeled) > 1 and all("note" in cell for cell in labeled)
        for c, cell in enumerate(row):
            fmt = cell.get("userEnteredFormat", {})
            wrap = fmt.get("wrapStrategy")
            if ((r, c) in merged or not pg.cell_text(cell) or wrap == "OVERFLOW_CELL"
                    or (wrap == "WRAP" and not header) or "formulaValue" in cell.get("userEnteredValue", {})):
                continue
            yield c, cell, fmt.get("textFormat", {})


def test_desktop_headers_and_values_fit_on_one_line(tabs):
    for tab in tabs[:4]:
        lay = tab.layout
        for c, cell, tf in _single_line_cells(lay):
            need = pg.text_px(pg.cell_text(cell), tf.get("fontSize", 10), tf.get("bold", False)) + pg.CELL_PADDING_PX
            assert lay.col_widths[c] >= min(need, pg.MAX_FIT_PX) - 1, (tab.title, pg.cell_text(cell), lay.col_widths[c])


def test_fitting_only_widens(tabs, grid):
    assert all(w >= base for w, (_, _, (_, base, _)) in
               zip(grid.layout.col_widths, [(g, col, x) for g, col, cols in pg.DAILY_GROUPS for x in cols]))
    hdr = [pg.cell_text(c) for c in grid.layout.rows[3]]
    assert grid.layout.col_widths[hdr.index("Home 10+")] > 62               # was clipped at 62 px


def test_performance_still_fits_a_phone(perf_grid):
    assert sum(perf_grid.layout.col_widths[:3]) <= 340



# --- venues / neutral sites ------------------------------------------------------------------------


def _espn(home, away, neutral, venue="Baha Mar Convention Center", city="Nassau", country="Bahamas"):
    return {"competitions": [{"neutralSite": neutral, "venue": {"fullName": venue, "address": {"city": city, "country": country}},
                              "competitors": [{"homeAway": "home", "team": {"displayName": home, "location": home}},
                                              {"homeAway": "away", "team": {"displayName": away, "location": away}}]}]}


def test_venues_match_either_orientation_and_zero_hca(db_path):
    from src import venues
    with db.get_db_connection(db_path=db_path) as con:
        con.execute("DELETE FROM model_predictions WHERE game_id = 't1'")          # unlocked: hca can change
        from src.team_names import TeamNameMapper
        mapper = TeamNameMapper(["Kansas", "Houston", "Duke", "Michigan St."])
        parsed = venues.parse_scoreboard({"events": [_espn("Houston Cougars", "Kansas Jayhawks", True)]}, mapper)
        assert parsed == [venues.Venue("Houston", "Kansas", True, "Baha Mar Convention Center", "Nassau, Bahamas")]
        run = venues.store(con, TODAY, parsed)                                   # ESPN has Houston "home"
        assert (run.matched, run.neutral, run.hca_zeroed) == (1, 1, 1)
        assert con.execute("SELECT hca_eff, venue FROM historical_games WHERE game_id = 't1'").fetchone() == \
            (0.0, "Baha Mar Convention Center")
        lp.make_picks(con, TODAY, now=datetime(2026, 11, 20, 15), n_sims=2000)
        g, = lp.slate_games(con, TODAY)
        assert g.neutral and g.venue == "Baha Mar Convention Center" and g.city == "Nassau, Bahamas"
        grid = pg.build_daily_grid(con, TODAY)
        mobile = pg.build_mobile_grid(con, TODAY)
    row = [pg.cell_text(c) for c in grid.layout.rows[4][4:7]]
    assert row == ["Neutral", "Baha Mar Convention Center", "Nassau, Bahamas"]
    assert any("Neutral site" in pg.cell_text(r[0]) for r in mobile.layout.rows)


def test_locked_picks_keep_their_hca(db_path):
    from src import venues
    with db.get_db_connection(db_path=db_path) as con:                          # t1 is already locked
        run = venues.store(con, TODAY, [venues.Venue("Kansas", "Houston", True, "Arena", "City, ST")])
        assert run.hca_zeroed == 0 and run.neutral == 1
        assert con.execute("SELECT hca_eff FROM historical_games WHERE game_id = 't1'").fetchone() == (1.9,)


def test_ledger_shows_site(db_path):
    from src import venues
    with db.get_db_connection(db_path=db_path) as con:
        venues.store(con, YESTERDAY, [venues.Venue("Duke", "Michigan St.", False, "Cameron Indoor Stadium", "Durham, NC")])
        grid = pg.build_ledger_grid(con, TODAY)
    assert [pg.cell_text(c) for c in grid.layout.rows[7][3:6]] == ["Home", "Cameron Indoor Stadium", "Durham, NC"]
