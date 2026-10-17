# Rebuild-Audit Congress Trade Tracker: Gesamtbericht

**Stand:** 2026-10-17
**Code:** Worktree `agent/committe_matcher`, inklusive uncommitteter Änderungen
**Daten:** zwei Master-Stände, siehe Tabelle

| Datenstand | Zeilen / Größe | genutzt von |
|---|---|---|
| Worktree-Master vom 31.07. | 114.245 Zeilen, 643 MB | 01, 03, 04, 08, 11 |
| Haupt-Worktree-Master vom 11.08. | 114.347 Zeilen, 645 MB | 02, 13 |
| Quiver-XLSX vom 2026-08-03 | 114.347 Zeilen | 02, 13 |

**Methode:**
- Zuerst gaben 4 Explorer-Agents einen Überblick.
- Danach liefen 12 parallele Tiefenaudits, davon 11 mit Code- und Datenprüfung und eines als Webrecherche.
- Zum Schluss hat ein unabhängiger Agent diesen Bericht gegengeprüft (`13_gegenpruefung.md`). Seine Korrekturen sind eingearbeitet.
- Alle Prüfungen waren read-only. Es gab keine kostenpflichtigen API-Aufrufe.
- Zahlen stammen aus den Prüfskripten der Agents. Diese liegen in `/tmp/audit_*` und sind nicht dauerhaft gespeichert.

Die Details stehen in den Teilberichten `01` bis `13`, siehe Abschnitt 8. Dieser Bericht ist die Einstiegsseite und die Referenz für einen möglichen Rebuild.

---

## 0. Kurzfazit

**Empfehlung: Gestufter Neuaufbau statt Big-Bang-Rebuild.**

1. **Stufe 1, Sanieren durch Löschen (wenige Tage):**
   - Sofortmaßnahmen aus 6.5 umsetzen.
   - Ballast aus Abschnitt 5 archivieren.
   - Delta-Pfad und Einzel-Stage-Modus stilllegen.
   - Damit werden die Zahlen belastbar und die Codebasis schrumpft auf etwa 15.000 bis 20.000 Zeilen.
2. **Entscheidungs-Gate:** Datenquelle, Quelle für historische Ausschuss-Mitgliedschaften und Zweck des Scores werden auf den sanierten Daten entschieden.
3. **Stufe 2, Neubau des Rückgrats in einem neuen Repo mit festen Leitplanken (Abschnitt 6.4):**
   - normalisiertes Datenmodell mit Zeitachse
   - Referenzdaten über BioGuideID
   - Wertpapier-Tabelle
   - Filter-Engine mit „nicht bewertbar“
   - Die korrekten Fachmodule, das Frontend und alle Datenbestände werden übernommen.

### Begründung

1. **Die Ergebnisse sind heute nicht belastbar.** Die meisten Ursachen sind aber lokal behebbar.

   | Befund | Ausmaß | lokal behebbar? |
   |---|---|---|
   | Renditen sind Platzhalter | 43 % aller Trades bzw. 53 % der Trades mit Rendite; 2012–2020 ist jede Rendite 0 | ja: Ursache `period='5y'` |
   | Falsche Stammdaten und Ausschüsse | 6.859 Trades; die Person selbst stimmt | ja |
   | ETF-Erkennung, Share-Class-Format | Ein-Zeilen-Fehler | ja |
   | Asset-Gate schließt nicht aus | betrifft alle ausgeschlossenen Zeilen | ja |
   | Delta ≠ Vollauf | — | ja, durch Löschen des Delta-Pfads |
   | Ausschuss-Mitgliedschaften ohne Zeitachse | — | **nein, strukturell** |
   | Flat-CSV ohne Referenztabellen | 643 MB, 89 Spalten | **nein, strukturell** |

   Beim Zeitachsen-Problem fehlt vor allem eine Datenquelle für historische Mitgliedschaften. Ein Rebuild allein löst das nicht.
2. **Grüne Tests geben falsche Sicherheit.** Alle 2.303 Tests bestehen. Sie prüfen Funktionen gegen Mocks, aber keine Prüfung schaut auf das Endprodukt. Die ETF-Lücke (H1) geht direkt auf einen Mock zurück, der nicht der echten OpenFIGI-Antwort entspricht. Die rund 28.600 Testzeilen bremsen einen Umbau eher, als dass sie ihn absichern.
3. **Der größte Teil des Codes liefert keinen Produktwert.** Von rund 48.800 Produktivzeilen sind grob 8.000 bis 10.000 für das heutige Produkt nötig.
   - Committee Matcher: 18.800 Zeilen. Heute laufen davon rund 650 zur Laufzeit, nötig wäre ein Loader mit etwa 80 Zeilen.
   - Eigene Trade-Scraper: rund 4.600 Zeilen, vollständig ungenutzt.
   - Stufe 1 holt den größten Teil dieses Gewinns schon durch Löschen.
4. **Ein Neubau des Rückgrats ist trotzdem sinnvoll.** Datenmodell, Zeitachse und eine Filter-Engine mit „nicht bewertbar“ sind die Teile, die Benes Hauptprobleme betreffen: Übersicht, unverlässliche Filter, keine Architekturarbeit möglich. Diese Teile lassen sich in der Flat-CSV mit der 3.277-Zeilen-God-Class nur mühsam nachrüsten.

### Warum nicht sofort der komplette Rebuild

- Ein Rebuild ohne vorherige Sanierung wird gegen einen fehlerhaften Altbestand abgenommen.
- Er trägt das Risiko, wieder zu verslopen. Der Bestand ist genau so entstanden: Agent-Sessions ohne Größenbudget.
- Er würde ein Score-Konstrukt neu bauen, das auf sauberen Daten ab 2021-07 keinen Vorteil gegenüber dem Markt zeigt (Spearman gegen AR -0,016, Teilbericht 04). Ob der Score als Auffälligkeits-Flag oder als Prognose gedacht ist, muss **vor** dem Neubau feststehen.

### Was ohne jeden Umbau gilt

- Das Frontend funktioniert und kann weitgehend übernommen werden (09).
- Abnormal Returns, Swarm-Signale, Amount-Parser, Ticker-Klassifikator-Regeln und der Quiver-Download sind korrekt.
- **Vorbedingung für beide Stufen:** Der Quiver-Import läuft seit dem 2026-08-03 nicht. Ursachen: zuerst Abo bzw. Timeout, seit 10/2026 ein Cookie-Overlay. Der systemd-Timer meldet nichts. Quiver ist die einzige aktive Trade-Quelle.

---

## 1. Systemlandkarte (Ist)

### 1.1 Datenfluss

```
systemd-Timer 03:00  (deploy/systemd/quiver-import.timer)
  -> scrapers/quiver_scraper.py          Playwright-Login bei Quiver, XLSX-Export
  -> data/raw/congress-trading-all.xlsx  114.347 Zeilen, 20 Spalten, 12 MB
  -> scripts/import_trades.py            17 Stages in einer God-Class (3.277 Z.)
       rename/date -> Name-Matching + Committees -> Ticker-Klassifikation -> OpenFIGI
       -> SEC-CIK -> yfinance-Sektor -> Volumen -> Renditen/AR -> Gate/Tags/Vote-Timing
       -> Signale -> Score -> Suspicious-Export -> Save
  -> data/processed/trades_master_complete.csv  89 Spalten, 643 MB
  -> app/streamlit_app.py                lädt die komplette CSV
```

