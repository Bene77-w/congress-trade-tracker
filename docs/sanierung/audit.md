# Sanierungsaudit (neu) – Congress Trade Tracker

Stand: 2026-11-20. Code-Stand: Branch `agent/sanierung-neuaudit` = Stand nach Sanierungsstufe 1 (`538ffff`), Basis vor Stufe 1 = Tag `pre-stufe1` (`d00ff2d`).
Quellen: 10 Dimensionsaudits (PIPE, TICK, SIG, CM, APP, SCR, TEST, CONF, DATA, ARCH) mit Skeptiker-Prüfung. Zwischenergebnisse liegen unter `/tmp/audit-neu/<dimension>/`.

**Lesehilfe**
- Befund-IDs verweisen auf den Anhang (Abschnitt 12).
- Die Schwere ist die vom Skeptiker korrigierte. Wurde ein Befund nur „teilweise“ bestätigt, steht er hier in korrigierter Form.
- **[ungeprüft]** heißt: Der Befund hat MITTEL oder NIEDRIG und wurde nicht vom Skeptiker nachgerechnet.
- **Alter Code:** Alle Vollaufzahlen stammen aus dem Dry-Run vom 2026-11-18, der im Haupt-Checkout mit `d00ff2d` lief, also vor Stufe 1.

---

## 1. Kurzfazit

| Kernziel | Befund | Beleg |
|---|---|---|
| (1) Vereinfachen | Der Produktivcode umfasst 42.089 Zeilen. Die Hälfte davon (21.033) ist der Committee Matcher, der zur Laufzeit keinen Konsumenten hat. Mit E1 (Matcher einfrieren), dem Suspicious-Export ohne Leser, den Roster-Parsern und den Fetcher-Endpunkten ohne Leser fallen **sicher rund 24.000 Zeilen (−57 %)** weg. Mit den offenen Entscheidungen sind **−65 bis −70 % (Ziel ≤ 15.000 Zeilen)** realistisch. Die Tests schrumpfen von 26.697 auf rund 9–10k Zeilen, die Laufzeit von 214 s auf unter 20 s. | CM-1, PIPE-4/EVT-1/S1A-1, SCR-7, SCR-5, TEST-6 |
| (2) Belastbare Zahlen | Nicht belastbar. Die Datenquelle ist seit 2026-08-03 tot (SCR-1). Die Assetklasse wird an 6 Stellen ohne Point-in-time-Bezug entschieden: Optionen, Fonds und Munis laufen als Aktie, recycelte Ticker als ETF (TICK-1/2/3/4). Die Vote-Daten decken nur den 119. Kongress ab und sind seit Mai veraltet und teilweise korrupt (SCR-2/3/4). Für 2013–2017 liegt die Kursabdeckung strukturell unter 80 % (TICK-5). | Abschnitt 4 |
| (3) Entscheidungsgrundlage | Der Vollauf vom 18.11. lief mit Altcode und brachte 0 neue Trades (PIPE-2, ES-17). Die vorläufige Event-Study zeigt gesamt keinen Abnormal Return. Der Score korreliert marktbereinigt leicht negativ (ES-7). Gepoolt ist die Methode durch die Handelsrichtung verzerrt (ES-4). Signalentscheidungen sind deshalb erst **nach Daten-Fixes und einem neuen Vollauf** möglich (Messplan in Abschnitt 8). | ES-1…ES-17 |
| Stufe 1 | Stufe 1 hat vor allem gelöscht (Produktivcode +837/−9.047 Zeilen). Ein Großteil der neuen Härtung floss aber in den Suspicious-Export, der keinen Leser hat (S1A-1). H1 verschiebt den Point-in-time-Defekt, statt ihn zu beheben (S1A-2). | Abschnitt 9 |

---

## 2. Ist-Architektur am Code

### 2.1 Module und Größen (wc -l, ohne tests/)

| Bereich | Zeilen | Rolle | Tiefe |
|---|---:|---|---|
| `committee_matcher/` + `scripts/committee_matcher_*.py` | 19.221 + 1.812 | Offline-Werkzeug, das einmalig das Mapping erzeugt hat | keine Laufzeitkante (CM-1) |
| `app/` | 5.425 | Streamlit-Dashboard, davon `streamlit_app.py` 2.453 auf Modulebene | flach, globaler Zustand (DASH-11) |
| `scripts/` ohne Matcher | 3.494 | `import_trades.py` 1.900, `reset_pipeline` 483, `build_reference_data` 411, `analyse_event_study` 349, `build_deploy_snapshot` 233, `report_integrity` 117 | Pipeline als flaches Skript in Klassenform (PIPE-6) |
| `utils/` | 3.669 | Ticker-Klassifikation, OpenFIGI, Sektor, Issuer, Netz-Retry, ReferenceData | Assetklasse über 6 Stellen verteilt (TICK-9) |
| `scrapers/` | 3.147 | Quiver (271), Vote-Fetcher (1.322), Roster-Parser (~1.420) | Parser ohne Produktwert (SCR-7) |
| `scoring/` | 2.040 | post_trade_returns (1.310), abnormal_returns (350), trade_score (304) | Kurs-Cache ohne öffentliche Schnittstelle (S1A-8) |
| `signals/` | 1.578 | 12 Signalklassen | flache Module mit kopiertem Event-Snapshot (ARCH-1) |
| `config/settings.py` | 957 | 214 Namen, 66 % Kommentar | globaler Kopplungspunkt (PIPE-12, CFG-1) |
| `tags/` + `gates/` | 676 + 69 | CommitteeTag, VoteTimingTag, AssetClassGate | Gate ist ein flaches Modul (PIPE-13) |
| **Summe Produktivcode** | **42.089** | | |
| `tests/` | 26.697 (60 Dateien, 2.121 Tests) | davon Matcher 14.487 | |
| Markdown (versioniert, ohne data/) | 14.939 | | |

### 2.2 Datenfluss

```
 systemd quiver-import.timer (deaktiviert, SCR-1)
   └─ scrapers/quiver_scraper.py ─► data/raw/congress-trading-all.xlsx  (letzter Download 2026-08-03)
                                          │
 scripts/import_trades.py  TradeImporter.run()   [1 wides DataFrame, 91 Spalten, kein Schema; PIPE-6]
   1-2  load_and_rename, normalize_dates, trade_id (Hash ohne Lot/Filing → 10,8 % Duplikate; PIPE-3)
   3    ReferenceData.assign ◄─ data/reference/*.csv ◄─ build_reference_data.py ◄─ congress-legislators (Netz)
                                                           ▲
                         data/committee_matcher_v2/reviews/map-fullrun-20261016/{reviewed_assignments,
                         reviewed_unit_coverage}.csv ◄─ committee_matcher/ (offline, keine Python-Kante; CM-1)
   4-7  classify(Regex) ► OpenFIGI (ETP→ETF) ► EDGAR ► yfinance-Sektor   [heutiger Ticker, ohne Datum; TICK-1]
   9    post_trade_returns / abnormal_returns ◄─► data/cache/price_history.parquet ◄─ yfinance (period=max seit S1)
   11   CommitteeTag ─ AssetClassGate(ticker_type) ─ VoteTimingTag ◄─ data/congress_api/*.json
                                                        ◄─ scrapers/fetch_congress_data.py (nur 119, Lock, Aliasing; SCR-2/3/4)
   12-14 12 Signale (nur ~gate) ─► 3 *_events_stage.csv ─┐
   15   compute_trade_scores (5·committee_full + 20·swarm_buy)  │
   16   Suspicious-Export ─► suspicious_events/assets/members.csv  ◄┘   (KEIN Leser; PIPE-4)
   17   save_results ─► trades_master_complete.csv (645 MB, direkt geschrieben; S1A-6)
                       + trades_master.csv (curated, kein Leser; PIPE-8)
                                          │
        ┌─────────────────────────────────┼──────────────────────────────────┐
        ▼                                 ▼                                  ▼
 build_deploy_snapshot.py          app/streamlit_app.py               scripts/analyse_event_study.py
 (Parquet + Kurs-Cache +           liest Master/Snapshot, rechnet      liest Master fest verdrahtet,
  Referenztabellen → deploy/)      Score, Renditen, Ausschüsse nach;   rechnet AR neu, importiert
                                   importiert private scoring/signals  app.score_correlation (umgekehrte Kante)
```

### 2.3 Kopplungen (Leakage, fehlende Seams)

| Kopplung | Beleg | Befund |
|---|---|---|
| Das breite DataFrame ist das einzige Interface zwischen den Stages. Signale lesen Tag-Spalten und JSON-Formate direkt. | `signals/committee_signal.py:117` (parse_relevant_sectors) | PIPE-6 |
| Netz-Clients werden in `run()` erzeugt, Pfade sind Modul-Globals. Der E2E-Test braucht ~25 Monkeypatches. | `import_trades.py:1650-1706`; `tests/test_suspicious_export.py:73-90, 724-735` | PIPE-6, S1A-7 |
| Der Domänenbegriff „Richtung“ ist eine private Funktion in einem Signalmodul und wird 39-mal importiert, auch von `app/` und `scoring/`. | `signals/committee_signal.py:52-68`; Duplikat `app/staging_export.py:40-55` | PIPE-10 |
| Private Funktionen des Kurs-Caches werden von 6 Modulen importiert. | `abnormal_returns.py:41-46`, `report_integrity.py:20`, `app/streamlit_app.py:1441,1761`, `member_detail.py:155` | S1A-8, DASH-10 |
| Das Analyse-Skript importiert aus dem UI-Paket. | `scripts/analyse_event_study.py:39-43` | ES-9 |
| Die Assetklasse wird an 6 Stellen entschieden; zwei Quellen (`tickertype_org`, `quote_type_yf`) sind ungenutzt. | `settings.py:29-33`, `ticker_normalizer.py:1087`, `ticker_classifier.py:238`, `issuer_group_mapper.py:45-54`, `ticker_routing.py:13-30` | TICK-9 |
| `settings`: 214 Namen, 36 importierende Module, 63–70 `getattr`-Schatten-Defaults; 15 Defaults weichen ab, darunter MATCH_MODE. | `tags/committee_tag.py:101` | CFG-1, PIPE-12 |
| Der Ausschuss geht als ID → Name (F2-String) → Parse → Name → ID durch. | `utils/reference_data.py:84-104, 139-160` | CM-11, S1A-16 |
| Zwei Kongressbegriffe: `CONGRESS = 119` im Fetcher und `REFERENCE_CONGRESSES` in den Settings. | `fetch_congress_data.py:53`, `settings.py:407-417` | SCR-6 |
| Die Lock-Semantik des Fetchers ist im Aufrufer ohne PID-Prüfung nachgebaut. | `import_trades.py:637-649` vs. `fetch_congress_data.py:1247-1264` | SCR-4 |
| Das Dashboard-Image installiert die Abhängigkeiten von Scrapern und Matcher (Playwright, pdfplumber, openrouter). | `deploy/Dockerfile.app:15` | SCR-12 |

---

## 3. Größenbilanz

### 3.1 Produktivcode

„Sicher“ heißt: bestätigt, teilweise bestätigt mit korrigiertem Wert oder entschieden (E1–E5). „Bedingt“ heißt: Der Befund ist ungeprüft oder hängt an einer offenen Entscheidung (Abschnitt 11).

