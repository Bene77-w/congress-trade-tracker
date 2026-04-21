# Analyse: Transaktionsgrößen in trades_master.csv

**Datenbasis:** 9.591 Transaktionen | Stand: März 2026

---

## Kernerkenntnisse

| Kennzahl | Wert |
|---|---|
| Häufigste Kategorie | `$1,001 – $15,000` (74,1% aller Trades) |
| Gewichteter Durchschnitt | **~$71.965** |
| Datenbasis | 9.591 Transaktionen (100% auswertbar) |

> **Interpretation:** Der Durchschnitt liegt weit über der häufigsten Kategorie — verursacht durch seltene Großtransaktionen im Millionenbereich (klassische Rechts-Schiefe-Verteilung).

---

## Vollständige Verteilung

| Kategorie | Anzahl | Anteil | Mittelpunkt (Schätzwert) |
|---|---|---|---|
| $1,001 – $15,000 | 7.107 | 74,1% | $8.000 |
| $15,001 – $50,000 | 1.462 | 15,2% | $32.500 |
| $50,001 – $100,000 | 409 | 4,3% | $75.000 |
| $100,001 – $250,000 | 278 | 2,9% | $175.000 |
| $250,001 – $500,000 | 182 | 1,9% | $375.000 |
| $500,001 – $1,000,000 | 81 | 0,8% | $750.000 |
| $1,000,001 – $5,000,000 | 42 | 0,4% | $3.000.000 |
| $5,000,001 – $25,000,000 | 14 | 0,1% | $15.000.000 |
| $25,000,001 – $50,000,000 | 1 | 0,0% | $37.500.000 |
| Exakte Einzelwerte (<$15) | 8 | 0,0% | direkt verwendet |

---

## Methodik

- **Ranges:** Mittelpunkt aus Unter- und Obergrenze berechnet (z.B. `$1,001–$15,000` → `$8.000`)
- **Einzelwerte:** Direkt verwendet (z.B. `$224`, `$800`)
- **Gewichteter Durchschnitt:** `Σ(Mittelpunkt × Anzahl) / Gesamtanzahl`
- **Limitation:** Mittelpunkt ist eine Annäherung — tatsächliche Beträge innerhalb der Ranges unbekannt

---

## Fazit

**3 von 4 Trades** bewegen sich im kleinsten Bracket (`$1,001–$15,000`). Für den Scoring-Filter sollte die Volumen-Schwelle daher oberhalb von $15.000 angesetzt werden, um relevante Trades von Kleinsttransaktionen zu trennen.
