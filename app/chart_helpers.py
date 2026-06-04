"""Chart-Helfer fuer das Streamlit-Dashboard (Trade-Detail-Kursverlauf).

Reine, Streamlit-unabhaengige Funktionen, damit die Marker-Logik unit-testbar
bleibt — ``app/streamlit_app.py`` fuehrt beim Import Streamlit-Code aus und ist
daher nicht direkt importierbar.

Kernzweck: Teil- vs Vollverkaeufe im Kursverlauf optisch markieren. Die
Klassifikation erfolgt direkt aus ``transaction_type`` (disclosed Realitaet des
Trades) — NICHT aus der abgeleiteten Exit-Detection (``exit_status``), die fuer
Verkaeufe regelmaessig ``held`` liefert und den Marker damit nie ausloeste.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go


# Marker-Stile je transaction_type. Das Symbol traegt den Hauptunterschied
# (Teilverkauf = nach unten offenes Dreieck "Reduktion", Vollverkauf = Kreuz
# "Schluss"), die Farbe verstaerkt ihn.
SALE_KIND_MARKERS: dict[str, dict] = {
    'Sale (Partial)': {
        'label': 'Teilverkauf', 'symbol': 'triangle-down-open',
        'color': '#7c3aed', 'size': 14,
    },
    'Sale (Full)': {
        'label': 'Vollverkauf', 'symbol': 'x',
        'color': 'orange', 'size': 14,
    },
}


def sale_kind_marker(transaction_type) -> dict | None:
    """Marker-Stil (label/symbol/color/size) fuer einen ``transaction_type``.

    Liefert nur fuer Teil-/Vollverkaeufe einen Stil; fuer alle anderen Werte
    (Purchase, Sale ohne Zusatz, Exchange, NaN, …) ``None`` — diese werden
    nicht zusaetzlich markiert. Gibt stets eine frische Kopie zurueck (kein
    Aliasing der Modul-Konstante).
    """
    style = SALE_KIND_MARKERS.get(str(transaction_type))
    return dict(style) if style is not None else None


def build_sale_kind_traces(records) -> list:
    """Plotly-Scatter-Traces fuer Teil-/Vollverkaufs-Marker.

    ``records`` ist iterierbar ueber Dicts mit den Schluesseln ``date``,
    ``price``, ``transaction_type`` und optional ``name`` (fuer den Hover).
    Zeilen ohne Verkaufs-``transaction_type`` oder mit fehlendem Datum/Preis
    werden uebersprungen. Pro vorkommender Verkaufsart wird genau EIN Trace
    erzeugt (deterministische Reihenfolge: Teilverkauf vor Vollverkauf), damit
    die Legende kompakt bleibt. Leere Eingabe -> leere Liste.
    """
    buckets: dict[str, tuple[list, list, list]] = {}
    for rec in records:
        tt = rec.get('transaction_type')
        style = sale_kind_marker(tt)
        if style is None:
            continue
        d = rec.get('date')
        p = rec.get('price')
        if d is None or pd.isna(d) or p is None or pd.isna(p):
            continue
        xs, ys, texts = buckets.setdefault(str(tt), ([], [], []))
        xs.append(pd.to_datetime(d))
        ys.append(p)
        name = rec.get('name')
        texts.append(f"{name} · {style['label']}" if name else style['label'])

    traces = []
    for tt, style in SALE_KIND_MARKERS.items():
        if tt not in buckets:
            continue
        xs, ys, texts = buckets[tt]
        traces.append(go.Scatter(
            x=xs, y=ys, mode='markers',
            name=style['label'], text=texts,
            marker=dict(
                size=style['size'], color=style['color'],
                symbol=style['symbol'],
                line=dict(width=2, color=style['color']),
            ),
            hovertemplate=(
                "<b>%{text}</b><br>%{x|%Y-%m-%d}<br>"
                "Kurs: %{y:.2f} USD<extra></extra>"
            ),
        ))
    return traces