| Bereich | heute | sicher | bedingt / ungeprüft | Ziel (ca.) | Belege |
|---|---:|---:|---:|---:|---|
| Committee Matcher | 21.033 | −21.033 | – | 0 | CM-1, E1 |
| `scripts/import_trades.py` | 1.900 | −520 | −300 | ~1.000 (Orchestrator ~80, Rest in Module) | S1A-1, PIPE-8/9/13/14, S1A-11/13 |
| `signals/` + `utils/signal_event_id.py` | 1.677 | −380 Event-Snapshots/IDs, −200 committee (E2) | −600 (vektorisieren, SIG-3/5/7) | ~500 | PIPE-4, CM-6, ARCH-1 |
| `utils/` ohne signal_event_id | 3.570 | – | −1.345 | ~2.200 | TICK-8/9/10/11/12/13 |
| `scrapers/` | 3.147 | −1.420 Parser, −350…−450 Endpunkte | −130 (Senate-Rohsicherung) | ~1.150 | SCR-7, SCR-5 |
| `scripts/` übrige | 1.594 | −15 | −420 (reset_pipeline) | ~1.150 | ES-8, SCR-9 |
| `scoring/` | 2.040 | – | −500 | ~1.550 | SCORE-1, S1A-4/10, PIPE-11, ES-9 |
| `tags/` + `gates/` | 745 | −60 (Match-Modi, E2) | −65 Gate; −403 VoteTimingTag (nur falls floor_action entfällt) | ~600 | CFG-2, GATE-4, SIG-6 |
| `app/` | 5.425 | – | −1.800 | ~3.600 | DASH-4/5/6/8/9/11/15/16, S1A-5 |
| `config/settings.py` | 957 | −126 (Matcher-Block, E1) | −90 | ~350–400 (+ Konstanten zu den Konsumenten) | CM-10, CFG-3/4/6 |
| **Summe** | **42.089** | **≈ −24.100 (−57 %)** | **≈ −5.650** | | |
| Neu hinzukommender Code | | | +400…+600 (Instrument-Auflösung, event_study-Modul, Coverage-NA, Gates) | | Abschnitt 6 |

**Realistisches Ziel:** 13.000–15.000 Zeilen Produktivcode, also **−65 bis −70 %**. Vorschlag für den Ratchet nach E5: Startwert gleich dem Ist-Stand, Zielzahl **15.000** nach Abschluss der Löschtickets, danach weiter senken.

### 3.2 Tests und Doku

| Bereich | heute | sicher | bedingt | Ziel |
|---|---:|---:|---:|---:|
| Tests gesamt | 26.697 | −14.487 Matcher (E1), −~700 `test_suspicious_export`, −141 `test_signal_event_id`, −95 `test_senate_xml_parser` | −~2.600 (Score-Editor ~1.100, Canary 379+, VPS-E2E 271, staging 190, TICK-Pfade ~360, `test_issuer_ticker` 122, AST-Tests −150) | **9.000–10.000**, plus ~400 neue Verhaltenstests (Abschnitt 10) |
| Testlaufzeit | 214 s (davon ~90 % Matcher) | < 20 s (Produkttests offline 19,6 s) | | < 20 s |
| Doku (Markdown) | 14.939 | −~2.800 Matcher-Pläne, −~650 zu gelöschtem Code | −~7.000 Neuordnung | 3.500–4.000 |

Belege: TEST-6, CM-9, TEST-7/8/9/13, DOKU-1/4/5/6.

---

## 4. Defekte in den Zahlen

Die Befunde sind nach Mechanismus zusammengeführt. Die Zahlen gelten für den Vollauf mit Altcode (114.347 Zeilen), sofern nicht anders angegeben.

### 4.1 Kanonische Zahlen (Auflösung der Widersprüche)

| Größe | Kanonischer Wert | Zählbasis / Auflösung |
|---|---|---|
| trade_id-Duplikate | **12.335 überzählige Zeilen (10,8 %)**. Sie verteilen sich auf 10.446 mehrfach vergebene IDs; zusammen sind **22.781 Zeilen (19,9 %)** an einer Duplikatgruppe beteiligt. Es gibt 102.012 eindeutige IDs. | Die 20.351 aus EVT-1 sind doppelte Paare (signal_event_id, trade_id) in den Event-Dateien, also eine andere Basis. Die Formulierung aus TEST-10 („20 % der Zeilen doppelt“) ist falsch; richtig ist: 19,9 % der Zeilen sind beteiligt. Rund 9.300–9.460 Gruppen sind in disclosure_date, Upload und last_modified identisch (PIPE-3, ES-15). |
| Ticker-Recycling (Aktie wird zum ETF) | **1.154 Zeilen / 47 Ticker, davon 997 mit Signal und 173 mit Score > 0. Abzüglich ~35 bekannter Nicht-Aktien (CONN, DSCO, PSTR) bleiben ≈ 1.120 Aktienzeilen.** | Heuristik H-syn (`/tmp/audit-neu/synthese/recycle.py`): `ticker_type=stock`, `security_type_figi=ETP`, kein Fonds- oder Anleihe-Marker im Quiver-Namen, keine Namens-Token-Überlappung mit `asset_name_figi`. Die übrigen Werte (932–1.224) streuen nur wegen der Heuristik (TICK-1, GATE-2, TEST-1, S1A-2, ES-16). Top: FB 549, PCLN 94, ANSS 77, QLTI 67, INFO 60, UN 59. |
| Optionen durch das Gate | Laut Quelle 1.124 Optionen (OP 637 + „Stock Option“ 487). Nach der ETP-Regel aus Stufe 1 kommen **≈ 1.044–1.048 durch das Gate, davon 991 mit Signal und 258 mit Score > 0**. | Die 76 Optionen auf ETP-Basiswerte (SPY, DIA) fallen durch H1 ohnehin heraus (GATE-1 korrigiert, TICK-2). |
| Trades ohne Return | **62.161 Trades aus 2012–2020 (54,4 % von 114.347)** liegen bei 0 % Kursabdeckung. Ohne `signed_ar_90d` sind insgesamt **61,5 %** aller 114.347 Zeilen. | Basis sind immer alle 114.347 Zeilen. Die „70.400 (61,6 %)“ aus ES-1 sind falsch (Skeptiker ES-1). |
| TradeImporter | 39 Methoden + 6 Modulfunktionen = 45. Es gibt 29 `monkeypatch.setattr`-Zeilen, davon ~25 auf Globals, Klassen oder private Methoden; 17 Pfad-Globals. | Skeptiker PIPE-6 |
| Matcher-Anteil der Tests | Matcher-Tests machen **≈ 90 % der Laufzeit** aus (202 von 224 s laut CM, 184 von 199 s laut TEST), dazu 1.285 von 2.121 Tests und 14.487 von 26.697 Zeilen. | Die Messungen liefen in unterschiedlichen Umgebungen; beide liegen bei 90–93 %. 21.033 Produktivzeilen = 19.221 Paket + 1.812 `scripts/committee_matcher_*.py`. |
| Vote-Daten | Zwei Ursachen ergänzen sich. (a) Die Daten decken **gewollt nur den 119. Kongress** ab (`CONGRESS=119`). (b) Der Stand ist seit 2026-05-02 veraltet, weil eine **verwaiste Lock-Datei** (PID 42005, 2026-05-07) den Auto-Refresh blockiert. Die Pipeline macht dabei nur eine WARN-Zeile und rechnet mit dem alten Stand weiter (still). | PIPE-1 (korrigiert), SCR-3, SCR-4. Schwere SCR-3: **HOCH**, weil floor_action mit Gewicht 0 nicht in den Score eingeht. |

### 4.2 Defekttabelle