Referenzdaten, die eingespeist werden:
- `data/mappings/*member&(sub)committees.csv`: aktueller Stand, ohne Zeitachse
- Committee-Mapping-Revision `map-fullrun-20261016` über `config/committee_mapping.py`
- `bill_actions.json` aus `scrapers/fetch_congress_data.py`
- Preis-Cache `data/cache/price_history.parquet`

Hinweis zum Datenstand: Laut Teilbericht 03 nutzt der Worktree-Master vom 31.07. noch das Legacy-Mapping, nicht die V2-Revision (`map-fullrun-20261016`). Die Committee-Signale im Master spiegeln die V2-Zuordnung also noch nicht wider.

### 1.2 Externe Dienste

| Dienst | Wofür | Fundort | Key/Kosten | Zustand |
|---|---|---|---|---|
| Quiver Quantitative | einzige Trade-Quelle | `scrapers/quiver_scraper.py:128`, `config/settings.py:455-489` | Login, Premium-Abo | **seit 2026-08-03 ausgefallen** (02) |
| OpenFIGI | Ticker → FIGI/Security-Type | `utils/ticker_normalizer.py:215` | optionaler Key, frei | läuft, aber falsch ausgewertet (01) |
| SEC EDGAR | `company_tickers_exchange.json` → CIK | `utils/issuer_group_mapper.py:90` | User-Agent, frei | läuft, nur aktuelle Ticker |
| yfinance | Sektor, Kurse, Paper-Return | `utils/ticker_sector.py:77`, `scoring/post_trade_returns.py:640` | frei, inoffiziell, Lizenz nur private Nutzung (12) | Cache erst ab 2021-05 (04) |
| Congress.gov API | Bills, Actions, Subjects | `scrapers/fetch_congress_data.py:53` | Key, frei | auf Kongress 119 hartkodiert (06) |
| senate.gov XML | Senate-Mitgliedschaften | `scrapers/senate_committee_xml/` | frei | brauchbar (06) |
| House Clerk PDF | House-Mitgliedschaften | `scrapers/house_pdf_parsing_tool/` | frei | Einmal-Snapshot 06.01.2026 |
| Codex/Claude CLI-Abo | Committee → Industry-Mapping | `committee_matcher/submapper.py` | Abo | Fullrun erledigt |
| OpenRouter | API-Mapper (aufgegeben) | `committee_matcher/provider.py` | kostenpflichtig | nicht produktiv |
| Anthropic Vision | PDF-Extraktion der eigenen Scraper | `scrapers/house_pdf_extractor*.py` | kostenpflichtig | toter Pfad |

### 1.3 Umfang

| Verzeichnis | Py-Zeilen | Produktiv genutzt? |
|---|---:|---|
| `committee_matcher/` | 18.802 | rund 650 Zeilen nötig (Loader in `config/`), Rest Infrastruktur oder aufgegeben (07) |
| `scrapers/` | 8.304 | nur `quiver_scraper.py` (271) und die Committee-Parser; die PTR-Scraper sind tot (05) |
| `scripts/` | 6.781 | `import_trades.py` (3.277) und `reset_pipeline.py`; mehrere Einmal-Migrationen und Diagnose-Skripte (10) |
| `app/` | 5.143 | ja (09) |
| `utils/` | 3.985 | ja, aber Ticker-Logik defekt (01) |
| `scoring/` | 1.944 | ja (04) |
| `signals/`, `tags/`, `gates/` | 2.332 | ja (03) |
| `config/` | 1.533 | ja; `settings.py` mit 878 Zeilen |
| **Summe Produktivcode** | **≈ 48.800** | |
| `tests/` | 28.616 | 2.303 grün, aber keine Prüfung des Endprodukts |
| Markdown | ≈ 17.700 | `DATA_STRUCTURE.md` 2.565 Z., `CLI_Bedienungsanleitung.md` 2.117 Z. |
| Daten | 643 MB Master + 591 MB „kuratiert“ + 3,5 GB Backups + 34 MB Committee-Matcher-Läufe | normalisiert < 50 MB (02, 11) |

---

## 2. Feature-Katalog mit Verweisen

Legende für die Spalte **Rebuild**:

| Wert | Bedeutung |
|---|---|
| **übernehmen** | Code im Wesentlichen 1:1 übernehmen |
| **portieren** | Logik und Wissen übernehmen, Code neu schreiben |
| **neu** | Konzept neu entwerfen |
| **verwerfen** | weglassen |
| **extern** | durch eine externe Quelle ersetzen |

### 2.1 Datenbeschaffung (Trades)

| Feature | Fundort | Zustand | Rebuild |
|---|---|---|---|
| Quiver-Download per Playwright, atomares `os.replace` | `scrapers/quiver_scraper.py:44,128-235`, Selektoren `config/settings.py:457-486` | funktioniert grundsätzlich; Cookie-Overlay `fc-consent-root` blockiert seit 10/2026 | **übernehmen** + Health-Check |
| systemd-Timer mit `Persistent=true` | `deploy/systemd/quiver-import.*` | läuft, meldet keine Fehler | **übernehmen** + Alarm |
| House-PTR-Scraper (ZIP/XML-Index, Skip-Patterns, A-K-Betragsklassen) | `scrapers/house_scraper.py` | tot, nur Tests | **portieren nur bei Quiver-Ausfall**, etwa 300 Zeilen (05) |
| Senate-PTR-Scraper (DataTables-POST `length=-1`) | `scrapers/senate_scraper.py` | tot | Wissen behalten (05) |
| PDF-/Vision-Extraktion House v1/v2, Senate | `scrapers/house_pdf_extractor.py`, `_v2.py`, `senate_pdf_extractor.py` | tot; v2 ohne Test; bekannte OCR-Mängel | **verwerfen** |
| Diagnose-Skripte | `scripts/diagnose_scraper.py`, `diagnose_master_quiver_delta.py` | veraltet, Logik-Kopie | **verwerfen** |

### 2.2 Pipeline und Datenmodell

| Feature | Fundort | Zustand | Rebuild |
|---|---|---|---|
| 17-Stage-Pipeline, Voll/Delta/Einzel-Stage/dry_run | `scripts/import_trades.py:2901` (`run`), `:2549` (`run_delta`), `:1993-2525` (`_stage_*`) | dreifach implementiert; Delta ≠ Voll | **neu** |
| trade_id als SHA256-Inhaltshash, Multiset-Delta | `utils/trade_id.py:33,68,83` | korrekt; Quiver-Korrekturen erzeugen Dubletten | **portieren** (Schlüssel aus der Quelle + Versionierung) |
| Amount-Parser inkl. „Over $50M“ und Einzelwerte | `utils/amount_parser.py:35-103` | funktioniert | **übernehmen** |
| Datums-Fallback (tx_date fehlt oder liegt nach disclosure) | `scripts/import_trades.py:478-518` | funktioniert | **übernehmen** |
| Name-Matching per Fuzzy (WRatio ≥ 85) | `utils/name_matcher.py:270,335` | **defekt**: falsche Stammdaten und Ausschüsse bei 6.859 Trades (Person selbst stimmt) | **verwerfen**, Join über `BioGuideID` aus Quiver |
| Reset mit Whitelist/Protected/Archiv | `scripts/reset_pipeline.py` | solide | **übernehmen** |
| Flat-CSV mit 89 Spalten | `data/processed/trades_master_complete.csv` | 71 % der Bytes in `Insider_Sectors` | **neu** (Abschnitt 6.2) |

### 2.3 Abgeordnete und Ausschüsse

