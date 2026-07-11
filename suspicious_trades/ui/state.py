"""Session-State-Defaults und Ableitungen des Control-UI (§5a-Feinstruktur).

Alle Funktionen sind reine Datei-/Settings-Ableitungen ohne Taipy-Abhaengigkeit
— die Seitenmodule binden ihre Ergebnisse an den Session-State. Persistenter
Zustand liegt ausschliesslich in Dateien (status.json, config_overrides.json,
prompt_overrides/, hints.jsonl); das UI ist stateless neu startbar.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import html as _html
import json
import logging
from pathlib import Path
import shutil
from typing import Any, Mapping
from urllib.parse import quote

import pandas as pd

from .. import config, hints, prompts
from . import runctl

logger = logging.getLogger(__name__)


def effective_settings() -> dict[str, Any]:
    """Frische effektive Config (Overrides koennen sich jederzeit aendern)."""

    return config.effective(reload=True)


# -- Formatierung -------------------------------------------------------------


def fmt_ts(value: Any) -> str:
    """ISO-Zeitstempel kompakt (lokale Zeit, Minutengenauigkeit)."""

    if not value:
        return "-"
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return str(value)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone()
    return parsed.strftime("%Y-%m-%d %H:%M")


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def duration_str(start: Any, end: Any) -> str:
    """Dauer zwischen zwei ISO-Zeitpunkten (Ende offen = bis jetzt)."""

    begin = _parse_iso(start)
    if begin is None:
        return "-"
    finish = _parse_iso(end) or datetime.now(timezone.utc)
    seconds = max(0.0, (finish - begin).total_seconds())
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}min"
    return f"{seconds / 3600:.1f}h"


def fmt_usd(value: Any) -> str:
    try:
        return f"{float(value):.4f} USD"
    except (TypeError, ValueError):
        return "-"


# -- Runs & Steps --------------------------------------------------------------


def run_ids(settings: Mapping[str, Any]) -> list[str]:
    """run_ids aller bekannten Laeufe, juengste zuerst (Historie-Limit)."""

    limit = int(settings.get("UI_RUN_HISTORY_LIMIT", 50))
    return [
        str(item.get("run_id"))
        for item in runctl.list_runs(settings, limit=limit)
        if item.get("run_id")
    ]


def runs_overview_df(settings: Mapping[str, Any]) -> pd.DataFrame:
    """Historien-Tabelle aller Laeufe (Zustand, Zeiten, Kosten)."""

    rows = []
    limit = int(settings.get("UI_RUN_HISTORY_LIMIT", 50))
    for item in runctl.list_runs(settings, limit=limit):
        run_id = str(item.get("run_id"))
        search = runctl.search_usage_totals(settings, run_id)
        rows.append(
            {
                "Run": run_id,
                "Pipeline": item.get("pipeline"),
                "Zustand": item.get("state"),
                "Start": fmt_ts(item.get("started_at")),
                "Dauer": duration_str(item.get("started_at"), item.get("finished_at")),
                "LLM-USD": round(float(item.get("cost_usd_total") or 0.0), 4),
                "Credits": round(float(search.get("credits") or 0.0), 1),
            }
        )
    columns = ["Run", "Pipeline", "Zustand", "Start", "Dauer", "LLM-USD", "Credits"]
    return pd.DataFrame(rows, columns=columns)


def steps_df(status: Mapping[str, Any] | None) -> pd.DataFrame:
    """Step-Tabelle eines Status-JSON (Klickquelle des Uebergabe-Browsers)."""

    columns = ["Step", "Zustand", "Start", "Dauer", "Kosten USD", "Artefakte", "Fehler"]
    if not status:
        return pd.DataFrame(columns=columns)
    rows = []
    for step in status.get("steps", []):
        artifacts = step.get("artifacts") or []
        rows.append(
            {
                "Step": step.get("step"),
                "Zustand": step.get("state"),
                "Start": fmt_ts(step.get("started_at")),
                "Dauer": duration_str(step.get("started_at"), step.get("finished_at"))
                if step.get("started_at")
                else "-",
                "Kosten USD": round(float(step.get("cost_usd") or 0.0), 4),
                "Artefakte": ", ".join(str(a) for a in artifacts) or "-",
                "Fehler": step.get("error") or "",
            }
        )
    return pd.DataFrame(rows, columns=columns)


def artifacts_of_run(status: Mapping[str, Any] | None) -> list[str]:
    """Alle Artefakt-Pfade (relativ zur DATA_DIR) eines Laufs in Step-Reihenfolge."""

    if not status:
        return []
    seen: list[str] = []
    for step in status.get("steps", []):
        for artifact in step.get("artifacts") or []:
            label = str(artifact)
            if label not in seen:
                seen.append(label)
    return seen


def doc_url(rel_path: str) -> str:
    """URL der Renderer-Route fuer ein Artefakt (Pfad relativ zur DATA_DIR)."""

    return f"/st-doc?path={quote(str(rel_path))}"


# -- Agent-Status-Board (Node-Diagramm als selbststaendiges SVG) ---------------

# Farbwelt je Step-Zustand: (Fuellung, Rand, Textfarbe, Statuslabel).
_NODE_STYLE = {
    "pending": ("#0d1631", "#28375f", "#7484ad", "wartet"),
    "running": ("#0f2350", "#4cc9f0", "#d9f3ff", "laeuft"),
    "done": ("#0c2438", "#2ee6a8", "#c9ffe9", "fertig"),
    "failed": ("#2b1026", "#ff5470", "#ffd4dd", "fehlgeschlagen"),
    "aborted": ("#261b0d", "#ffb454", "#ffe7c4", "abgebrochen"),
    "skipped": ("#101527", "#55617f", "#8a94b5", "uebersprungen"),
}

_SVG_STYLE = """
  text { font-family: 'Segoe UI', Arial, sans-serif; }
  .pulse { animation: pulse 1.6s ease-in-out infinite; }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.45; } }
  .flow { stroke-dasharray: 7 6; animation: flow 1.2s linear infinite; }
  @keyframes flow { to { stroke-dashoffset: -26; } }
