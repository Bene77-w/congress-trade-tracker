# 11 Datenqualität: trades_master_complete.csv

Stand der Datei: 2026-07-31 (ImportDate überwiegend 2026-07-03). Analyse read-only, Skripte in /tmp/audit_datenqualitaet/.

## 1. Struktur

| Kennzahl | Wert |
|---|---|
| Zeilen | 114.245 |
| Spalten | 89 |
| Dateigröße | 643 MB (CSV) |
| Verschiedene trade_id | 101.916 (12.329 Zeilen sind Mehrfachvorkommen) |
| Mitglieder (bio_guide_id) | 342 (House 349 Namensvarianten, Senate 70) |
| Zeitraum Transaktion | 2012-02-27 bis 2026-07-08 |

### Top-10 Spalten nach Bytes

| Spalte | MB | Anteil | Bemerkung |
|---|---|---|---|
| Insider_Sectors | 458,0 | 71 % | JSON-Liste (Ø 4.009 Zeichen, max 19.959) pro Zeile; nur 200 verschiedene Werte, also pro Mitglied identisch wiederholt |
| Member_Committees_Full | 24,1 | 3,8 % | Mitglieds-Attribut, 209 Varianten |
| Vote_Timing_Hits | 11,0 | 1,7 % | 96 % davon `[]` |
| Relevant_Sectors | 5,3 | 0,8 % | 64 % `[]` |
| asset_name | 3,0 | | |
| signals_triggered | 2,5 | | |
| ImportDate | 2,2 | | nur 3 verschiedene Werte, je Zeile ~19 Byte |
| classification_reason | 1,9 | | 27 Werte |
| industry_yf | 1,9 | | |
| amount | 1,9 | | 468 Werte, Text |

Fazit: Die Dateigröße ist fast vollständig ein Denormalisierungsproblem. Insider_Sectors + Member_Committees_Full machen ~75 % aus, beides sind reine Mitglieds-Attribute (342 Mitglieder), pro Trade-Zeile kopiert. Ohne diese beiden Spalten wären es ca. 150 MB; normalisiert (Member-Tabelle) deutlich unter 100 MB.

## 2. Füllquoten

Leer = '', nan, [] oder {}.

| Gruppe | Füllquote | Anmerkung |
|---|---|---|
| Kern (ticker, asset_name, Datum, Typ, amount, name, bio_guide_id, chamber) | ~100 % | ticker nie leer, auch bei Nicht-Aktien (siehe 4) |
| Member_Party/State/Chamber | 99,7 % | 399 Zeilen leer, alle Markwayne Mullin |
| district | 88,3 % | Senate nie (korrekt); 1.995 House-Zeilen ohne district |
| Member_Committees_Full, Insider_Sectors | 80,8 % | ~19 % der Trades ohne Ausschusszuordnung |
| FIGI-Block (6 Spalten) | 83,1 % | |
| sector_yf/industry_yf/quote_type_yf | 79,4 % | 23.548 ohne Sektor, davon 21.726 mit ticker_type=stock |
| post_return_30d | 82,6 % | sinkt auf 74,0 % bei 365d |
| excess_return | 92,2 % | |
| Exit-Block (exit_*, effective_return_*, opposing_trade_ids) | 53,3 % | nur geschlossene Positionen |
| paper_return_pct, price_as_of | 4,0 % | |
| status | 51,1 % | |
| comment | 49,4 % | fast nur "Handfiled Trade" |
| description | 5,0 % | |
| Relevant_Sectors | 16,7 % | |
| trade_score_breakdown | 24,5 % | |
| ex_officio_role_keys | 8,6 % | |
| Vote_Timing_Hits | 3,7 % | |
| trade_warnings | 5,6 % | nur Wert `paradox_swarm` |

### Leer, konstant, redundant

