# Manual-Review Anleitung

Dieses Dokument erklärt **alle manuellen Review-Vorgänge** im Congress-Trade-Tracker und liefert für jeden eine Schritt-für-Schritt-Anleitung.

> **Kernidee:** Die Pipeline arbeitet zu ~95–100% automatisch. Was sie *nicht* selbst entscheiden kann, schreibt sie in dedizierte Review-CSVs unter `data/processed/`. Du arbeitest diese ab und trägst die Ergebnisse in **kuratierte Mapping-Dateien** unter `data/mappings/` zurück. Beim nächsten Pipeline-Lauf werden die Korrekturen automatisch angewendet.

---

## Übersicht der Review-Dateien

| Review-Typ | Output (was prüfen) | Kuratiertes Mapping (wohin eintragen) | Mechanismus |
|------------|---------------------|----------------------------------------|-------------|
| 1. **Namen** (Politiker) | `data/processed/unmatched_names.csv` | `data/mappings/name_aliases.csv` | NameMatcher liest Alias bei nächstem Lauf |
| 2. **Manual-Review-Tickers** | `data/processed/manual_review_tickers.csv` | (Klassifikator-Regeln in `utils/ticker_classifier.py` oder Whitelist) | Klassifikator markiert mehrdeutige Ticker |
| 3. **Vorzugsaktien** (Preferred) | `data/processed/unmatched_preferred.csv` | `data/mappings/Vorzugsaktien_mapping.csv` | TickerSectorEnricher mappt Preferred → Stamm |
| 4. **Duplikate** | `data/processed/duplicate_inspection_sample.csv` | (keine — nur Sichtprüfung; ggf. `DEDUP_STRATEGY` in `config/settings.py`) | Sichtprüfung der Trade-ID-Hash-Strategie |

Reine **Output-Dateien ohne Review-Pflicht** (Information / Diagnose):

| Datei | Zweck |
|-------|-------|
| `delisted_trades.csv` | Trades zu Tickern, für die yfinance keinen Sektor mehr liefert (delisted, ge-mergt, umbenannt). Information, keine Aktion nötig. |
| `ticker_liste_index.csv` | Helfer-Index: pro Ticker der älteste Trade (mit `asset_name` + `transaction_date`). Wird durch `script.py` aus `delisted_trades_stage.csv` erzeugt; nur als Lookup-Tabelle für manuelle Recherchen gedacht. |

---

## 1. Namens-Review — `unmatched_names.csv` → `name_aliases.csv`

### Worum geht es?

Die Scraper liefern Politiker-Namen in unterschiedlichsten Schreibweisen (`A. Mitchell McConnell Jr.`, `James Banks`, `Rob Bresnahan`, …). Der `NameMatcher` versucht in dieser Reihenfolge:

1. **Alias-Lookup** (`name_aliases.csv`)
2. **Exakt-Match** auf die offizielle Namens-Liste (`house_member&(sub)committees.csv` / `senate_member&(sub)committees.csv`)
3. **Fuzzy-Match** (rapidfuzz, Threshold 85%)

Wenn alle drei fehlschlagen, landet der Name in `unmatched_names.csv`.

### Spalten in `unmatched_names.csv`

```
trade_name, count, chamber, best_match, best_match_score
Thomas Macarthur, 2758, house, Thomas Massie, 62
"Perdue, David", 2242, senate, "McCormick, David", 55
```

- `trade_name`: Name wie aus dem Scraper
- `count`: Wie oft dieser Name vorkommt (Sortierschlüssel — hohe Counts zuerst)
- `chamber`: `house` oder `senate`
- `best_match` / `best_match_score`: Bester rapidfuzz-Treffer + Score (<85%, deshalb verworfen)

### Spalten in `name_aliases.csv`

```
trade_name, correct_name, chamber
A. Mitchell McConnell Jr., "McConnell, A. Mitchell", senate
```

- `trade_name`: **Exakt** der Name wie in `unmatched_names.csv` (Schreibweise, Kommata, Suffix `Jr.`)
- `correct_name`: Der offizielle Name aus `house/senate_member&(sub)committees.csv`
- `chamber`: `house` oder `senate`

### Schritt-für-Schritt

1. **Datei öffnen:** `data/processed/unmatched_names.csv`
2. **Nach `count` sortieren** (sollte schon so sein) — die Zeilen mit den meisten betroffenen Trades zuerst.
3. **Pro Zeile** prüfen, ob der `best_match` plausibel ist:
   - Google-Suche `"<trade_name>" Congress` → identifiziere echten Politiker
   - Vergleiche mit `data/mappings/house_member&(sub)committees.csv` bzw. `senate_member&(sub)committees.csv`