"""


def build_board_svg(status: Mapping[str, Any] | None, *, per_row: int = 5) -> str:
    """Node-Diagramm des Laufs: ein Knoten je Step, Zustand farblich (§5a Punkt 2).

    Selbststaendiges SVG (inkl. Pulsier-/Fluss-Animation per CSS) — wird als
    data-URL in ein Taipy-``image``-Element gebunden und rendert damit auch in
    einem ``<img>``-Kontext ohne Skripte.
    """

    steps = list(status.get("steps", [])) if status else []
    if not steps:
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="90">'
            f"<style>{_SVG_STYLE}</style>"
            '<rect x="1" y="1" width="638" height="88" rx="14" fill="#0a1020"'
            ' stroke="#28375f"/>'
            '<text x="320" y="50" text-anchor="middle" fill="#7484ad"'
            ' font-size="14">Kein Lauf ausgewaehlt</text></svg>'
        )

    node_w, node_h = 178, 64
    hgap, vgap = 46, 56
    margin = 18
    per_row = max(1, per_row)
    rows = (len(steps) + per_row - 1) // per_row
    cols = min(per_row, len(steps))
    width = margin * 2 + cols * node_w + (cols - 1) * hgap
    height = margin * 2 + rows * node_h + (rows - 1) * vgap

    def position(index: int) -> tuple[float, float]:
        row, col = divmod(index, per_row)
        x = margin + col * (node_w + hgap)
        y = margin + row * (node_h + vgap)
        return x, y

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"'
        f' viewBox="0 0 {width} {height}">',
        f"<style>{_SVG_STYLE}</style>",
        "<defs>"
        '<filter id="glow" x="-60%" y="-60%" width="220%" height="220%">'
        '<feDropShadow dx="0" dy="0" stdDeviation="6" flood-color="#4cc9f0"'
        ' flood-opacity="0.55"/></filter>'
        '<filter id="glowdone" x="-60%" y="-60%" width="220%" height="220%">'
        '<feDropShadow dx="0" dy="0" stdDeviation="4" flood-color="#2ee6a8"'
        ' flood-opacity="0.35"/></filter>'
        "</defs>",
    ]

    # Verbindungslinien zuerst (liegen unter den Knoten).
    for index in range(len(steps) - 1):
        x1, y1 = position(index)
        x2, y2 = position(index + 1)
        target_state = str(steps[index + 1].get("state", "pending"))
        source_state = str(steps[index].get("state", "pending"))
        active = target_state == "running"
        travelled = source_state in {"done", "failed", "aborted", "skipped"}
        color = "#4cc9f0" if active else ("#2ee6a8" if travelled else "#28375f")
        klass = ' class="flow"' if active else ""
        if abs(y1 - y2) < 1:  # gleiche Zeile: sanft geschwungene Linie
            sx, sy = x1 + node_w, y1 + node_h / 2
            ex, ey = x2, y2 + node_h / 2
            mid = (sx + ex) / 2
            path = f"M {sx} {sy} C {mid} {sy - 14}, {mid} {ey + 14}, {ex} {ey}"
        else:  # Zeilenumbruch: abgerundeter Bogen nach unten zur naechsten Zeile
            sx, sy = x1 + node_w / 2, y1 + node_h
            ex, ey = x2 + node_w / 2, y2
            midy = (sy + ey) / 2
            path = f"M {sx} {sy} C {sx} {midy}, {ex} {midy}, {ex} {ey}"
        parts.append(
            f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2"'
            f' stroke-linecap="round"{klass}/>'
        )
        parts.append(f'<circle cx="{ex}" cy="{ey}" r="3" fill="{color}"/>')

    for index, step in enumerate(steps):
        x, y = position(index)
        state_name = str(step.get("state", "pending"))
        fill, stroke, text_color, label = _NODE_STYLE.get(
            state_name, _NODE_STYLE["pending"]
        )
        glow = ""
        klass = ""
        if state_name == "running":
            glow = ' filter="url(#glow)"'
            klass = ' class="pulse"'
        elif state_name == "done":
            glow = ' filter="url(#glowdone)"'
        dash = ' stroke-dasharray="5 4"' if state_name == "skipped" else ""
        name = _html.escape(str(step.get("step", "?")))
        detail = label
        if step.get("started_at"):
            detail += " · " + duration_str(step.get("started_at"), step.get("finished_at"))
        cost = float(step.get("cost_usd") or 0.0)
        cost_line = f"{cost:.4f} USD" if cost > 0 else ""
        parts.append(
            f'<g{klass}>'
            f'<rect x="{x}" y="{y}" width="{node_w}" height="{node_h}" rx="14"'
            f' fill="{fill}" stroke="{stroke}" stroke-width="2"{dash}{glow}/>'
            f'<circle cx="{x + 18}" cy="{y + 22}" r="5" fill="{stroke}"/>'
            f'<text x="{x + 32}" y="{y + 27}" fill="#eef3ff" font-size="13.5"'
            f' font-weight="600">{name}</text>'
            f'<text x="{x + 32}" y="{y + 46}" fill="{text_color}"'
            f' font-size="11">{_html.escape(detail)}</text>'
            + (
                f'<text x="{x + node_w - 10}" y="{y + 46}" fill="{text_color}"'
                f' font-size="10" text-anchor="end">{cost_line}</text>'
                if cost_line
                else ""
            )
            + "</g>"
        )

    parts.append("</svg>")
    return "".join(parts)


def board_data_url(status: Mapping[str, Any] | None, *, per_row: int = 5) -> str:
    """SVG des Boards als base64-data-URL fuer das Taipy-``image``-Element."""

    svg = build_board_svg(status, per_row=per_row)
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


# -- Trade-Zwischenlager (§5a Punkt 5) -----------------------------------------


def staging_dir(settings: Mapping[str, Any]) -> Path:
    return Path(settings["STAGING_DIR"])


def staged_dir(settings: Mapping[str, Any]) -> Path:
    return Path(settings["DATA_DIR"]) / str(
        settings.get("STAGED_TRADES_DIRNAME", "staged_trades")
    )


def _csv_files_df(directory: Path) -> pd.DataFrame:
    columns = ["Datei", "Geaendert", "Zeilen"]
    if not directory.is_dir():
        return pd.DataFrame(columns=columns)
    rows = []
    for path in sorted(directory.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            with path.open(encoding="utf-8", errors="replace") as handle:
                line_count = max(0, sum(1 for _ in handle) - 1)
        except OSError:
            line_count = -1
        rows.append(
            {
                "Datei": path.name,
                "Geaendert": datetime.fromtimestamp(path.stat().st_mtime).strftime(
                    "%Y-%m-%d %H:%M"
                ),
                "Zeilen": line_count,
            }
        )
    return pd.DataFrame(rows, columns=columns)


def staging_files_df(settings: Mapping[str, Any]) -> pd.DataFrame:
    """Export-Dateien der Haupt-App im variantenuebergreifenden Zwischenlager."""

    return _csv_files_df(staging_dir(settings))


def staged_files_df(settings: Mapping[str, Any]) -> pd.DataFrame:
    """Bereits fuer den naechsten Lauf uebernommene Dateien (aktive Auswahl)."""

    return _csv_files_df(staged_dir(settings))


def preview_csv(settings: Mapping[str, Any], path: Path) -> pd.DataFrame:
    """CSV-Vorschau (gekappt auf ``UI_PREVIEW_ROWS``)."""

    limit = int(settings.get("UI_PREVIEW_ROWS", 200))
    try:
        return pd.read_csv(path, dtype=str, keep_default_na=False, nrows=limit)
    except Exception as exc:
        logger.warning("CSV-Vorschau fuer %s fehlgeschlagen: %s", path, exc)
        return pd.DataFrame({"Fehler": [str(exc)]})


def adopt_staging_file(settings: Mapping[str, Any], filename: str) -> Path:
    """Kopiert einen Zwischenlager-Export nach ``staged_trades/`` (Uebernahme)."""

    source = staging_dir(settings) / filename
    if not source.is_file():
        raise FileNotFoundError(f"Export-Datei nicht gefunden: {source}")
    target_dir = staged_dir(settings)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / source.name
    shutil.copy2(source, target)
    return target


def remove_staged_file(settings: Mapping[str, Any], filename: str) -> bool:
    """Entfernt eine Datei aus der aktiven Auswahl."""

    path = staged_dir(settings) / filename
    if path.is_file():
        path.unlink()
        return True
    return False


def active_selection_summary(settings: Mapping[str, Any]) -> str:
    """Kurzfassung der aktiven Auswahl (dedupliziert per Staging-Reader)."""

    try:
        from .. import trades_access

        frame = trades_access.read_staging_trades(settings)
    except Exception as exc:
        return f"Aktive Auswahl nicht lesbar: {exc}"
    if frame.empty:
        return "Keine aktive Trade-Auswahl uebernommen."
    tickers = sorted(
        {
            t.strip().upper()
            for t in frame.get("ticker", pd.Series(dtype=str)).astype(str)
            if t.strip()
        }
    )
    members = frame.get("name", pd.Series(dtype=str)).astype(str).nunique()
    return (
        f"{len(frame)} Trades (dedupliziert) · {members} Member · "
        f"{len(tickers)} Ticker: {', '.join(tickers[:12])}"
        + (" ..." if len(tickers) > 12 else "")
    )


# -- Event- & Dossier-Browser (§5a Punkt 10) -----------------------------------


def events_path(settings: Mapping[str, Any]) -> Path:
    return Path(settings["DATA_DIR"]) / str(settings.get("EVENTS_FILENAME", "events.jsonl"))


def load_events_df(settings: Mapping[str, Any]) -> pd.DataFrame:
    """events.jsonl als DataFrame (juengste Updates zuerst)."""

    columns = [
        "event_id",
        "status",
        "event_class",
        "source",
        "title",
        "event_date",
        "update_date",
        "expected_outcome_date",
        "actual_outcome_date",
        "first_seen",
        "last_updated",
        "url",
    ]
    path = events_path(settings)
    if not path.is_file():
        return pd.DataFrame(columns=columns)
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            rows.append({key: record.get(key) for key in columns})
    frame = pd.DataFrame(rows, columns=columns)
    if not frame.empty:
        frame = frame.sort_values("last_updated", ascending=False, kind="stable")
    return frame.reset_index(drop=True)


def filter_events(
    frame: pd.DataFrame,
    *,
    status: str = "alle",
    event_class: str = "alle",
    source: str = "alle",
    search: str = "",
) -> pd.DataFrame:
    """Filtert die Event-Tabelle nach Status, Klasse, Quelle und Freitext."""

    result = frame
    if status and status != "alle":
        result = result[result["status"] == status]
    if event_class and event_class != "alle":
        result = result[result["event_class"] == event_class]
    if source and source != "alle":
        result = result[result["source"] == source]
    if search and search.strip():
        needle = search.strip().lower()
        mask = (
            result["title"].astype(str).str.lower().str.contains(needle, regex=False)
            | result["event_id"].astype(str).str.lower().str.contains(needle, regex=False)
        )
        result = result[mask]
    return result.reset_index(drop=True)


def outputs_root(settings: Mapping[str, Any]) -> Path:
    return Path(settings["DATA_DIR"]) / str(settings.get("OUTPUTS_DIRNAME", "outputs"))


def dossier_files(settings: Mapping[str, Any]) -> list[str]:
    """Alle Markdown-Dokumente unter outputs/ (relativ zur DATA_DIR).

    Deckt Enddokumente UND kuenftige Dossier-Fallakten ab; Empfehlungen
    verlinken ueber ``dossier_path`` auf dieselben relativen Pfade.
    """

    root = outputs_root(settings)
    data_dir = Path(settings["DATA_DIR"])
    if not root.is_dir():
        return []
    return sorted(
        str(path.relative_to(data_dir))
        for path in root.rglob("*.md")
        if path.is_file()
    )


def recommendations_df(settings: Mapping[str, Any]) -> pd.DataFrame:
    """recommendations.csv als Tabelle (leer, falls noch kein Ledger)."""

    path = Path(settings["DATA_DIR"]) / str(
        settings.get("RECOMMENDATIONS_FILENAME", "recommendations.csv")
    )
    if not path.is_file():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, dtype=str, keep_default_na=False)
    except Exception as exc:
        logger.warning("recommendations.csv nicht lesbar: %s", exc)
        return pd.DataFrame({"Fehler": [str(exc)]})


# -- User-Hinweise (§5a Punkt 4) ------------------------------------------------


def hints_df(settings: Mapping[str, Any], *, include_archived: bool = True) -> pd.DataFrame:
    """Alle Hinweise als Tabelle (offen und konsumiert; archivierte optional)."""

    columns = ["hint_id", "created_at", "text", "ticker", "topic", "status"]
    rows = []
    for hint in hints.load_hints(settings):
        if hint.get("archived") and not include_archived:
            continue
        if hint.get("archived"):
            status = "archiviert"
        elif hint.get("consumed_by_run"):
            status = f"konsumiert ({hint.get('consumed_by_run')})"
        else:
            status = "offen"
        rows.append(
            {
                "hint_id": hint.get("hint_id"),
                "created_at": fmt_ts(hint.get("created_at")),
                "text": hint.get("text"),
                "ticker": hint.get("ticker") or "",
                "topic": hint.get("topic") or "",
                "status": status,
            }
        )
    return pd.DataFrame(rows, columns=columns)


# -- Kosten (§5a Punkt 6) --------------------------------------------------------


def cost_history(settings: Mapping[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Historie ueber alle Runs: (LLM je Rolle/Modell, Tavily je Rolle/Endpunkt)."""

    directory = runctl.runs_dir(settings)
    role_rows: dict[tuple[str, str], dict[str, Any]] = {}
    search_rows: dict[tuple[str, str], dict[str, Any]] = {}
    if directory.is_dir():
        for summary in directory.glob("*.json"):
            if summary.name.endswith(".status.json"):
                continue
            try:
                payload = json.loads(summary.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for key, bucket in (payload.get("usage_by_role_model") or {}).items():
                role, _, model = str(key).partition("|")
                target = role_rows.setdefault(
                    (role, model),
                    {"Rolle": role, "Modell": model, "Calls": 0, "USD": 0.0},
                )
                target["Calls"] += int(bucket.get("calls", 0) or 0)
                target["USD"] += float(bucket.get("usd", 0.0) or 0.0)
        for jsonl in directory.glob("*.search_usage.jsonl"):
            for row in runctl._read_jsonl(jsonl):
                role = str(row.get("role") or "?")
                endpoint = str(row.get("endpoint") or "?")
                target = search_rows.setdefault(
                    (role, endpoint),
                    {"Rolle": role, "Endpunkt": endpoint, "Calls": 0, "Credits": 0.0},
                )
                target["Calls"] += 1
                try:
                    target["Credits"] += float(row.get("credits") or 0.0)
                except (TypeError, ValueError):
                    pass
    llm_df = pd.DataFrame(
        sorted(role_rows.values(), key=lambda r: -r["USD"]),
        columns=["Rolle", "Modell", "Calls", "USD"],
    )
    if not llm_df.empty:
        llm_df["USD"] = llm_df["USD"].round(4)
    search_df = pd.DataFrame(
        sorted(search_rows.values(), key=lambda r: -r["Credits"]),
        columns=["Rolle", "Endpunkt", "Calls", "Credits"],
    )
    return llm_df, search_df


def query_tavily_usage(settings: Mapping[str, Any]) -> str:
    """Fragt den Monats-Budget-Stand via ``GET /usage`` ab (NUR auf User-Klick).

    Dies ist der einzige Live-Aufruf des Control-UI; er kostet keine Credits,
    erfordert aber einen gueltigen ``TAVILY_API_KEY``.
    """

    try:
        from ..search_api import TavilySearchAPI

        client = TavilySearchAPI(run_id="ui-usage", role="control_ui", settings=settings)
        usage = client.get_usage()
    except Exception as exc:
        return f"Abfrage fehlgeschlagen: {exc}"
    budget = int(settings.get("TAVILY", {}).get("monthly_credit_budget", 0))
    reserve = int(settings.get("TAVILY", {}).get("credit_reserve", 0))
    return (
        f"Tavily /usage: {json.dumps(usage, ensure_ascii=False)[:600]} · "
        f"Monats-Budget (Config): {budget} · Reserve: {reserve}"
    )


# -- Konfigurations-Editor (§5a Punkt 7, §5d) ------------------------------------

# Gruppierung der Config-Schluessel fuer das Formular; nicht gelistete Schluessel
# landen automatisch in "Sonstiges" (Vorwaertskompatibilitaet bei neuen Keys).
CONFIG_GROUPS: list[tuple[str, list[str]]] = [
    (
        "LLM",
        [
            "LLM_NUM_RETRIES",
            "LLM_TIMEOUT_SECONDS",
            "LLM_FALLBACK_ENABLED",
            "OPENROUTER_MODEL_MAP",
            "COST_CAP_ENABLED",
            "COST_CAP_USD_PER_RUN",
            "AGENT_SPECS",
        ],
    ),
    (
        "Preise",
        ["PRICING", "PRICING_PERIODS", "PRICING_TOKEN_TIERS"],
    ),
    (
        "Search & Tavily",
        ["SEARCH_PROVIDER", "SEARCH_API", "TAVILY", "SEARCH_MAX_RETRIES"],
    ),
    (
        "Ingestion & Registry",
        [
            "INGEST_LOOKBACK_DAYS",
            "INGEST_SOURCES_ENABLED",
            "EVENT_CLOSE_AFTER_DAYS",
            "PROCUREMENT_AWARD_TIMEOUT_DAYS",
            "WAR_GOV_MIN_CONTRACT_USD",
            "FEDREG_DOC_TYPES",
            "INGEST_TIMEOUT_SECONDS",
            "FEDERAL_REGISTER_API_URL",
            "FEDERAL_REGISTER_PER_PAGE",
            "WAR_GOV_RSS_URL",
            "SAM_GOV_API_URL",
            "SAM_GOV_LIMIT",
            "EVENTS_FILENAME",
        ],
    ),
    (
        "Trades & Bewertung",
        [
            "TRADE_SOURCE",
            "TRADE_DATE_FIELD",
            "TRADE_WINDOW_DAYS",
            "EVAL_MODE",
            "EVAL_OUTCOME_BUFFER_DAYS",
            "EVAL_MAX_DAYS",
            "EVAL_HORIZON_DAYS",
            "USER_HINTS_ENABLED",
        ],
    ),
    (
        "Reporting & Dateinamen",
        [
            "SUMMARY_MAX_TOKENS",
            "PROMPT_DIR",
            "PROMPT_OVERRIDES_DIRNAME",
            "STAGED_TRADES_DIRNAME",
            "RUNS_DIRNAME",
            "OUTPUTS_DIRNAME",
            "USER_HINTS_DIRNAME",
            "HINTS_FILENAME",
            "RECOMMENDATIONS_FILENAME",
            "RECOMMENDATION_DOC_FILENAME",
            "REPORT_DOC_FILENAME",
        ],
    ),
    (
        "Smoke & Cache-Check",
        [
            "SMOKE_MODELS",
            "SMOKE_MAX_OUTPUT_TOKENS",
            "SMOKE_SEARCH_QUERY",
            "SMOKE_SEARCH_MAX_RESULTS",
            "SMOKE_SEARCH_LOOKBACK_DAYS",
            "CACHE_CHECK_MODEL",
            "CACHE_CHECK_MAX_OUTPUT_TOKENS",
            "CACHE_CHECK_TOOL_MAX_OUTPUT_TOKENS",
            "CACHE_CHECK_SYSTEM_TOKENS",
            "CACHE_CHECK_PROVIDER_TO_LOCAL_TOKEN_RATIO",
            "CACHE_CHECK_REQUIRED_TOOL_TURNS",
        ],
    ),
    (
        "Control-UI",
        [
            "STATUS_POLL_SECONDS",
            "STOP_GRACE_SECONDS",
            "UI_PORTS",
            "UI_HOST",
            "UI_DARK_MODE",
            "UI_RUN_HISTORY_LIMIT",
            "UI_LOG_TAIL_LINES",
            "UI_PREVIEW_ROWS",
            "UI_START_COMMANDS",
            "DUMMY_RUN_STEPS",
            "DUMMY_RUN_SECONDS_PER_STEP",
        ],
    ),
]


def _config_defaults() -> dict[str, Any]:
    return {key: value for key, value in vars(config).items() if key.isupper()}


def editable_config_keys() -> list[tuple[str, str]]:
    """(Gruppe, Schluessel) aller editierbaren Keys in Formular-Reihenfolge.

    Pfad-Konstanten (``Path``-Defaults) sind bewusst NICHT editierbar — sie
    leiten sich aus PROJECT_ROOT/DATA_DIR ab (Relativ-Regel) und wuerden im
    JSON-Override am Typ-Check scheitern.
    """

    defaults = _config_defaults()
    grouped: list[tuple[str, str]] = []
    listed: set[str] = set()
    for group, keys in CONFIG_GROUPS:
        for key in keys:
            if key in defaults and not isinstance(defaults[key], Path):
                grouped.append((group, key))
                listed.add(key)
    for key in sorted(defaults):
        if key in listed or isinstance(defaults[key], Path):
            continue
        grouped.append(("Sonstiges", key))
    return grouped


def path_config_df() -> pd.DataFrame:
    """Read-only Ansicht der abgeleiteten Pfad-Konstanten."""

    rows = [
        {"Schluessel": key, "Pfad": str(value)}
        for key, value in sorted(_config_defaults().items())
        if isinstance(value, Path)
    ]
    return pd.DataFrame(rows, columns=["Schluessel", "Pfad"])


def config_value_to_text(value: Any) -> str:
    """Default-/Override-Wert als editierbarer JSON-Text."""

    if isinstance(value, str):
        return value
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True)


