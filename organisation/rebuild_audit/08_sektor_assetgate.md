# Audit 08: Sektor-/Industry-Anreicherung und Asset-Class-Gate

Stand: 2026-10-17. Datenbasis: `data/processed/trades_master_complete.csv` (114.245 Zeilen, nur per usecols gelesen), Hilfsskripte in `/tmp/audit_sektor/`. Keine yfinance-Requests ausgeführt (Befunde stammen aus Datenbestand und Caches).

## 1. Features

| Feature | Fundstelle |
|---|---|
| yfinance-Sektor/Industry-Enricher (`TickerSectorEnricher.enrich_batch`), liefert `sector_yf`, `industry_yf`, `quote_type_yf` | `utils/ticker_sector.py:77`, `:130` |
| Routing: nur STOCK/SHARECLASS/FOREIGN_STOCK/ETF/NUMERIC_SUFFIX werden abgefragt, PREFERRED über Parent-Ticker | `utils/ticker_routing.py:13`, `utils/ticker_sector.py:165-182` |
| Positiv-Cache (30-Tage-TTL) + Null-Cache (ohne TTL), JSON, Checkpoint alle 100 Ticker | `utils/ticker_sector.py:215-285`, `config/settings.py:329-338` |
| Bestätigungs-Prompt ab 100 neuen Tickern (per Default abgeschaltet) | `utils/ticker_sector.py:198`, `settings.py:333` |
| Delisted-Report + Retry-Runde für Netzfehler-Ticker | `utils/ticker_sector.py:_run_retry_round`, `_write_delisted_stage` |
| Identitäts-Check, damit leeres `.info` nicht als "delisted" gecacht wird | `utils/ticker_sector.py:37` |
| Einheitliches Retry/Backoff (2..128 s, 8 Versuche, ~254 s je Operation), Transient-/Permanent-Klassifikation per Exception-Typ und Textmuster | `utils/network_retry.py:40,88,116` |
| Netzwerk-Canary (Basket SPY/AAPL/MSFT, GREEN/RED, Verdikt-Cache) | `utils/network_canary.py:39` |
| Asset-Class-Gate ("Filter 0"): setzt `asset_class_excluded = ~ticker_type in {stock, foreign_stock, shareclass}` | `gates/asset_class_gate.py:26`, `config/settings.py:29`, Aufruf `scripts/import_trades.py:1161` |
| Taxonomie-Referenz yfinance (11 Sektoren, Industries; JSON/XLSX/"enriched" mit deutschen Beschreibungen) | `data/mappings/yf_sectors_with_industries*.{json,xlsx}` |
| Stage-Verdrahtung (3 fast identische Enricher-Blöcke) | `scripts/import_trades.py:2165, 2660, 2997` |

Die Sektor-Caches (`ticker_industry_cache.json` 2.739 Einträge, `ticker_0_cache.json` 2.231) liegen nicht in diesem Worktree, sondern im Haupt-Checkout `.../congress_tracker/data/mappings/`. `data/cache/` enthält nur `price_history.parquet` (kein Sektorbezug).

## 2. Funktionsprüfung

### Belegt

**Füllquote sector_yf (alle Trades):**

| ticker_type | Trades | Sektor-Quote | Gate-ausgeschlossen |
|---|---|---|---|
| stock | 112.176 | 80,6 % | 0 % |
| shareclass | 897 | 16,5 % | 0 % |
| foreign_stock | 33 | 36,4 % | 0 % |
| preferred | 167 | 52,1 % | 100 % |
| numeric_suffix | 559 | 0 % | 100 % |
| unknown / invalid / bond / warrant / when_issued / long_alpha_suspect / option / misc / unit | 413 | 0 % | 100 % |

