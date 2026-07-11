"""Markdown -> gestylte HTML-Lese-Ansicht in Artefakt-Qualitaet (§5a Punkt 8, §5c).

Der Renderer erzeugt vollstaendige, in sich geschlossene HTML-Dokumente im
dunklen Dashboard-Stil (Typografie-Hierarchie, Tabellen-Styling, Quellen-Links).
Das Control-UI bettet sie ueber eine eigene Flask-Route als iframe ein; die
Dokumente funktionieren aber auch standalone im Browser. Neben Markdown werden
JSON/JSONL (strukturiert) und CSV (tabellarisch) gerendert — damit deckt
``render_artifact`` alle Uebergabe-Artefakte des Status-Boards ab (§5a Punkt 3).

Markdown wird primaer mit dem Paket ``markdown`` (feste Taipy-Abhaengigkeit)
konvertiert; fehlt es, faellt der Renderer auf eine escaped ``<pre>``-Ansicht
zurueck, statt zu crashen.
"""

from __future__ import annotations

import html as _html
import io
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


# Zentrale Artefakt-Typografie: dunkle Palette passend zum Control-UI-Theme.
DOCUMENT_CSS = """
:root {
  --bg: #070b18;
  --panel: #0d1530;
  --panel-2: #111c3d;
  --border: #24345e;
  --text: #dbe4ff;
  --muted: #8b98c4;
  --accent: #4cc9f0;
  --accent-2: #8f7bff;
  --good: #2ee6a8;
  --bad: #ff5470;
}
* { box-sizing: border-box; }
html, body {
  margin: 0;
  padding: 0;
  background: var(--bg);
  color: var(--text);
}
body {
  font-family: "Segoe UI", "Inter", "Helvetica Neue", Arial, sans-serif;
  font-size: 15.5px;
  line-height: 1.65;
}
.st-doc {
  max-width: 880px;
  margin: 0 auto;
  padding: 40px 44px 72px 44px;
}
.st-doc-meta {
  color: var(--muted);
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.14em;
  border-bottom: 1px solid var(--border);
  padding-bottom: 10px;
  margin-bottom: 26px;
}
.st-doc h1, .st-doc h2, .st-doc h3, .st-doc h4 {
  color: #f2f5ff;
  font-weight: 650;
  line-height: 1.25;
  margin: 1.7em 0 0.55em 0;
}
.st-doc h1 {
  font-size: 1.9em;
  margin-top: 0.2em;
  background: linear-gradient(90deg, #e8efff, var(--accent));
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}
.st-doc h2 {
  font-size: 1.42em;
  border-bottom: 1px solid var(--border);
  padding-bottom: 0.28em;
}
.st-doc h3 { font-size: 1.15em; color: var(--accent); }
.st-doc h4 { font-size: 1.0em; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; }
.st-doc p { margin: 0.65em 0; }
.st-doc a { color: var(--accent); text-decoration: none; border-bottom: 1px dotted rgba(76, 201, 240, 0.5); }
.st-doc a:hover { color: #9ae6ff; border-bottom-style: solid; }
.st-doc strong { color: #ffffff; }
.st-doc ul, .st-doc ol { padding-left: 1.5em; margin: 0.6em 0; }
.st-doc li { margin: 0.28em 0; }
.st-doc blockquote {
  margin: 1em 0;
  padding: 0.55em 1.1em;
  border-left: 3px solid var(--accent-2);
  background: rgba(143, 123, 255, 0.08);
  border-radius: 0 8px 8px 0;
  color: #c9d4f6;
}
.st-doc code {
  font-family: "JetBrains Mono", "Fira Code", "Consolas", monospace;
  font-size: 0.88em;
  background: rgba(76, 201, 240, 0.10);
  border: 1px solid rgba(76, 201, 240, 0.18);
  border-radius: 5px;
  padding: 0.08em 0.4em;
  color: #a8e6ff;
}
.st-doc pre {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 14px 18px;
  overflow-x: auto;
  line-height: 1.5;
}
.st-doc pre code { background: none; border: none; padding: 0; color: #cfe3ff; }
.st-doc hr { border: none; border-top: 1px solid var(--border); margin: 2em 0; }
.st-doc table {
  border-collapse: collapse;
  width: 100%;
  margin: 1.1em 0;
  font-size: 0.93em;
  background: rgba(13, 21, 48, 0.6);
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
}
.st-doc th {
  background: linear-gradient(180deg, #16244d, #101a3a);
  color: #eaf1ff;
  text-align: left;
  font-weight: 600;
  padding: 9px 13px;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}
.st-doc td {
  padding: 8px 13px;
  border-bottom: 1px solid rgba(36, 52, 94, 0.55);
  vertical-align: top;
}
.st-doc tr:last-child td { border-bottom: none; }
.st-doc tr:nth-child(even) td { background: rgba(17, 28, 61, 0.45); }
.st-doc .st-empty { color: var(--muted); font-style: italic; }
"""