| ID | Schwere | Mechanismus | Zahlenwirkung | durch Stufe 1 behoben? | Vorschlag |
|---|---|---|---|---|---|
| SCR-1 | KRITISCH | Die einzige Trade-Quelle (Quiver-Export) schlägt seit 2026-08-04 fehl: 95 ERROR-Zeilen, der Login klappt, aber der Export-Link wird nicht gefunden; der Timer ist deaktiviert. | Neueste Meldung 2026-07-21; alle Trades danach fehlen. Der Vollauf brachte 0 neue Zeilen (ES-17). | nein | Abo bzw. Selektor manuell prüfen (O1). Frische-Kennzahl in `report_integrity` aufnehmen. |
| ES-2 | KRITISCH | Im August-Master hat der alte Lookup t0 und t0+H auf denselben ersten Cache-Kurs gelegt. | 49.532 Zeilen (43,3 %) mit künstlichem `post_return_90d = 0`, sehr wahrscheinlich auch im Dashboard. | Code ja (Gap-Schutz), Daten nein | Neuen Vollauf **speichern und deployen**. Den August-Master nicht als Return-Vergleich nutzen. |
| ES-3 / CM-4 / S1A-17 | KRITISCH (ES-3) / MITTEL (CM-4) | Referenzdaten gibt es nur für 118 und 119; 118 nutzt die Mitgliedschaften des 119. | 79.274 Trades (69,3 %) mit `congress_not_covered`; Committee-Signale gegenüber August −71 % (19.088 → 5.474); Score > 0 von 24,5 % auf 14,2 %. | nein (P4 hat den Legacy-Fallback entfernt) | **E3 entschieden:** dokumentieren, kein Nachladen. Offen: `committee_full_rate` mit dem Nenner aller Trades (`app/member_metrics.py:739`) und den Score-Hard-Gate über die Zeit kennzeichnen (CM-4 korrigiert). Die Event-Study grenzt schon über `committee_eligible_mask` ein. |
| CM-3 | HOCH | Wie ES-3: Für Trades 2023/24 gelten die Ausschüsse von 2025/26. | 43 % der committee_full-Signale (2.365) sind anachronistisch. 575 Trades fallen wegen Ausscheidens auf `no_membership`; die 108 Kammerwechsler-Trades sind nur in der Anzeige falsch. | nein | **E3 entschieden:** bewusst so lassen und dokumentieren. Die Event-Study weist 118 und 119 getrennt aus. |
| CM-2 / SCR-6 | HOCH | Ab 2027-01-03 (in 44 Tagen) liegen neue Trades außerhalb von `congresses.csv` und fallen still auf `not_covered`. Der Fetcher holt weiter nur 119. `build_reference_data` schreibt die Tabellen vor `check()`. | Neue Trades zu 100 % ohne Committee- und Vote-Signal (119: 16,9 % committee_full). | nein | **E3:** laut scheitern statt still null. Gate in `import_trades`, unaufgelöste Units als FEHLER **vor** dem Schreiben, ein gemeinsamer Kongressbegriff. |
| TICK-1 / GATE-2 / TEST-1 / S1A-2 / ES-16 | HOCH | OpenFIGI, yfinance und EDGAR lösen den **heutigen** Ticker ohne Handelsdatum auf (`ticker_normalizer.py:791-800, 888-898`). H1 macht daraus seit Stufe 1 einen ETF-Ausschluss. Pseudo- und Falschticker der Quelle (GOGO = Google, KEN, BCPC, CAE, RH) bekommen fremde Kurse. | ≈ 1.120 Aktienzeilen fälschlich ausgeschlossen (997 mit Signal, 173 Score > 0), siehe 4.1. Fremdkurse bei weiteren Tickern: Heuristik 3.889 Zeilen auf 265 Tickern, davon ~840 harmlose Umbenennungen (nicht exakt quantifiziert). | **verschoben**: vorher Fremdkurse, jetzt Ausschluss | Primär ein Namensabgleich `asset_name` ↔ `asset_name_figi`, ergänzt um einen Datumstest (erster Kurs ≤ Trade + 5 Tage). Bei Widerspruch gilt `asset_class=unresolved`: weder ETF noch Fremdkurs. Kein Fix pro Ticker (O11). |
| TICK-2 / GATE-1 | HOCH | `tickertype_org` (Quiver-Typcode) wird umbenannt, aber nie gelesen (`import_trades.py:239`). Optionen laufen als Aktie des Basiswerts. | ≈ 1.044–1.048 Optionen durch das Gate (991 mit Signal, 258 Score > 0). In 371 Beschreibungen steht PUT, damit ist das Vorzeichen der Returns falsch. Dazu kommen 87 Anleihe-Codes. ~150 Optionen tragen den Code ST oder keinen Code und werden vom Mapping nicht erfasst. | nein | `tickertype_org` als erste Quelle der Assetklasse nutzen (OP/„Stock Option“ → option, CS/GS/„Corporate Bond“ → bond, AB/ET → fund). Zusätzlich PUT/CALL/OPTION in `description` prüfen. Schwere HOCH wegen der Vorzeichenfehler; GATE-1 bewertet nur die Signalseite mit MITTEL. |
| TICK-3 / GATE-3 / TEST-4 | HOCH (TICK-3) | Die ETP-Korrektur ist eine Blacklist mit einem einzigen Eintrag (`ticker_normalizer.py:1087`). Open-End-, Closed-End- und Dachfonds bleiben `stock`. | 422 Fondszeilen (300 mit Signal, 10 Score > 0), dazu bis zu 650 Zeilen mit Fondsnamen ohne FIGI (244 davon VRNG). Die Returns sind echte Fondskurse; die Wirkung trifft Signale und Score. 196 Closed-End-Zeilen haben Sektor „Financial Services“. | nein | Whitelist auf `securityType` (Common Stock, ADR, REIT, MLP, NY Reg Shrs, Ltd Part, Royalty Trst). BDCs bewusst entscheiden (O10). |
| TICK-4 | MITTEL | Der Muni-Override greift nur bei UNIT/PREFERRED (`ticker_classifier.py:240-241`). | 585 Zeilen, 564 mit Signal, 59 bepreist. RH/NYC (−582 %) erfasst erst ein breiteres Bond-Muster. | nein | Den Typ-Guard entfernen, mit Negativprobe GCBC. Danach ein breiteres Bond-Muster mit Stichprobe. |
| TICK-5 / ES-1 / ES-11 | HOCH | Der Cache beginnt 2021-05-28 (altes `5y`). `period=max` und Rückbefüllung stehen seit Stufe 1 im Code; 1.480 Ticker sind delistet, übernommen oder umbenannt. | Heute 0 % Kurse für 2012–2020 (62.161 Trades). Obergrenze nach der Rückbefüllung: **84 %**, je Jahr 2013 63,8 %, 2014 69,9 %, 2015 71,2 %, 2016 72,4 %, 2017 76,5 %, 2018 79,3 %, 2019 78,7 %. Echte Survivorship-Lücke ≈ 16.300 Trades (14,9 %). | Code teilweise (max, Share-Class-Symbol), Daten nein | Das P7-Gate (≥ 80 % je Jahr) ist für 2013–2019 nicht erreichbar und muss angepasst werden (O13). Abdeckung je Jahr immer mitberichten. Optional eine Umbenennungstabelle (O12). |
| ES-12 | NIEDRIG (Risiko für P7) | Der Kurs-Cache enthält dauerhafte Niveausprünge (Splices): KLAC 304,29 → 30,91 am 2021-06-14, CTVA 46,08 → 6,94. _build_price_lookup filtert nur NaN und ≤ 0. | Heute nur 5 Trades verfälscht (NYC, CISS). Als **Risiko** gilt: Die Rückbefüllung mit `period=max` führt über `_merge_into_cache` erneut alte und neue Serien zusammen (Vermutung). | nein | Vor der Rückbefüllung den Cache löschen (S1A-4) statt zu mergen. Plausibilitätsgate auf Niveausprünge, nicht nur auf Spikes. |
| SCR-3 / PIPE-1 | HOCH | Vote-Daten gibt es nur für den 119. Kongress. `Vote_Timing_Hit=False` unterscheidet „nicht auswertbar“ nicht von „kein Treffer“. | 95.934 Trades (84 %) ohne Abdeckung. Vote-Timing-Quote und Red-Flag je Mitglied (Schwelle 20 %) sind verdünnt; der Score ist nicht betroffen (Gewicht 0). | nein | Außerhalb der Abdeckung `NA` statt False setzen. Quoten und Event-Study nur über abgedeckte Trades rechnen. |
| SCR-4 | HOCH | Eine verwaiste Lock-Datei (PID 42005) blockiert seit 2026-05-07 jeden Auto-Refresh. Der Aufrufer prüft die PID nicht. | 0 von 1.518 Trades nach 2026-05-03 mit Treffer (erwartet ~250–290); auch der April ist gedrückt (13,6 % statt 17–21 %). | nein | Lock-Prüfung mit Stale-Erkennung in den Fetcher verlegen. **Erst nach SCR-2** die Lock-Datei entfernen. |
| SCR-2 | HOCH | Listen-Aliasing im Delta-Update: `new_details = new_actions = new_committees = []` (`fetch_congress_data.py:1116-1117`). | 724 von 13.469 Bills (5,4 %) haben keine `actions`; geschätzt sind einige Dutzend Roll-Call-Bills verloren. **Latent:** Jeder Delta-Lauf korrumpiert erneut. | nein | Listen getrennt initialisieren und die 724 Bills neu laden. Test mit 51 Bills. **Vor SCR-4 umsetzen.** |
| SCR-8 | HOCH | Die Event-Study vergleicht floor_action „mit/ohne“ über alle Jahre, das Signal kann aber nur ab 2025 feuern. | Die Gruppe „ohne“ enthält 95.934 nicht beobachtbare Trades. Der Confounder ist Epoche bzw. Zusammensetzung, nicht die Marktphase (AR gegen SPY). | nein | Allgemeine `signal_eligible_mask(df, signal)`, dazu `--master`. |
| PIPE-3 / DASH-2 / CNT-1 / ES-15 / TEST-10 | HOCH (PIPE-3; Kategorie Inkonsistenz) | `trade_id` hasht 8 Quellspalten ohne Lot oder Filing (`utils/trade_id.py:33-42`). Nur Korrelationsansicht und Event-Study deduplizieren, Pipeline und Kerntabs nicht. Sequenz- und Schwarm-Signale schreiben per ID auf alle Zeilen. | Drei verschiedene Populationen; 168 IDs mit widersprüchlichen Signalen; 213 Abweichungen zwischen Master und Events. **Die Fehlerrichtung ist offen:** Entweder überzählt das Dashboard um bis zu 10,8 %, oder die Event-Study verwirft echte Lots. | nein | Die Entscheidung einmal im Ingest treffen (O3): Dedup oder `lot_index` in der ID. |
| ES-4 | HOCH | Der gepoolte Vergleich „mit/ohne Signal“ ist durch die Handelsrichtung verzerrt: Käufe liegen bei −0,58 %, Verkäufe bei +0,72 %. | swarm_buy: gepoolt +0,57 pp (n. s.), nur Käufe +1,50 pp, CI [+0,61; +2,30]. committee_full: Käufe +1,47 pp, Verkäufe −2,75 pp. | nein | Schichtung nach Richtung (`strata`) im gemeinsamen Event-Study-Modul. |
| ES-7 | HOCH | Das Dashboard validiert den Score am rohen `post_return` (Default 180 Tage). | Spearman roh +0,056 bis +0,062, marktbereinigt −0,013 bis −0,018: das Vorzeichen kippt. | nein | Den AR als Standard-Lineal verwenden; die Pro-Signal-Tabelle ersetzt den Score-Spearman (O7). |
| SIG-1 | HOCH | swarm_buy (Gewicht 20) feuert bei 2 Käufern in 7 Tagen ohne Bezug zur Grundrate. Verkettete Cluster reichen bis 63 Tage. | Quote bei den Top-20-Tickern 57,8 %, sonst 15,8 %. 64 % der swarm_buy-Zeilen stammen von Emittenten mit mehr als 20 Käufern (Dosis-Effekt). | nein | Prüfung gegen die Grundrate in der Event-Study; Cluster auf das qualifizierende Teilfenster beschränken. |
| ES-5 | MITTEL | Ro Khanna stellt 38 % des N. Es wird nur nach Mitglied geclustert. | Je nach Cluster-Wahl verschiebt sich p in beide Richtungen (swarm_buy 0,16 → 0,55; committee_full 0,51 → 0,18). | nein | Zweifaches Clustering; Sensitivität ohne das Top-1-Mitglied und mitgliedergewichtet. |
| ES-6 | MITTEL | Konstrukt B (Anker disclosure_date) kennt kein `knowable_date` des Signals. | Heute wird B nicht pro Signal getestet; künftig hätten 39 % der Schwarm-Zeilen Look-ahead. | nein | `knowable_date` je Event einführen, bevor B pro Signal getestet wird. |
| ES-8 | MITTEL | Das Report-Skript enthält fest eingetragene Befunde („KEIN Signal …“, „H=90“, „R² 1,2 %“). | contrarian bei H120 ist p = 0,002, der Text behauptet das Gegenteil. | nein | Feste Texte löschen; `--master/--cache/--out`. |
| SCORE-1 | MITTEL | Der Default-Score ist exakt 5·committee_full + 20·swarm_buy (4 Werte). | Nimmt 4 Werte an, Spearman zum AR −0,013. | nein | Siehe O5. Das Preset „Bene2“ belegt, dass der Editor genutzt wurde. |
| CM-5 | MITTEL | Elternausschüsse ohne eigene Branchen (8 delegierte: HSAP, HSBA, HSIF, HSJU, HSPW, HLIG, SSCM, SSEV) vererben nichts. | +26 committee_full-Trades, 25 davon bei Chair oder Ranking Member. | nein | **E4 entschieden:** Nur Chair und Ranking Member erben die Industries aller Unterausschüsse. |
| CM-6 / SIG-2 / ES-10 / CFG-2 / TEST-12 | MITTEL [ungeprüft, Daten mehrfach unabhängig gleich] | Bei MATCH_MODE `sector_and_industry` gilt committee ≡ committee_full. | 5.474 doppelte Events; signal_count +1 bei 5.474 Trades; doppelte Hypothese in der Event-Study. | nein | **E2 entschieden:** `committee` entfällt, ein Committee-Modul, `committee_ex_officio` stützt sich auf committee_full. |
| CM-15 | NIEDRIG | Mitgliedschaften gelten für den ganzen Kongress; `first_seen`/`last_seen` werden ignoriert. | 39 von 3.109 committee_full-Trades im 119. Kongress (tolerant), strikt 466. | nein | Filter nach Handelsdatum (~5 Zeilen). |
| SIG-4 | NIEDRIG | extreme_report vermischt späte und schnelle Meldungen in einem Token. | Fast-Zweig 3.283 Treffer, davon 49 Datums-Fallback-Artefakte. | nein | In late/fast trennen oder fast streichen; `date_fallback`-Flag einführen. |
| SIG-6 | MITTEL [ungeprüft] | floor_action prüft weder Kammer noch Mitglied. | P(floor_action \| committee) = 51 %. | nein | Kammerfilter einbauen oder streichen (O6/O8). |
| TICK-7 | MITTEL | Der Sektor-Null-Cache hat keine TTL. | ~160 echte Aktien-Trades ohne Sektor, also ohne Committee-Signal. | nein | Null-Cache als Dict mit Datum und Recheck. |
| DASH-1 | MITTEL | Members werden nach (name, chamber) gruppiert statt nach `bio_guide_id`. | 419 statt 342 Personen; 2.322 Trades (2,0 %) abgespalten, 21 Personen stark verzerrt. | nein | Nach bio_guide_id gruppieren, Kammer aggregieren; Namensnormalisierung in der Pipeline. |
| DASH-7 | MITTEL | Der Asset-Name kommt nur aus `asset_name_figi`. | 1.735 von 5.078 Assets (34 %) ohne Namen. | nein | Rückgriff auf `asset_name`. |
| TICK-6 | MITTEL [ungeprüft] | Ausgeschlossene ETFs landen mit AR in der Gruppe „ohne Signal“. | ~3.600 Trades (ca. 3 %) mit AR ≈ 0 in der Vergleichsgruppe. | nein | `analysis_population` filtert `asset_class_excluded`. |
| TICK-13 | NIEDRIG | Pseudoticker (*TEST, NUMERIC_SUFFIX) werden heuristisch klassifiziert. | 46 Zeilen „invalid“, 559 Zeilen ausgeschlossen (meist zu Recht); 33 FOREIGN ohne Kurs. | nein | Als `unresolved` statt `invalid` führen; Suffixlisten (81/26) nur mit Ersatzregel löschen. |
| TEST-5 | MITTEL [ungeprüft] | Es gibt keine Plausibilitäts-Invarianten. | 87 Zeilen mit \|Return 90d\| > 100 %; 7 Zeilen < −100 % (30 Tage). | nein | `report_integrity.check(df)` als Datenvertrag vor `--save`. |

---

## 5. Lösch- und Vereinfachungskandidaten

Unter „Zeilen“ steht Produktivcode, nach dem Schrägstrich die Tests.