| Spalte | Befund |
|---|---|
| exch_code | 0 % gefüllt (faktisch leer), status_suffix 0 %, series_letter 0,9 % |
| ImportDate | 3 Werte, 99,5 % identisch |
| classification_confidence | 99,3 % `high` |
| asset_class_excluded | 99 % False |
| manual_review_flag | 99,4 % False |
| Member_Chamber vs chamber | 99,6 % identisch, 109 Zeilen widersprechen sich |
| ticker vs ticker_base | 98,7 % identisch |
| ticker vs ticker_figi | 82,6 % identisch |
| Vote_Timing_Hit, Vote_Timing_Hit_Count, Vote_Timing_Hits | drei Darstellungen derselben Information |
| Return-Paare post_price_Nd / post_return_Nd / signed_ar_Nd | 3 x 7 Horizonte, alle abgeleitet aus Preisen |
| trade_volume_usd / trade_amount_max_usd / asset_tradingvolume_usd | Betragsrepräsentationen von amount (letzteres weicht in 85 % ab, andere Semantik) |
| issuer_ticker, issuer_group_key | 100 % gefüllt, aber issuer_ticker enthält FIGI-IDs (z. B. BBG001S5TD05), nicht Ticker (Name irreführend) |
| status | `NEW` (House) vs `New` (Senate) vs leer: kodiert faktisch nur die Quelle/Kammer |

## 3. Schlüssel und Duplikate

| Prüfung | Ergebnis |
|---|---|
| trade_id leer | 0 |
| trade_id eindeutig? | Nein: 101.916 unique bei 114.245 Zeilen. 9.384 IDs 2x, 579 IDs 3x, 345 IDs 4x, Maximum 44x (2c2e2bcb6382b984, McCaul) |
| Komplett identische Zeilen | 11.149 |
| Zeilen, die sich bei gleicher trade_id unterscheiden | nur in csv_upload_time, last_modified, disclosure_date (123 IDs), signal_count/signals_triggered (54 IDs), description (46) |
| Duplikate nach name+Datum+ticker+amount+Typ | 14.730 überzählig (12,9 %) |
| zusätzlich asset_name+subholding | 13.812; mit disclosure_date/comment: 11.737 |

Interpretation: trade_id ist ein Hash über Inhaltsfelder und kollidiert, weil dieselbe Transaktion mehrfach auftaucht (Re-Uploads, Amendments, Mehrfach-Meldungen) und die Pipeline nicht dedupliziert. Ein Teil sind echte Mehrfachpositionen am selben Tag (gleiche Range, gleicher Ticker, z. B. Dividenden-Reinvests), die per Hash zusammenfallen. Beides ist ohne Quell-Zeilenreferenz nicht trennbar. Ein Schlüssel der Quelle (Dokument-ID + Zeilennummer) fehlt im Modell.

Verteilung der Duplikate: Ro Khanna stellt 25,8 % aller Zeilen (29.475), Michael McCaul 21,7 % (24.772). Zusammen 47 % des gesamten Datensatzes stammen von zwei Mitgliedern; Khanna hat 8,8 % interne Duplikate auf Transaktionsebene. Für statistische Auswertungen dominieren diese zwei die Ergebnisse.

## 4. Plausibilität

### Zeitraum, Kammern

| Jahr | House | Senate |
|---|---|---|
| 2012 | 7 | 2 |
| 2013 | 397 | 18 |
| 2014 | 3.613 | 1.607 |
| 2015 | 5.194 | 729 |
| 2016 | 6.230 | 918 |
| 2017 | 7.553 | 1.218 |
| 2018 | 10.297 | 1.219 |
| 2019 | 10.386 | 756 |
| 2020 | 10.618 | 1.399 |
| 2021 | 6.721 | 531 |
| 2022 | 9.184 | 677 |
| 2023 | 8.639 | 887 |
| 2024 | 6.554 | 580 |
| 2025 | 12.527 | 614 |
| 2026 (bis Juli) | 4.962 | 208 |

House 90 %, Senate 10 %. 2012-2013 nahezu leer (STOCK Act-Offenlegung beginnt dort; plausibel). Die Senatsmengen schwanken stark (1.607 in 2014 gegenüber 531 in 2021) und deuten auf unvollständige Quelle statt echter Aktivität hin. Juli 2026 hat nur 6 Trades (Datenstand). Dezember 2025 auffällig niedrig (421).