| Feature | Fundort | Zustand | Rebuild |
|---|---|---|---|
| House-Committee-PDF-Parser (x-Schwelle R/D, Alphabetenliste als Kreuzprüfung) | `scrapers/house_pdf_parsing_tool/parse_house_committee_pdf.py:587,718,765` | funktioniert, Einmal-Snapshot, kein Pytest | **extern** (`unitedstates/congress-legislators`), Wissen behalten |
| Senate-PDF-Parser | `scrapers/senate_pdf_parsing_tool/` | redundant zur XML | **verwerfen** |
| Senate-XML-Fetch und -Parser | `scrapers/senate_committee_xml/fetch_senate_xml.py:43,109`, `parse_senate_xml.py:25,76` | funktioniert | **übernehmen** als Fallback oder Abgleich |
| F2-Grammatik für Committee-Strings | `utils/committee_parser.py:35` | funktioniert | **verwerfen** (normalisierte Tabelle statt String-Grammatik) |
| Kongressdaten (Bills, Actions, Subjects) | `scrapers/fetch_congress_data.py:72-1200` | auf Kongress 119 hartkodiert, Abstimmungen nur 01/2025–04/2026 | **portieren**, mehrere Kongresse |
| Rollen (Chair/Ranking/Ex officio) | `tags/committee_tag.py:13-60`, Spalte `ExOfficio` | Begriff kollidiert mit der echten Ex-officio-Rolle (06) | **neu** (Rollen-Enum je Mitgliedschaft) |

### 2.4 Ticker, Wertpapiere, Sektor

| Feature | Fundort | Zustand | Rebuild |
|---|---|---|---|
| Offline-Klassifikator (17-stufiger Regex-Baum, 18 TickerTypes) | `utils/ticker_classifier.py:260,270`, Regex `:140-182` | Regeln sauber | **übernehmen** (Regex, Suffix- und Exchange-Tabellen `:89,:99,:129`) |
| Muni-Override per Wortgrenze | `utils/ticker_classifier.py:225-253` | funktioniert | **übernehmen** |
| OpenFIGI-Normalisierung, Routing je Strategie | `utils/ticker_normalizer.py:215,290` | **defekt** (ETF, Share-Class, Zeitbezug) | **neu**, etwa 3–5 Personentage (01) |
| OpenFIGI-Formatumsetzung | `utils/ticker_normalizer.py:803-816` | **falsch**: `BRK.B` statt `BRK/B` | **portieren** mit verifizierten Regeln |
| ETF-Korrektur STOCK → ETF | `utils/ticker_normalizer.py:1074-1092` | **greift nie** | **neu** |
| SQLite-Cache mit TTL | `utils/ticker_normalizer.py:854-1000` | funktioniert | **portieren** |
| issuer_ticker-Fallback-Kette, CIK-Gruppierung | `utils/issuer_ticker.py:53`, `utils/issuer_group_mapper.py:90-130` | GOOG/GOOGL korrekt; ETFs fälschlich über Sponsor-CIK gruppiert | **portieren** (CIK nie für ETFs) |
| yfinance-Sektor-Enricher | `utils/ticker_sector.py:77,130` | 20 % ohne Sektor (Survivorship), Ticker-Wiederverwendung (ALCO) | **portieren** + Umbenennungs-Mapping + Namensabgleich |
| Retry/Backoff und Netzwerk-Canary | `utils/network_retry.py`, `utils/network_canary.py` | überentwickelt; Idee „leeres `.info` ≠ delisted“ ist richtig | **portieren**, stark reduziert |
| Asset-Class-Gate („Filter 0“) | `gates/asset_class_gate.py:26-60`, `config/settings.py:29` | setzt nur ein Flag, das nur Vote-Timing beachtet; ETFs rutschen durch | **neu** |

### 2.5 Filter, Tags, Signale

Die volle Tabelle mit Parametern steht in `03_filter_signale.md`, Abschnitt 1.

| Feature | Fundort | Zustand | Rebuild |
|---|---|---|---|
| CommitteeTag (Ausschuss-Sektor-Match, „Filter 3“) | `tags/committee_tag.py:105-240` | Logik ok, Eingangsdaten zeitlich falsch | **portieren** auf zeitlich gültige Mitgliedschaften |
| Parent-Core additiv | `config/settings.py:451` | fachlich richtig | **übernehmen** (Regel) |
| VoteTimingTag / floor_action („Filter 4“) | `tags/vote_timing_tag.py:298-403`, `signals/floor_action_signal.py` | praktisch wertlos: 99 % Trefferquote im abgedeckten Zeitraum, davor 0 % | **neu** (Kammer, eigene Roll-Call-Position, engeres Fenster) oder streichen |
| committee / committee_full / ex_officio | `signals/committee_*_signal.py` | im Strict-Modus deckungsgleich | **portieren**, zusammenlegen |
| swarm_buy/sell/conflict/paradox | `signals/swarm_*_signal.py` | **funktioniert**: 96 % echte Schwärme | **übernehmen** |
| accumulative | `signals/accumulative_signal.py` | aufgebläht, da Lots am selben Tag als Aufbau zählen | **portieren** (Tagesaggregation) |
| contrarian, extreme_report, trade_volume | `signals/contrarian_signal.py`, `extreme_report_signal.py`, `trade_volume_signal.py` | funktionieren, Gewicht 0 | **portieren** |
| signal_event_id | `utils/signal_event_id.py` | instabil zwischen Delta und Voll | **neu** |

### 2.6 Scoring und Renditen

| Feature | Fundort | Zustand | Rebuild |
|---|---|---|---|
| Trade-Score mit Hard-Gate | `scoring/trade_score.py:129`, Gewichte `config/settings.py:171,196` | faktisch `5·committee_full + 20·swarm_buy`; kein Vorteil gegenüber dem Markt | **neu** als Auffälligkeits-Flag mit Begründung |
| Post-Trade-Renditen 30–365 d | `scoring/post_trade_returns.py:512,640` | Rechenlogik korrekt; **Daten vor 2021-05 sind Artefakte** | **portieren** (vektorisiert; Fetch, Cache und Returns getrennt) |
| Sell-Sign-Konvention | `scoring/post_trade_returns.py:623` | korrekt | **übernehmen** |
| Abnormal Returns vs. SPY, Anker Transaktion/Meldung, Cluster-Bootstrap | `scoring/abnormal_returns.py:51-137,267,292` | **korrekt**: 1.489 Werte nachgerechnet | **übernehmen** |
| Exit-Erkennung inkl. Teil-Close | `scoring/post_trade_returns.py:1051` | Idee gut; Delta aktualisiert alte Trades nicht | **portieren** |
| Preis-Cache (Parquet, Delisting-Marker, atomar) | `scoring/post_trade_returns.py:65,192,455` | Abdeckung erst ab 2021-05-28 (`period='5y'`); Stand bis 2026-07-15 (Worktree) bzw. 2026-08-10 (Haupt-Worktree) | **portieren** + Abdeckungsprüfung |
| Event-Study | `scripts/analyse_event_study.py` | eigenständig | **portieren** als Analysemodul |

### 2.7 Committee → Industry-Mapping (Committee Matcher V2)