| ID | Was | Zeilen | Produktwert | Aufrufer | Risiko |
|---|---|---:|---|---|---|
| CM-1, CM-7/8/9/10/16, TEST-6/7, DOKU-4 (**E1 entschieden**) | Committee Matcher: Paket, 2 Skripte, 17 Testdateien, Doku, Settings-Block, `data/config/committee_matcher_*`, Dev-Artefakte, openrouter/jsonpath | 21.033 / 14.487 (+~2.800 Doku, ~126 Settings) | keiner zur Laufzeit; der Offline-Neubau ist bytegleich (522/522) | nur CLI | Zahlen 0. Zu beachten: Leser in `build_reference_data.py:152-157` und `data/reference/raw/119/manifest.json`; `approved_baseline.json` als Industry-Validierungsliste behalten. |
| PIPE-4, EVT-1, S1A-1, S1A-11 | Suspicious-Export, 3 Event-Stages, Migration, In-Flight-Score, `signal_event_id`, Event-Snapshots, SUSPICIOUS_*-Settings | ~900 / ~850 | keiner (Dashboard, Deploy und Event-Study lesen nicht) | `run()`/`save()`, `reset_pipeline` | Das Signal-Interface muss auf (trade_id, signal_type) umgestellt werden. Externe Nutzung erfragen (O4). |
| SCR-7 | House-PDF-Parser, `parse_senate_xml`, `generate_committee_structure`, `validate_house`, PDFs, 26 XML, Roster-CSVs | 1.423 / 95 | keiner; die Voraussetzung (roster_coverage des Matchers) entfällt mit E1 | Matcher-Roster-Coverage, Bericht von `build_reference_data` (Zeilenzahl) | Gering; Schutzbaseline und Tests gehen mit E1. |
| SCR-5 | Fetcher-Endpunkte committees und summaries; details erst nach Umstellung auf `bills_list` | 350–450 / 0 | keiner | Fetcher, `reset_pipeline` | `policy_area` und `title` werden für `Vote_Timing_Hits` gebraucht. |
| SCR-9 [ungeprüft] | `reset_pipeline.py` auf ~60 Zeilen oder ganz weg | −420 | gering (nur wegen der append-only-Events) | Doku | Nach PIPE-4 entfällt der Hauptzweck. |
| PIPE-8, CFG-8 [ungeprüft] | Kuratierter Master `trades_master.csv`, `--regenerate_curated`, CURATED_* | 85 | Excel-Sicht, eventuell manuell genutzt | kein Code | Nutzungsfrage (O15). |
| PIPE-7, TICK-14 [ungeprüft] | 19 Master-Spalten ohne Leser, `Member_Chamber`, `signal_count` | 25 | keiner | nur Schreiber | `figi` liest `report_integrity`. |
| PIPE-5 | `Insider_Sectors` nicht persistieren (53 % der Master-Bytes) | ±0 | Anzeige bleibt (über Referenz abgeleitet) | `member_metrics.py:345-358` | Nur Größe bzw. RAM; Deploy ist schon entschärft (category). |
| TICK-8 [ungeprüft] | OpenFIGI-Pfade Search, CUSIP, Manual-Review | 300 / 150 | keiner (nur gate-ausgeschlossene Typen) | Normalizer | FOREIGN_STOCK (33 Trades, 0 bepreist). |
| TICK-10 [ungeprüft] | Eigene Backoff-/429-/Retry-Queues in Normalizer und Sektor; `flush_every` (S1A-10) | 250+8 / 150 | keiner (Duplikat von `retry_call`) | intern | 429/Retry-After muss in `retry_call`. |
| TICK-11 [ungeprüft] | Delisted-Report, Bestätigungs-Prompt, Preferred-Sektor | 130 / 60 | Diagnose, manuell | promotet, `reset_pipeline` | Nutzungsfrage (O15). |
| TICK-12 [ungeprüft] | `utils/issuer_ticker.py` (Spalte existiert schon) | 75 / 122 | keiner | 10 Signale | Leerwertsemantik vereinheitlichen. |
| GATE-4, PIPE-13 [ungeprüft] | Paket `gates/` als Maske in `run()` | 65 | Gate bleibt, nur die Hülle entfällt | `import_trades` | gering |
| S1A-4 [ungeprüft] | Rückbefüllungs-Zweig + `price_history_full_fetch.json` | 55 | nur die einmalige Migration 5y → max | `compute_post_trade_returns` | Migration per Cache-Reset in P7 erledigen. |
| CM-6/SIG-2 (**E2**) + CFG-2 | Signal `committee`, Match-Modi `sector_or_industry`/`hybrid`, MATCH_MODE-Setting | ~260 | keiner (≡ committee_full) | Score-Trigger, Presets, Glossar | SCORING_EX_OFFICIO_TRIGGERS umstellen. |
| SIG-5 [ungeprüft] | Signal trade_volume (amount > 15.000) | 140 / 161 | Hint; die Information steht in `amount` | Score-Stufen, Presets | O5/O6 |
| SIG-7 [ungeprüft] | swarm_conflict + swarm_paradox zusammenlegen oder löschen | 200 | nur eine Warnung | Score (Dämpfer) | O5/O6 |
| SIG-3 [ungeprüft] | accumulative/contrarian löschen oder schärfen | 250 | Hint | Score-Editor | nach der Event-Study (O6) |
| SIG-6 [ungeprüft] | floor_action + VoteTimingTag + Vote-Fetcher | 403 + 1.322 | Signal mit Gewicht 0, Member-Quote | Score, Member-Detail | O8; nur falls floor_action entfällt |
| DASH-5 | Score-Gewichte-Editor, Presets, Signal-Ausschluss, Recompute | 650 / ~1.100 | Entwicklerwerkzeug, nachweislich genutzt („Bene2“) | Sidebar | User-Entscheidung (O5). Sofort: Recompute nur bei Gewicht ≠ 0. |
| DASH-9 [ungeprüft] | Tab Score-Analyse | 380 | Validierung im UI; Standardansicht hat n = 0 | `streamlit_app` | O7 |
| DASH-4 | Recent-Tab mit Trades zusammenlegen; Horizont-Filter nur auf Rendite-Aggregate | 170 | Funktion bleibt (paper_return) | – | Produktentscheidung (O19) |
| DASH-6 [ungeprüft] | Server-CSV-Export `staging_export` | 210 / 190 | keiner nachweisbar (1 Datei vom 01.08.) | Recent-Tab | O15 |
| DASH-8 [ungeprüft] | Tab „Post Trade Performance“ | 95 | doppelt das Trade-Popup | – | gering |
| DASH-13 [ungeprüft] | Root-Dockerfile + docker-compose.yml | 41 | keiner | Doku | O15 |
| DASH-15/16 [ungeprüft] | Inline-CSS → Theme; 9 Glossar-Einträge ohne Aufruf | 140 + 45 | Optik bleibt | – | gering |
| SCR-13 [ungeprüft] | `linux_setup.sh` (`rm -rf venv`, Selenium-Zeit) | 109 (sh) | keiner | – | gering |
| SCR-10 [ungeprüft] | Legacy-Mappings, `former_members.csv`, Struktur-md | 30 + Daten | keiner | Schutzbaseline (geht mit E1) | gering |
| TEST-13 [ungeprüft] | VPS-E2E-Testgerüst | 0 / 271 + 497 | Absicherung eines manuellen Deploys | – | Nutzungsfrage (O15) |
| CFG-5/7, PIPE-14 [ungeprüft] | Wirkungslose Flags `SWARM_TRACK_DIRECTIONS`/`SWARM_FLAG_CONFLICTS`/`SWARM_GROUPING_KEY`, `load_trade_data`, `pipeline_state.json` | ~25 | keiner | Existenztest | gering |
| Vereinfachungen ohne Löschung | TICK-9 Assetklasse (−550), ARCH-1/PIPE-9/S1A-13 Signal-Interface (−~300 über EVT-1 hinaus), CFG-1 getattr-Defaults (−25), CFG-6 (−60), ES-9 (−70), S1A-5 (−50), S1A-6 (−20), S1A-9 (−25), S1A-12 (−5) | ~−1.100 | Verhalten bleibt | – | siehe Abschnitt 6 |

---

## 6. Entkopplung und Zielarchitektur

Markierung: **[CD]** = Schnittstelle vor der Umsetzung mit codebase-design (deep module, seam) ausarbeiten. **[AR]** = braucht ein Architektur-Review, weil sich Modulgrenzen oder das Datenmodell ändern.

```
run(raw, adapters, config)                       ← Orchestrator ~80 Z., keine Modul-Globals
  ├─ ingest(raw) → trades                        [CD][AR] trade_id/lot_index, direction, date_fallback
  ├─ members(trades, reference) → +Sitze          ReferenceData: Sitz-IDs statt F2-Roundtrip (CM-11/S1A-16), E3/E4
  ├─ instruments.resolve(trades, figi, yf) → asset_class, yf_symbol, issuer_key, sector   [CD][AR]
  ├─ prices: PriceHistory(load, close_at_or_after, ensure)                                 [CD]
  ├─ returns(trades, prices) → post_return_*, signed_ar_* (nur Konstrukt A)
  ├─ votes.ensure(congresses) → actions, subjects (Lock intern, Coverage)
  ├─ signals.evaluate(trades, ctx) → long table (trade_id, signal) + Coverage-NA           [CD][AR]
  ├─ score(signals, weights)                     lineare Summe (bzw. nach O5)
  └─ store: MASTER_SCHEMA, atomic_write, stage/save, Datenvertrag check(df)
analysis/event_study.py: population, eligible_mask(signal), compare_signal(strata, clusters)  [CD]
app/data.py (Read-Model): load_trades(cols), price_series, direction                          [CD]
```

| # | Modulgrenze | Inhalt / Interface | Belege | Markierung |
|---|---|---|---|---|
| 1 | **ingest** | Umbenennen, Datum (mit `date_fallback`-Flag), eindeutige Zeilen-ID (Dedup oder `lot_index`, O3), Spalte `direction` (ersetzt `_direction_from_transaction_type`, 39 Importe) | PIPE-3, PIPE-10, SIG-4 | [CD][AR] |
| 2 | **instruments** (deep module) | `resolve(ticker, asset_name, tickertype_org, transaction_date) → {asset_class ∈ equity/fund/option/bond/other/unresolved, yf_symbol, issuer_key, sector}`. Quellen in dieser Reihenfolge: Quiver-Typcode → FIGI-Whitelist → Muni-/Fondsname → Namensabgleich und Datumstest → Regex nur für die Symbolform. Gate, Kurs-Applicability und IssuerGroupMapper lesen nur `asset_class`; `ticker_routing`, `_SKIP/_BYPASS` und `ASSET_CLASS_KEEP_TYPES` entfallen. `yf_symbol` wird eine Spalte. | TICK-1/2/3/4/9, S1A-9 | [CD][AR] |
| 3 | **members/reference** | ReferenceData liefert Sitzlisten (committee_id, Rolle) und den Gültigkeitszeitraum. E4-Vererbung (Chair/RM) beim Laden. Kongress 120 schlägt laut fehl (E3). Das Mapping liegt als versionierte Referenzdaten unter `data/reference/` (E1). | CM-2, CM-5, CM-11, CM-15 | [CD] |
| 4 | **prices** | Öffentliche `PriceHistory` statt privater Funktionen in 6 Modulen; ein Lookup für Post-Trade, Paper und AR; Plausibilitätsgate auf Niveausprünge. | S1A-8, PIPE-11, ES-12 | [CD] |
| 5 | **votes** | `ensure_vote_data(congresses)`: nur actions und subjects; Lock mit Stale-Erkennung intern; ein gemeinsamer Kongressbegriff mit der Referenz. | SCR-2/4/5/6 | – |
| 6 | **signals** | `evaluate()` liefert (trade_id, signal) bzw. Bool-Spalten, vektorisiert; „nicht auswertbar“ = NA (Vote, Committee); keine Event-Snapshots, kein `signal_event_id`; Gate als Maske. **E2:** ein Committee-Modul statt drei. | ARCH-1, PIPE-4/9, CM-6, PIPE-1 | [CD][AR] |
| 7 | **store** | `MASTER_SCHEMA` als einzige Spaltenliste; ein `atomic_write` für alle Schreibstellen einschließlich Master; Datenvertrag vor `--save`; Code-Revision im Manifest. | PIPE-7, S1A-6, TEST-5, PIPE-2 | – |
| 8 | **analysis/event_study** | Population, `signal_eligible_mask`, `compare_signal(strata=['direction'], clusters=['member','ticker_month'])`, Holm-Korrektur. Dashboard und Skript nutzen dasselbe Modul; das Skript importiert nicht mehr aus `app/`. | ES-4/5/9/13, SCR-8 | [CD] |
| 9 | **app/data.py** (Read-Model) | Die einzige Seam zur Pipeline: Parquet mit Spaltenliste, `st.cache_resource`, öffentliche Kurs-Serie; Ausschuss-Industries aus dem Master statt Referenzkopie. | DASH-3/10/12, S1A-5 | [CD] |
| 10 | **config** | Je Stage ein Config-Objekt; keine `getattr`-Defaults; API- und Scraper-Konstanten zu ihrem einzigen Konsumenten; Matcher-Settings entfallen (E1). | CFG-1/3/4, PIPE-12 | – |