def parse_config_text(key: str, text: str) -> Any:
    """Parst einen Editor-Text zurueck in den Typ des Defaults.

    Strings werden roh uebernommen; alle anderen Typen via JSON. Ein
    Parse-/Typ-Fehler wirft ``ValueError`` mit lesbarer Meldung.
    """

    default = _config_defaults().get(key)
    if isinstance(default, str):
        return text
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Ungueltiges JSON fuer {key}: {exc}") from exc
    if default is not None and not config._types_compatible(default, value):
        raise ValueError(
            f"Typ-Konflikt fuer {key}: erwartet {type(default).__name__}, "
            f"erhalten {type(value).__name__}."
        )
    return value


def current_overrides() -> dict[str, Any]:
    return config.read_overrides()


def set_override(key: str, value: Any) -> None:
    """Setzt genau EINEN Override (nur Abweichungen, atomar, §5d)."""

    defaults = _config_defaults()
    overrides = config.read_overrides()
    if key in defaults and defaults[key] == value:
        overrides.pop(key, None)
    else:
        overrides[key] = value
    config.write_overrides(overrides)
    config.effective(reload=True)


def reset_override(key: str) -> None:
    """Entfernt einen Override-Schluessel (Reset auf Default)."""

    overrides = config.read_overrides()
    if key in overrides:
        overrides.pop(key)
        config.write_overrides(overrides)
        config.effective(reload=True)