def markdown_to_html(md_text: str) -> str:
    """Konvertiert Markdown in ein HTML-Fragment (Tabellen + Code-Bloecke aktiv)."""

    if not md_text or not md_text.strip():
        return '<p class="st-empty">(leeres Dokument)</p>'
    try:
        import markdown as _markdown

        return _markdown.markdown(
            md_text,
            extensions=["tables", "fenced_code", "sane_lists", "nl2br"],
            output_format="html",
        )
    except Exception as exc:  # pragma: no cover - nur ohne markdown-Paket
        logger.warning("Markdown-Konvertierung fehlgeschlagen (%s); Fallback <pre>.", exc)
        return f"<pre>{_html.escape(md_text)}</pre>"


def _wrap_document(body_html: str, *, title: str | None, meta: str | None = None) -> str:
    """Verpackt ein Fragment in ein vollstaendiges, gestyltes HTML-Dokument."""

    heading = _html.escape(title) if title else ""
    meta_html = (
        f'<div class="st-doc-meta">{_html.escape(meta)}</div>' if meta else ""
    )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="de"><head><meta charset="utf-8">'
        f"<title>{heading or 'Dokument'}</title>"
        f"<style>{DOCUMENT_CSS}</style></head>"
        f'<body><article class="st-doc">{meta_html}{body_html}</article></body></html>'
    )


def render_document(md_text: str, *, title: str | None = None, meta: str | None = None) -> str:
    """Markdown -> vollstaendiges gestyltes HTML-Dokument (Ergebnis-Ansicht)."""

    return _wrap_document(markdown_to_html(md_text), title=title, meta=meta)


def render_json(text: str, *, title: str | None = None, meta: str | None = None) -> str:
    """JSON/JSONL strukturiert (pretty-printed, escaped) als Lese-Ansicht."""

    pretty_parts: list[str] = []
    for chunk in text.splitlines() if _looks_like_jsonl(text) else [text]:
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            pretty_parts.append(json.dumps(json.loads(chunk), indent=2, ensure_ascii=False))
        except json.JSONDecodeError:
            pretty_parts.append(chunk)
    pretty = "\n\n".join(pretty_parts) if pretty_parts else "(leer)"
    body = f"<pre><code>{_html.escape(pretty)}</code></pre>"
    return _wrap_document(body, title=title, meta=meta)


def _looks_like_jsonl(text: str) -> bool:
    lines = [line for line in text.splitlines() if line.strip()]
    return len(lines) > 1 and all(line.lstrip().startswith("{") for line in lines)


def render_csv(
    text: str,
    *,
    title: str | None = None,
    meta: str | None = None,
    max_rows: int = 500,
) -> str:
    """CSV tabellarisch als gestylte HTML-Tabelle (gekappt auf ``max_rows``)."""

    import csv

    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return _wrap_document('<p class="st-empty">(leere CSV)</p>', title=title, meta=meta)

    header, data = rows[0], rows[1:]
    clipped = len(data) > max_rows
    data = data[:max_rows]

    head_html = "".join(f"<th>{_html.escape(cell)}</th>" for cell in header)
    body_rows = "".join(
        "<tr>" + "".join(f"<td>{_html.escape(cell)}</td>" for cell in row) + "</tr>"
        for row in data
    )
    note = (
        f'<p class="st-empty">Anzeige auf {max_rows} Zeilen gekappt.</p>' if clipped else ""
    )
    body = (
        f"<table><thead><tr>{head_html}</tr></thead>"
        f"<tbody>{body_rows}</tbody></table>{note}"
    )
    return _wrap_document(body, title=title, meta=meta)


def render_text(text: str, *, title: str | None = None, meta: str | None = None) -> str:
    """Beliebiger Text escaped und monospaced (Logs, unbekannte Formate)."""

    body = f"<pre><code>{_html.escape(text)}</code></pre>"
    return _wrap_document(body, title=title, meta=meta)


def render_artifact(path: Path, *, title: str | None = None) -> str:
    """Rendert ein Artefakt anhand seiner Endung (md/json/jsonl/csv/sonstiges).

    Rueckgabe ist immer ein vollstaendiges HTML-Dokument; Lesefehler werden als
    lesbare Fehlerseite gerendert statt geworfen (UI-Robustheit).
    """

    path = Path(path)
    doc_title = title or path.name
    meta = str(path.name)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        body = f'<p class="st-empty">Artefakt nicht lesbar: {_html.escape(str(exc))}</p>'
        return _wrap_document(body, title=doc_title, meta=meta)

    suffix = path.suffix.lower()
    if suffix in {".md", ".markdown"}:
        return render_document(text, title=doc_title, meta=meta)
    if suffix in {".json", ".jsonl"}:
        return render_json(text, title=doc_title, meta=meta)
    if suffix == ".csv":
        return render_csv(text, title=doc_title, meta=meta)
    return render_text(text, title=doc_title, meta=meta)
