"""Publish the 'How this works' papers (docs/*.md) to Google Docs.

The Markdown files in ``docs/`` are the source of truth. Each is converted to
HTML and uploaded as the new content of an existing Google Doc; Drive converts
the HTML into native Doc formatting (headings, tables, sub/superscripts).

Setup (same pattern as the sheet: the service account can't own files):
  1. Create two empty Google Docs in the account that owns the sheet.
  2. Share each with the service account's client_email as Editor, and set
     General access to "Anyone with the link -> Viewer" if the sheet is public.
  3. In .env: HOW_PICKS_DOC_ID=<id> and HOW_RANKINGS_DOC_ID=<id>.

The sheet links to the docs (Daily Picks / Power Rankings) whenever these IDs
are set.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import markdown

from src.db import PROJECT_ROOT

DOCS_DIR = PROJECT_ROOT / "docs"


@dataclass(frozen=True)
class Paper:
    key: str
    env: str
    source: Path
    title: str


PAPERS = (
    Paper("picks", "HOW_PICKS_DOC_ID", DOCS_DIR / "how_picks_work.md", "How the Daily Picks Work"),
    Paper("rankings", "HOW_RANKINGS_DOC_ID", DOCS_DIR / "how_rankings_work.md", "How the Power Rankings Work"),
)

_CSS = """
body { font-family: Arial, sans-serif; font-size: 11pt; line-height: 1.4; color: #1f2937; }
h1 { font-size: 22pt; color: #1F2A44; }
h2 { font-size: 15pt; color: #1F2A44; margin-top: 18pt; }
table { border-collapse: collapse; }
th { background: #E6EBF2; font-weight: bold; }
th, td { border: 1px solid #CBD5E1; padding: 4pt 8pt; }
blockquote { color: #1F2A44; font-weight: bold; }
"""


def doc_id(key: str) -> str | None:
    paper = next(p for p in PAPERS if p.key == key)
    return os.getenv(paper.env) or None


def doc_url(key: str) -> str | None:
    """Public link to a paper's Google Doc, if configured."""
    did = doc_id(key)
    return f"https://docs.google.com/document/d/{did}/edit?usp=sharing" if did else None


def to_html(md_text: str, title: str) -> str:
    body = markdown.markdown(md_text, extensions=["tables"])
    return (f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{title}</title>"
            f"<style>{_CSS}</style></head><body>{body}</body></html>")


def publish(drive, papers=PAPERS) -> dict[str, str]:
    """Upload each configured paper into its Doc. Returns {key: url}."""
    from googleapiclient.http import MediaInMemoryUpload

    out = {}
    for p in papers:
        did = os.getenv(p.env)
        if not did:
            continue
        html = to_html(p.source.read_text(encoding="utf-8"), p.title)
        media = MediaInMemoryUpload(html.encode("utf-8"), mimetype="text/html", resumable=False)
        drive.files().update(fileId=did, media_body=media, body={"name": p.title}).execute()
        out[p.key] = doc_url(p.key)
    return out