**Architekturgrenzen für import-linter (E5):**
1. `app` importiert nur `app.*`, `analysis.*` und öffentliche Read-Model- bzw. Preis-Schnittstellen; nichts aus `signals`/`tags`/`scripts`.
2. `scripts.*` und `analysis.*` importieren nicht aus `app`.
3. Keine Importe von `_privaten` Namen über Paketgrenzen.
4. `signals.*` importieren keine anderen Signalmodule.
5. Netz-Clients nur in Adapter-Modulen.

Regeln 1–3 blockieren, 4–5 warnen zunächst.

**Reihenfolge der Umbauten:** PIPE-4 (Export löschen) vor PIPE-6 (Seams schneiden), weil damit 7 Pfad-Patches und viele Globals wegfallen. SCR-2 vor SCR-4. Das instruments-Modul vor dem nächsten Vollauf als Entscheidungsgrundlage (O2).

---

## 7. Committee Matcher – Optionen (Entscheidung E1 ist gefallen)

| Option | Inhalt | Produktivzeilen | Testzeilen | Doku | Risiko für die Zahlen | Wartungsrisiko |
|---|---|---:|---:|---:|---|---|
| 1 Unverändert | alles bleibt | 0 | 0 | 0 | 0 | hoch: 35,5k Zeilen, ~200 s Testlaufzeit |
| **2 Einfrieren (Archiv-Tag) – entschieden (E1)** | Tag setzen, dann Paket, Skripte, Tests, Doku, Settings, `data/config/committee_matcher_*`, Dev-Artefakte und Requirements löschen | **−21.033** | **−14.487** | −~2.800 | **0** (Neubau bytegleich 522/522, CM-1) | gering; Neulauf nur aus dem Archiv |
| 3 Minimalpfad | nur submapper + fullrun_import + Ladeschicht | −~10.900 | −~8.000 | – | 0 | weiter hoch (CLI-Parser von Codex/Claude versionsabhängig) |
| **4 Handpflege – entschieden (E1, mit 2)** | Die 2 produktiven CSVs liegen versioniert unter `data/reference/mapping/`; neue Kongresse über `map_units` (Namensabgleich) + `unit_overrides.csv` | +0…30 | +~30 (Fehlertest) | Pflegeablauf | 0; neue Unterausschüsse von Hand | gering |

**Einarbeitung von E1:**
- Archiv-Tag (z. B. `committee-matcher-archiv`) auf dem Stand vor dem Löschen setzen.
- `reviewed_assignments.csv` und `reviewed_unit_coverage.csv` (Revision `map-fullrun-20261016`) nach `data/reference/mapping/` übernehmen, optional auf die 8 bzw. 6 gelesenen Spalten verschlankt.
- `REFERENCE_REVISIONS_PATH` umstellen und `jurisdiction_manifest` entfernen. Dabei auch `build_reference_data.py:152-157` und `data/reference/raw/119/manifest.json` anpassen (CM-1, korrigiert).
- Die Industry-Taxonomie (`taxonomy/approved_baseline.json`) als Validierungsliste für die Handpflege behalten oder durch die beobachteten `industry_yf` ersetzen.
- Unaufgelöste Units in `build_reference_data` sind ein FEHLER, und das Gate läuft **vor** dem Schreiben (CM-2, korrigiert).
- Mit E1 entfallen außerdem SCR-7 (Voraussetzung roster_coverage), SCR-10 (Schutzbaseline), CFG-3, CM-10, CM-12, CM-16, TEST-6/7, DOKU-4 und der Matcher-Anteil der Testlaufzeit.

**Einarbeitung von E2:**
- Das Signal `committee` entfällt.
- `committee_signal.py`, `committee_full_signal.py` und `committee_ex_officio_signal.py` werden ein Modul mit gemeinsamem Builder.
- MATCH_MODE und die Modi `sector_or_industry`/`hybrid` entfallen.
- `SCORING_EX_OFFICIO_TRIGGERS` zeigt auf `committee_full`; Presets und Glossar werden angepasst.

**Einarbeitung von E3:**
- Abgedeckt bleibt der 119. Kongress.
- Der 118. wird **bewusst** mit den Mitgliedschaften des 119. bewertet und so in der Doku, im Dashboard (Hinweis) und in der Event-Study (118 und 119 getrennt ausgewiesen) dokumentiert.
- 113–117 werden nicht erhoben.
- Für den 120. Kongress: laut scheitern statt still null (CM-2).

**Einarbeitung von E4:**
- Ist der Elternausschuss ohne eigene Industries und das Mitglied dort Chair oder Ranking Member, erbt es die Industries aller Unterausschüsse (+25 Trades, CM-5).

---

## 8. Vollauf-Kennzahlen, vorläufige Event-Study, Messplan

> **ALTER CODE.** Alle Zahlen dieses Abschnitts stammen aus dem Dry-Run vom 2026-11-18 mit `d00ff2d` (vor Stufe 1). Er wurde nicht gespeichert, und gegenüber dem August-Master kamen 0 neue Trades hinzu. Der Lauf zeigt nachweislich Zustände, die der aktuelle Code nicht mehr erzeugt: 983 ausgeschlossene Zeilen mit Signal, 0 ETF (PIPE-2, S1A-3).

### 8.1 Kennzahlen

| Kennzahl | Wert | Beleg |
|---|---|---|
| Zeilen / eindeutige trade_id / Mitglieder | 114.347 / 102.012 / 342 bio_guide_id (419 Namensvarianten) | DATA, DASH-1 |
| Zeitraum | transaction_date 2012-02-27 bis 2026-07-20 | ES-17 |
| asset_class_excluded | 1.140 (1,0 %); im Altcode 983 davon mit Signal | PIPE-2 |
| ETF / ETP | ticker_type `etf` = 0; ETP laut FIGI 3.638 | ES-16, GATE-2 |
| Kurs-Abdeckung (signed_ar_90d) | 38,5 % gesamt; 2012–2020 0 %; 2021 47 %; 2022 86 %; 2025 94 % | ES-1 |
| Sektor / FIGI | 78,9 % / 82,1 % | DATA |
| Committee-Status | not_covered 79.274 (69,3 %), matched 34.448, no_membership 625 | ES-3 |
| Signale (Token-Zeilen) | accumulative 60.385; trade_volume 29.664; contrarian 19.251; extreme_report 11.741; swarm_buy 11.541; swarm_sell 11.336; swarm_paradox 6.420; committee = committee_full 5.474; floor_action 2.814; committee_ex_officio 2.748; swarm_conflict 1.242 | DATA |
| trade_score | 0: 98.118 / 5: 4.688 / 20: 10.755 / 25: 786 (Score > 0: 14,2 %) | SCORE-1 |
| Vote_Timing_Hit | 2012–2023 0; 2024 24; 2025 16,1 %; 2026-01–04 18,9 %; ab 2026-05-03 0/1.518 | SCR-3, SCR-4 |
| Laufzeit | 67 min, davon Schwarm ~13 min | PIPE-15 |

### 8.2 Vorläufige Event-Study (Konstrukt A, SPY-bereinigt, nach Mitglied geclustert)

| Ergebnis | Wert | Beleg |
|---|---|---|
| Population / auswertbar (90 Tage) | 101.384 / 39.958 Trades aus 207 Mitgliedern; nur 2021-06 bis 2026 (ein Marktregime) | ES-1 |
| Mittelwert AR | A30 −0,13 % [−0,21; +0,01]; A90 +0,05 % [−0,13; +0,36]; A120 −0,04 %; A180 −0,19 %: gesamt nicht von 0 unterscheidbar | DATA |
| Score | Spearman roh +0,056…+0,062, AR −0,013…−0,018 | ES-7 |
| Richtung | Käufe −0,58 %, Verkäufe +0,72 % (Median −1,45 / +1,66) | ES-4 |
| swarm_buy | gepoolt +0,57 pp (n. s.); **Käufe +1,50 pp [+0,61; +2,30]**, p = 0,002 (Mitglied) bzw. 0,040 (Ticker) | ES-4, ES-5 |
| committee_full | Käufe +1,47 pp; Verkäufe −2,75 pp; gepoolt n. s. | ES-4 |
| contrarian | H120 −0,79 pp, p = 0,002 (einziger Treffer unter 12 Signalen; Bonferroni-Schwelle 0,0021) | ES-8, ES-13 |
| Konzentration | Ro Khanna 38 % des N, Top 5 Mitglieder ~60 %; 47 % aller committee_full-Signale von einem Mitglied (K000389) | ES-5, CM-13 |

### 8.3 Methodische Bewertung

| Schwäche | Wirkung | Befund |
|---|---|---|
| Nur ein Marktregime (2021+), Survivorship (≈ 14,9 % dauerhaft ohne Kurs) | Externe Validität begrenzt | ES-1, TICK-5 |
| Gepoolter Vergleich ohne Richtungsschicht | Vorzeichen und Signifikanz kippen | ES-4 |
| Ein-Weg-Clustering | p-Werte hängen von der Cluster-Wahl ab, in beide Richtungen | ES-5 |
| Mehrfachtests ohne Korrektur, keine vorab festgelegten Hypothesen; Ausreißer-Flag ±3 % trifft 84 % der Trades | Treffer im Rahmen des Zufalls | ES-13 [ungeprüft] |
| floor_action über Jahre ohne Vote-Daten | Confounder durch Epoche bzw. Zusammensetzung | SCR-8 |
| Konstrukt B ohne `knowable_date` | Look-ahead, sobald B pro Signal getestet wird | ES-6 |
| Fehlklassifikation (Optionen mit Vorzeichen, Fonds, Munis, Recycling) und ETFs in der Vergleichsgruppe | Verdünnung, Vorzeichenfehler | TICK-1/2/3/4/6 |
| Duplikat-Population ungeklärt | Gewichtung unsicher | PIPE-3, ES-15 |
| Report-Skript mit fest eingetragenen Schlüssen, fest verdrahteten Pfaden, Kopplung an `app/` | falsche Textaussage | ES-8, ES-9 |
| Exit-Erkennung (`effective_return_pct`) | **ungeprüft**, siehe 11.3 | – |

### 8.4 Messplan für den neuen Vollauf

**Vorbedingungen (Gates vor dem Lauf):**
- Quelldaten frisch: max. transaction_date ≥ heute − 60 Tage (SCR-1).
- Daten-Fixes TICK-1(b), TICK-2, TICK-3, TICK-4, SCR-2, dann SCR-4, Vote/Committee-Coverage als NA, `analysis_population` ohne `asset_class_excluded`.
- Kurs-Cache gelöscht statt gemergt (S1A-4, ES-12); NaN-Marker der 18 Share-Classes entfernt.
- Code-Revision im Manifest (PIPE-2).

**Kennzahlen (je Jahr, maschinenlesbar als CSV/JSON neben dem Markdown):**
- Zeilen, IDs, Duplikatgruppen.
- `asset_class`-Verteilung, ausgeschlossene Zeilen (Soll: 0 mit Signal), `unresolved`.
- Kursabdeckung je Horizont gegen die Ticker-Obergrenze.
- Sektor, Committee- und Vote-Coverage.
- Signale je Typ, Score-Verteilung.
- Fehlende Kurse nach Ursache (Delisting, Umbenennung, Pseudoticker).
- Treffer des Plausibilitätsgates (Sprünge und Niveausprünge).

**Erwartete Deltas gegenüber dem Altlauf (schriftlich vorab):**
- Ausgeschlossene Zeilen mit Signal = 0.
- ETF > 0.
- FB/PCLN/ANSS nicht als ETF, sondern `equity` oder `unresolved`.
- Optionen 0 im Universum.
- Abdeckung 2013–2020 > 0, nahe der Obergrenze aus TICK-5.

**Event-Study:**
- Vorab festgelegte Hypothesenliste mit höchstens 6 Hypothesen (O6).
- A90/A120 primär; B nur mit `knowable_date`.
- Je Hypothese: Schichtung nach Richtung, zweifaches Clustering (Mitglied × Ticker-Monat), Holm-Korrektur.
- Sensitivitäten: ohne Top-1-Mitglied, mitgliedergewichtet, 2013–2020 gegen 2021+, Winsorisierung 1/99, Duplikat-Varianten, 118 und 119 getrennt.

