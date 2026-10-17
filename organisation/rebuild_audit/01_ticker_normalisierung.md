# Rebuild-Audit 01 – Ticker-Normalisierung

Stand: 2026-10-17 · Grundlage: Worktree `agent/committe_matcher`, `data/processed/trades_master_complete.csv` (114.245 Zeilen), OpenFIGI-Cache `../congress_tracker/data/cache/ticker_type_cache.db` (5.191 Einträge, nur lesend geöffnet), eine kostenlose OpenFIGI-Stichprobe (1 Request, 4 Jobs). Hilfsskripte: `/tmp/audit_ticker/`.

**Kurzurteil:** Die Vermutung des Owners stimmt. Der Klassifikator arbeitet nach seiner eigenen Spezifikation korrekt, die nachgelagerte Normalisierung hat aber drei belegte Defekte, die sich bis in Returns, Schwarm-Gruppierung und Asset-Gate fortsetzen. Die Tests sind grün, weil sie diese Fälle mit unrealistischen Mocks abdecken.

---

## 1. Features

| Feature | Ort | Kurzbeschreibung |
|---|---|---|
| Offline-Klassifikator (Regex-Entscheidungsbaum, 17 Schritte) | `utils/ticker_classifier.py:270` (`_classify_inner`), Einstieg `:260` | rein syntaktisch: Ticker-String → `TickerType` + `ApiStrategy` + `ticker_base` |
| TickerType- und ApiStrategy-Enums | `utils/ticker_classifier.py:30`, `:51` | 18 Typen, 6 Strategien |
| Exchange-Suffix-Liste und Yahoo→OpenFIGI-Exchange-Mapping | `utils/ticker_classifier.py:89`, `:99` | 84 Yahoo-Suffixe, 26 Codes |
| Datenmüll-Liste | `utils/ticker_classifier.py:129` | 12 feste Strings |
| Muni-Override (UNIT/PREFERRED + Muni-Wort im Namen → BOND) | `utils/ticker_classifier.py:225-253` | einziger Ort, an dem `asset_name` überhaupt ausgewertet wird |
| OpenFIGI-Normalisierung, Routing pro Strategie | `utils/ticker_normalizer.py:215` (`normalize_batch`), `:290` | Dedup, Mapping, Search, CUSIP, optimistisches Mapping für STOCK |
| Search-Query-Cleaner | `utils/ticker_normalizer.py:95` | entfernt CMN/INC/CORP usw. |
| Typ-Filter für Search-Treffer | `utils/ticker_normalizer.py:43-70`, `:504` | |
| Ticker-Format-Umsetzung für OpenFIGI | `utils/ticker_normalizer.py:803` | `BRK-B`→`BRK.B`, `X PRA`→`X/PA` |
| ETF-Korrektur STOCK→ETF | `utils/ticker_normalizer.py:1074-1092` | **defekt, siehe 2.1** |
| SQLite-Cache mit TTL (90 Tage positiv, 7 Tage negativ) und Migrationen | `utils/ticker_normalizer.py:854-1000` | |
| HTTP-Resilienz (429-Backoff, Retry-After, 5xx, Netzwerkfehler, Retry-Runde, Abbruchschwellen) | `utils/ticker_normalizer.py:667`, `:704-790`, `:1000-1070` | |
| issuer_ticker-Fallback-Kette `share_class_figi → ticker_base → ticker` | `utils/issuer_ticker.py:53`, vektorisierte Kopie in `scripts/import_trades.py:2105-2113` | |
| Issuer-Gruppierung über SEC-EDGAR-CIK | `utils/issuer_group_mapper.py:90-130`, `:193` | SKIP-, Bypass- und CIK-Zweig, Fallback `TICKER:` |
| Routing-Sets für yfinance | `utils/ticker_routing.py:13`, `:25` | |
| Ignore-Liste | `data/mappings/ignored_tickers.csv` (1 Eintrag), `scripts/import_trades.py:722`, `:736` | |
| Pipeline-Stages tickerclassify, tickeropenfigi, issuer_group, tickeryfinance | `scripts/import_trades.py:2049`, `:2075`, `:2117`, `:2150`; Stage-Matrix `:250-272` | |
| Rekonstruktion der Klassifikation aus DataFrame | `scripts/import_trades.py:663` | klassifiziert in jeder Stage jede Zeile erneut |
| yfinance-Sektor-Enricher mit Delisted-Report | `utils/ticker_sector.py:77`, `:136` (647 Zeilen, nur am Rand geprüft) | |

## 2. Funktionsprüfung

