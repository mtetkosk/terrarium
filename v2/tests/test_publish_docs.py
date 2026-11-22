"""'How this works' papers: Markdown -> HTML -> Google Docs, and links on the sheet."""

from datetime import date

import pytest

from src import publish_docs as pd


def test_papers_exist_and_convert():
    for p in pd.PAPERS:
        html = pd.to_html(p.source.read_text(), p.title)
        assert html.startswith("<!DOCTYPE html>") and f"<title>{p.title}</title>" in html
        assert "<table>" in html and "<h2>" in html and "<sub>" in html       # tables, sections, math subscripts
        assert "not betting advice" in html


def test_rankings_paper_explains_ndi_as_expected_record():
    text = pd.PAPERS[1].source.read_text()
    assert "does **not** mean" in text and "352" in text and "#2 Illinois | 59%" in text


class FakeFiles:
    def __init__(self):
        self.updates = []

    def files(self):
        return self

    def update(self, fileId, media_body, body):
        self.updates.append((fileId, media_body.mimetype(), body["name"]))
        return self

    def execute(self):
        return {}


def test_publish_only_configured_docs(monkeypatch):
    monkeypatch.setenv("HOW_PICKS_DOC_ID", "DOC1")
    monkeypatch.delenv("HOW_RANKINGS_DOC_ID", raising=False)
    drive = FakeFiles()
    urls = pd.publish(drive)
    assert drive.updates == [("DOC1", "text/html", "How the Daily Picks Work")]
    assert urls == {"picks": "https://docs.google.com/document/d/DOC1/edit?usp=sharing"}
    assert pd.doc_url("rankings") is None
