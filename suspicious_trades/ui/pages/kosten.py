"""Seite ``kosten``: Kosten-Historie ueber alle Runs (§5a Block 6, Historie).

LLM-USD je Rolle/Modell und Tavily-Credits je Rolle/Endpunkt als
Vergleichsbasis des Drei-Wege-Experiments, dazu die Run-Historie. Der
Monats-Budget-Stand (``GET /usage``) wird ausschliesslich auf User-Klick
abgefragt — der einzige Live-Aufruf des Control-UI (kostet keine Credits).
"""

from __future__ import annotations

import logging

import pandas as pd
import taipy.gui.builder as tgb

from .. import state as st

logger = logging.getLogger(__name__)

ko_runs_df = pd.DataFrame()
ko_llm_df = pd.DataFrame(columns=["Rolle", "Modell", "Calls", "USD"])
ko_search_df = pd.DataFrame(columns=["Rolle", "Endpunkt", "Calls", "Credits"])
ko_budget_text = (
    "Monats-Budget-Stand noch nicht abgefragt (GET /usage laeuft nur auf Klick)."
)
ko_totals_label = ""


def refresh(s) -> None:
    settings = st.effective_settings()
    llm_df, search_df = st.cost_history(settings)
    runs_df = st.runs_overview_df(settings)
    total_usd = float(llm_df["USD"].sum()) if not llm_df.empty else 0.0
    total_credits = float(search_df["Credits"].sum()) if not search_df.empty else 0.0
    with s as ss:
        ss.ko_runs_df = runs_df
        ss.ko_llm_df = llm_df
        ss.ko_search_df = search_df
        ss.ko_totals_label = (
            f"Summe ueber alle Runs: {total_usd:.4f} USD LLM · "
            f"{total_credits:.1f} Tavily-Credits"
        )


def on_query_budget(s, id=None, payload=None) -> None:
    settings = st.effective_settings()
    with s as ss:
        ss.ko_budget_text = st.query_tavily_usage(settings)


with tgb.Page() as page:
    with tgb.part(class_name="st-panel"):
        tgb.text("## Kosten-Historie", mode="md")
        tgb.text("{ko_totals_label}", class_name="st-big")
        tgb.table(data="{ko_runs_df}", page_size=12, rebuild=True, class_name="st-table")

    with tgb.layout(columns="1 1"):
        with tgb.part(class_name="st-panel"):
            tgb.text("### LLM-USD je Rolle/Modell", mode="md")
            tgb.table(data="{ko_llm_df}", page_size=12, rebuild=True, class_name="st-table")
        with tgb.part(class_name="st-panel"):
            tgb.text("### Tavily-Credits je Rolle/Endpunkt", mode="md")
            tgb.table(data="{ko_search_df}", page_size=12, rebuild=True, class_name="st-table")

    with tgb.part(class_name="st-panel"):
        tgb.text("## Tavily-Monats-Budget", mode="md")
        tgb.button("Budget-Stand abfragen (GET /usage)", on_action=on_query_budget)
        tgb.text("{ko_budget_text}", class_name="st-muted")