**Abnahme:**
- `report_integrity` (als Datenvertrag) gegen die alte Stage.
- Danach erst `--save` und Deploy (ES-2).

---

## 9. Bewertung von Stufe 1 aus Architektursicht

| Element | Bewertung | Behalten / Zurückbauen | Beleg |
|---|---|---|---|
| Löschungen (Scraper, Delta-Pfad, Stage-Flags; import_trades 3.204 → 1.900) | richtig | behalten | S1A-Zusammenfassung |
| Ein Pipeline-Pfad `--dry_run`/`--save` | richtig; aber der Timer schreibt weiter direkt den Master (SCR-14) | behalten, flaglosen Modus entscheiden (O14) | SCR-14 |
| H2 Gate-Maske vor den Signalen | richtig, im Code belegt, noch nicht im Vollauf gemessen | behalten | PIPE-2, S1A-3 |
| H6 accumulative über Handelstage | richtig, senkt die Trefferquote aber nur auf 39,8 % | behalten, Signal nach O6 | SIG-3 |
| H1 ETP → ETF | Ursache richtig erkannt, aber ohne Point-in-time-Prüfung: ≈ 1.120 Aktienzeilen werden ausgeschlossen | an den Namensabgleich koppeln (instruments-Modul) | S1A-2, TICK-1 |
| P4 ReferenceData (deep module), Matcher-Seam als Dateigrenze | gut | behalten; F2-Roundtrip später auf IDs umstellen | CM-1, S1A-16 |
| P4 App liest Referenztabellen zur Anzeige (reference_dir, Snapshot-Kopie) | erzeugt eine zweite Datenquelle und damit 3 KRITISCH-Befunde zu „gemischtem Stand“ | **zurückbauen** (Industries aus dem Master) | S1A-5 |
| P5 `yf_symbol`-Regel, `period=max` | richtig | behalten; `yf_symbol` als Spalte | S1A-9 |
| P5 Rückbefüllungszweig + Markerdatei | einmalige Migration als Dauerzustand gebaut; lädt täglich max für 1.249–1.635 Ticker | **zurückbauen** (Cache-Reset in P7, inkrementeller Refresh) | S1A-4 |
| Export-Härtung (atomic append, ID_DTYPES, In-Flight-Scores, Migration) | Härtung eines Pfads ohne Leser | **mit dem Export löschen** | S1A-1, S1A-11, S1A-12 |
| `_write_csv_atomic` | Der Master selbst ist nicht atomar; 5 Ersetz-Implementierungen | **ein** `atomic_write`, auch für den Master | S1A-6 |
| `report_integrity.py` | sinnvoll, misst aber die neuen Defektklassen nicht | zum Datenvertrag ausbauen | SIG-T1, TEST-5 |
| Doku-Nachzug | unvollständig (63/32/33 Zeilen zu gelöschtem Code) | Doku-Schnitt (Abschnitt 10) | DOKU-1 |
| Dry-Run schreibt `manual_review_tickers.csv` | dokumentiertes Verhalten, **kein Defekt** (S1A-14 korrigiert) | unverändert | S1A-14 |
| Altlasten (ungenutzte Importe, veralteter Docstring, Delisted-Zeile fehlt im Vollauf) | klein | aufräumen | S1A-15 [ungeprüft] |

---

## 10. Tests und Doku

### 10.1 Befund

| Thema | Befund | Beleg |
|---|---|---|
| Umfang | 2.121 Tests grün und offline, aber zu 61 % Matcher-Tests (≈ 90 % der Laufzeit) | TEST-6, CM-9 |
| Zahlenkette | Kein Test durchläuft Klassifikation → Gate → Kurse → AR → Score. Der einzige Pipeline-Test fälscht Normalizer, Returns und AR (`test_suspicious_export.py:669-680, 730-733`). | TEST-2 |
| Entscheidungsgrundlage | `compute_abnormal_returns` und `analyse_event_study` sind ungetestet; `clustered_bootstrap_diff` nur schwach indirekt. | TEST-3 |
| Defektklassen | Keine Tests für Recycling (FB), Fondstypen, Optionen, Munis, doppelte IDs; die H6-Fixture nimmt eindeutige IDs an. | TEST-1, TEST-4, TICK-15, TEST-10 |
| Fetcher | 1.322 Zeilen ohne Test; SCR-2 und SCR-4 blieben deshalb unentdeckt. | SCR-15 |
| Zuschnitt | Nach Tickets statt nach Modulen (intervall4, *_fixes, p4–p9); AST/exec-Tests auf private Funktionen von `streamlit_app`; Canary-Tests an Interna gekoppelt | TEST-8, TEST-9, TEST-14 [ungeprüft] |
| Doku | 14.939 Zeilen; 67 % der Commits fassen Doku an. Kanonische Dokus beschreiben gelöschte Skripte und Flags (DOKU-1, MITTEL). Das Spaltenschema hat 3 Phantomspalten und ~7 echte Lücken (DOKU-2, NIEDRIG). AGENTS.md-Stand veraltet (DOKU-3). 2.791 Zeilen abgeschlossene Matcher-Pläne (DOKU-4). ~860 Zeilen zu gelöschtem Scraper-Code (DOKU-5). | DOKU-1…9 |
| Config | 2 wirkungslose Schalter, als wirksam dokumentiert; 63 Schatten-Defaults; ~50 externe Konstanten | CFG-1/4/5 |

### 10.2 Zielbild

**Tests: 9–10k Zeilen, nach Modul geschnitten, Standardlauf unter 20 s.** Neue Verhaltenstests (~400 Zeilen):
1. Golden Path `--dry_run` mit aufgezeichneten OpenFIGI-Antworten und synthetischen Kursen. Prüfung von `asset_class`, Gate, `post_return`, `signed_ar`, Signalen und Score gegen handgerechnete Werte (TEST-2).
2. `instruments.resolve`: je Klasse ein echter Fall (Open-End-Fonds, OP-Option, Muni AONC + Negativprobe GCBC, FB-Trade 2015 → `unresolved`) (TEST-1/4, TICK-15).
3. AR und Event-Study auf einer Fixture-Stage (Kauf, Verkauf, fehlender Benchmark, Einheit als Bruchteil) (TEST-3).
4. Datenvertrag `check(df)` (TEST-5).
5. Fetcher-Delta mit 51 Bills und Stale-Lock (SCR-15).
6. Doppelte trade_ids in Signalen und Aggregaten (TEST-10).

Gelöscht werden: Matcher-Tests (E1), Export-Tests, `signal_event_id`, Senate-Parser, AST-Tests (nach Extraktion von `app/prefilter`), Canary-Interna (nach TICK-10).

**Doku: 3.500–4.000 Zeilen.**
- AGENTS.md (≤ 60 Zeilen, Stand in 5 Punkten)
- ARCHITEKTUR.md (Modulkarte, ersetzt Pipelines.md)
- DATA_STRUCTURE.md (nur Schemata, aus dem Header erzeugt, ohne Datumsmarker)
- CLI.md (nur Produktivskripte)
- `deploy/README_deployment.md`
- 3 Fachdokus
- Pläne, Reviews und CHANGELOG ins Archiv

Neue Regel: Doku nur bei Änderungen an Schema, CLI oder Architektur (O16). Das Final Review einer Löschstufe enthält einen grep über `organisation/` nach den gelöschten Namen (DOKU-1).

---

## 11. Entscheidungen

### 11.1 Entschieden (im Interview)