| Feature | Fundort | Zustand | Rebuild |
|---|---|---|---|
| Produkt: 225 Units → 0–5 yfinance-Industries mit Beleg | `data/committee_matcher_v2/reviews/map-fullrun-20261016/reviewed_assignments.csv` (522 Z.), `reviewed_unit_coverage.csv` | vollständig; 24 % der Belege nur „Recherche“; 6 von 15 Stichproben fragwürdig | **übernehmen** als Datenbestand, Recherche-Fälle nachprüfen |
| Jurisdiktions-Inputs (48 Markdowns, House Rule X, Senate) | `data/committee_matcher_inputs/md/`, `corpus_manifest.yaml` | **wertvollster Rohstoff** | **übernehmen** |
| Prompt `mapping-system-v3` | `committee_matcher/prompts.py` | brauchbar | **übernehmen** + Pflichtzitat |
| Loader (Legacy-Fassade) | `config/committee_mapping.py:62`, `config/committee_revision.py:137,310` | funktioniert | **portieren** (etwa 80 Zeilen) |
| Submapper-CLI, API-Mapper, Workflow, Review, Evaluation, Gold-Set | `committee_matcher/*.py` (18.800 Z.) | rund 85 % ohne Produktnutzen | **verwerfen**; Neuaufbau als Skript mit 300–500 Zeilen (07) |
| Legacy-Mapping-CSV | `data/mappings/subcommittee_sector_industry_mapping.csv` | veraltet, teils überdehnt | **verwerfen** |

### 2.8 Frontend

| Feature | Fundort | Zustand | Rebuild |
|---|---|---|---|
| Sidebar: Datumsbasis, Zeitfenster, Token-Suche, Min-Score, Horizont, Spaltenwahl, Signal-Ausschluss, Gewichts-Editor, Presets | `app/streamlit_app.py:581-899`, `app/scoring_presets.py` | funktioniert | **übernehmen** |
| Tab Trades + Trade-Detail-Popup (Kurschart, Exit-Marker, verwandte Trades, Teil-Close) | `app/streamlit_app.py:1331,1714-1763`, `app/chart_helpers.py` | funktioniert | **übernehmen** |
| Tab Assets (Aggregation je Issuer) | `app/streamlit_app.py:271,1765` | funktioniert | **übernehmen**, Aggregation ins Analysepaket |
| Tab Members + Member-Detail (4 Unter-Tabs, Red Flags, Sektor-Konzentration, Trajektorien, AR) | `app/streamlit_app.py:319,1799`, `app/member_detail.py:197,612,636,722`, `app/member_metrics.py` | funktioniert; Metriken (785 Z.) im UI | **portieren**, Metriken ins Analysepaket |
| Tab Post Trade Performance | `app/streamlit_app.py:1877-1966` | funktioniert | **übernehmen** |
| Tab Recent Trades + CSV-Export nach `data/agent_exports/` | `app/streamlit_app.py:1968-2132`, `app/staging_export.py:114` | funktioniert, kein Browser-Download | **übernehmen** |
| Tab Score-Analyse (Spearman, Buckets, Signal-Tabelle mit Cluster-Bootstrap) | `app/streamlit_app.py:2148-2336`, `app/score_correlation.py` | Methodik gut | **übernehmen** (Methodik) |
| Glossar | `app/glossary.py` | gut | **übernehmen** |

---

## 3. Belegte Defekte nach Schwere

Die Nummern in der letzten Spalte verweisen auf die Teilberichte. „Belegt“ heißt: durch Daten, Code oder einen Live-Check nachgewiesen. Vermutungen stehen in den Teilberichten getrennt.

### Kritisch: verfälscht die Kernaussage

| # | Befund | Ausmaß | Ursache | Fundort | Bericht |
|---|---|---|---|---|---|
| K1 | Renditen vor 2021-05 sind Platzhalter | Return und AR exakt 0 bei 43 % aller Trades bzw. 53 % der Trades mit Rendite; 2012–2020 ist jede vorhandene Rendite 0 | Preis-Cache beginnt **wegen** `period='5y'` am 2021-05-28; keine wirksame Lückenprüfung im erzeugenden Lauf (`_max_gap` liegt uncommittet im Worktree) | `config/settings.py:561`, `scoring/post_trade_returns.py:171,512`, `data/cache/price_history.parquet` | 04, 11, 13 |
| K2 | Falsche Stammdaten und Ausschüsse | 6.859 Trades (6 %), davon 6.016 mit fremden Committees; Name und BioGuideID stimmen, falsch sind Party/State/Committees. Beispiele: Rooney bekommt Fleischmanns Daten, Lamar Smith die von Chris Smith (State NJ), „Mr. Lamar Smith“ State WA | Fuzzy-Match ohne Bundesstaat und ohne BioGuideID; überschreibt die korrekten Quiver-Felder Party/State | `utils/name_matcher.py:335`, `scripts/import_trades.py:612-613` | 02, 13 |
| K3 | Ausschuss-Mitgliedschaften ohne Zeitachse | Committee-Trefferquote steigt von 6,5 % (2013) auf 25 % (2025); Khanna wird 2017 über einen Ausschuss von 2023 geflaggt | Roster-CSVs sind ein Snapshot des 119. Kongresses | `data/mappings/*member&(sub)committees.csv`, `tags/committee_tag.py` | 03, 06 |
| K4 | Ticker ohne Handelsdatum aufgelöst | 719 FB-Trades zeigen auf einen ProShares-ETF; ähnlich PCLN, INFO, UN, ABX; rund 7,6 % FIGI-Zeilen ohne Namensübereinstimmung (Heuristik, Nenner unklar, siehe 7) | Lookup gegen aktuelle Ticker-Belegung | `utils/ticker_normalizer.py` | 01, 08 |
| K5 | Identische Zeilen | trade_id nicht eindeutig: 12.329 überzählige Zeilen, 11.149 komplett identisch; Khanna und McCaul stellen 47 % aller Zeilen | **Die Quiver-XLSX enthält bereits 11.152 identische Zeilen**, Master = XLSX = 114.347 Zeilen; die Pipeline erzeugt im Vollauf keine Dubletten. Vermutlich Mehrfach-Lots ohne exportiertes Unterscheidungsmerkmal (Subholding nur zu 3,4 % gefüllt, z. B. McCaul EOG 44-mal). Ungelöst ist nur, dass sie als Einzeltrades zählen. Theoretisches Risiko: Quiver-Korrekturen erzeugen im Delta neue Zeilen (Inhaltshash). | `utils/trade_id.py:32`, `config/settings.py` | 02, 11, 13 |
| K6 | Datenimport steht still | letzte XLSX vom 2026-08-03 | Abo bzw. Timeout, dann Cookie-Overlay; kein Alarm | `logs/quiver_scraper.log`, `deploy/systemd/` | 02 |

### Hoch: verfälscht Signale oder Score

| # | Befund | Ausmaß | Fundort | Bericht |
|---|---|---|---|---|
| H1 | ETF-Erkennung greift nie | 0 Zeilen mit Typ `etf`; 3.938 Fonds-Zeilen laufen als `stock`, davon 3.482 ETPs (SPY, QQQ, VOO, GLD …, etwa 177 Mio. USD) | `utils/ticker_normalizer.py:1082-1084` (prüft `securityType2` statt `securityType == "ETP"`) | 01, 08 |
| H2 | Asset-Gate schließt nicht aus | 987 von 1.139 ausgeschlossenen Zeilen tragen Signale, 17 mit Score > 0 | `gates/asset_class_gate.py` (nur Flag) | 03 |
| H3 | Share-Classes ohne FIGI | 897 von 897 Zeilen | `utils/ticker_normalizer.py:816` (`BRK.B` statt `BRK/B`) | 01, 13 |
| H4 | Delta ≠ Vollauf | alte Käufe bleiben `held`, Sequenz- und Schwarm-Signale werden nicht nachgezogen; Event-IDs instabil | `scripts/import_trades.py:2716-2718,2755-2771` | 02, 03 |
| H5 | floor_action ohne Aussagekraft | Abstimmungsdaten nur 01/2025–04/2026; dort 99,2 % Treffer bei Committee-Trades; keine Kammerprüfung | `tags/vote_timing_tag.py:298-403`, `scrapers/fetch_congress_data.py:53` | 03 |
| H6 | accumulative aufgebläht | 52,8 % Trefferquote, davon 33.084 nur durch Lots am selben Tag | `signals/accumulative_signal.py` | 03 |
| H7 | Stille Falsch-Negative | 22.496 Trades ohne Sektor, 21.916 ohne Committees; kein Status „nicht bewertbar“ | `tags/committee_tag.py`, `utils/ticker_sector.py` | 03, 08 |
| H8 | Committee-Mapping schwach belegt | 123 von 522 Zuordnungen nur mit Beleg „Recherche“; Confidence fast immer 3; 6 von 15 Stichproben fragwürdig (z. B. Senate Budget → Oil & Gas) | `reviewed_assignments.csv` | 07 |