def set_source_enabled(source: str, enabled: bool) -> None:
    """Quellen-Schalter (§5a Punkt 1): Deep-Merge-Override nur fuer diese Quelle."""

    defaults = _config_defaults().get("INGEST_SOURCES_ENABLED", {})
    overrides = config.read_overrides()
    block = dict(overrides.get("INGEST_SOURCES_ENABLED", {}))
    if defaults.get(source) == enabled:
        block.pop(source, None)
    else:
        block[source] = enabled
    if block:
        overrides["INGEST_SOURCES_ENABLED"] = block
    else:
        overrides.pop("INGEST_SOURCES_ENABLED", None)
    config.write_overrides(overrides)
    config.effective(reload=True)


def disable_all_sources() -> None:
    """"Alle Quellen deaktivieren"-Knopf (User-Entscheid 2026-07-10)."""

    overrides = config.read_overrides()
    defaults = _config_defaults().get("INGEST_SOURCES_ENABLED", {})
    overrides["INGEST_SOURCES_ENABLED"] = {source: False for source in defaults}
    config.write_overrides(overrides)
    config.effective(reload=True)


# -- Prompt-Editor (§5a Punkt 9) --------------------------------------------------


def prompt_roles(settings: Mapping[str, Any]) -> list[str]:
    return prompts.list_prompt_roles(settings)


def prompt_info(role: str, settings: Mapping[str, Any]) -> dict[str, Any]:
    """Editor-Zustand einer Rolle: Default-Text, effektiver Text, Override-Flag."""

    try:
        effective_text = prompts.load_prompt(role, settings)
    except FileNotFoundError:
        effective_text = ""
    return {
        "role": role,
        "default_text": prompts.load_default_prompt(role, settings),
        "effective_text": effective_text,
        "has_override": prompts.has_override(role, settings),
    }