4. **Drei Fälle:**

   | Fall | Aktion |
   |------|--------|
   | (a) Name ist ein Member, der in der offiziellen Liste anders geschrieben ist | Trage in `name_aliases.csv` ein: `<trade_name>,<correct_name>,<chamber>` |
   | (b) Name ist ein **ehemaliges** Mitglied (heute nicht mehr im Kongress) | Prüfe `data/raw/former_members.csv`. Wenn dort drin: kein Alias nötig — die Pipeline matcht ehemalige Members separat. Wenn nicht drin: dort eintragen. |
   | (c) Name gehört zu einem Family-Member / Spouse | Diese werden bewusst nicht gematcht (Name-of-Filer ≠ Politiker). Trade kann ignoriert werden, ggf. später Filter ergänzen. |

5. **Datei speichern** (UTF-8, Komma-Separator).
6. **Pipeline neu laufen lassen** — beim nächsten `python scripts/import_trades.py` werden die Aliase via `NameMatcher._load_aliases()` automatisch übernommen.
7. **Verifizieren:** Nach dem Lauf sollte `unmatched_names.csv` weniger Zeilen / niedrigere Counts haben.

### Was passiert mit unmatched Namen in `trades_master.csv`?

> **Wichtig (deine Frage):** Bei unmatched Namen bleibt der **Original-Name** aus dem Scraper in der Spalte `name` der `trades_master.csv` stehen. Die Spalten, die durch das Matching befüllt würden (`bio_guide_id`, `Member_Party`, `committees`, `ex_officio`, `state`, `chamber`-Korrektur), bleiben **leer / NaN**. Der Trade wird **nicht verworfen** — er fließt in die Master-CSV ein, nur eben ohne Member-Anreicherung.

Sobald du den Alias hinzugefügt hast und die Pipeline erneut läuft, wird der Trade beim Re-Import korrekt angereichert.

---

## 2. Ticker-Review — `manual_review_tickers.csv`

### Worum geht es?

`utils/ticker_classifier.py` ist ein Offline-Regex-Entscheidungsbaum, der Roh-Ticker in 17 Typen klassifiziert (`STOCK_US_STANDARD`, `ETF`, `OPTION`, `CUSIP`, `WARRANT`, …). Manche Roh-Ticker sind **strukturell mehrdeutig** und werden mit `manual_review_flag=True` markiert. Diese landen aggregiert in `manual_review_tickers.csv`.

Typische Auslöser:
- `NUMERIC_SUFFIX` — Ticker mit angehängter Ziffer wie `WB1`, `DG1`, `TX1`. Häufig Quiver-Eigenheit (Duplikat-Marker), kann aber auch eine Subholding sein.
- `LONG_ALPHA_SUSPECT` — Ticker mit ≥6 Buchstaben (kein Standard-NYSE/NASDAQ-Format).
- `AMBIGUOUS_*` — Mehrere Regeln treffen gleichzeitig.

### Spalten

```
raw_ticker, ticker_base, ticker_type, classification_reason,
count_of_trades, first_trade_date, last_trade_date, example_member
WB1, WB, numeric_suffix, NUMERIC_SUFFIX, 131, 2016-08-10, 2024-08-09, Michael T. McCaul
```

- `raw_ticker`: Ticker wie aus dem Scraper
- `ticker_base`: Vermutete Basis (Ziffer entfernt) — nur Hypothese
- `ticker_type` / `classification_reason`: Welcher Pfad im Klassifikator gegriffen hat
- `count_of_trades`, `first_/last_trade_date`, `example_member`: Sortier- und Recherchehilfen

### Schritt-für-Schritt

1. **Datei öffnen:** `data/processed/manual_review_tickers.csv`
2. **Nach `count_of_trades` sortieren** — die Tickers mit dem höchsten Volumen zuerst (Pareto: 80% des Reviews liegt in 20% der Zeilen).
3. **Pro Zeile recherchieren** (z.B. via OpenFIGI-Web-UI, Yahoo Finance, SEC EDGAR):
   - **Ist `ticker_base` der echte Ticker?** (z.B. `WB` = Weibo)
   - **Oder ist `raw_ticker` ein eigenständiges Wertpapier?** (z.B. eine Tranche, ein Subholding, ein nicht-US-Listing)