- Behaltene Trades (Gate): 113.106 (volumen ca. 4,49 Mrd. USD), davon rund 20 % ohne Sektor (22.496 Trades, 2.142 von 4.865 Tickern). Volumengewichtet 77,1 % abgedeckt.
- Quote nach Jahr (behaltene Trades): 2013 59 %, 2016 67 %, 2019 76 %, 2022 85 %, 2025 93 %. Ursache ist Survivorship: yfinance liefert nur für aktuell gelistete Ticker einen Sektor. Ältere Trades sind systematisch schlechter angereichert, ausgerechnet der Backtest-Zeitraum.
- Größte Lücken: FB (549 Trades, Meta-Altticker), BRK.B (436, Punkt statt Bindestrich, Normalisierung greift nicht), WBA, ALXN, CELG, FDC, FI, SQ, ATVI, CBS, MMC (Umbenennungen/Delistings). Rund 18.700 behaltene Trades haben zudem keine FIGI.
- Industry-Taxonomie: alle vorkommenden `industry_yf`-Werte stehen in `yf_sectors_with_industries.json` (0 unbekannte). Kein Ticker hat mehrere Sektoren. Veraltete Branchennamen sind im Datenbestand daher nicht nachweisbar. Die Taxonomie wird aber aus denselben yfinance-Werten gespeist und prüft nichts unabhängig.
- Stichprobe 15 Ticker (AMD Tech/Semiconductors, ESS REIT-Residential, ACHC Healthcare/Medical Care Facilities, LINE REIT-Industrial, LZ Industrials, ROIV Biotech, MTSFY Real Estate, HCA, PHIN Auto Parts, WTS, COHR u. a.): 13 von 15 plausibel. COHR ("Scientific & Technical Instruments") ist yfinance-seitig fraglich, aber aktuell so geliefert. OCLCF ("ORACLE CORPORATION ORDINARY SHARES", Technology/Software) ist plausibel, der Ticker aber ein Mismatch-Verdacht (Oracle Corp Japan).
- **Fehlzuordnung durch Ticker-Wiederverwendung:** ALCO (3 Trades, Asset "ALCOA INC") bekommt `Consumer Defensive / Farm Products`, das ist Alico Inc (aktueller ALCO). Der Cache kennt nur den heutigen Emittenten, kein Abgleich mit `asset_name`.
- **ETFs passieren das Gate als "stock":** Es gibt keinen ticker_type `etf` im Master (`TickerType.ETF` ist laut Classifier "NUR von Enricher setzbar", wird aber nie gesetzt). `quote_type_yf` ist nur EQUITY oder leer, nie ETF. Im Behaltenen: 3.482 Trades mit `security_type_figi == "ETP"` (418 Ticker, ca. 177 Mio. USD, u. a. SPY 108, QQQ 58, VEA, GLD, IWD, VOO, VTI), plus Open-End-/Closed-End-/Fund-of-Funds (ca. 456). Sektor haben nur 2 dieser ETP-Trades. FB wird von OpenFIGI fälschlich als ETP geliefert (ein Ticker-Kollisionsproblem), ist also Meta-Aktie und ETP-Label zugleich.
- **Optionen als stock:** SPY/QQQ-Trades heißen "CALL SPDR S&P 500 ETF TR $470 EXP ...", werden aber als `stock` behalten (Optionen-Erkennung nur bei 3 Zeilen). VRNG (383 Trades) ist laut asset_name ein Vanguard-Indexfonds bzw. eine Munibond-Emission, läuft als `stock`.
- **Was fällt raus (1.139 Trades = 1,0 %):** numeric_suffix 559, preferred 167, unknown 153, invalid 94, bond 65, warrant 36, when_issued 35, long_alpha_suspect 25, option 3, sonst 2. Das ist größtenteils richtig. Fragwürdig: preferred (167) werden trotz vorhandenem Parent-Sektor (52 % gefüllt) ausgeschlossen, d. h. Aufwand ohne Verwendung. numeric_suffix (559) werden sogar an yfinance geschickt (Routing) und danach verworfen, Quote 0 %.
- Tests: `test_ticker_sector`, `test_network_retry`, `test_network_canary`, `test_network_resilience`, `test_post_trade_canary`: 85 passed in 1,6 s. Alle gemockt, kein echter Netzaufruf. Für `AssetClassGate` selbst existiert kein eigener Test (nur indirekte Treffer in `test_committee_mapping_v2_p3.py` / `test_committee_tag.py`, Gate dort nicht Testgegenstand).

### Vermutet / nicht geprüft
- Wie die Cache-TTL (30 Tage) den Datenbestand bei einem Neulauf verschiebt: Einträge ohne `cached_at` leben ewig, alle 2.739 aktuellen haben aber `cached_at` (2026-08-01). Nach Ablauf werden sie neu geholt, ein inzwischen delisteter Ticker landet im Null-Cache, die vorherige Sektor-Info geht verloren (kein Fallback auf alten Wert).
- Der Null-Cache hat keine TTL: ein einmal als "kein Sektor" gewerteter Ticker (z. B. temporäre yfinance-Aussetzer mit Identität) wird nie wiederholt. Bei FB, SPY, WBA ist er tatsächlich drin.
- Live-yfinance-Stichprobe wurde bewusst nicht gezogen; Branchenumbenennungen von yfinance (z. B. 2023/24) sind nur indirekt durch die Konsistenz mit der Taxonomiedatei bewertet.

## 3. Code-Qualität