### Mittel

| # | Befund | Fundort | Bericht |
|---|---|---|---|
| M1 | Rund 1.100 Optionen und rund 1.100 anleiheartige Zeilen als Aktie (Asset-Typ der Quelle ignoriert) | `utils/ticker_classifier.py`, `import_trades.py:2049` | 01 |
| M2 | Senator Mullin ohne Match (399 Trades, trotzdem 96 mit Score > 0) | `utils/name_matcher.py` | 02 |
| M3 | 23 stille Code-Defaults bei fehlenden Settings, 2 Settings wirkungslos | `config/settings.py`, diverse `getattr` | 03 |
| M4 | `excess_return` mit inkonsistenter Einheit; `post_return_365d` außerhalb plausibler Grenzen (-2.314 %) | Master | 11 |
| M5 | Survivorship: 16 % `delisted`; Sektor 2013 nur zu 59 % gefüllt | `utils/ticker_sector.py`, Preis-Cache | 04, 08 |
| M6 | `auto_adjust` mischt Dividenden und Splits (1–2 % Drift bei Dividendenzahlern) | `scoring/post_trade_returns.py:172` | 04 |
| M7 | Senate-Committee-Namen passen nicht zwischen Roster und Master-Liste (70 Subcommittee-Paare); Namensformat House/Senate verschieden | `data/mappings/` | 06 |
| M8 | Frontend lädt 643 MB ohne `usecols`; Score-Neuberechnung bei jedem Rerun | `app/streamlit_app.py:190-220` | 09 |
| M9 | Save nicht atomar; `STAGE_DEPS` und `pipeline_state.json` veraltet bzw. funktionslos | `scripts/import_trades.py:233,2509,2537` | 02 |

**Folge für bisherige Erkenntnisse:** Der Befund „trade_score korreliert kaum mit dem Return“ wurde auf kontaminierten Daten erhoben (K1, K2, K3, H1). Teilbericht 04 hat ihn auf dem sauberen Ausschnitt ab 2021-07 (n = 40.929) erneut geprüft:
- Spearman gegen AR: -0,016. Der Score hat also auch dort keinen Vorteil gegenüber dem Markt.
- Der Roh-Return von `swarm_buy` (+4,4 %) ist Markt-Timing und verschwindet nach Marktbereinigung.

Wenn K2 bis K4 und H1 behoben sind, ist eine erneute Prüfung nötig.

---

## 4. Was gut ist und mitgenommen werden sollte

### 4.1 Code (übernehmen oder eng portieren)

| Baustein | Fundort |
|---|---|
| Amount-Parser + Testfälle | `utils/amount_parser.py`, `tests/test_amount_parser.py` |
| Datums-Fallback-Regeln | `scripts/import_trades.py:478-518` |
| Quiver-Download (atomar) + Selektoren | `scrapers/quiver_scraper.py`, `config/settings.py:457-486` |
| Reset-Muster (Whitelist, Protected, Archiv) | `scripts/reset_pipeline.py` |
| Ticker-Klassifikator: Regex, Suffix- und Exchange-Tabellen, Muni-Override | `utils/ticker_classifier.py:89,99,129,140-182,225-253` |
| Abnormal-Return-Kern (vektorisiert, Sell-Sign einmal, Cluster-Bootstrap) | `scoring/abnormal_returns.py:51-137,267` |
| Swarm-Cluster-Logik inkl. Conflict/Paradox | `signals/swarm_*_signal.py` |
| Senate-XML-Fetch/-Parser | `scrapers/senate_committee_xml/` |
| Score-Analyse-Methodik (Spearman, geclusterter Bootstrap, Limitationen) | `app/score_correlation.py`, `app/streamlit_app.py:2148-2336` |
| Glossar, Presets-Konzept | `app/glossary.py`, `app/scoring_presets.py` |

### 4.2 Datenbestände (unbedingt sichern)

| Bestand | Fundort |
|---|---|
| Jurisdiktions-Markdowns (48 Dateien) + Manifest | `data/committee_matcher_inputs/md/`, `corpus_manifest.yaml` |
| Finale Committee → Industry-Zuordnung + Unit-Abdeckung | `data/committee_matcher_v2/reviews/map-fullrun-20261016/` |
| Taxonomie (yfinance, 11 Sektoren) | `data/committee_matcher_v2/taxonomy/approved_baseline.json`, `data/mappings/yf_sectors_with_industries*.json` |
| Roster und Committee-Listen (119. Kongress) | `data/mappings/house_member&(sub)committees.csv`, `senate_member&(sub)committees.csv`, `us_*_all_committees.csv` |
| Legislative-Subject → Sektor, Subcommittee → Region | `data/mappings/legislative_subject_mapping_complete.csv`, `subcommittee_region_mapping.csv` |
| Ehemalige Mitglieder, Namensaliase | `data/raw/former_members.csv`, `data/mappings/name_aliases.csv` |
| Preis-Cache (als Startbestand) | `data/cache/price_history.parquet` |
| OpenFIGI-Cache (securityType-Paare) | SQLite-Cache des Normalizers, siehe 01, Abschnitt 4 |

### 4.3 Recherche- und Erfahrungswissen

| Thema | Fundort |
|---|---|
| OpenFIGI-Formatregeln (live verifiziert: `BRK/B`, ETP in `securityType`, Search ohne `exchCode`) | `01_ticker_normalisierung.md`, Abschnitt 4 |
| Edge-Case-Liste für Ticker-Tests (FB→META, PCLN→BKNG, ABX→GOLD, RDS.A/B→SHEL, BRK.B, BF.B, GOOG/GOOGL, Muni-Pseudo-Ticker …) | `01_ticker_normalisierung.md`, Abschnitt 4 |
| Ticker-/Issuer-Designprinzipien (CIK für Multi-Class, nie für ETFs) | `organisation/Ticker_Architektur.md` |
| Scraper-Wissen House/Senate/Paper-PTR, Hand-Delivered-Formulartypen A–D | `organisation/tool_specific_md/Trade_scraper_status.md`, `HAND_DELIVERED_PDF_ANALYSE.md`, `Senate_website_structure.md` |
| House-Committee-PDF-Layout | `organisation/tool_specific_md/house_all_pdf_info.md` |
| Signal-Konzepte, Schwellen, Rollenregeln | `organisation/Filter_Instanz_Parameter.md`, `organisation/tool_specific_md/signal_info.md`, `03_filter_signale.md`, Abschnitt 4 |
| Vote-Timing-Erkenntnis: Ohne Kammer, eigene Abstimmung und Basisrate ist das Signal wertlos | `organisation/tool_specific_md/vote_timing_audit.md`, `03_filter_signale.md` |
| Preis-Aktualisierung je Laufmodus | `organisation/Preis_Aktualisierung.md` |
| Entscheidungshistorie Committee Matcher (was aufgegeben wurde und warum) | `committee_matcher/AUFGEGEBENER_PLANTEIL.md`, `SUBMAPPER_ANLEITUNG.md` |
| Quiver-Schema (20 Spalten inkl. `BioGuideID`, `last_modified`) | `scripts/import_trades.py:384-405` |

