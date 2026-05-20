# Vote-Timing Audit (Stand 2026-05-20)

Stichproben-Plausibilitätscheck der 5.357 Vote-Timing-Hits aus `trades_master_complete.csv` (Smoke-Run 20.05.2026).

## Methode

Basis: `data/processed/trades_master_complete.csv` (111.986 Trades). Alle Trades mit `Vote_Timing_Hit=True` (5.357) wurden selektiert. Die `Vote_Timing_Hits`-Spalte (JSON-Liste) wurde explodiert → 60.663 einzelne Bill-Trade-Paare. Audit-Script: One-Shot-Python-REPL, kein persistentes Script-File. Wo finde ich die Subject-Listen der bills data/congress_api/bill_subjects.json

Zeitfenster-Semantik (aus `config/settings.py`): `VOTE_TIMING_WINDOW_BEFORE=30` (Trade bis 30d vor Vote), `VOTE_TIMING_WINDOW_AFTER=5` (Trade bis 5d nach Vote).

---

## Ergebnisse

### 1. Verteilung `days_to_vote`

`days_to_vote > 0` = Trade VOR Vote (klassischer Insider-Trade-Verdacht). `days_to_vote < 0` = Trade NACH Vote (innerhalb des 5d-Toleranzfensters).

| Bin (Tage vor/nach Vote) | Hit-Einträge | Anteil |
|---|---|---|
| -5..-4 | 3.792 | 6,3% |
| -3..-1 | 5.453 | 9,0% |
| 0..4 | 7.168 | 11,8% |
| 5..9 | 9.650 | 15,9% |
| 10..14 | 8.733 | 14,4% |
| 15..19 | 6.540 | 10,8% |
| 20..29 | 19.327 | 31,9% |
| 30+ | 0 | 0,0% |

Größter Bin: **20–29 Tage vor Vote** (31,9%). Das ist am Rand des 30d-Fensters und somit der am wenigsten verdächtige Bereich. Kein einziger Eintrag jenseits des konfigurierten Limits — Fenster-Logik korrekt.

### 2. Konfidenz-Verteilung

| Konfidenz | Hit-Einträge | Anteil |
|---|---|---|
| Hoch | 53.831 | 88,7% |
| Mittel | 6.572 | 10,8% |
| Niedrig | 260 | 0,4% |

Sehr gesunde Verteilung: 88,7% der Hit-Einträge basieren auf Hoch-Konfidenz-Subject-Mappings. Die Warnschwelle aus Plan (> 60% Niedrig) wird bei weitem nicht erreicht.

### 3. Top-10 Bills nach Anzahl betroffener Trades

| Trades betroffen | Anteil | Policy Area | Bill-Title (gekürzt) |
|---|---|---|---|
| 1.010 | 18,9% | Economics and Public Finance | Military Construction and Veterans Affairs, Agriculture, and Legislative Branch Appropriations Act |
| 910 | 17,0% | Economics and Public Finance | Continuing Appropriations, Agriculture, Legislative Branch, Military Construction… |
| 885 | 16,5% | Environmental Protection | Providing for congressional disapproval (CRA) — rule by EPA |
| 757 | 14,1% | Environmental Protection | Providing congressional disapproval (CRA) — rule |
| 757 | 14,1% | Environmental Protection | Providing congressional disapproval (CRA) — rule |
| 722 | 13,5% | Finance and Financial Sector | Disapproving the rule submitted by the Bureau of Consumer Financial Protection |
| 713 | 13,3% | Environmental Protection | Providing congressional disapproval (CRA) — rule |
| 642 | 12,0% | Economics and Public Finance | Rescissions Act of 2025 |
| 582 | 10,9% | Economics and Public Finance | Full-Year Continuing Appropriations and Extensions Act, 2025 |
| 537 | 10,0% | Government Operations and Politics | Veterans Accessibility Advisory Committee Act of 2025 |