### Tests
`pytest tests/test_ticker_normalizer.py tests/test_ticker_classifier.py tests/test_issuer_ticker.py tests/test_issuer_group_mapper.py tests/test_ticker_sector.py` → **161 passed in 2,51 s** (belegt). Alle externen Aufrufe werden gemockt. Die Mocks bilden die echte OpenFIGI-Antwort in den entscheidenden Fällen falsch nach (siehe 2.1).

### Verteilung `ticker_type` im Master (belegt)
stock 112.176 (98,2 %) · shareclass 897 · numeric_suffix 559 · preferred 167 · unknown 153 · invalid 94 · bond 65 · warrant 36 · when_issued 35 · foreign_stock 33 · long_alpha_suspect 25 · option 3 · misc 1 · unit 1 · **etf 0** · ignored 0.
Die Unknown-Quote ist mit 0,13 % klein. Das ist aber kein Qualitätsmerkmal: Jeder String aus 1–5 Buchstaben wird ohne inhaltliche Prüfung als `stock` eingestuft (`ticker_classifier.py`, Schritt 15).

FIGI-Abdeckung pro Typ: stock 84,2 % · numeric_suffix 71,6 % · bond 70,8 % · preferred 45,5 % · foreign_stock 30,3 % · warrant 11,1 % · **shareclass 0,0 %**.

### 2.1 DEFEKT: ETF-Erkennung greift nie (belegt)
- Die Prüfung in `ticker_normalizer.py:1082-1084` lautet `raw_st2 = payload.get("raw_security_type2") or payload.get("security_type_figi")` und danach `raw_st2 == "ETP"`. OpenFIGI liefert für ETFs aber `securityType="ETP"` und `securityType2="Mutual Fund"`. Belegt durch die Live-Antwort für SPY und durch den Cache (423 Einträge `ETP/Mutual Fund`, 0 Einträge mit `securityType2=ETP`). `raw_security_type2` ist also nie leer und nie gleich "ETP", die Bedingung ist nie wahr.
- Der Test `tests/test_ticker_normalizer.py:205-216` mockt `securityType2: "ETP"`. Diese Antwort gibt es in der Realität nicht, deshalb ist der Test grün.
- Folgen (belegt):
  - 0 Zeilen mit `etf`. 3.957 Zeilen sind laut FIGI ein Fonds (ETP 3.482, Closed-End-Fonds 254, Open-End-Fonds 200 usw.). Davon tragen 3.938 den Typ `stock` und haben `asset_class_excluded=False`. Sie laufen als Einzelaktien ins Scoring.
  - Der ETF-Bypass im CIK-Mapper (`issuer_group_mapper.py:54`) ist toter Pfad. ETFs werden deshalb über die Sponsor-CIK gruppiert, genau das, was `Ticker_Architektur.md` D4 als semantisch falsch ausschließt. Beispiel: `CIK:1415311` fasst EUO, VIXY und YCS (alle ProShares Trust II) zu einem Issuer zusammen.

### 2.2 DEFEKT: Share-Classes bekommen nie einen FIGI (belegt)
- `_normalize_for_openfigi` (`:803`) setzt `BRK-B` in `BRK.B` um. Live-Probe gegen OpenFIGI: `BRK.B` → `"No identifier found"`, `BRK/B` → Treffer BBG000DWG505, `BF/B` → Treffer. Das von Bloomberg erwartete Format ist der Schrägstrich.
- Ergebnis: 897/897 shareclass-Zeilen ohne FIGI. Betroffen u. a. BRK.B (436), BRK-A (99), LGF.B (91), LEN-B (48), BF.B (37). Im Cache stehen die Fehlschläge als Negativ-Einträge (`BRK-B:TICKER:* empty`), die alle 7 Tage erneut angefragt werden.
- Die CIK-Gruppierung rettet einen Teil: BRK.A, BRK-A und BRK.B landen alle auf `CIK:1067983`. Sie funktioniert aber nur, wenn EDGAR den Ticker kennt. RDS-A/RDS.B, LGF.B und CWEN.A fallen auf getrennte `TICKER:`-Keys zurück.
- Nebenbefund: `Ticker_Architektur.md:40` behauptet `ticker_base=BRK` für `BRK.A`. Der Code liefert `BRK-A` (`ticker_classifier.py`, Schritt 12).

### 2.3 DEFEKT: Ticker-Wiederverwendung und Delistings, keine zeitbezogene Auflösung (belegt)
OpenFIGI und EDGAR werden mit dem **heutigen** Ticker abgefragt, unabhängig vom Handelsdatum. Ein Abgleich mit `asset_name` findet nicht statt.

