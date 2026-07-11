"""Renderer-Grundfaelle: Markdown/JSON/CSV -> gestylte HTML-Dokumente (B4)."""

from __future__ import annotations

from pathlib import Path

from suspicious_trades.reporting import renderer


def test_markdown_basics_headings_bold_links_code():
    html = renderer.markdown_to_html(
        "# Titel\n\n**fett** und [Link](https://example.com)\n\n"
        "```\ncode block\n```\n"
    )
    assert "<h1>Titel</h1>" in html
    assert "<strong>fett</strong>" in html
    assert '<a href="https://example.com">Link</a>' in html
    assert "code block" in html


def test_markdown_tables_render_as_html_tables():
    html = renderer.markdown_to_html(
        "| Platz | Ticker |\n|---|---|\n| 1 | TST |\n"
    )
    assert "<table>" in html
    assert "<th>Platz</th>" in html
    assert "<td>TST</td>" in html


def test_markdown_empty_document_placeholder():
    assert "leeres Dokument" in renderer.markdown_to_html("   ")


def test_render_document_is_full_styled_html():
    doc = renderer.render_document("# A", title="Mein Titel", meta="empfehlung.md")
    assert doc.startswith("<!DOCTYPE html>")
    assert "<style>" in doc
    assert "<title>Mein Titel</title>" in doc
    assert "st-doc" in doc
    assert "empfehlung.md" in doc


def test_render_json_pretty_prints_json_and_jsonl():
    doc = renderer.render_json('{"a": 1}')
    assert '&quot;a&quot;: 1' in doc
    jsonl = renderer.render_json('{"a": 1}\n{"b": 2}')
    assert '&quot;b&quot;: 2' in jsonl


def test_render_csv_table_and_clipping():
    text = "col1,col2\n" + "\n".join(f"a{i},b{i}" for i in range(5))
    doc = renderer.render_csv(text, max_rows=3)
    assert "<th>col1</th>" in doc
    assert "<td>a0</td>" in doc
    assert "a4" not in doc
    assert "gekappt" in doc


def test_render_artifact_dispatch(tmp_path: Path):
    md = tmp_path / "doc.md"
    md.write_text("# Hallo", encoding="utf-8")
    assert "<h1>Hallo</h1>" in renderer.render_artifact(md)

    js = tmp_path / "events.jsonl"
    js.write_text('{"event_id": "x"}\n', encoding="utf-8")
    assert "event_id" in renderer.render_artifact(js)

    csv_file = tmp_path / "t.csv"
    csv_file.write_text("a,b\n1,2\n", encoding="utf-8")
    assert "<table>" in renderer.render_artifact(csv_file)

    other = tmp_path / "run.log"
    other.write_text("<zeile>", encoding="utf-8")
    rendered = renderer.render_artifact(other)
    assert "&lt;zeile&gt;" in rendered


def test_render_artifact_missing_file_is_error_page(tmp_path: Path):
    doc = renderer.render_artifact(tmp_path / "fehlt.md")
    assert "nicht lesbar" in doc
    assert doc.startswith("<!DOCTYPE html>")