### Meldeverzug (disclosure_date minus transaction_date, Tage)

disclosure_date ist als String gemischt (552 Zeilen mit Uhrzeit, Länge 19), beim naiven Parsen fallen 99,5 % heraus; nach Kürzen auf 10 Zeichen alle parsebar.

| Kennzahl | Wert |
|---|---|
| Median | 28 |
| 25 % / 75 % | 18 / 37 |
| 95 % / 99 % | 119 / 546 |
| Maximum | 4.112 |
| negativ | 0 |
| = 0 Tage | 262 |
| > 45 Tage (gesetzliche Frist) | 14.833 (13,0 %) |
| > 90 Tage | 6.375 |
| > 365 Tage | 2.505 |

Anteil >45 Tage nach Jahr: 2014-2021 ca. 11-24 %, 2022-2025 ca. 4-9 %, 2026 0,3 %. Ausreißer: Ro Khanna, Transaktion 2015-03-07 mit Meldungen 2025-11 bis 2026-06 (3.897 bis 4.112 Tage), mehrfach als verschiedene Disclosure-Zeilen derselben trade_id; wahrscheinlich Nachmeldungen/Korrekturen oder Parsing-Fehler im Quelldatum. 2012er Trades haben Median 714 Tage Verzug (Nachmeldungen alter Bestände).

### Beträge

Ranges korrekt auf Standard-Buckets (73,6 % `$1,001 - $15,000`), trade_amount_max_usd = obere Grenze. Auffälligkeiten:
- Rohwerte, die keine Range sind: Einzelbeträge statt Range (z. B. `$15.00`, `$224.00`, `$647.63`), abgeschnittene Ranges (`$15,001` 8x, `$50,001` 4x, `$100,001` 3x, `$1,000,000` 4x), Freitext "Transaction in a Spouse or Dependent Child Asset over $1,000,000" (3x), Variante `$15,000 - $50,000` (11x, statt `$15,001`).
- 1 Zeile mit trade_volume_usd = 0 (Susie Lee, 6cb2ceca5c749e08).
- `Over $50,000,000` 9x, Obergrenze 50 Mio. gesetzt.

### Transaktionstypen

Purchase 57.883, Sale 49.961, Sale (Full) 3.477, Sale (Partial) 2.270, Exchange 642, SALE 11, PURCHASE 1. Die Groß-Schreibweisen (Khanna) zeigen fehlende Normalisierung; Sale-Varianten sind nicht zu einem Feld (Typ + full/partial) getrennt.

### Mitglieder

- Namen nicht normalisiert: 68 bio_guide_ids haben mehrere Namensvarianten ("Earl Blumenauer", "Mr. Earl Blumenauer"; "Banks, James E.", "James E Hon Banks", "James E. Banks"; "Jr, David A Perdue"). 70 Namen im Format "Nachname, Vorname" sind allein 11.363 Zeilen (Senate). Gruppierung nach name statt bio_guide_id würde Mitglieder aufspalten.
- 109 Zeilen, in denen chamber und Member_Chamber widersprechen (z. B. "Banks, James E." Senate vs. House; Rouzer, Cawthorn als Senate), weil Member-Stammdaten nach Namen gematcht werden und nicht nach dem Zeitpunkt der Amtszeit.
- Party: D 57.383, R 56.443, Libertarian 19 (Amash), I 1, leer 399 (Mullin).

### Ticker

| Prüfung | Wert |
|---|---|
| ticker leer | 0, aber ticker_type = stock nur 98,2 % |
| ticker_type unknown 153, invalid 94, bond 65, warrant 36, when_issued 35, foreign_stock 33, long_alpha_suspect 25, option 3 | |
| Sonderformate | `WB1`, `DG1`, `TX1`, `AGN1` (Delisting-Suffix `1`), `LLY-WD`, `AAM-PA`, `SOLV#` |
| Klartext als Ticker | `GLAS FUNDS, LP` (15), `ICAPITAL` (Asset "SL PARTNERS VII"), `ATEST-A` ("SALES TAX SECURITIZATION"), `COLPAL` (Colgate) |
| Ohne Sektor trotz Stock | 21.726 (19 %); häufig `FB` 549, `BRK.B` 436, `VRNG` 383, `WBA`, `ALXN`, `CELG`, `FDC`: Ticker-Umbenennungen/Delistings/Punkt-Schreibweise |
| Ohne post_return_30d | 19.851 (17,4 %); davon 18.095 `delisted` |