- `ticker_sector.py` (647 Z.) ist eine gute, aber überladene Klasse: Cache-Persistenz, Retry-Queue, Canary, Delisted-Report, Bestätigungsdialog und `print`-Logging gemischt. `_fetch_misses` und `_run_retry_round` duplizieren den Cache-Schreibblock. Cache wird bei jedem Save komplett neu geschrieben (kein atomares Schreiben, kein Tmp-File).
- Netzwerkstack (`network_retry` 186 Z. + `network_canary` 177 Z. + Einstellungen + ~770 Zeilen Tests) ist für ein einmalig/periodisch laufendes Batch-Tool mit 2.700 Tickern überentwickelt: 8 Versuche, ca. 254 s Wartezeit je Ticker, dazu Canary-Orakel, Consecutive-Error-Abort und zusätzliche Retry-Runde. Die Idee des Canary (leeres `.info` vs. Delisting unterscheiden) ist aber fachlich richtig und löst ein echtes yfinance-Problem.
- Kerndefekt liegt nicht in Robustheit, sondern in der Semantik: Gate arbeitet nur auf `ticker_type` (Ticker-Syntax/OpenFIGI), ignoriert `security_type_figi`, `quote_type_yf` und `asset_name`, obwohl diese ETFs/Fonds/Optionen eindeutig erkennen würden. Das Modul-Docstring verspricht "ETFs, Options, Mutual Funds" auszuschließen, das stimmt nicht.
- `TickerType.ETF`, `SECTOR_LOOKUP_TYPES` mit ETF/NUMERIC_SUFFIX und das Gate widersprechen sich (abgefragt, aber nie verwendet).
- Gate selbst (60 Z.) ist sauber, aber ohne Test.
- `import_trades.py` enthält die Enricher-Verdrahtung dreifach (Zeilen 2165, 2660, 2997), Copy-Paste in einer 3.277-Zeilen-Datei.
- Taxonomie in drei Formaten (JSON, XLSX, "enriched" mit LLM-Beschreibungen): nur das JSON wird funktional gebraucht.

## 4. Wiederverwendbares Wissen

- yfinance-`.info` liefert bei Fehlern oft `{}` ohne Exception; leer ist nicht gleich delisted. Der Identitätsfeld-Check (`quoteType/symbol/shortName`) plus Canary-Basket ist eine tragfähige Unterscheidung.
- Sektor-Anreicherung über aktuelles yfinance ist survivorship-behaftet: ca. 35 % der Trades aus 2013-2016 und ca. 7 % aus 2025 ohne Sektor. Für Historie braucht es eine point-in-time-Quelle oder ein manuelles Mapping für Umbenennungen (FB->META, BRK.B->BRK-B, WBA, SQ->XYZ, FI->FISV etc.).
- Ticker-Wiederverwendung (ALCO) erfordert Abgleich Emittentenname vs. yfinance `longName`.
- Die 11 yfinance-Sektornamen (Technology, Financial Services, Healthcare, Industrials, Consumer Cyclical/Defensive, Communication Services, Energy, Basic Materials, Real Estate, Utilities) sind im Datenbestand komplett und konsistent; `yf_sectors_with_industries.json` (4,3 KB) ist die brauchbare Referenz.
- Zur Asset-Klassifikation taugen `security_type_figi` (Common Stock, ADR, REIT, MLP, ETP, Open-End Fund, ...) und die Wörter CALL/PUT/EXP/ETF im `asset_name` besser als die Ticker-Syntax. Vorsicht: OpenFIGI-Ticker-Lookup liefert bei umbenannten Tickern (FB) falsche Typen.
- Polite-Rate: 0,5 s Pause je yfinance-Call, ca. 2.700 Ticker = ca. 25 Minuten.

## 5. Rebuild-Empfehlung

**Neu schreiben, aber klein.** Sektor-Enricher und Gate sind als Konzept einfach; die aktuelle Implementierung ist 10x zu groß und hat inhaltliche Lücken.

- Behalten (Wissen/Konfiguration, nicht Code): Canary-Idee (als 20-Zeilen-Helfer), Identitätsprüfung, `yf_sectors_with_industries.json`, die Tickerbeispiel-Liste der Umbenennungen, Positiv-Cache-Format.
- Neu bauen: (1) ein Stage-Modul ca. 150 Zeilen: Cache-Dict, yfinance mit einfachem Retry (3 Versuche, kurzes Backoff), Null-Cache mit TTL (z. B. 90 Tage), atomisches Schreiben; (2) Gate als Funktion auf `security_type_figi` + `quote_type_yf` + Namensregex (Optionen/ETF/Fonds), ausgewiesen mit Grund-Spalte statt Bool; (3) Sektor-Historie: manuelles Umbenennungs-Mapping plus Emittentennamen-Abgleich; (4) Tests für das Gate (derzeit keine).
- Retry/Canary-Zusatzkomplexität (ca. 360 Zeilen + 770 Zeilen Tests) nur reduziert übernehmen; ein einzelner Canary-Check zu Stagebeginn reicht.
- Datenqualitäts-Sofortmaßnahmen, auch ohne Rebuild: ETP/Fonds/Optionen im Gate ausschließen (ca. 3.900 Trades, 3,4 %), FB/BRK.B/Umbenennungen mappen (rund 2.000+ Trades zusätzlich mit Sektor), Preferred-Parent-Sektor entweder nutzen oder Lookup sparen.