**Befund:** Die Top-2 Bills (Appropriations Acts) betreffen je ~18% und ~17% aller Hit-Trades. Appropriations-Gesetze haben extrem breite Subject-Listen (Verteidigung, Landwirtschaft, Gesundheit, Technologie), weshalb ihr Sektor-Footprint fast alle Handelsportfolios abdeckt. Das ist ein strukturelles Signal-Qualitätsproblem, kein Datenfehler (→ Empfehlungen).

### 4. Top-Sektoren der betroffenen Trades

Basis: `Relevant_Sectors`-Spalte der Hit-Trades (Committee × Ticker Intersection).

| Sektor | Trades | Anteil (von 5.357 Hit-Trades) |
|---|---|---|
| Technology | 11.131 | 207,8% |
| Industrials | 3.643 | 68,0% |
| Financial Services | 2.241 | 41,8% |
| Healthcare | 868 | 16,2% |
| Consumer Cyclical | 459 | 8,6% |
| Consumer Defensive | 237 | 4,4% |
| Communication Services | 186 | 3,5% |
| Energy | 161 | 3,0% |

Anteil > 100% ist möglich, da ein Trade mehrere Sektoren haben kann. Technology dominiert stark (207,8%) — erklärt durch den hohen Anteil von MSFT, AAPL, AMZN, GOOGL im Gesamt-Datensatz und die breiten Tech-Subjects in Appropriations-Bills.

### 5. Negative `days_to_vote`

Trades die NACH einem Vote stattfanden (aber innerhalb des 5d-Toleranzfensters):

- **9.245 / 60.663 Hit-Einträge = 12,1%**

Unterhalb der Warnschwelle von 15%. Der 5d-After-Toleranz-Puffer ist damit nicht überdimensioniert.

### 6. Mehrfach-Hits per Trade

| Hit_Count | Trades | Hit_Count | Trades |
|---|---|---|---|
| 1 | 195 | 5 | 295 |
| 2 | 220 | 6 | 262 |
| 3 | 68 | 7 | 279 |
| 4 | 135 | 8 | 333 |

Max `Vote_Timing_Hit_Count` = **26** (Thomas H. Kean Jr, AMZN).

Top-3 Trades mit den meisten Hits:

| Name | Ticker | Hit_Count |
|---|---|---|
| Thomas H. Kean Jr | AMZN | 26 |
| Boozman, John | ORCL | 25 |
| Gilbert Cisneros | OKTA | 25 |

Alle drei Ticker (AMZN, ORCL, OKTA) decken mehrere Sektoren ab (Technology + Consumer Cyclical), was kombiniert mit den breiten Appropriations-Bills zu hohen Hit-Counts führt.

### 7. Top-Member nach Hit-Anteil (Hits/Gesamt-Trades)

| Member | Hits | Gesamt-Trades | Hit-Rate |
|---|---|---|---|
| David Rouzer | 1 | 1 | 100% |
| Moreno, Bernardo | 1 | 1 | 100% |
| Scott Desjarlais | 2 | 2 | 100% |
| Schiff, Adam B. | 1 | 1 | 100% |
| Tony Wied | 16 | 25 | 64% |
| Julie Johnson | 303 | 501 | 60% |
| Julia Letlow | 130 | 228 | 57% |
| Rob Bresnahan | 344 | 607 | 57% |
| David J. Taylor | 95 | 168 | 57% |
| John Mcguire | 5 | 9 | 56% |

Die 100%-Mitglieder haben nur 1–2 Trades — statistisch nicht aussagekräftig. **Rob Bresnahan** (344/607, 57%) und **Julie Johnson** (303/501, 60%) sind interessanter: hohe absolute Treffermengen bei mehreren hundert Trades, was auf breite Ausschuss-Überschneidungen und aktives Trading in Sektoren mit vielen Abstimmungen hindeutet.

---

## Befunde