4. **Vier Fälle:**

   | Fall | Aktion |
   |------|--------|
   | (a) Ticker ist eindeutig die Basis (`WB1` ≡ `WB`) | Klassifikations-Regel in `utils/ticker_classifier.py` schärfen ODER Quiver-Suffix-Stripper in `import_trades.py` aktivieren (siehe Code-Kommentare). |
   | (b) Ticker ist ein eigenständiges Wertpapier | OpenFIGI-Lookup manuell durchführen, Ergebnis in passenden Cache (`ticker_normalizer` SQLite-Tabelle) eintragen ODER Klassifikator-Regel ergänzen. |
   | (c) Ticker ist Müll (z.B. „N/A", `---`) | In Klassifikator als `INVALID` whitelisten — diese Trades werden dann verworfen. |
   | (d) Unsicher | Liegen lassen, beim nächsten Review-Zyklus erneut prüfen. Niedrige Counts können auch dauerhaft unklassifiziert bleiben. |

5. **Code-Änderungen testen:**
   ```bash
   pytest tests/test_ticker_classifier.py -v
   ```
6. **Pipeline neu laufen lassen** — `import_trades.py` ruft `_write_manual_review_subset(df)` neu auf; gelöste Tickers verschwinden aus der CSV.

> **Hinweis:** Anders als bei Namen gibt es **keine simple Mapping-CSV** für Tickers. Korrekturen erfordern in der Regel eine Anpassung der Klassifikator-Regeln. Das ist Absicht: Ticker-Strukturen folgen Mustern, nicht Einzelfällen — eine Whitelist würde sich nicht skalieren.

---

## 3. Vorzugsaktien-Review — `unmatched_preferred.csv` → `Vorzugsaktien_mapping.csv`

### Worum geht es?

Vorzugsaktien (Preferred Shares) haben eigene Ticker-Konventionen, die yfinance nicht direkt versteht:
- `EFC$D`, `T$A`, `BAC$I` (Dollar-Notation) — yfinance kennt diese teilweise
- `AAM-PA`, `ALIN-P-E` (Hyphen-Notation) — yfinance liefert oft `None`

`utils/ticker_sector.py` hat zwei Mechanismen:

1. **Hyphen-Fallback** automatisch (`BRK-B` → `BRK/B`).
2. **Preferred-Mapping** — explizite Tabelle `Vorzugsaktien_mapping.csv`: Preferred-Ticker → Stamm-Ticker (von dort wird dann der Sektor übernommen, weil Preferred und Stamm zur gleichen Firma gehören).

Wenn weder Mapping-Eintrag existiert noch yfinance Daten liefert, landet der Ticker in `unmatched_preferred.csv`.

### Spalten

`unmatched_preferred.csv`:
```
preferred_ticker
AAM-PA
ALIN-P-E
ATH-PC
```

`Vorzugsaktien_mapping.csv`:
```
stammaktien_ticker, vorzugsaktien_ticker, Company
EFC, EFC$D, ELLINGTON FINANCIAL INC.
T, T$A, AT&T INC.
```

### Schritt-für-Schritt

1. **Datei öffnen:** `data/processed/unmatched_preferred.csv`
2. **Pro Ticker recherchieren:**
   - Konvention erkennen: `XXX-PA`, `XXX-PB` = Preferred Series A/B; `XXX$A` = SEC-Notation für gleiches.
   - Echten Stamm-Ticker finden (z.B. `AAM-PA` → `AAM` = Apollo Asset Management Inc.)
   - Verifizieren: Yahoo Finance / OpenFIGI / SEC EDGAR
3. **In `Vorzugsaktien_mapping.csv` eintragen:**
   ```
   AAM,AAM-PA,APOLLO ASSET MANAGEMENT INC.
   ```
   - `stammaktien_ticker`: Echter Underlying-Ticker
   - `vorzugsaktien_ticker`: **Exakt** wie in `unmatched_preferred.csv` (Schreibweise!)
   - `Company`: Optional, nur Lesbarkeit
4. **Pipeline neu laufen lassen** — `TickerSectorEnricher` lädt das Mapping bei Init (`_load_preferred_mapping`), beim nächsten Lauf wird `AAM-PA` automatisch über `AAM` aufgelöst und in den positiv-Cache geschrieben.
5. **Verifizieren:** `unmatched_preferred.csv` sollte den Eintrag nicht mehr enthalten; `trades_master.csv` hat für diese Trades nun korrekte `sector` / `industry` Werte.

---

## 4. Duplikat-Review — `duplicate_inspection_sample.csv`

### Worum geht es?

`utils/trade_id.py` erzeugt eine 16-stellige Hash-ID pro Trade aus festen Spalten (Member, Ticker, Date, Amount, Type …). Zwei Zeilen mit gleicher `trade_id` sind **per Definition Duplikate**.

`config/settings.py::DEDUP_STRATEGY` steuert die Behandlung:
- `'keep_all'` (Default): Duplikate bleiben — beide Zeilen in `trades_master_complete.csv`.
- `'last_wins'`: Bei Duplikaten wird nur die zuletzt gesehene Zeile behalten.

`duplicate_inspection_sample.csv` ist eine **Stichprobe** der erkannten Duplikate (nicht alle, sondern eine repräsentative Auswahl) — als Sanity-Check, ob die Hash-Strategie Sinn ergibt.

### Spalten

```
trade_id, name, ticker, asset_name, transaction_type, amount,
transaction_date, subholding, comment, disclosure_date, ...
cc586efbf1a1, Michael T. McCaul, CRAI, ..., Purchase, $1,001 - $15,000, 2018-08-23, ...
cc586efbf1a1, Michael T. McCaul, CRAI, ..., Purchase, $1,001 - $15,000, 2018-08-23, ...
```

Zwei Zeilen mit **gleicher** `trade_id` ⇒ Pipeline hält sie für Duplikate.

### Schritt-für-Schritt

1. **Datei öffnen.** Zeilen sind nach `trade_id` gruppiert (Paare/Trios).
2. **Pro Gruppe prüfen:**
   - Sind die Zeilen **wirklich identisch**? (gleicher Member, Ticker, Datum, Betrag, Type)
   - Wenn ja: Duplikat ist korrekt erkannt → keine Aktion nötig.
   - Wenn **nein**, aber Trade-ID identisch: Hash-Strategie ist zu grob — relevante unterscheidende Spalte fehlt im Hash.

3. **Zwei Aktionen:**

   | Befund | Aktion |
   |--------|--------|
   | Echte Duplikate (Quiver hat zwei Disclosure-Dokumente für denselben Trade verlinkt) | `DEDUP_STRATEGY = 'last_wins'` in `config/settings.py` setzen, damit Master entdoppelt wird. |
   | Falsch-Duplikate (verschiedene Trades, gleiche Hash) | `utils/trade_id.py::TRADE_ID_COLUMNS` erweitern (z.B. `subholding` oder `comment` mit aufnehmen). Test in `tests/test_trade_id.py` ergänzen. |

4. **Pipeline neu laufen lassen** — neue Hashes / neue Strategie wird angewendet.

---

## 5. Reine Output-Dateien (kein Review nötig)

### `data/mappings/Vorzugsaktien_mapping.csv` (Input, nicht Output)

Statisch gepflegtes Mapping Preferred-Ticker → Stamm-Ticker. Wird von `TickerSectorEnricher` bei Init geladen. **Du editierst diese Datei direkt** als Reaktion auf `unmatched_preferred.csv` (siehe §3).

### `data/processed/delisted_trades.csv`

Liste der Tickers, für die yfinance auch nach Hyphen-Fallback und Preferred-Lookup keinen Sektor liefert. Mögliche Ursachen:
- Firma ist delisted / insolvent / akquiriert.
- Ticker wurde umbenannt (z.B. `FB` → `META`).
- Spezial-Instrumente (Warrants, Rights) ohne Sektor-Tag.

**Kein Review nötig** — Information für spätere Datenqualitäts-Reports. Die Trades fließen weiter in den Master, nur die Spalten `sector` / `industry` bleiben leer.

Spalten:
```
ticker, ticker_type, company_name, classification_reason, transaction_date
BRK.B, shareclass, Berkshire Hathaway Inc. New Common Stock, SHARECLASS_DOT_SINGLE_LETTER, 2026-03-31
```

### `data/processed/ticker_liste_index.csv`

Wird von `script.py` (im Repo-Root) aus `delisted_trades_stage.csv` erzeugt. Hilfs-Index: pro Ticker ein einzelner Eintrag mit `asset_name` + ältestem `transaction_date`. **Reine Recherchehilfe** für manuelle Lookups (welcher Trade kam zuerst, was war der Asset-Name?). Keine Pipeline-Funktion.

---

## Zusammenfassung — Review-Reihenfolge nach jedem Pipeline-Lauf

Empfohlene Abarbeitungs-Reihenfolge (nach Hebelwirkung):

1. **`unmatched_names.csv`** — größter Hebel: ein Alias kann tausende Trades anreichern.
2. **`manual_review_tickers.csv`** — Top-10 nach `count_of_trades` durcharbeiten.
3. **`unmatched_preferred.csv`** — meist kurz, schnell abgearbeitet.
4. **`duplicate_inspection_sample.csv`** — nur stichprobenartig, einmal pro Major-Release.
5. **`delisted_trades.csv` / `ticker_liste_index.csv`** — nur lesen, nicht bearbeiten.

> **Faustregel:** Nach 3–4 Review-Iterationen sollte `unmatched_names.csv` auf <50 Zeilen schrumpfen, `manual_review_tickers.csv` auf <100. Was dann übrigbleibt, ist meist „long tail" und erfordert Pipeline-Code-Änderungen, nicht mehr nur Mapping-Pflege.