Der exit_status `delisted` (18.095 Zeilen, 15,8 %) ist ein großer Hinweis: Kurs-Lookup scheitert bei Ticker-Wechsel, nicht nur bei echten Delistings.

### Returns: schwerwiegende Plausibilitätsprobleme

- post_return_30d ist in 44,2 % aller vorhandenen Werte exakt 0,0 (365d: 38,7 %); in 50.459 Fällen ist post_price_30d == post_trade_baseline_price. Das sind überwiegend Platzhalter/Forward-Fill statt echter Kurse (Beispiel: 2bba515954a1f6d7, AMCR, Trade 2026-04-24: 30d-Preis liegt im Zeitfenster noch nicht vor, Rendite 0). Alle Auswertungen auf Return-Basis sind damit verzerrt (Median 0).
- excess_return: Median -40,7, Mittel -16,7, Maximum 24.339, Minimum -478; Einheit unklar und widerspricht post_return (z. B. NVDA-Trades mit excess_return 17.608 bei post_return_365d 0,0). Die Spalte ist faktisch unbrauchbar bzw. nicht dokumentiert konsistent.
- post_return_365d Minimum -2.314 %, Maximum +1.214 %: unmögliche Werte (Long-Position kann nicht weniger als -100 % verlieren); effective_return_pct min -1.049 %, paper_return_pct min -297 %. Fehlende Clipping/Short-Behandlung bzw. Preis-/Split-Fehler.
- exit_status/effective_return: closed_* ~99,9 % mit Wert, alle anderen 0 %; Purchases `held` 9.534, Sales `held` 20.288 (Sale mit Status "held" ist logisch fraglich, da Verkauf ohne Bestandsvorlauf).

### Signale und Scores

| Kennzahl | Wert |
|---|---|
| trade_score = 0 | 86.234 (75,5 %) |
| trade_score = 5 | 16.493 |
| trade_score = 20 | 8.945 |
| trade_score = 25 | 2.573 |
| Trades mit Score > 0 | 24,5 % |
| signal_count = 0 | 20.452 (17,9 %), = 1: 39.258, = 2: 27.074, >= 3: 27.461 |

Der Score hat nur vier diskrete Werte (0/5/20/25), also faktisch eine Kategorie, kein stetiger Score. signal_count und Score sind schwach gekoppelt: 37.475 Trades mit 1 Signal haben Score 0; Score 5 tritt erst ab 2 Signalen auf, Score 25 ab 3. Häufigstes Signal `accumulative` (21.684 allein, weitere in Kombinationen), `paradox_swarm` als einzige Warnung (6.417). Vote_Timing_Hit True nur 3,7 %.

Zusatzbefund: status-Spalte kodiert Kammer (`NEW` House, `New` Senate, leer = ältere House-Zeilen), `comment` = "Handfiled Trade" bei 48,6 %: das ist Quellenmetadaten, kein Inhalt.

## 5. Auffällige Zeilen (10 Beispiele)