| Nr. | Entscheidung | Eingearbeitet in |
|---|---|---|
| **E1** | Committee Matcher per Archiv-Tag einfrieren und aus dem Branch löschen (Code, Tests, Doku, Settings, Dev-Artefakte); die 2 produktiven Mapping-CSVs als versionierte Referenzdaten unter `data/reference/` übernehmen. | 3, 5, 6 (#3, #10), 7 |
| **E2** | Signal `committee` (Halbtreffer) entfällt; ein Committee-Modul statt drei; keine Doppelzählung. | 4 (CM-6), 5, 6 (#6), 7 |
| **E3** | Kongressabdeckung: 119 wie heute; 118 bleibt bewusst mit den Mitgliedschaften des 119. bewertet (dokumentieren, nicht neu erheben); 120 später, bis dahin laut scheitern statt still null (CM-2); 113–117 nicht. | 4 (ES-3, CM-3, CM-2), 6 (#3), 7, 8.4 |
| **E4** | Elternausschuss ohne eigene Branchen: nur Chair/Ranking Member erben die Zuständigkeit aller Unterausschüsse. | 4 (CM-5), 6 (#3), 7 |
| **E5** | CONSTRAINTS.md mit zwei Dimensionen: Zeilenbudget (Ratchet; Zielzahl laut Audit **15.000 Produktivzeilen**, Tests ≤ 10.000) und Architekturgrenzen (import-linter, Regeln in Abschnitt 6). Grundregeln blockieren, der Rest warnt zunächst. Volle Testsuite je Ticket (nach E1 unter 20 s). | 3, 6 |

### 11.2 Im Interview entschieden (2026-11-20, jeweils die Empfehlung; O15: kuratierter Master bleibt). Der darauf aufbauende Plan (Issues #1–#20) ist verworfen und archiviert in `plan-stufe2-verworfen.md`; Bene hat sich am 2026-11-25 für einen Neubau entschieden.

| Nr. | Frage | Optionen | Empfehlung | Befunde |
|---|---|---|---|---|
| O1 | Trade-Quelle | (a) Quiver-Abo und Export-Selektor prüfen und reparieren; (b) andere Quelle (eigene PTR-Scraper); (c) Produkt bewusst auf historische Analyse begrenzen | (a) zuerst prüfen: Der Login funktioniert, nur der Export-Link fehlt; eine Abo- oder Selektorfrage. Ohne Quelle (c) und das Monitoring zurückbauen. | SCR-1 |
| O2 | Reihenfolge Vollauf vs. Sanierung | (a) P7-Vollauf jetzt mit Stufe-1-Code (ARCH); (b) erst Daten-Gates und Fixes, dann Vollauf (TICK, SIG, TEST, DATA) | **(b).** Die kleinen Daten-Fixes (TICK-1b, TICK-2/3/4, SCR-2 → SCR-4, Coverage-NA, Cache-Reset) kommen vor dem Vollauf, der als Entscheidungsgrundlage dient. Löschtickets ohne Zahlenwirkung (E1, PIPE-4, SCR-7) können parallel laufen. Ein zusätzlicher Dry-Run mit Stufe-1-Code nur als Messung von H1/H2, nicht als Grundlage. | PIPE-2, S1A-3, TICK-1/2, GATE-2 |
| O3 | trade_id-Duplikate | (a) Dedup nach trade_id im Ingest; (b) `lot_index` in die ID; (c) Stichprobe von ~20 Gruppen gegen die PTR (Netz) | (c) dann (b): Identische Zeilen aus demselben Upload sprechen eher für echte Lots. Bis zur Klärung rechnet die Event-Study beide Varianten. | PIPE-3, DASH-2, ES-15 |
| O4 | Suspicious-Export | (a) löschen; (b) behalten (externer Leser) | (a), sofern es keinen externen Leser gibt | PIPE-4, EVT-1, S1A-1 |
| O5 | Score-Mechanik und Editor | (a) Editor, Presets und Recompute löschen, Score linear; (b) Editor behalten; (c) Gewichts-Exploration ins Event-Study-Skript | (c) + lineare Summe. Dämpfer, Ex-Officio-Boni und Volumen-Stufen nur behalten, wenn die Event-Study sie stützt (Preset „Bene2“ zeigt frühere Nutzung). | SCORE-1, DASH-5 |
| O6 | Signal-Katalog und Hypothesenliste (streichen vs. testen) | Streichkandidaten: trade_volume, swarm_conflict, swarm_paradox, extreme_report fast. Testkandidaten: swarm_buy, committee_full, contrarian, accumulative, floor_action | **Ohne Test streichen:** trade_volume (das Volumen ist als Schicht bzw. Kovariate in der Event-Study besser aufgehoben), swarm_conflict mit paradox zusammenlegen. **Vorab getestet (höchstens 6, Holm):** (1) swarm_buy, nur Käufe, gegen die Grundrate; (2) committee_full, Käufe; (3) committee_full, Verkäufe; (4) contrarian (Δ ≥ 1 Tag); (5) accumulative (≥ 3 Handelstage); (6) floor_action, nur abgedeckte Trades. Alles andere explorativ. | SIG-1/3/5/6/7, SIG-4, ES-13 |
| O7 | Tab Score-Analyse | (a) löschen, Validierung nur im Report; (b) behalten auf gemeinsamem `analysis/event_study`-Modul mit AR-Lineal | Das gemeinsame Modul **in jedem Fall**. Den Tab löschen, sofern er für Besucher keine Funktion hat (sonst (b)). | DASH-9, ES-7, ES-9 |
| O8 | Vote-Daten und floor_action | (a) Historie 113–118 nachladen (~7–8 h je Kongress); (b) nur ab 119, Coverage-NA; (c) floor_action mit Kammerfilter; (d) floor_action streichen (−~1.800 Zeilen mit Fetcher und Tag) | (b) + Entscheidung (c)/(d) nach Hypothese 6 | SCR-3, PIPE-1, SIG-6 |
| O9 | Optionen | (a) ausschließen; (b) eigene Klasse mit Call/Put-Korrektur aus dem Freitext | (a) | TICK-2, GATE-1 |
| O10 | Fonds | Open-End klar ausschließen; Closed-End und BDCs (z. B. AINV) ausschließen oder behalten? | Closed-End ausschließen, BDCs als Aktie behalten (Whitelist um „BDC“ ergänzen, falls OpenFIGI das liefert; sonst Namensliste prüfen) | TICK-3, GATE-3 |
| O11 | Point-in-time-Auflösung | (a) Namensabgleich + Datumstest → `unresolved`; (b) kostenpflichtige Quelle mit Symbolhistorie | (a) jetzt, (b) später nur bei Bedarf | TICK-1, S1A-2 |
| O12 | Umbenennungstabelle | (a) pflegen (Top-200 ≈ 66 % der Lücke); (b) Lücke nur ausweisen | (b) + eine kleine Tabelle nur für echte Umbenennungen (SQ → XYZ, ABC → COR, FLT → CPAY, FI → FISV) | TICK-5, ES-11 |
| O13 | P7-Gate Kursabdeckung | (a) bei ≥ 80 % je Jahr bleiben; (b) relativ zur Ticker-Obergrenze je Jahr | (b), z. B. ≥ 95 % der Obergrenze je Jahr | TICK-5 |
| O14 | Automatischer Lauf | (a) nur Dry-Run, `--save` manuell; (b) Direktmodus mit Datenvertrag als Gate | (a) und den flaglosen Modus löschen (ein Schreibweg) | SCR-14 |
| O15 | Nutzungsfragen | Werden genutzt: kuratierter Master, `delisted_trades.csv`, `--manual_review_only`, `data/agent_exports`, Root-Docker, VPS-E2E, `excess_return`? | Alles löschen, was nicht ausdrücklich genutzt wird | PIPE-8/14, TICK-11, DASH-6/13, TEST-13, PIPE-7 |
| O16 | Doku-Regel, CHANGELOG, Skill `doku-update` | (a) Pflichtregel behalten; (b) Doku nur bei Schema-, CLI- oder Architekturänderung, CHANGELOG ein Absatz je Stufe | (b) | DOKU-6, DOKU-9 |
| O17 | Insider_Sectors | (a) im Master behalten; (b) nicht persistieren, in der App ableiten | (b) zusammen mit S1A-5 (App liest Industries aus einer schlanken Master-Spalte) | PIPE-5, S1A-5 |
| O18 | Senate-Rohsicherung (`--official-snapshot`) | behalten / löschen | löschen | SCR-7 |
| O19 | Recent-Tab und Horizont-Filter | (a) wie heute; (b) Horizont-Filter nur für Rendite-Aggregate, Recent in Trades | (b) | DASH-4 |

### 11.3 Offene Prüfpunkte (nicht nachauditiert)

| Prüfpunkt | Warum offen | Vorgeschlagene Prüfung |
|---|---|---|
| **Exit-Erkennung / `effective_return_pct`** (`scoring/post_trade_returns.py`, `_compute_exit_columns`) | Keine Dimension hat die FIFO-/Exit-Logik, `opposing_trade_ids` und die Partial-Close-Zerlegung (`streamlit_app.py:1761`, eigene Return-Rechnung) gegen Daten geprüft. **Ungeprüft.** | Stichprobe von Exits gegen manuelle FIFO-Rechnung; Wechselwirkung mit den trade_id-Duplikaten (O3) |
| **Abnahme des nächsten Vollaufs: Reihenfolge der Fixes** | Ob H1 vor oder nach dem Namensabgleich aktiv ist, ändert ≈ 1.120 Zeilen; SCR-2 muss vor SCR-4 kommen. **Ungeprüft** ist, ob weitere Fixes voneinander abhängen. | Fix-Reihenfolge als Ticketkante festlegen; erwartete Deltas vorab schreiben (8.4) |
| **Splice-Risiko der `period=max`-Rückbefüllung** | KLAC und CTVA zeigen Niveausprünge im Cache. Ob `_merge_into_cache` bei der Rückbefüllung unterschiedlich adjustierte Serien zusammensetzt, ist eine **Vermutung** (ES-12, korrigiert). | Vor P7 den Cache leeren statt mergen; Gate auf Niveausprünge; Stichprobe gegen eine unabhängige Kursquelle |

---

## 12. Anhang: alle Befunde mit Prüfurteil

Urteile: **best.** = bestätigt, **teilw.** = teilweise bestätigt (korrigierte Form im Text), **ungepr.** = nicht vom Skeptiker geprüft. Ein Pfeil in der Spalte Schwere zeigt die Korrektur durch den Skeptiker. Die ID „SIG-T1“ steht für den Befund „TEST-1“ der Dimension SIG; sie wurde umbenannt, weil die ID mit TEST-1 der Test-Dimension kollidiert.

| ID | Kat. | Schwere | Titel (kurz) | Urteil |
|---|---|---|---|---|
| PIPE-1 | defekt | HOCH | Vote-Timing nur 119, seit 2026-05-02 veraltet, stiller Fallback | best. (präzisiert) |
| PIPE-2 | risiko | HOCH | Vollauf 18.11. bildet aktuellen Code nicht ab | best. |
| PIPE-3 | inkonsistenz | HOCH | 10,8 % trade_id-Duplikate, drei Populationen | teilw. (Richtung offen) |
| PIPE-4 | loeschen | HOCH | Suspicious-Export ohne Leser | best. |
| PIPE-5 | vereinfachen | HOCH→MITTEL | Insider_Sectors 53 % des Masters | teilw. |
| PIPE-6 | entkoppeln | HOCH→MITTEL | Flache Pipeline, 27 Monkeypatches | teilw. (39 Methoden, 17 Globals) |
| PIPE-7 | vereinfachen | MITTEL | 19 Master-Spalten ohne Leser | ungepr. |
| PIPE-8 | loeschen | MITTEL | Kuratierter Master ohne Leser | ungepr. |
| PIPE-9 | vereinfachen | MITTEL | Doppelte Signal-Aggregation, dreifache issuer_ticker-Herleitung | ungepr. |
| PIPE-10 | entkoppeln | MITTEL | Richtung als private Funktion, 39 Importe | ungepr. |
| PIPE-11 | vereinfachen | MITTEL | AR-Stage verwirft Konstrukt B; Kurs-Cache 3× geladen | ungepr. |
| PIPE-12 | risiko | MITTEL | settings als globaler Kopplungspunkt | ungepr. |
| PIPE-13 | vereinfachen | NIEDRIG | AssetClassGate flach; veralteter Docstring | ungepr. |
| PIPE-14 | loeschen | NIEDRIG | load_trade_data, pipeline_state.json | ungepr. |
| PIPE-15 | test | NIEDRIG | 67 min Laufzeit, Zeilenschleifen | ungepr. |
| TICK-1 | defekt | KRITISCH→HOCH | Point-in-time-Tickerauflösung fehlt | teilw. (≈1.040–1.120 Aktien; Pseudoticker) |
| TICK-2 | defekt | HOCH | tickertype_org ignoriert, Optionen als Aktie | best. (AB → fund) |
| TICK-3 | defekt | HOCH | ETF-Fix nur ETP | best. |
| TICK-4 | defekt | HOCH→MITTEL | Muni-Erkennung nur UNIT/PREFERRED | teilw. |
| TICK-5 | risiko | HOCH | Kursabdeckung ≤ 84 %, P7-Gate unerreichbar | teilw. (≈16.300 Survivorship) |
| TICK-6 | risiko | MITTEL | ETFs in Vergleichsgruppe | ungepr. |
| TICK-7 | defekt | MITTEL | Sektor-Null-Cache ohne TTL | teilw. (~160 Aktien-Trades) |
| TICK-8 | loeschen | MITTEL | OpenFIGI Search/CUSIP/Manual-Review | ungepr. |
| TICK-9 | entkoppeln | MITTEL | Assetklasse an 6 Stellen | ungepr. |
| TICK-10 | vereinfachen | MITTEL | Drei Netz-Robustheitsmechanismen | ungepr. |
| TICK-11 | loeschen | NIEDRIG | Delisted-Report, Prompt, Preferred-Sektor | ungepr. |
| TICK-12 | vereinfachen | NIEDRIG | issuer_ticker doppelt | ungepr. |
| TICK-13 | defekt | NIEDRIG | Regex-Fehlgriffe NTEST, Suffixe | teilw. (81/26; Pseudoticker) |
| TICK-14 | loeschen | NIEDRIG | 7 Tickerspalten ohne Leser | ungepr. |
| TICK-15 | test | NIEDRIG | Fixtures nur ETP/Common Stock | ungepr. |
| GATE-1 | defekt | HOCH→MITTEL | Gate lässt Optionen durch | teilw. (≈1.044 / 258 Score>0) |
| GATE-2 | defekt | HOCH | ETP-Regel schließt recycelte Aktien aus | best. (932–1.033 je Heuristik) |
| GATE-3 | defekt | MITTEL→NIEDRIG | Fonds passieren; numeric_suffix | teilw. |
| GATE-4 | vereinfachen | NIEDRIG | gates/ flach, Volumen vor Gate | ungepr. |
| EVT-1 | loeschen | HOCH | Event-/Suspicious-Export ohne Abnehmer | best. |
| SCORE-1 | vereinfachen | HOCH→MITTEL | Default-Score = 2 Gewichte | teilw. (Preset „Bene2“) |
| SIG-1 | risiko | HOCH | swarm_buy misst Beliebtheit | best. |
| SIG-2 | loeschen | MITTEL | committee ≡ committee_full | ungepr. (E2) |
| SIG-3 | vereinfachen | MITTEL | accumulative/contrarian kaum selektiv | ungepr. |
| SIG-4 | defekt | MITTEL→NIEDRIG | extreme_report vermischt Hypothesen | teilw. (49 Artefakte) |
| SIG-5 | loeschen | MITTEL | trade_volume = amount > 15.000 | ungepr. |
| SIG-6 | risiko | MITTEL | floor_action ohne Kammer | ungepr. |
| SIG-7 | loeschen | NIEDRIG | Schwarm-Familie ohne Score-Wirkung | ungepr. |
| CNT-1 | risiko | MITTEL | trade_id nicht eindeutig | ungepr. |
| ARCH-1 | entkoppeln | MITTEL | 12 flache Signalklassen | ungepr. |
| SIG-T1 | test | NIEDRIG | report_integrity misst Defektklassen nicht | ungepr. |
| CM-1 | loeschen | HOCH | Matcher ohne Laufzeit-Konsument | best. (E1) |
| CM-2 | risiko | HOCH | Kongress 120 still null | best. (Gate vor Schreiben) |
| CM-3 | defekt | HOCH | 118 mit Ausschüssen des 119 | teilw. (575; E3) |
| CM-4 | risiko | HOCH→MITTEL | 69 % ohne Kongress | teilw. (Event-Study schon eingegrenzt) |
| CM-5 | defekt | MITTEL | Chairs ohne Elternbranchen | teilw. (8 Ausschüsse; E4) |
| CM-6 | vereinfachen | MITTEL | committee ≡ committee_full | ungepr. (E2) |
| CM-7 | loeschen | MITTEL | submapper.py | ungepr. (E1) |
| CM-8 | loeschen | MITTEL | API-Mapping-Weg | ungepr. (E1) |
| CM-9 | test | MITTEL | Matcher-Tests ~90 % Laufzeit | ungepr. (E1) |
| CM-10 | entkoppeln | MITTEL | Matcher-Settings, Pfade, Docker | ungepr. (E1) |
| CM-11 | vereinfachen | NIEDRIG | ID→Name→ID-Roundtrip | ungepr. |
| CM-12 | doku | NIEDRIG | veraltete Matcher-Doku | ungepr. (E1) |
| CM-13 | risiko | MITTEL | 47 % committee_full von einem Mitglied | ungepr. |
| CM-14 | vereinfachen | NIEDRIG | Konfidenz ohne Produktwirkung | ungepr. |
| CM-15 | defekt | NIEDRIG | first_seen/last_seen ignoriert | best. |
| CM-16 | loeschen | NIEDRIG | Legacy-Mapping, Dev-Artefakte | ungepr. (E1) |
| DASH-1 | defekt | HOCH→MITTEL | Members nach Name gruppiert | teilw. (2.322 abgespalten) |
| DASH-2 | inkonsistenz | HOCH→MITTEL | Kerntabs zählen Duplikate | teilw. (Obergrenze) |
| DASH-3 | risiko | HOCH→MITTEL | Alle Tabs je Klick, 624 MB | teilw. (nur „Alles“/CSV) |
| DASH-4 | vereinfachen | HOCH→MITTEL | Horizont-Filter leert Standardansicht | teilw. |
| DASH-5 | loeschen | HOCH→MITTEL | Score-Editor/Presets/Recompute | teilw. (User-Entscheidung) |
| DASH-6 | loeschen | MITTEL | staging_export | ungepr. |
| DASH-7 | defekt | MITTEL | 34 % Assets ohne Namen | best. |
| DASH-8 | loeschen | MITTEL | Tab Post Trade Performance | ungepr. |
| DASH-9 | entkoppeln | MITTEL | Score-Analyse im Dashboard | ungepr. |
| DASH-10 | entkoppeln | MITTEL | App nutzt private Pipeline-Interna | ungepr. |
| DASH-11 | vereinfachen | MITTEL | streamlit_app 2.453 Z. global | ungepr. |
| DASH-12 | risiko | MITTEL | CSV-Loader alle Spalten | ungepr. |
| DASH-13 | loeschen | NIEDRIG | Root-Docker ohne Aufrufer | ungepr. |
| DASH-14 | risiko | NIEDRIG | Member-Verläufe live aus Cache | ungepr. |
| DASH-15 | vereinfachen | NIEDRIG | 158 Z. Inline-CSS | ungepr. |
| DASH-16 | loeschen | NIEDRIG | 9 Glossar-Einträge ungenutzt | ungepr. |
| SCR-1 | defekt | KRITISCH | Quiver liefert seit 2026-08-03 nichts | best. (Ursache offen) |
| SCR-2 | defekt | KRITISCH→HOCH | Aliasing im Vote-Delta-Update | teilw. (Dutzende Roll-Calls; latent) |
| SCR-3 | defekt | KRITISCH→HOCH | Vote-Daten nur 119 | teilw. (Score nicht betroffen) |
| SCR-4 | defekt | HOCH | Verwaiste Lock-Datei | best. |
| SCR-5 | loeschen | HOCH | 3 Fetcher-Endpunkte ohne Leser | teilw. (details nötig) |
| SCR-6 | entkoppeln | MITTEL | Zwei Kongressbegriffe | ungepr. |
| SCR-7 | loeschen | HOCH | Roster-Parser ohne Produktwert | teilw. (Schutzbaseline; mit E1 erledigt) |
| SCR-8 | defekt | HOCH | floor_action-Confounder in Event-Study | teilw. (Epoche statt Marktphase) |
| SCR-9 | vereinfachen | MITTEL | reset_pipeline Komfort | ungepr. |
| SCR-10 | loeschen | MITTEL | Legacy-Mappings ohne Leser | ungepr. |
| SCR-11 | entkoppeln | NIEDRIG | Sektor-Caches in data/mappings | ungepr. |
| SCR-12 | entkoppeln | MITTEL | Dashboard-Image mit Tool-Abhängigkeiten | ungepr. |
| SCR-13 | loeschen | NIEDRIG | linux_setup.sh veraltet | ungepr. |
| SCR-14 | risiko | MITTEL | Timer schreibt Master direkt | ungepr. |
| SCR-15 | test | MITTEL | Fetcher ohne Tests | ungepr. |
| SCR-16 | risiko | NIEDRIG | Committee nur 118/119 | ungepr. (E3) |
| TEST-1 | defekt | KRITISCH→HOCH | FB → ProShares ungetestet | teilw. (= TICK-1) |
| TEST-2 | test | HOCH | Keine Zahlenkette im Test | best. |
| TEST-3 | test | HOCH | AR/Event-Study ohne Test | teilw. (Bootstrap indirekt) |
| TEST-4 | defekt | MITTEL | Fonds passieren Gate | teilw. |
| TEST-5 | test | MITTEL | Keine Plausibilitäts-Invarianten | ungepr. |
| TEST-6 | entkoppeln | HOCH | Matcher-Tests 54 % / 61 % / 93 % | best. (E1) |
| TEST-7 | loeschen | MITTEL | P0-Migrationsskripte | ungepr. (E1) |
| TEST-8 | vereinfachen | MITTEL | AST/exec-Tests | ungepr. |
| TEST-9 | vereinfachen | MITTEL | Canary-Tests gekoppelt | ungepr. |
| TEST-10 | risiko | MITTEL | Fixtures nehmen eindeutige IDs an (Zahl korrigiert: 19,9 % beteiligt) | ungepr. |
| TEST-11 | risiko | MITTEL | Stufe-1-Fixes nur isoliert belegt | ungepr. |
| TEST-12 | vereinfachen | NIEDRIG | committee ≡ committee_full; signal_count | ungepr. (E2) |
| TEST-13 | loeschen | NIEDRIG | VPS-E2E-Gerüst | ungepr. |
| TEST-14 | vereinfachen | NIEDRIG | Tests nach Tickets | ungepr. |
| DOKU-1 | doku | HOCH→MITTEL | Doku beschreibt gelöschten Code | teilw. (Pipeline 3c bleibt) |
| DOKU-2 | doku | HOCH→NIEDRIG | Spaltenschema weicht ab | teilw. (3 Phantom, ~7 Lücken) |
| DOKU-3 | doku | MITTEL | AGENTS.md-Stand veraltet | ungepr. |
| DOKU-4 | loeschen | MITTEL | 2.791 Z. Matcher-Pläne | ungepr. (E1) |
| DOKU-5 | loeschen | MITTEL | ~860 Z. Scraper-Doku | ungepr. |
| DOKU-6 | doku | MITTEL | Doku-Pflicht erzeugt Churn | ungepr. |
| DOKU-7 | doku | NIEDRIG | veraltete .env-Vorlagen | ungepr. |
| DOKU-8 | doku | NIEDRIG | Pipeline 8 / §8 überholt | ungepr. |
| DOKU-9 | loeschen | NIEDRIG | CHANGELOG/docs/reviews | ungepr. |
| CFG-1 | vereinfachen | MITTEL | 63 getattr-Schatten-Defaults | ungepr. |
| CFG-2 | risiko | MITTEL | MATCH_MODE macht committee zur Kopie | ungepr. (E2) |
| CFG-3 | entkoppeln | MITTEL | 35 Matcher-Settings global | ungepr. (E1) |
| CFG-4 | vereinfachen | NIEDRIG | ~50 externe Konstanten | ungepr. |
| CFG-5 | loeschen | NIEDRIG | 2 wirkungslose Swarm-Flags | ungepr. |
| CFG-6 | vereinfachen | NIEDRIG | Historienkommentare in settings | ungepr. |
| CFG-7 | risiko | NIEDRIG | SWARM_GROUPING_KEY wirkt nur zum Teil | ungepr. |
| CFG-8 | vereinfachen | NIEDRIG | Kuratierter Master ohne Konsument | ungepr. |
| ES-1 | defekt | KRITISCH | Kurs-Cache ab 2021 | teilw. (62.161 / 54,4 %; Code behoben) |
| ES-2 | defekt | KRITISCH | 49.532 Null-Returns im August-Master | best. |
| ES-3 | defekt | KRITISCH | Ausschussdaten nur 119 | best. (E3) |
| ES-4 | risiko | HOCH | Richtungs-Confounder | best. |
| ES-5 | risiko | HOCH→MITTEL | Unsicherheit / Clustering | teilw. |
| ES-6 | risiko | HOCH→MITTEL | Look-ahead Konstrukt B | teilw. (heute nicht getestet) |
| ES-7 | defekt | HOCH | Dashboard am rohen Return | best. |
| ES-8 | defekt | MITTEL | Fest eingetragene Report-Befunde | best. |
| ES-9 | entkoppeln | MITTEL | Zwei Pro-Signal-Implementierungen | ungepr. |
| ES-10 | vereinfachen | MITTEL | committee ≡ committee_full | ungepr. (E2) |
| ES-11 | defekt | MITTEL | Survivorship / Umbenennungen | teilw. (Share-Class in S1 behoben) |
| ES-12 | defekt | MITTEL→NIEDRIG | Kein Kurs-Plausibilitätsgate | teilw. (5 Trades; Splice-Risiko) |
| ES-13 | risiko | MITTEL | Mehrfachtests ohne Korrektur | ungepr. |
| ES-14 | vereinfachen | NIEDRIG | Pipeline verwirft B und Outlier | ungepr. |
| ES-15 | risiko | MITTEL | Duplikate verworfen, ungeklärt | ungepr. |
| ES-16 | defekt | MITTEL→NIEDRIG | ETF-Erkennung 0 | teilw. (in S1 aktiv; Recycling-Folge) |
| ES-17 | doku | HOCH→MITTEL | Messplan fehlt, 0 neue Trades | teilw. (P7-Plan ergänzen) |
| S1A-1 | loeschen | HOCH | Suspicious-Export, Härtung in totes Produkt | best. |
| S1A-2 | defekt | HOCH→MITTEL | H1 macht ≈1.100 Aktienzeilen zu ETF | teilw. (K4-Begründung bleibt) |
| S1A-3 | risiko | HOCH | Stufe 1 nicht auf Vollauf gemessen | best. |
| S1A-4 | vereinfachen | MITTEL | Rückbefüllung als Dauerzustand | ungepr. |
| S1A-5 | entkoppeln | MITTEL | App rechnet Ausschuss-Industries nach | ungepr. |
| S1A-6 | risiko | MITTEL | Master nicht atomar; 5 Ersetz-Implementierungen | ungepr. |
| S1A-7 | entkoppeln | MITTEL | TradeImporter flach | ungepr. |
| S1A-8 | entkoppeln | MITTEL | Kurs-Cache ohne öffentliche Schnittstelle | ungepr. |
| S1A-9 | vereinfachen | MITTEL | yf_symbol doppelt, Caches uneinheitlich | ungepr. |
| S1A-10 | loeschen | NIEDRIG | flush_every tot | ungepr. |
| S1A-11 | loeschen | NIEDRIG | Schema-Migration erledigt | ungepr. |
| S1A-12 | vereinfachen | NIEDRIG | ID_DTYPES ohne realen Fall | ungepr. |
| S1A-13 | vereinfachen | NIEDRIG | Sequenz/Schwarm-Duplikat | ungepr. |
| S1A-14 | defekt→kein Defekt | NIEDRIG | Dry-Run schreibt manual_review_tickers.csv | teilw. (dokumentiertes Verhalten) |
| S1A-15 | doku | NIEDRIG | Altlasten Importe/Docstring | ungepr. |
| S1A-16 | vereinfachen | NIEDRIG | IDs als Namens-Strings | ungepr. |
| S1A-17 | risiko | MITTEL | 69 % ohne Kongress | ungepr. (E3) |

Kanonische Zahl zum Ticker-Recycling: `/tmp/audit-neu/synthese/recycle.py` (lesend auf `/tmp/audit-neu/ticker/stage.parquet`).