- ✅ **Plausibel:** Konfidenz-Verteilung ist sehr gesund (88,7% Hoch). Die Schwelle für Niedrig-Dominanz (>60%) wird nicht annähernd erreicht.
- ✅ **Plausibel:** Negative `days_to_vote`-Anteil (12,1%) liegt unter der Warnschwelle von 15% — der 5d-Toleranz-Puffer ist korrekt dimensioniert.
- ✅ **Plausibel:** Kein Hit-Eintrag jenseits der konfigurierten Fenster-Grenzen (0% im Bin „30+"). Fenster-Logik korrekt implementiert.
- ✅ **Plausibel:** Maximaler Hit_Count von 26 ist erklärbar durch breite Multi-Sektor-Ticker (AMZN) × breite Appropriations-Bills — kein Subject-Spam-Muster erkennbar.
- ⚠️ **Auffällig:** **Appropriations-Bills dominieren die Top-Bills.** Die zwei betroffensten Bills (Military Construction + Continuing Appropriations) treffen zusammen ~35% aller Hit-Trades. Appropriations-Gesetze haben sehr breite Subject-Listen → fast jeder Tech-Trade in einem 30d-Fenster um eine Abstimmung trifft. Das erhöht die False-Positive-Rate für reine Buy-and-Hold-Trader.
- ⚠️ **Auffällig:** **Technology dominiert die Sektor-Verteilung** mit 207,8% (Mehrfachzählung durch Multi-Sektor-Trades). Kein anderer Sektor kommt annähernd heran. Wird durch hohen MSFT/AAPL/AMZN-Anteil im Datensatz getrieben, der mit Tech-relevanten Subjects vieler Bills zusammentrifft.
- ⚠️ **Auffällig:** **Bin 20–29 Tage ist der größte Bin** (31,9%). Trades fast einen Monat vor einem Vote sind inhärent weniger verdächtig als Trades 0–5 Tage davor. Das Fenster von 30d ist pragmatisch, aber relativ weit. Eine Aufspaltung in Score-Gewichtung nach `days_to_vote` könnte die Signalqualität verbessern.

## Empfehlungen (User entscheidet)

1. 🔧 **Appropriations-Bills aus Subject-Mapping herausnehmen oder downweigten:** Bills mit Policy Area „Economics and Public Finance" + Titel-Pattern „Appropriations Act" / „Continuing Appropriations" könnten einen eigenen Konfidenz-Malus bekommen, da ihr Sektor-Footprint zu generisch ist. Alternativ: ein `min_sector_specificity`-Filter, der Bills mit > 5 gemappten Sektoren ausblendet.
2. 🔧 **`days_to_vote`-gewichteten Score erwägen:** Statt flat Score=15 für jeden `floor_action`-Hit könnte `days_to_vote` als Dämpfungsfaktor einfließen (z. B. lineare Abnahme von 30d → 0 Tage vor Vote: 15 → 30 Score-Punkte). Das würde den 20–29d-Bin strukturell abwerten.
3. 🔧 **Rob Bresnahan / Julie Johnson manuell prüfen:** Beide haben > 57% Hit-Rate bei > 300 absoluten Hits. Ob das echte Insider-Aktivität oder breite Portfolio-Überschneidung mit Ausschuss-Abstimmungen ist, lässt sich nur im Einzelfall beurteilen.
4. **vote signal unabhängig von relevant sektors** member kann auch ausserhalb eines Relevanten committees Insider-wissen zu einem Flooraction ausgang kennen, insebesondere ex officios. dazu müsste aber andere Datum threshholds und scoreweight gelten um die Zuverlässigkeit des Signals garantieren(● Doch, Relevant_Sectors ist zentral für den Vote-Timing-Filter — es ist die Trade-Seite der Sektor-Intersection. Konkret in vote_timing_tag.py:338–362:

  # Trade-Sektoren aus Relevant_Sectors lesen
  trade_sectors = self._extract_trade_sectors(trade.get('Relevant_Sectors', ''))
  if not trade_sectors:
      continue  # ← Trade ohne Sektoren → immer übersprungen

  # ...
  # Sektor-Schnittmenge mit dem Vote prüfen
  if not trade_sectors.intersection(vote['_sectors_set']):
      continue  # ← kein Sektor-Match → kein Hit

  Die Logik ist also:

  Treffer nur wenn:
    trade_sectors (aus Relevant_Sectors)
      ∩  vote_sectors (aus bill_subjects → subject_mapping)
      ≠ ∅
) 