| # | trade_id | Problem |
|---|---|---|
| 1 | 2c2e2bcb6382b984 | 44 identische Zeilen (McCaul, Sale): massiver Duplikat-Cluster mit identischem Inhalt |
| 2 | 9340e19368f897a5 | Ro Khanna, Transaktion 2015-03-07, Meldungen 2025-11 bis 2026-05, 3.897 bis 4.083 Tage Verzug, mehrfach |
| 3 | ff34bcb14ebab879 | Khanna, Verzug 4.020 und 4.112 Tage (Maximum des Datensatzes) |
| 4 | 183b476bc2d1c95c | NVDA: excess_return 17.608 bei post_return_365d 0,0; Einheit/Berechnung inkonsistent |
| 5 | 2bba515954a1f6d7 | AMCR (2026-04-24): post_return_30d 0 durch Preis == Baseline, kein echter Kurs |
| 6 | 6cb2ceca5c749e08 | Susie Lee, trade_volume_usd = 0 |
| 7 | d601b397e02dc778 | Nancy Pelosi, amount "$15.00" (Einzelbetrag statt Range), Typ Exchange |
| 8 | 2609291e22f572e3 | Shri Thanedar, amount "$15,001" (abgeschnittene Range) |
| 9 | 58d25d5b86a1bbb6 | Ro Khanna, Typ "SALE" (Großschreibung, nicht normalisiert) |
| 10 | 9fc86bb7c6d1424f | "Banks, James E." chamber Senate, Member_Chamber House (Stammdaten-Widerspruch); weitere: aa09377e1fc9fb04 (Rouzer House/Senate vertauscht) |

Weitere: 9e09b0f27ecfe92f (ticker "ICAPITAL" für "SL PARTNERS VII", ticker_type invalid), 0ab5d60ad3e433ef (McCaul, ticker "WB1" = Altticker mit Suffix), 812b240ec8d3875c (Carper, 2012-Transaktion mit 877 Tagen Verzug).

## 6. Empfehlung für das Datenmodell im Rebuild

1. Normalisieren in Tabellen statt einer 89-Spalten-Wide-Table: `members` (bio_guide_id, Name kanonisch, Partei, Staat, Kammer, Amtszeiten), `committee_memberships` (Mitglied, Ausschuss, Rolle, von/bis), `trades` (nur Quellfelder), `securities` (ticker, figi, issuer, sector, historische Ticker-Aliasse), `prices`, `trade_metrics` (Returns je Horizont, long-format statt 7x3 Spalten), `signals` (long-format: trade_id, signal, wert). Erwartete Größe: unter 100 MB statt 643 MB. Insider_Sectors/Member_Committees_Full nie in Trade-Zeilen speichern, sondern per Join/Ableitung.
2. Stabiler Primärschlüssel aus Quelle: Dokument-ID + Zeilenindex (+ Version/Amendment-Flag). Hash-IDs nur als Zusatz. Dedup-Regel explizit (Amendment ersetzt Original) und Duplikate nicht stillschweigend mitzählen: ca. 12 % der Zeilen sind aktuell Mehrfach.
3. Mitglieder ausschließlich über bio_guide_id führen; Namen nur als Anzeige. Chamber/Partei zeitabhängig (Amtszeit zum Transaktionsdatum), nicht per Namensmatch.
4. Rohwerte und abgeleitete Werte trennen: Betrag als `amount_min`/`amount_max` (nullable) + `amount_raw`; Transaktionstyp als Enum (purchase/sale) + `partial`-Flag; Datum als echtes Datum, ein Format.
5. Returns: nur echte Kurse speichern; fehlend = NULL, nie 0 oder Baseline-Platzhalter; Einheit festlegen (Prozent vs Faktor), Wertebereichs-Checks (>= -100 %), Short-/Sale-Logik explizit, Ticker-Historie (FB zu META, BRK.B, Delisting-Suffix `1`) über securities-Tabelle auflösen, damit `delisted` nicht 16 % umfasst. `excess_return` neu definieren oder weglassen.
6. Score als Ableitung zur Laufzeit/Materialized View, mit stetiger oder dokumentierter Skala; nicht 4 Stufen in der Fakten-Tabelle persistieren.
7. Konstante und Provenienz-Spalten (ImportDate, csv_upload_time, status, comment, exch_code, status_suffix, manual_review_flag) in eine Lauf-/Lineage-Tabelle oder streichen.
8. Datenvalidierung als harte Pipeline-Gates (Eindeutigkeit, Wertebereiche, Anteil NULL je Spalte, Kammerabgleich) mit Bericht pro Lauf; statt CSV besser Parquet/SQLite/DuckDB.
