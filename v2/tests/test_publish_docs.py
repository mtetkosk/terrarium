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
    assert "does **not** mean" in text and "346" in text and "#2 Illinois | 57%" in text


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


def test_local_images_are_embedded(tmp_path):
    from src import publish_docs as pd
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "chart.png").write_bytes(b"\x89PNG fake")
    html = pd.to_html("![A chart](images/chart.png)\n\n![Remote](https://example.com/x.png)\n\n"
                      "![Missing](images/nope.png)", "T", base_dir=tmp_path)
    assert 'src="data:image/png;base64,iVBORyBmYWtl" width="624"' in html
    assert 'src="https://example.com/x.png"' in html and 'src="images/nope.png"' in html


def test_picks_paper_images_exist():
    import re
    from src import publish_docs as pd
    text = (pd.DOCS_DIR / "how_picks_work.md").read_text()
    images = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text)
    assert len(images) == 4 and all((pd.DOCS_DIR / p).is_file() for p in images)
