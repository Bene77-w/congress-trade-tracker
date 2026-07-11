"""Seite ``events``: Event- & Dossier-Browser (§5a Block 10).

(a) Durchsuch- und filterbare Ansicht der Ereignisliste ``events.jsonl``
(Status, Event-Klasse, Quelle, Zeitraum-Filter auf ``first_seen`` fuer frisch
ingestete Events) mit Detail-Ansicht inkl. Quell-Link. (b) Alle Dossiers/
Output-Dokumente gerendert einsehbar; ``recommendations.csv`` verlinkt ueber
``dossier_path`` auf dieselben Dokumente.
"""

from __future__ import annotations

import json
import logging

import pandas as pd
import taipy.gui.builder as tgb

from .. import state as st

logger = logging.getLogger(__name__)

_EVENT_COLUMNS = [
    "event_id",
    "status",
    "event_class",
    "source",
    "title",
    "event_date",
    "expected_outcome_date",
    "actual_outcome_date",
    "first_seen",
]

ev_all_df = pd.DataFrame(columns=_EVENT_COLUMNS)
ev_df = pd.DataFrame(columns=_EVENT_COLUMNS)
ev_status = "alle"
ev_status_options = ["alle", "open", "outcome_known", "closed", "expired"]
ev_class = "alle"
ev_class_options = ["alle"]
ev_source = "alle"
ev_source_options = ["alle"]
ev_fresh = "alle"
ev_fresh_options = ["alle", "letzte 7 Tage", "letzte 30 Tage"]
ev_search = ""
ev_detail_md = "Kein Event ausgewaehlt."
ev_count_label = "0 Events"

dos_options: list[str] = []
dos_selected = ""
dos_doc_url = ""
dos_doc_visible = False
rec_df = pd.DataFrame()


def _apply_filters(s) -> None:
    frame = s.ev_all_df
    filtered = st.filter_events(
        frame,
        status=s.ev_status,
        event_class=s.ev_class,
        source=s.ev_source,
        search=s.ev_search,
    )
    if s.ev_fresh != "alle" and not filtered.empty:
        days = 7 if "7" in s.ev_fresh else 30
        cutoff = pd.Timestamp.now() - pd.Timedelta(days=days)
        seen = pd.to_datetime(filtered["first_seen"], errors="coerce")
        filtered = filtered[seen >= cutoff].reset_index(drop=True)
    with s as ss:
        ss.ev_df = filtered[_EVENT_COLUMNS] if not filtered.empty else pd.DataFrame(
            columns=_EVENT_COLUMNS
        )
        ss.ev_count_label = f"{len(filtered)} von {len(frame)} Events"


def refresh(s) -> None:
    settings = st.effective_settings()
    frame = st.load_events_df(settings)
    classes = ["alle"] + sorted(
        {str(v) for v in frame.get("event_class", pd.Series(dtype=str)).dropna()}
    )
    sources = ["alle"] + sorted(
        {str(v) for v in frame.get("source", pd.Series(dtype=str)).dropna()}
    )
    with s as ss:
        ss.ev_all_df = frame
        ss.ev_class_options = classes
        ss.ev_source_options = sources
        ss.rec_df = st.recommendations_df(settings)
        ss.dos_options = st.dossier_files(settings)
        if ss.dos_selected not in ss.dos_options:
            ss.dos_selected = ss.dos_options[0] if ss.dos_options else ""
    _apply_filters(s)
    _show_dossier(s, s.dos_selected)


def on_filter_change(s, var_name=None, value=None) -> None:
    _apply_filters(s)


def on_event_click(s, var_name=None, payload=None) -> None:
    index = (payload or {}).get("index")
    if index is None:
        return
    try:
        row = s.ev_df.iloc[int(index)]
    except (IndexError, ValueError):
        return
    event_id = str(row["event_id"])
    full = s.ev_all_df[s.ev_all_df["event_id"] == event_id]
    record = full.iloc[0].to_dict() if not full.empty else row.to_dict()
    url = record.get("url")
    lines = [f"### {record.get('title') or event_id}", ""]
    for key in (
        "event_id",
        "status",
        "event_class",
        "source",
        "event_date",
        "update_date",
        "expected_outcome_date",
        "actual_outcome_date",
        "first_seen",
        "last_updated",
    ):
        lines.append(f"- **{key}**: {record.get(key) if record.get(key) is not None else '-'}")
    if url:
        lines.append(f"- **Quell-Link**: [{url}]({url})")
    with s as ss:
        ss.ev_detail_md = "\n".join(lines)


def _show_dossier(s, rel_path: str) -> None:
    with s as ss:
        if rel_path:
            ss.dos_doc_url = st.doc_url(rel_path)
            ss.dos_doc_visible = True
        else:
            ss.dos_doc_url = ""
            ss.dos_doc_visible = False


def on_dossier_selected(s, var_name=None, value=None) -> None:
    _show_dossier(s, s.dos_selected)


with tgb.Page() as page:
    with tgb.part(class_name="st-panel"):
        tgb.text("## Event-Browser (events.jsonl)", mode="md")
        with tgb.layout(columns="1 1 1 1 2"):
            tgb.selector(
                value="{ev_status}", lov="{ev_status_options}", dropdown=True,
                label="Status", on_change=on_filter_change, class_name="fullwidth",
            )
            tgb.selector(
                value="{ev_class}", lov="{ev_class_options}", dropdown=True,
                label="Event-Klasse", on_change=on_filter_change, class_name="fullwidth",
            )
            tgb.selector(
                value="{ev_source}", lov="{ev_source_options}", dropdown=True,
                label="Quelle", on_change=on_filter_change, class_name="fullwidth",
            )
            tgb.selector(
                value="{ev_fresh}", lov="{ev_fresh_options}", dropdown=True,
                label="Frisch (first_seen)", on_change=on_filter_change,
                class_name="fullwidth",
            )
            tgb.input(
                value="{ev_search}", label="Suche in Titel/ID",
                on_change=on_filter_change, class_name="fullwidth",
            )
        tgb.text("{ev_count_label}", class_name="st-muted")
        tgb.table(
            data="{ev_df}", page_size=15, on_action=on_event_click,
            rebuild=True, filter=True, class_name="st-table",
        )
        with tgb.part(class_name="st-detail"):
            tgb.text("{ev_detail_md}", mode="md")

    with tgb.part(class_name="st-panel"):
        tgb.text("## Dossier- & Dokument-Browser", mode="md")
        tgb.selector(
            value="{dos_selected}", lov="{dos_options}", dropdown=True,
            label="Dokument (outputs/)", on_change=on_dossier_selected,
            class_name="fullwidth",
        )
        with tgb.part(render="{dos_doc_visible}"):
            tgb.part(page="{dos_doc_url}", height="60vh", class_name="st-frame")
        with tgb.part(render="{not dos_doc_visible}"):
            tgb.text("Noch keine Dokumente unter outputs/ vorhanden.", class_name="st-muted")
        with tgb.expandable(title="Empfehlungs-Ledger (recommendations.csv)", expanded=False):
            tgb.table(data="{rec_df}", page_size=15, rebuild=True, class_name="st-table")
