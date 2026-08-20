"""Laien-Glossar fuer das Streamlit-Dashboard (Single Source of Truth).

Jeder Eintrag ist ein kompakter, fuer Laien verstaendlicher Erklaertext zu
einem Fachbegriff, der im UI angezeigt wird. Verdrahtung ueber den nativen
``help=``-Parameter von ``st.metric`` / ``st.column_config.*`` / Widgets /
``st.subheader`` — d.h. Streamlit rendert daneben sein subtiles, eingekreistes
Info-Icon; Hover/Tap blendet den Text ein.

Konvention: Keys sind term-orientiert (snake_case), NICHT stellen-orientiert —
derselbe Begriff (z.B. ``score`` in mehreren Tabs/Spalten) zieht denselben Text.
Helper ``help_for(key)`` ist tolerant (gibt ``None`` bei unbekanntem Key zurueck),
damit ``help=help_for(...)`` an einer Stelle ohne Eintrag schadlos no-op ist.
"""
from __future__ import annotations

GLOSSARY: dict[str, str] = {
    # ------------------------------------------------------------------ Score
    "score": (
        "Verdachts-Score: buendelt alle ausgeloesten Signale zu einer Zahl "
        "(hoeher = auffaelliger). Es ist KEINE Renditeprognose, sondern ein "
        "Mass fuer potenzielle Interessenkonflikte/Auffaelligkeit."
    ),
    "score_breakdown": (
        "Aufschluesselung, welches Signal wie viele Score-Punkte beigetragen "
        "hat — zeigt, woher der Score eines Trades kommt."
    ),
    "min_score": (
        "Blendet alle Trades unter diesem Score-Wert aus (Schieberegler-"
        "Untergrenze)."
    ),
    "override_active": (
        "Eigene Score-Gewichte oder Signal-Filter sind aktiv — die Werte "
        "weichen damit bewusst vom Standard-Scoring ab."
    ),
    # ------------------------------------------------------------ Korrelation
    "spearman": (
        "Spearman-Rangkorrelation (ρ): misst auf einer Skala von -1 bis +1, ob "
        "ein hoeherer Score mit einem hoeheren Return einhergeht — ueber die "
        "Platzierung (Rang) statt die Rohwerte. +1 = perfekter Gleichlauf, 0 = "
        "kein Zusammenhang, -1 = gegenlaeufig. Robust gegen Ausreisser, daher "
        "die Leitkennzahl dieses Tabs."
    ),
    "pearson": (
        "Pearson-Korrelation (r): misst den linearen Zusammenhang zwischen Score "
        "und Return auf Basis der Rohwerte (-1 bis +1). Empfindlich gegenueber "
        "Ausreissern — ein einzelner Extrem-Return kann den Wert stark verzerren."
    ),
    # ----------------------------------------------------------------- Returns
    "post_return": (
        "Kurs-Gewinn/-Verlust des gehandelten Titels eine feste Zahl Tage nach "
        "dem Trade, richtungsbereinigt: bei Verkaeufen invertiert, sodass "
        "positiv immer heisst 'der Member lag richtig'."
    ),
    "effective_return": (
        "Tatsaechlich realisierte Rendite bis zum (Teil-)Verkauf — im Gegensatz "
        "zu den rein rechnerischen Horizont-Renditen."
    ),
    "paper_return": (
        "Buchgewinn/-verlust zum aktuellen Kurs (Mark-to-Market) — noch NICHT "
        "realisiert, die Position ist evtl. noch offen."
    ),
    "combined_return": (
        "Rendite eines Trades, egal ob schon verkauft oder noch offen: bei "
        "geschlossenen Positionen die tatsaechlich erzielte, bei offenen der "
        "aktuelle Buchgewinn. Die Spalte 'Art' zeigt, welche der beiden es "
        "ist."
    ),
    "paper_return_terminal": (
        "Rendite am natuerlichen Endpunkt des Trades: beim Voll-Verkauf dessen "
        "Zeitpunkt, sonst nach 180 Tagen."
    ),
    "avg_return": (
        "Durchschnittliche Rendite ueber den primaeren Return-Horizont "
        "(eingestellt in der Sidebar)."
    ),
    "max_return": "Hoechste Einzel-Rendite ueber den primaeren Return-Horizont.",
    "pct_profitable": (
        "Anteil der Trades mit positiver Rendite (gemessen am primaeren "
        "Horizont)."
    ),
    "mark_to_market": (
        "Bewertung zum aktuellen Marktkurs — der Buchwert, wuerde man jetzt "
        "glattstellen (nicht der realisierte Gewinn)."
    ),
    # ------------------------------------------------------------------- Daten
    "disclosure_date": (
        "Meldedatum: wann der Trade offiziell veroeffentlicht wurde — das darf "
        "bis zu 45 Tage nach dem eigentlichen Kauf/Verkauf liegen."
    ),
    "transaction_date": (
        "Tatsaechliches Datum von Kauf/Verkauf — massgeblich fuer Analysen, "
        "nicht das spaetere Meldedatum."
    ),
    "datumsbasis": (
        "Welches Datum die Filter verwenden: das echte Handelsdatum "
        "(transaction_date) oder das Meldedatum (disclosure_date)."
    ),
    # ----------------------------------------------------------------- Horizont
    "horizon": (
        "Zeitfenster nach dem Trade, ueber das die Rendite gemessen wird "
        "(30 bis 180 Tage). Der 'primaere' Horizont steuert Sortierung und "
        "Sichtbarkeit."
    ),
    "horizon_filter": (
        "Blendet Trades aus, die erst nach dem gewaehlten Horizont (teil-)"
        "verkauft werden, sowie noch offene Positionen — damit Renditen "
        "vergleichbar bleiben."
    ),
    "first_close": (
        "Tage vom Kauf/Verkauf bis zur ersten Gegen-Transaktion (erster Teil- "
        "oder Voll-Verkauf) — der Moment, ab dem die Position abgebaut wird."
    ),
    "exit_status": (
        "Status der Position: voll geschlossen, teilweise geschlossen oder noch "
        "offen."
    ),
    # ------------------------------------------------------------------ Volumen
    "trade_volume": "Geschaetztes Geldvolumen dieses einzelnen Trades (USD).",
    "kapital": (
        "Obergrenze der offiziell gemeldeten Betragsspanne des Trades — Trades "
        "werden nur als Spanne (z.B. 1.001-15.000 $) gemeldet."
    ),
    "asset_volume": (
        "Durchschnittliches taegliches Handelsvolumen des Wertpapiers am Markt "
        "(Liquiditaet) — NICHT das Volumen dieses Trades."
    ),
    "trigger_floor": (
        "Mindest-Volumen, ab dem das Trade-Volumen-Signal ueberhaupt "
        "Score-Punkte vergibt."
    ),
    "trade_volume_signal": (
        "Schaltet das Signal an/aus, das grosse Transaktionsvolumina als "
        "auffaellig bewertet."
    ),
    # ------------------------------------------------------------------- Ticker
    "issuer": (
        "Emittent/Unternehmen hinter dem Wertpapier (uebergeordneter Ticker) — "
        "buendelt z.B. verschiedene Optionen/Anleihen desselben Konzerns."
    ),
    "relevant_sectors": (
        "Wirtschaftssektoren, denen der gehandelte Titel zugeordnet ist."
    ),
    # ------------------------------------------------------------------ Signale
    "signals": (
        "Primaersignale, die bei diesem Trade angeschlagen haben und den Score "
        "bilden (Standard: Committee-Voll-Treffer und Swarm-Buy). Nur diese "
        "Signale fliessen in den trade_score ein; uebrige Signale erscheinen als "
        "'Hints' im Trade-Detail-Popup."
    ),
    "hints": (
        "Zusaetzliche Indikatoren ohne Score-Einfluss (Default-Gewicht 0), z.B. "
        "Halb-Treffer-Committee, Floor-Action, Volumen, Ex-Officio. Rein als "
        "Kontext fuer die Transaktionsentscheidung; ueber den Gewichts-Editor "
        "bei Bedarf scorewirksam schaltbar (wirkt nur zusammen mit einem "
        "Primaersignal)."
    ),
    "committee_match": (
        "Der Trade-Sektor ueberschneidet sich mit einem Wirtschaftsbereich, den "
        "der Member ueber einen Ausschuss beaufsichtigt."
    ),
    "committee_full": (
        "Anteil der Trades in einer Branche, die der Member per Ausschuss(-"
        "Fuehrung) beaufsichtigt — klassischer Interessenkonflikt-Indikator."
    ),
    "ex_officio": (
        "Fuehrungsrollen (Chair, Ranking Member usw.) wirken kraft Amtes "
        "ausschussweit — auch ohne formale Mitgliedschaft im Unterausschuss."
    ),
    "swarm_paradox": (
        "Gegenlaeufiger Schwarm: im selben Fenster kaufen UND verkaufen "
        "mehrere Politiker denselben Titel gleichzeitig. Das ist ein "
        "Gegenindiz — es daempft den Schwarm-Beitrag (Faktor Paradox-Damper) "
        "bzw. setzt das Warning 'paradox_swarm'."
    ),
    "paradox_damper": (
        "Daempfungsfaktor, der die Score-Verstaerkung durch den Schwarm-Effekt "
        "abschwaecht (1 = volle Wirkung, kleiner = gedaempft)."
    ),
    "vote_timing": (
        "Anteil der Trades, die zeitlich auffaellig nahe an einer zugehoerigen "
        "Abstimmung lagen — moeglicher Hinweis auf Insider-Timing."
    ),
    # ------------------------------------------------------------ Member-Detail
    "outperformance": (
        "Mehrrendite des Members gegenueber dem Durchschnitt aller anderen "
        "Politiker, in Prozentpunkten (positiv = der Member schneidet besser ab)."
    ),
    "red_flag_ampel": (
        "Ampel aus 3 Indikatoren (Vote-Timing, Ausschuss-Trades, Mehrrendite). "
        "Je mehr anschlagen, desto auffaelliger das Trading-Muster des Members."
    ),
    "best_trade": "Die hoechste Einzel-Rendite, die dieser Member erzielt hat.",
    "sector_concentration": (
        "Verteilung des Handelsvolumens auf Branchen; orange markiert sind die "
        "vom Member ueber Ausschuesse beaufsichtigten Branchen."
    ),
    # --------------------------------------------------------------- Post-Trade
    "partial_close": (
        "Vereinfachte Zerlegung von Teil-Verkaeufen in realisierte Rendite — "
        "Mengen werden ohne strikte Kauf-Reihenfolge (non-FIFO) verrechnet."
    ),
    # ------------------------------------------------------------------- Config
    "gewicht": (
        "Wie stark dieses Signal in den Gesamt-Score eingeht — hoeheres Gewicht "
        "= groesserer Score-Beitrag."
    ),
    "preset": (
        "Gespeicherter Satz von Score-Gewichten — schnell umschaltbar, z.B. "
        "'konservativ' vs. 'aggressiv'."
    ),
}


def help_for(key: str) -> str | None:
    """Erklaertext zu ``key`` oder ``None`` (tolerant fuer no-op ``help=``)."""
    return GLOSSARY.get(key)