| Ticker | Trades | asset_name (Quelle) | heutiger FIGI-Treffer | Folge |
|---|---|---|---|---|
| FB | 719 (2014–2026) | FACEBOOK INC | PROSHARES S&P DYNAMIC BUFFER (ETP) | Preisreihe des ETF ab 2025-06-26; Baseline 39,09 für Trades ab 2015; post_return_90d = 0,0 |
| PCLN | 134 | PRICELINE GROUP | PICTET CLEANER PLANET (ETP) | gleiches Muster, Baseline 25,18 |
| INFO | 66 | IHS MARKIT | HARBOR PANAGORA ETF | |
| QLTI | 131 | ALTISOURCE RESIDENTIAL | GMO INTERNATIONAL QLTY ETF | |
| UN | 91 | UNILEVER | CORGI UNH 2X DAILY ETF | |
| ABX | 4 | BARRICK GOLD | ABACUS GLOBAL MANAGEMENT | falscher Sektor (Financial Services), falsche CIK |
| TROX, B, SI | 9/2/5 | Rolls Royce?, Barnes, Silvergate | Tronox, Barrick, Shoulder Innov. | falsche CIK/Sektor |
| X, TWTR, ATVI | 96/108/123 | US Steel, Twitter, Activision | kein Treffer | kein Return, kein Sektor |

- FB und META werden nicht zusammengeführt: FB läuft unter `TICKER:FB` mit der Share-Class-FIGI des ETF, META unter `CIK:1326801`.
- Größenordnung (heuristisch, Wortüberlappung `asset_name` vs. `asset_name_figi` = 0): 9.126 von 119.415 Zeilen mit FIGI (7,6 %), 631 Ticker. Darin steckt auch Rauschen durch kaputte Quellnamen. Die oben genannten Fälle sind einzeln geprüft.
- **Nebenbefund außerhalb des Bereichs (Returns):** `price_history.parquet` beginnt bei den meisten Tickern am 2021-05-28 (5-Jahres-Fenster). Bei 50.387 von 92.602 Zeilen mit `post_return_90d` startet die Preisreihe mehr als 30 Tage nach dem Trade. 47.591 Zeilen haben exakt `return=0` mit `price_90d == price_180d == baseline` (z. B. AAPL 677, MSFT 628). Das ist kein Ticker-Fehler, verfälscht aber jede Return-Auswertung. Bitte mit Audit 04 abgleichen.

### 2.4 DEFEKT: Der Asset-Typ der Quelle wird ignoriert (belegt)
- `tickertype_org` (Quiver `TickerType`, `import_trades.py:388`) wird eingelesen, aber nirgends ausgewertet.
- 1.119 Zeilen mit `OP`/`Stock Option` sind als `stock` (1.115) bzw. `shareclass` (4) klassifiziert. Optionsgeschäfte zählen damit als Aktiengeschäfte auf den Basiswert. Nur 3 Zeilen tragen den Typ `option`, weil die Regex ausschließlich das OCC-Format erkennt (`:143`).
- Anleihe-artige `asset_name` (BDS/REV/SCH DIST/Kupon-%): 1.269 Zeilen, davon 1.107 als `stock`. Beispiele: `SDOT` = "SOUTH CAROLINA ST PUB SVC AUTH REV" → FIGI "SADOT GROUP INC"; `TAXI` = "TEX INDPT SCH DIST" → ein ETF; `HWAY` = "HAWAII ST GO BDS" → ein ETF. Der Muni-Override (`:238`) greift nur für UNIT und PREFERRED, nicht für STOCK.
- Vermutung: Diese Muni-"Ticker" sind vom Upstream-Scraper aus Namensfragmenten erzeugt (CSAL, LEARQ, VRNG, EASTW).

