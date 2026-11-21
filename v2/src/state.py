"""Save / restore the DuckDB warehouse as sorted CSVs (one per table).

Used by the cloud daily job: the database lives in a private git repo between
runs. Rows are sorted by every column, so each day's commit only adds that
day's new rows (tiny diffs, readable history of every locked pick) instead of
rewriting a 13 MB binary file.

    python cli.py state export DIR    # warehouse -> DIR/<table>.csv
    python cli.py state import DIR    # DIR/<table>.csv -> fresh warehouse
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from src import db

TABLES = tuple(db.SCHEMA)


class StateError(RuntimeError):
    pass


def _columns(con: duckdb.DuckDBPyConnection, table: str) -> list[str]:
    return [r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = ? ORDER BY ordinal_position",
        [table]).fetchall()]


def export_state(con: duckdb.DuckDBPyConnection, out_dir: Path) -> dict[str, int]:
    """Write every table to ``out_dir/<table>.csv`` sorted by all columns."""
    if con.execute("SELECT count(*) FROM kenpom_snapshots").fetchone()[0] == 0:
        raise StateError("refusing to export an empty warehouse (no KenPom snapshots): "
                         "the restore probably failed, and this would overwrite the saved state")
    out_dir.mkdir(parents=True, exist_ok=True)
    counts = {}
    for table in TABLES:
        cols = ", ".join(f'"{c}"' for c in _columns(con, table))
        path = out_dir / f"{table}.csv"
        con.execute(f"COPY (SELECT {cols} FROM {table} ORDER BY ALL) TO '{path}' (HEADER, DELIMITER ',')")
        counts[table] = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    return counts


def import_state(in_dir: Path, db_path: Path) -> dict[str, int]:
    """Build a fresh warehouse at ``db_path`` from ``in_dir``'s CSVs."""
    missing = [t for t in TABLES if not (in_dir / f"{t}.csv").exists()]
    if missing and "kenpom_snapshots" in missing:
        raise StateError(f"no saved state in {in_dir} (missing {', '.join(missing)})")
    if db_path.exists():
        db_path.unlink()
    counts = {}
    with db.get_db_connection(db_path=db_path) as con:
        for table in TABLES:
            path = in_dir / f"{table}.csv"
            if path.exists():
                con.execute(f"COPY {table} FROM '{path}' (HEADER, DELIMITER ',')")
            counts[table] = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    return counts
