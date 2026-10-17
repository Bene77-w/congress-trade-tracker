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


def build_recent_pool(
    df: pd.DataFrame,
    window_days: int,
    *,
    reference_date=None,
    date_col: str = 'transaction_date',
) -> pd.DataFrame:
    """Recent-Trades-Subset fuer das Monitoring (rein, Streamlit-unabhaengig).

    Behaelt Zeilen mit ``date_col >= reference_date - window_days`` und ergaenzt
    die abgeleitete Spalte ``days_since_trade`` (= Tage seit dem Trade, Bezug
    ``reference_date``). Gegensatz zum Horizont-Filter: OFFENE Positionen
    (``first_close_offset_days`` = NaN) bleiben sichtbar — hier wird allein nach
    Handelsdatum gefiltert, NICHT nach Exit.

    ``reference_date`` Standard = heute (normalisiert). Zeilen mit fehlendem
    Datum fallen raus. Liefert eine Kopie; Sortierung/Top-N macht der Aufrufer.
    """
    if reference_date is None:
        reference_date = pd.Timestamp.now().normalize()
    else:
        reference_date = pd.Timestamp(reference_date).normalize()

    # Erst filtern, dann kopieren: die Vollkopie vorab waere bei 114k Zeilen
    # rund 80 MB, von denen das Fenster meist nur einen Bruchteil behaelt.
    if date_col not in df.columns:
        out = df.iloc[0:0].copy()
        out['days_since_trade'] = pd.Series(dtype='float64')
        return out

    dates = pd.to_datetime(df[date_col], errors='coerce')
    cutoff = reference_date - pd.Timedelta(days=window_days)
    mask = dates.notna() & (dates >= cutoff)
    out = df[mask].copy()
    out['days_since_trade'] = (
        reference_date - pd.to_datetime(out[date_col], errors='coerce')
    ).dt.days
    return out


# Welche Rendite-Spalte je Exit-Status die ehrliche Zahl ist. Geschlossene
# Positionen -> realisiert; offene -> Mark-to-Market. Nicht gelistete Status
# (delisted / not_applicable) bleiben bewusst leer statt eine Zahl zu erfinden.
COMBINED_RETURN_SOURCES: dict[str, str] = {
    'closed_full': 'effective_return_pct',
    'closed_partial': 'effective_return_pct',
    'held': 'paper_return_pct',
}


def add_combined_return(
    df: pd.DataFrame,
    *,
    value_col: str = 'combined_return_pct',
    kind_col: str = 'return_kind',
) -> pd.DataFrame:
    """Ergaenzt eine ueber offene UND geschlossene Trades sortierbare Rendite.

    Problem ohne diese Spalte: ``effective_return_pct`` ist nur bei
    geschlossenen Positionen gefuellt, ``paper_return_pct`` sinnvoll nur bei
    offenen — eine Sortierung nach „erfolgreichste Trades" ueber beide Gruppen
    hinweg ist deshalb mit keiner der beiden Spalten allein moeglich.

    Je Zeile wird nach ``exit_status`` die passende Quelle gezogen
    (s. ``COMBINED_RETURN_SOURCES``); ``kind_col`` haelt die Herkunft als
    Klartext ('realisiert' / 'laufend'), damit in der Tabelle sichtbar bleibt,
    welche der beiden Bedeutungen der Wert hat. Zeilen ohne passende Quelle
    (delisted, not_applicable, fehlender Kurs) bleiben NaN.

    Liefert eine Kopie; fehlende Quell-Spalten werden toleriert.
    """
    out = df.copy()
    value = pd.Series(float('nan'), index=out.index, dtype='float64')
    kind = pd.Series(pd.NA, index=out.index, dtype='string')
    if 'exit_status' not in out.columns:
        out[value_col] = value
        out[kind_col] = kind
        return out

    status = out['exit_status'].astype('string')
    for status_name, src in COMBINED_RETURN_SOURCES.items():
        if src not in out.columns:
            continue
        rows = status == status_name
        if not rows.any():
            continue
        vals = pd.to_numeric(out.loc[rows, src], errors='coerce')
        value.loc[rows] = vals
        kind.loc[rows & vals.notna().reindex(out.index, fill_value=False)] = (
            'realisiert' if src == 'effective_return_pct' else 'laufend'
        )

    out[value_col] = value
    out[kind_col] = kind
    return out