### 2.5 Was nachweislich funktioniert
- Die Klassifikator-Regeln für Preferreds (`$`, `-P-X`, `.PR`), Warrants, Units, When-Issued, Foreign-Suffixe und Crypto sind deterministisch und testabgedeckt (41 Tests). Für Preferreds liegt die FIGI-Abdeckung bei 45,5 %.
- Standard-US-Aktien: 84,2 % FIGI-Abdeckung. Die CIK-Gruppierung führt echte Multi-Class-Fälle korrekt zusammen (GOOG/GOOGL → `CIK:1652044`, LEN/LEN-B, UA/UAA, LBTYA/LBTYK). `issuer_group_key` ist zu 100 % befüllt (CIK 89.020, TICKER 24.990, SKIP 235).
- Der Negativ-Cache verhindert wiederholte Fehl-Requests. HTTP-Fehler werden bewusst nicht negativ gecacht (`:368`, `:440`). Netzwerkfehler werden nicht verschluckt: Es gibt Retry, Retry-Runde und danach einen `RuntimeError`-Abbruch. Nicht-Netzwerk-Exceptions (`:746`) und unlesbare JSON-Antworten (`:755`) werden nur geloggt und als leeres Ergebnis zurückgegeben. Das ist vertretbar, aber in der Stage-Ausgabe nicht sichtbar.
- Die Dedup-Schicht in `normalize_batch` funktioniert (frozen dataclass als Key).

### 2.6 Weitere Beobachtungen
- Nebenbefund: 12.329 doppelte `trade_id` im Master (z. B. `2c2e2bcb6382b984` 44-mal). Das ist kein Ticker-Thema, gehört zu Audit 11.
- `issuer_ticker.py`, Docstring Z. 10-14: Die Behauptung "shareClassFIGI führt GOOGL und GOOG zusammen" ist falsch. Laut Cache hat GOOGL BBG009S39JY5 und GOOG BBG009S3NB21. Die Spalte `issuer_ticker` führt also keine Klassen zusammen, nur `issuer_group_key` (CIK) tut das.
- `asset_name` ist pro Ticker stark inkonsistent: 749 Ticker haben mehr als 5 verschiedene Schreibweisen, dazu OCR-Müll wie "NONI CHYU 1105UŽEKA" und "EXAMPLE: MEGA CORP".

## 3. Code-Qualität

- **Komplexität vs. Nutzen schlecht:** Rund 2.800 Zeilen in 6 Modulen. Der eigentliche Nutzen (FIGI für US-Aktien plus CIK) wäre in etwa 300 Zeilen machbar. Allein die Resilienz-Logik (429, Retry-After-Datumsparsing, 5xx, Netzwerk-Pattern, Retry-Runde, Checkpoints) umfasst etwa 300 Zeilen.
- **Duplikate:**
  - `_process_mapping`, `_process_optimistic_mapping` und `_process_cusip` sind drei fast identische Batch-, Cache- und Negativ-Cache-Schleifen (`:333`, `:406`, `:602`).
  - Der Empty-Payload-Dict ist fünfmal ausgeschrieben.
  - `_NETWORK_ERROR_PATTERNS` ist "bewusst dupliziert" mit `ticker_sector` (`:41`).
  - Die issuer_ticker-Kette existiert zweimal: `issuer_ticker.py` und vektorisiert in `import_trades.py:2105`.
  - Der Klassifikator wiederholt den 11-Felder-Konstruktor etwa 25-mal, statt eine Regel-Tabelle zu nutzen.
- **Toter oder irreführender Code:**
  - `TickerType.ETF` wird nie gesetzt (2.1), damit sind ETF-Bypass und ETF-Routing tot.
  - `TickerType.IGNORED` hat 0 Zeilen, die Ignore-Liste enthält einen einzigen Eintrag.
  - `_cache_flush()` ist ein leerer Hook (`:995`), trotzdem wird "Cache-Checkpoint gespeichert" ausgegeben.
  - `@dataclass(frozen=True)` auf `TickerNormalizer` (`:137`) zusammen mit eigenem `__init__` und mutierenden Countern ist widersprüchlich und funktioniert nur zufällig.
- **Spec-Fetisch:** Kommentare wie "1:1 aus Pitch, NICHT verändern" und "Änderungen an der Reihenfolge ... verboten" (`ticker_classifier.py:10-14`, `:86`) frieren eine falsche Grundannahme ein: Klassifikation allein über die Syntax des Ticker-Strings.
- **Tests mit Scheinsicherheit:** Die Mocks prüfen den Code gegen sich selbst, nicht gegen echte API-Formate. Es gibt keinen Fixture-Test mit echten OpenFIGI-Antworten.
- **Stage-Design:** `_reconstruct_classifications` (`import_trades.py:663`) klassifiziert in jeder Stage alle 114k Zeilen neu und mischt die Ergebnisse mit DataFrame-Overrides. Das ist fehleranfällig und schwer nachvollziehbar.

## 4. Wiederverwendbares Wissen für einen Rebuild