---

## 5. Ballast-Bilanz

| Posten | Umfang | Bericht |
|---|---|---|
| Committee-Matcher-Infrastruktur ohne Produktnutzen | rund 18.000 Z. Code + rund 15.500 Z. Tests + rund 3.500 Z. Pläne + 34 MB Läufe | 07 |
| Eigene PTR-Scraper und Extraktoren (tot) | rund 4.600 Z. Code + rund 3.900 Z. Tests | 05 |
| Dreifache Pipeline-Orchestrierung | großer Teil der 3.277 Z. in `import_trades.py` | 02 |
| Tote oder Einmal-Skripte | 8–12 Dateien (z. B. `migrate_add_trade_ids.py`, `committee_matcher_migrate_inputs.py`, `analyze_senate_pdf.py`) | 10 |
| Nicht-Code-Ballast im Baum | rund 5 MB (`scrapers/Recherche-Aufgabe in Datei - Claude_files/`, Quell-PDFs, `scripts/excel_debug_output.txt`) | 05, 10 |
| Daten-Altstände | 3,5 GB Master-Backups, `*.pre_f2.csv`, `*.pre-p6-migration.bak`; 735 Dateien in `data/` weder getrackt noch ignoriert | 02, 10 |
| Doku | rund 17.700 Zeilen Markdown für ein Ein-Personen-Projekt; 2 tote Verweise; rund 6 erledigte Pläne | 10 |
| Ungenutzte Abhängigkeiten | `beautifulsoup4`, `pytest-mock`; `pytest` in den Produktiv-Requirements | 10 |

Aus Hygiene-Sicht allein wäre Aufräumen im Bestand machbar (10). Genau das ist Stufe 1 (6.5). Für den späteren Neubau des Rückgrats sprechen nicht der Ballast, sondern Datenmodell, fehlende Zeitachse und Filterstruktur.

---

## 6. Empfehlung

### 6.1 Abwägung der drei Wege

| Kriterium | A: Reparatur im Bestand | B: Sanieren durch Löschen | C: Neubau des Rückgrats |
|---|---|---|---|
| K1 Renditen | lokal, klein: `period='max'`, Lückenprüfung (liegt im Worktree), Neulauf | wie A | neuer Metrics-Schritt mit Abdeckungsprüfung |
| K2 Stammdaten | lokal, ca. 1 Tag: BioGuideID-Join, Party/State nicht überschreiben | wie A | von Anfang an BioGuideID |
| K3 Zeitachse | Datenquelle + neues Roster-Format + CommitteeTag; hoch | wie A, aber auf kleinerem Code | Tabelle mit gültig von/bis; **Datenquelle bleibt in jedem Weg offen** |
| K4 Ticker point-in-time | mittel: Namensabgleich, Umbenennungs-Mapping | wie A | Normalizer neu (3–5 Personentage) |
| K5 Duplikate | klein: Report + Aggregation der Lots je Tag | wie A | Rohdaten versioniert, Lots explizit |
| H1–H3 | je eine Zeile bzw. Filter vor die Signale | wie A | im Neubau enthalten |
| H4 Delta ≠ Voll | drei Pfade angleichen | **Delta-Pfad löschen**, nur `run()` | entfällt |
| Datenmodell (Flat-CSV, 89 Spalten) | bleibt | bleibt | normalisiert, < 50 MB |
| Filter-Engine mit „nicht bewertbar“ | nur mühsam nachrüstbar | nur mühsam nachrüstbar | eingebaut |
| Codeumfang danach | ≈ 48.800 Z. | ≈ 15.000–20.000 Z. | Ziel ≤ 8.000–10.000 Z. |
| Zeit bis zu belastbaren Zahlen | Tage | Tage | Wochen |
| Hauptrisiko | Übersicht bleibt verloren | God-Class und Mock-Tests bleiben | Second-System-Effekt, erneuter Slop (6.3) |

**Ergebnis:**
- Für korrekte Zahlen reicht B.
- Für Benes eigentliche Ziele reicht B allein nicht: Übersicht zurückgewinnen, verlässliche Filter, wieder Architekturarbeit möglich. Dafür sind Datenmodell, Zeitachse und Filter-Engine nötig, also C für das Rückgrat.
- Die Kombination **B → Gate → C** liefert schnell belastbare Daten. Sie trifft die Produktentscheidung auf sauberer Basis und gibt C einen korrigierten Altbestand als Vergleichsmaßstab.

### 6.2 Zielarchitektur

**Datenmodell:** SQLite oder DuckDB/Parquet statt einer CSV (02, 11).

| Tabelle | Schlüssel | Inhalt |
|---|---|---|
| `raw_trades` | Quellschlüssel + Zeilenhash | Quiver-Spalten 1:1, `first_seen`, `last_seen`, `superseded_by` (Korrekturen ersetzen statt duplizieren) |
| `members` | `bioguide_id` | Name, Partei, Staat, Kammer, Amtszeiten |
| `committees` | `committee_id` | Kammer, Parent, Name |
| `committee_memberships` | `bioguide_id`, `committee_id`, gültig von/bis | Rolle (Chair, Ranking, Ex officio, Member) |
| `committee_industries` | `committee_id`, `industry` | aus `reviewed_assignments.csv`, mit Beleg |
| `securities` | Wertpapier-ID (FIGI) + Gültigkeit | Typ, Issuer (CIK), Sektor/Industry, Name; Ticker-Historie |
| `prices` | Ticker, Datum | aus dem Parquet-Cache |
| `trade_metrics` | `trade_id` | Returns, AR, Exit; NULL statt 0 bei fehlenden Kursen |
| `trade_flags` | `trade_id`, Regel | Treffer, kein Treffer oder nicht bewertbar, mit Begründung (Long-Format) |
| `trade_scores` | `trade_id`, Score-Version | Score + Breakdown |

Das Dashboard liest eine schmale View. Erwartete Größe: unter 50 MB.

**Pipeline-Schnitt:**

1. `ingest`
2. `reference` (Mitglieder, Ausschüsse, Mitgliedschaften)
3. `securities`
4. `metrics`
5. `flags`
6. `score`

Inkrementell ist nur das teure Netz-IO: Downloads, Ticker-APIs, Kurse, jeweils pro eindeutigem Schlüssel gecacht. Alles Abgeleitete wird bei jedem Lauf deterministisch neu gerechnet. 114.000 Zeilen dauern vektorisiert nur Sekunden. Damit entfallen Delta-Pfad, Einzel-Stage-Modus, `STAGE_DEPS`, Checkpoints und Master-Backups.

**Filter-Engine** (03, Abschnitt 5):
- Deklarative Regeln in drei Schritten: Eignung (Asset-Klasse) → zeitlich gültige Anreicherung → Regeln.
- Jede Regel liefert `hit`, `no_hit` oder `not_evaluable`, jeweils mit Begründung.
- Die Konfiguration ist an einer Stelle typisiert und validiert, ohne stille Defaults.
- Ein Golden-Set echter Trades dient als Regressionstest.
- Die Trefferquoten je Jahr werden als Drift-Monitor verfolgt.

**Tests auf zwei Ebenen:**
- Unit-Tests mit **echten aufgezeichneten API-Antworten** als Fixtures statt Mocks.
- **Datenintegritäts-Gates** auf dem Endprodukt. Beispiele:
  - Anteil Return = 0
  - Eindeutigkeit der Schlüssel
  - Abdeckung des Kurs-Caches je Jahr
  - Anteil ohne Sektor bzw. ohne Committee
  - Trefferquote je Signal und Jahr