def build_partial_close_rows(opening: dict, events: list, sell_sign: float) -> list:
    """Zerlegt einen Eroeffnungs-Trade in eine Zeile pro Schliessungs-Event.

    Reine Display-Zerlegung (NICHT additiv, NICHT in den Master): pro
    gegensaetzlichem Trade (= Schliessungs-Event) eine eigene Zeile mit ihrem
    eigenen realisierten Return. Ein Voll-Close ohne Teilverkaeufe = genau EINE
    Zeile (wie ein einzelnes Event).

    ``opening`` Dict mit:
        ``trade_id`` (Parent-Hash), ``transaction_date`` (Eroeffnung),
        ``baseline_price`` (Einstands-Naeherung), ``volume`` (Eroeffnungs-USD).
    ``events`` iterierbar ueber Dicts (Gegen-Trades, CHRONOLOGISCH) mit:
        ``trade_id``, ``date``, ``volume``, ``price`` (Kurs am Event).
    ``sell_sign`` (+1 Kauf-Eroeffnung / -1 Verkauf-Eroeffnung) — vom
    Eroeffnungs-Trade geerbt; richtungsbewusster Return.

    Pro Event eine Zeile:
        ``trade_id`` = f"{parent}-pc{n}" (n = laufende Nummer EMITTIERTER Zeilen),
        ``parent_trade_id``, ``transaction_date`` (= Eroeffnung), ``close_date``,
        ``exit_volume`` (auf verbleibendes Eroeffnungs-Volumen gedeckelt),
        ``close_offset_days``, ``effective_return_pct``
        = (price/baseline - 1) * 100 * sell_sign.
    Events mit fehlendem ``price`` (oder ungueltigem ``baseline``) werden
    uebersprungen und zaehlen NICHT in der ``-pc{n}``-Nummerierung.

    Limitierung (Lite / non-FIFO): derselbe Verkauf kann bei mehreren Kaeufen
    als Gegen-Trade auftauchen — Volumina partitionieren global NICHT sauber.
    Die ``exit_volume``-Deckelung haelt nur die Summe PRO Eroeffnung <=
    Eroeffnungs-Volumen. Daher: Zerlegungs-Zeilen nie in Volumen-Aggregate.
    """
    parent = str(opening.get('trade_id'))
    baseline = opening.get('baseline_price')
    open_date = opening.get('transaction_date')
    open_volume = opening.get('volume')

    if baseline is None or pd.isna(baseline) or baseline <= 0:
        return []
    open_date = pd.to_datetime(open_date, errors='coerce')

    try:
        remaining = float(open_volume)
    except (TypeError, ValueError):
        remaining = None
    if remaining is not None and (pd.isna(remaining) or remaining < 0):
        remaining = None

    rows = []
    n = 0
    for ev in events:
        price = ev.get('price')
        if price is None or pd.isna(price):
            continue
        close_date = pd.to_datetime(ev.get('date'), errors='coerce')

        # Volumen auf verbleibendes Eroeffnungs-Volumen deckeln (per-Kauf-FIFO
        # auf der Verkaufsseite). Unbekanntes Event-Volumen -> NaN (kein Verbrauch).
        ev_vol = ev.get('volume')
        try:
            ev_vol = float(ev_vol)
        except (TypeError, ValueError):
            ev_vol = None
        if ev_vol is not None and pd.isna(ev_vol):
            ev_vol = None
        if ev_vol is None or remaining is None:
            exit_volume = ev_vol
        else:
            exit_volume = min(ev_vol, remaining)
            remaining = max(0.0, remaining - exit_volume)

        if pd.notna(close_date) and pd.notna(open_date):
            offset = float((close_date - open_date).days)
        else:
            offset = float('nan')

        n += 1
        rows.append({
            'trade_id': f"{parent}-pc{n}",
            'parent_trade_id': parent,
            'transaction_date': open_date,
            'close_date': close_date,
            'exit_volume': exit_volume,
            'close_offset_days': offset,
            'effective_return_pct': (float(price) / float(baseline) - 1.0)
                                    * 100.0 * sell_sign,
        })
    return rows