- **OpenFIGI-Formatregeln (live verifiziert):**
  - Share-Class mit Schrägstrich: `BRK/B`, `BF/B`. Punkt und Bindestrich treffen nicht.
  - Preferred als `ROOT/P<Serie>` bzw. `ROOT PR<Serie>` (`ticker_normalizer.py:803-816`, ungeprüft gegen live).
  - ETF-Erkennung über `securityType == "ETP"`, außerdem `securityType2 == "Mutual Fund"` und `securityType ∈ {Open-End Fund, Closed-End Fund, Fund of Funds}`.
  - Weitere vollständige `securityType`/`securityType2`-Paare stehen im Cache (Abfrage in `/tmp/audit_ticker/db.py`).
  - Search ohne `exchCode` verwenden, sonst 0 Treffer bei US-Preferreds (`:556`). Firmennamen-Cleaner `_clean_query_name` (`:95`) mit empirisch begründeter Suffixliste.
  - Rate-Limits und Pausen: `config/settings.py:353-366` (25/min ohne Key, Batch 10 bzw. 100).
- **Klassifikator-Regex** (`ticker_classifier.py:140-182`): OCC-Option, Preferred-Varianten, T-Bill, CUSIP (`^(?=.*\d)[A-Z0-9]{9}$`), Crypto-Paare, Test-Ticker. Die Reihenfolge-Logik "spezifisch vor generisch" ist sinnvoll und wiederverwendbar.
- **Mapping-Tabellen:** Yahoo-Suffixe (`:89`), Yahoo→Bloomberg-Exchange (`:99`), Muni-Marker mit Wortgrenze (`:225`, Begründung "AUTH" in SPAC-Namen), Datenmüll-Liste (`:129`).
- **CIK-Grundsatz** (`organisation/Ticker_Architektur.md` D1-D4): CIK als Issuer-Key für Multi-Class-Aktien, aber nie für ETFs (Sponsor-CIK). Diese Designentscheidung ist richtig, sie ist nur nicht umgesetzt. EDGAR-Quelle: `https://www.sec.gov/files/company_tickers_exchange.json` (enthält nur aktuelle Ticker).
- **Wichtigste Edge-Case-Liste für Tests eines Neubaus:** FB→META, PCLN→BKNG, INFO, UN/UL, QLTI, ABX→GOLD, TWTR, ATVI, X, RDS.A/RDS.B→SHEL, BRK.B/BRK-A/BRK.A, BF.B, LGF.B, LEN-B, CWEN.A, GOOG/GOOGL, Muni-Pseudo-Ticker (SDOT, TAXI, HWAY, CSAL), `tickertype_org=OP`.

## 5. Rebuild-Empfehlung

**Neu bauen.** Klassifikator-Regex und OpenFIGI-Formatwissen werden übernommen, die Architektur wird verworfen.

Begründung: Die drei Kernfehler (ETF, Share-Class, zeitbezogene Ticker-Identität) sind keine Einzelbugs. Sie folgen aus dem Ansatz "Ticker-String allein bestimmt das Wertpapier". Ein Neubau sollte:
1. **Quell-Asset-Typ zuerst auswerten:** `tickertype_org` bzw. PTR-Asset-Typ (Option, Bond, Fonds) vor jeder Regex prüfen.
2. **Identität zeitbezogen auflösen:** Ticker + Handelsdatum + `asset_name` → Wertpapier. Dafür eine kuratierte Tabelle für Ticker-Wechsel und Wiederverwendung (FB→META usw.), eine Namensabgleichs-Prüfung (Fuzzy-Score `asset_name` vs. FIGI-Name, bei Abweichung `manual_review`) und Preisdaten nur nach Datumsabdeckungs-Prüfung.
3. **Einen schlanken OpenFIGI-Client** mit einer generischen Batch-, Cache- und Negativ-Cache-Schleife und Fixture-Tests aus echten Antworten.
4. **ETF/Fonds über `securityType`** markieren und aus Einzelaktien-Signalen und CIK-Gruppierung herausnehmen.

Grober Aufwand: 3-5 Personentage. Davon entfallen etwa 1 Tag auf den Client und die Regeln und 2-3 Tage auf die Tabelle der Ticker-Wechsel plus Namensabgleich und Review-Workflow. Die Tabelle ist für die rund 600 auffälligen Ticker die eigentliche Arbeit; die Top 50 nach Trade-Anzahl decken den Großteil ab.

**Als Sofortmaßnahme im bestehenden Code möglich:** Bedingung in `ticker_normalizer.py:1082-1084` auf `raw_security_type == "ETP"` ändern und `_normalize_for_openfigi` auf `/` umstellen. Beides ist klein, behebt aber nicht 2.3 und 2.4.