**Externe Quellen statt Eigenbau** (12):
- `unitedstates/congress-legislators` für Mitglieder, IDs und Ausschüsse.
- SEC `company_tickers_exchange.json` für CIKs.
- Congress.gov API für Bills.
- Für historische Mitgliedschaften ist die Quellenlage lückenhaft, das muss entschieden werden (6.6).

### 6.3 Risiken eines Neubaus

| Risiko | Warum relevant | Gegenmaßnahme |
|---|---|---|
| Second-System-Effekt | Die Zielarchitektur bringt viel Neues auf einmal: Tabellen, Versionierung, Regel-Engine, Golden-Set, Drift-Monitor, mehrere Kongresse. Derselbe Ehrgeiz hat den Committee Matcher auf 18.800 Zeilen gebracht. | Größenbudget, Abbruchkriterium (6.4) |
| Erneuter Slop durch AI-Coding | Der Bestand ist durch Agent-Sessions ohne Größenbudget entstanden. | Leitplanken 6.4 als verbindliche Repo-Regeln in `AGENTS.md` |
| Verlust impliziten Wissens | Sonderfälle stecken nur im Code: Datums-Fallbacks, Namensbereinigung, Aliase, Muni-Override, Delisting-Marker, Exit-Toleranzen, Schwellen. | Wissensinventur vor dem Archivieren (6.4 Nr. 8) |
| Parallelbetrieb | Bene braucht das Dashboard während des Neubaus. | Altbestand nach Stufe 1 einfrieren, nur Bugfixes |
| Abnahme gegen fehlerhaften Altbestand | Ein Vergleich „≈ Altbestand“ misst gegen kontaminierte Daten. | Stufe 1 zuerst; je Gate die erwartete Abweichung angeben |
| Daten-Blocker | Datenquelle, historische Mitgliedschaften und Kursquelle sind offen. | Entscheidungs-Gate 6.6 vor Stufe 2 |
| Quiver-Lizenz | Kommerzielle Nutzung und Export sind ungeklärt (12). | vor einem öffentlichen Deployment klären |

### 6.4 Leitplanken gegen erneuten Slop

Diese Punkte gelten als Repo-Regeln ab dem ersten Commit von Stufe 2.

1. **Größenbudget je Schicht** als CI-Check (Richtwerte):

   | Schicht | Budget |
   |---|---:|
   | `ingest` | ≤ 400 Z. |
   | `reference` | ≤ 600 Z. |
   | `securities` | ≤ 900 Z. |
   | `metrics` | ≤ 600 Z. |
   | `flags` + `score` | ≤ 900 Z. |
   | `app` | ≤ 2.500 Z. |
   | **gesamt** | **≤ 8.000 Z.** |

   Wer ein Budget überschreitet, muss das im PR schriftlich begründen.
2. **Datei- und Funktionsgrenzen:**
   - Dateien ≤ 400 Zeilen, Funktionen ≤ 60 Zeilen
   - keine Orchestrator-Klasse
   - genau ein Pipeline-Einstiegspunkt
3. **Definition of Done je Phase:**
   - Datenintegritäts-Gate auf dem Endprodukt grün
   - mindestens ein Test mit aufgezeichneter echter API-Antwort
   - eine Doku-Seite pro Modul mit höchstens 150 Zeilen
   - eine CHANGELOG-Zeile
   - kein toter Code (`vulture`/`ruff` sauber)
   - Subagent-Review mit dem Auftrag „Was kann weg?“
4. **Keine Infrastruktur ohne Konsumenten:**
   - keine CLI-Subbefehle, Settings oder Frameworks ohne bestehendes Feature
   - kein Feature ohne Eintrag im Feature-Katalog
5. **Settings-Disziplin:**
   - typisierte Konfiguration (z. B. pydantic) mit Pflichtfeldern, ohne `getattr`-Defaults
   - höchstens ca. 60 Parameter
   - ein Parameter ohne Leser lässt den Start fehlschlagen
6. **Mock-Verbot für externe Formate:** OpenFIGI-, yfinance-, Quiver- und Congress.gov-Antworten nur als aufgezeichnete Fixtures.
7. **Agent-Arbeitsweise:**
   - eine Phase pro Branch
   - der Plan nennt Zeilenbudget und Löschliste
   - neue Module nur mit Eintrag in der Modulliste von `AGENTS.md`
8. **Wissensinventur vor dem Archivieren:**
   - Die Sonderfälle aus dem Altcode als Tabelle festhalten (Sonderfall, Fundort, übernehmen ja/nein).
   - Ausgangspunkt sind die Listen in Abschnitt 4 und eine Suche nach `if`-Sonderfällen.
9. **Freeze des Altbestands:** Nach Stufe 1 gibt es dort keine neuen Features mehr.
10. **Abbruchkriterium:** Ist nach Phase 3 das Budget um mehr als 50 % überschritten oder sind die Gates nicht grün, wird neu entschieden, ob bei B geblieben wird.

### 6.5 Stufe 1: Sanieren durch Löschen

Die Stufe ist eigenständig sinnvoll, auch wenn Stufe 2 nie kommt.

**Korrekturen:**
1. Den Quiver-Import wieder in Gang bringen: Abo klären, Cookie-Overlay `fc-consent-root` entfernen, Fehlschläge des Timers sichtbar melden.
2. ETF-Erkennung: `securityType == "ETP"` prüfen (`utils/ticker_normalizer.py:1082-1084`).
3. Share-Class-Format `BRK/B` für OpenFIGI (`utils/ticker_normalizer.py:816`).
4. Stammdaten über die Quiver-`BioGuideID` verknüpfen, Party/State nicht mehr überschreiben (`scripts/import_trades.py:612-613`).
5. Kurs-Fenster von `period='5y'` auf das volle Fenster umstellen (`config/settings.py:561`, `scoring/post_trade_returns.py:171`). Die Lückenprüfung `_max_gap` liegt uncommittet im Worktree. Danach `--stage_post_trade_performance` voll laufen lassen. Bis dahin Auswertungen auf Trades ab 2021-07 beschränken.
6. Asset-Gate als harten Ausschluss vor die Signale setzen.
7. accumulative: Lots am selben Tag zusammenfassen. Duplikat-Report gegen die Quiver-XLSX erstellen.

**Löschen bzw. archivieren:**
8. Delta-Pfad und Einzel-Stage-Modus stilllegen, nur `run()` behalten (`scripts/import_trades.py:2549`, `:1993-2525`).
9. Ballast laut Abschnitt 5 archivieren:
   - Committee-Matcher-Infrastruktur, sodass nur Loader und Assets bleiben
   - tote PTR-Scraper und Extraktoren (Markdown-Wissen behalten)
   - Einmal-Skripte
   - Webseiten-Dump
   - Master-Backups
   - erledigte Pläne

**Danach:**
10. Einen Vollauf durchführen und die Datenintegritäts-Kennzahlen festhalten (Anteil Return = 0, Duplikate, Abdeckung je Jahr). Das ist der korrigierte Vergleichsmaßstab.
11. Score und Signale auf den sanierten Daten neu bewerten (Event-Study, `scripts/analyse_event_study.py`).

K3 (Zeitachse) bleibt nach Stufe 1 offen. Bis zu einer Lösung sollten Ausschuss-Signale nur für Trades im 119. Kongress als belastbar gelten.

### 6.6 Entscheidungs-Gate vor Stufe 2

| # | Entscheidung | Optionen |
|---|---|---|
| 1 | Trade-Datenquelle | Quiver-Abo + Scraper-Fix; oder eigene House/Senate-Scraper. Ein kompakter House-Scraper hätte etwa 300 Zeilen, dann gehört er als eigene Phase in Stufe 2. Ein verifizierter kostenloser Ersatz ist nicht bekannt (12). |
| 2 | Historische Ausschuss-Mitgliedschaften | `congress-legislators` (historisch nur teilweise); ältere Clerk-PDFs mit dem vorhandenen Parser; Congress.gov API; oder Analysen nur ab dem 118./119. Kongress |
| 3 | Zweck des Scores | Auffälligkeit markieren (erklärbar, regelbasiert) oder Rendite vorhersagen (datengestützte Kalibrierung); Grundlage ist die Event-Study aus Stufe 1 |
| 4 | Kursquelle | yfinance (nur private Nutzung, Survivorship) oder Tiingo |
| 5 | Vote-Timing | neu bauen (Kammer, eigene Roll-Call-Position, Basisrate) oder streichen |
| 6 | Stufe 2 überhaupt | Ja, wenn Bene das Datenmodell, die Zeitachse und die Filter-Engine braucht. Nein, wenn die sanierte Fassung genügt. |

### 6.7 Phasenplan Stufe 2

Jede Phase endet mit einem Gate und erfüllt die Definition of Done aus 6.4. Verglichen wird mit dem sanierten Altbestand aus Stufe 1, jeweils mit der erwarteten Abweichung.

| Phase | Inhalt | Gate (Abnahme) |
|---|---|---|
| 0 | Repo-Skelett mit Leitplanken (CI-Budget, Linter, `AGENTS.md`), Asset-Verzeichnis aus 4.2, Wissensinventur | Leitplanken-Checks laufen; Inventur vollständig |
| 1 | `ingest` + `raw_trades` mit Versionierung | Zeilenzahl = Quelle; Lots explizit statt identischer Zeilen |
| 2 | `reference`: members über BioGuideID, Ausschüsse, Mitgliedschaften mit Zeitachse (Umfang laut Gate 6.6 Nr. 2) | 100 % der Trades mit Member; 0 Bundesstaat-Konflikte; Khanna-2017-Fall korrekt. Erwartet: Committee-Treffer vor 2023 sinken deutlich. |
| 3 | `securities`: Klassifikator übernehmen, OpenFIGI neu, Ticker-Historie, Namensabgleich, Sektor | Edge-Case-Liste aus 01 grün; ETFs korrekt; FB → Meta. Erwartet: weniger „stock“, mehr „etf“. **Abbruchkriterium prüfen.** |
| 4 | `metrics`: Kurse, Returns, AR, Exit (vektorisiert, NULL statt 0) | Anteil Return = 0 im normalen Rahmen; AR ab 2022 = Stufe-1-Werte |
| 5 | `flags` + `score` als Regel-Engine (Konzept laut Gate 6.6 Nr. 3) | Golden-Set; Trefferquoten je Jahr plausibel. Erwartet: accumulative deutlich niedriger. |
| 6 | Frontend übernehmen, auf die schmale View umstellen, Metriken ins Analysepaket | Tabs funktional gleichwertig; Ladezeit und Speicher gemessen |

## 7. Hinweise zur Aussagekraft

- **Datenstände:** Die Teilberichte rechnen auf zwei Master-Ständen (Tabelle im Kopf). Die Zahlen unterscheiden sich deshalb leicht, z. B. 114.245 vs. 114.347 Zeilen.
- **Cache-Stand:** Der Preis-Cache im Worktree reicht bis 2026-07-15, der im Haupt-Worktree bis 2026-08-10. Dort haben 1.511 Ticker ihren ersten Kurs erst im August 2026. Die Ursache ist ungeklärt, vermutlich Fetches mit kurzem Fenster oder nur Delisting-Marker.
- **Unabhängige Bestätigungen:** Mehrere Agents haben dieselben Befunde unabhängig gefunden:
  - K1: Agents 01, 04, 11
  - K3: Agents 02, 03, 06
  - H1: Agents 01, 08
  - K5: Agents 02, 03, 11
- **Gegenprüfung (13):** Sie hat K1–K6 und H1–H4 im Code bzw. in den Daten nachgeprüft:
  - K3, K4, K6, H1, H3 und H4 sind bestätigt.
  - K1, K2 und H2 sind bestätigt, aber präzisiert.
  - Die Ursachen-These zu K5 ist widerlegt.
- **K1-Zahlen:** Die Teilberichte nennen 44 %, 47.591 Zeilen, 50.459 Zeilen mit Preis = Baseline und „53 %“. Maßgeblich sind 43 % aller Trades bzw. 53 % der Trades mit Rendite (13).
- **K4-Nenner:** Bericht 01 rechnet mit 119.415 Zeilen, der Master hat aber nur 114.245. Die 7,6 % sind daher als Heuristik mit unklarer Basis zu lesen.
- **Rolle der PTR-Scraper:** Der Rechercheagent (12) nennt die eigenen Scraper „Primärquelle“. Das ist als Alternative zu Quiver gemeint. Im Code ist Quiver die einzige aktive Quelle (02, 05). Fällt Quiver weg, wird der House-Scraper zum kritischen Pfad (6.6 Nr. 1).
- **Schätzungen:** Die Aufwandsangaben der Agents sind grobe Orientierung, keine Planung. Beispiele: Ticker-Neubau 3–5 Personentage, Committee-Mapper 300–500 Zeilen, Stufe 1 „wenige Tage“.

---

## 8. Teilberichte

| Nr. | Datei | Bereich | Modell |
|---|---|---|---|
| 01 | [01_ticker_normalisierung.md](01_ticker_normalisierung.md) | Ticker-Klassifikation, OpenFIGI, Issuer | Opus |
| 02 | [02_pipeline_datenmodell.md](02_pipeline_datenmodell.md) | Pipeline, Datenmodell, Dienste, Name-Matching | Opus |
| 03 | [03_filter_signale.md](03_filter_signale.md) | Gates, Tags, Signale, Architekturskizze der Filter | Opus |
| 04 | [04_scoring_returns.md](04_scoring_returns.md) | Score, Renditen, Abnormal Returns, Preis-Cache | Sonnet |
| 05 | [05_ptr_scraper.md](05_ptr_scraper.md) | House/Senate-PTR-Scraper, PDF-Extraktion | Sonnet |
| 06 | [06_abgeordnete_committees.md](06_abgeordnete_committees.md) | Mitgliederlisten, Ausschüsse, Kongressdaten | Sonnet |
| 07 | [07_committee_matcher.md](07_committee_matcher.md) | Committee → Industry-Mapping V2 und Legacy | Sonnet |
| 08 | [08_sektor_assetgate.md](08_sektor_assetgate.md) | yfinance-Sektor, Netzwerk-Robustheit, Asset-Gate | Sonnet |
| 09 | [09_frontend.md](09_frontend.md) | Streamlit-Frontend | Haiku |
| 10 | [10_repo_hygiene_tests.md](10_repo_hygiene_tests.md) | Testsuite, toter Code, Daten-Inventar, Doku, Infra | Haiku |
| 11 | [11_datenqualitaet_master.md](11_datenqualitaet_master.md) | Datenqualität `trades_master_complete.csv` | Sonnet |
| 12 | [12_externe_alternativen.md](12_externe_alternativen.md) | Externe Datenquellen und Alternativen | Haiku |
| 13 | [13_gegenpruefung.md](13_gegenpruefung.md) | Unabhängige Gegenprüfung dieses Berichts | Opus |
