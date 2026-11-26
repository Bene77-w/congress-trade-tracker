# Plan „Sanierung Stufe 2“ (verworfen)

Archiv der GitHub-Issues #1–#20 (Wayfinder-Map und Tickets), Stand 2026-11-25.
Verworfen am 2026-11-25: Bene entscheidet sich für einen Neubau statt der Sanierung im Bestand.
Die Issues sind auf GitHub als „not planned“ geschlossen und verweisen auf diese Datei.

---

## #1 Sanierung Stufe 2

Labels: wayfinder:map

## Destination

Sanierte Codebase auf `agent/sanierung-neuaudit`: Produktivcode ≤ 15000 Zeilen (heute 42089), belastbare Zahlen (Datenvertrag grün), neuer Vollauf mit Kennzahlen und Event-Study als Entscheidungsgrundlage für die nächste Stufe. Merge, Push, `--save` und Deploy nur nach Freigabe durch Bene.

## Notes

- **Ausführung im Map (Abweichung von Wayfinder):** Die Kind-Tickets sind überwiegend Umsetzungstickets. Umgesetzt wird mit `implement-sequenziell` auf dem Integrationsbranch `agent/sanierung-neuaudit`, Review-Anker Tag `sanierung-anker` (`533d0ca`). Reihenfolge über die Blockierkanten; erstes freies Ticket nach Nummer.
- **Grundlagen (im Branch, nicht gepusht):** `docs/sanierung/audit.md` (Befund-IDs in den Tickets), `CONSTRAINTS.md`, `.importlinter`, `tools/check.sh`.
- **Vor jedem Ticket** `CONSTRAINTS.md` lesen; **am Ticketende** `tools/check.sh`; volle Testsuite je Ticket.
- **Modulgrenzen:** Tickets, die Schnittstellen umsetzen, folgen „Zielschnittstellen entwerfen“ (Skill `codebase-design`, `docs/sanierung/schnittstellen.md`).
- **Defekte test-first** (Skill `tdd`); gelöschte Tests mit Commit-Zeile `Tests-entfernt: <pfad> <Grund>`.
- **Review nach Risiko:** mechanische Lösch-Tickets ohne Interim Review; Zahlen-Tickets eine Runde (Generalist + Tests-Fachprüfer, weitere nur nach ≥ 4 behobenen KRITISCH); Modulgrenzen zusätzlich Architektur-Fachprüfer; am Ende `simplify` auf den Gesamtdiff, dann Final Review.
- **Vollaufdaten:** Die Stage im Haupt-Checkout (`data/processed/*_stage.csv`, 2026-11-18) stammt vom Code vor Stufe 1; nur lesen.

## Decisions so far

Interview 2026-11-20 (Begründungen: `docs/sanierung/audit.md` Abschnitt 11):

- **E1** Committee Matcher per Archiv-Tag einfrieren und löschen; die 2 Mapping-CSVs werden Referenzdaten
- **E2** Signal `committee` entfällt, ein Committee-Modul
- **E3** Kongress 119 wie heute, 118 bewusst mit 119er-Mitgliedschaften; nicht abgedeckte Daten lassen die Pipeline laut abbrechen
- **E4** Chair und Ranking Member erben Branchen der Unterausschüsse
- **E5** `CONSTRAINTS.md`: Grundregeln blockieren; Zeilen-Ratchet und Architekturgrenzen warnen bis 2026-12-04
- **O1** Quiver prüfen und reparieren · **O2** erst Daten-Fixes, dann Vollauf · **O3** PTR-Stichprobe, dann `lot_index` · **O4** Suspicious-Export löschen · **O5** Score linear, Editor weg · **O6** trade_volume weg, swarm_conflict + paradox zusammen, ≤ 6 Hypothesen mit Holm · **O7** Tab Score-Analyse weg, gemeinsames Event-Study-Modul · **O8** Vote-Daten nur ab 119, sonst NA · **O9** Optionen ausschließen · **O10** Closed-End-Fonds aus, BDCs Aktie · **O11** Ticker zum Handelsdatum, sonst `unresolved` · **O12** nur kleine Umbenennungstabelle · **O13** Kursabdeckung ≥ 95 % der erreichbaren Obergrenze je Jahr · **O14** nur Dry-Run automatisch · **O15** kuratierter Master bleibt, übrige Nutzungskandidaten weg · **O16** Doku nur bei Schema-, CLI-, Architekturänderung · **O17** `Insider_Sectors` nicht speichern · **O18** Senate-Rohsicherung weg · **O19** Recent-Tab in Trades

## Not yet specified

- Ersatzquelle für Trades, falls das Quiver-Abo nicht mehr besteht (hängt an „Trade-Quelle Quiver reparieren“)
- Behalten oder Streichen von floor_action, accumulative, contrarian nach der Event-Study
- Folgetickets aus der FIFO-Prüfung der Exit-Erkennung
- Ob die Event-Study ein Ergebnis liefert, das eine nächste Stufe (Filter-Neubau, Score-Gewichte) rechtfertigt

## Out of scope

- Referenzdaten für Kongress 120 bauen (E3: später, eigene Arbeit ab 2027-01-03)
- Kongresse 113–117 und echte 118er-Mitgliedschaften (E3)
- LLM-Neuläufe des Committee Matchers (E1)

---

## #2 Committee Matcher einfrieren und löschen

Labels: wayfinder:task

## Auftrag

- Tag `committee-matcher-archiv` auf den Stand vor dem Löschen setzen (lokal).
- `reviewed_assignments.csv` und `reviewed_unit_coverage.csv` aus `data/committee_matcher_v2/reviews/map-fullrun-20261016/` nach `data/reference/mapping/` übernehmen; `build_reference_data.py` (inkl. Z. 152–157) und `data/reference/raw/119/manifest.json` darauf umstellen; Industry-Taxonomie als Validierungsliste behalten.
- Test: Neubau von `committee_industries.csv` ist bytegleich (522/522).
- Löschen: `committee_matcher/`, `scripts/committee_matcher_*.py`, `tests/test_committee_matcher_*.py`, `tests/test_submapper.py`, `organisation/committee_matcher_*.md`, `organisation/committee_jurisdiction_input_template_codex.md`, `data/config/committee_matcher_*`, COMMITTEE_MATCHER_*-Settings, Dev-Artefakte unter `data/committee_matcher_v2/`, openrouter/jsonpath-Requirements, Legacy-Mappings und `former_members.csv` (SCR-10).

## Befunde

CM-1, CM-7, CM-8, CM-9, CM-10, CM-12, CM-16, SCR-10; Entscheidung E1 (`docs/sanierung/audit.md`)

## Abnahme

- [ ] Neubau bytegleich
- [ ] Pipeline-Dry-Run-Pfad importiert nichts Gelöschtes
- [ ] `tools/check.sh` grün; Obergrenzen in `CONSTRAINTS.md` auf den neuen Stand gesenkt
- [ ] Commit mit `Tests-entfernt:`-Zeilen

## Review

Kein Interim Review (mechanisch). Vor dem Abschluss grep nach den gelöschten Namen in Code und `organisation/`.

## Umfang

Geschätzte Netto-Änderung Produktivcode: −21.100 Zeilen. Teil der Spec „Sanierung Stufe 2“ (#1).

---

## #3 Suspicious-Export löschen

Labels: wayfinder:task

## Auftrag

- Suspicious-Export, die 3 Event-Stages, Migration, In-Flight-Score, `utils/signal_event_id.py`, Event-Snapshots und SUSPICIOUS_*-Settings löschen.
- Signale liefern weiter dieselben Master-Spalten (`signal_count`, `signals_triggered`, Score).
- Vor der Umsetzung erwartete Kennzahl-Änderung aufschreiben; danach auf der Vollauf-Stage (`/home/bene/Nextcloud/Projekte/bmb_trdprg/congress_tracker/data/processed/trades_stage.csv`, nur lesen) belegen.

## Befunde

PIPE-4, EVT-1, S1A-1, S1A-11, S1A-12; Entscheidung O4 (`docs/sanierung/audit.md`)

## Abnahme

- [ ] Signalspalten einer Dry-Run-Stichprobe identisch zum Stand vorher
- [ ] `--save` ohne Event-Dateien
- [ ] `tools/check.sh` grün; Obergrenzen in `CONSTRAINTS.md` auf den neuen Stand gesenkt

## Review

Ein Interim Review (Generalist + Tests-Fachprüfer). Weitere Runde nur nach ≥ 4 behobenen KRITISCH.

## Umfang

Geschätzte Netto-Änderung Produktivcode: −900 Zeilen. Teil der Spec „Sanierung Stufe 2“ (#1).

---

## #4 Scraper-, Skript- und Konfig-Ballast löschen

Labels: wayfinder:task
Blockiert durch: #2, #3

## Auftrag

- Roster-Parser: `scrapers/house_pdf_parsing_tool/`, `scrapers/senate_committee_xml/parse_senate_xml.py`, `generate_committee_structure.py`, PDFs, XML, Roster-CSVs (SCR-7).
- Senate-Rohsicherung `--official-snapshot` (O18); `scripts/reset_pipeline.py` (SCR-9); `linux_setup.sh` (SCR-13).
- O15-Löschliste: `delisted_trades.csv` samt Report und Bestätigungs-Prompt (TICK-11), `--manual_review_only`, `data/agent_exports`, Root-`Dockerfile`/`docker-compose.yml` (DASH-13), VPS-E2E-Testgerüst (TEST-13), Spalte `excess_return`. Der kuratierte Master bleibt.
- Wirkungslose Flags `SWARM_TRACK_DIRECTIONS`/`SWARM_FLAG_CONFLICTS`/`SWARM_GROUPING_KEY`, `load_trade_data`, `pipeline_state.json` (CFG-5/7, PIPE-14); Altlasten in `import_trades.py` (S1A-15).

## Befunde

SCR-7, SCR-9, SCR-10, SCR-13, TICK-11, DASH-13, TEST-13, CFG-5, CFG-7, PIPE-14, S1A-15; Entscheidungen O15, O18 (`docs/sanierung/audit.md`)

## Abnahme

- [ ] Deploy-Weg (`deploy/`, `scripts/build_deploy_snapshot.py`) unverändert lauffähig
- [ ] `tools/check.sh` grün; Obergrenzen in `CONSTRAINTS.md` auf den neuen Stand gesenkt

## Review

Kein Interim Review (mechanisch). Vor dem Abschluss grep nach den gelöschten Namen in Code und `organisation/`.

## Umfang

Geschätzte Netto-Änderung Produktivcode: −2.300 Zeilen. Teil der Spec „Sanierung Stufe 2“ (#1).

---

## #5 Dashboard verschlanken

Labels: wayfinder:task

## Auftrag

- Score-Editor, Presets, Signal-Ausschluss und Recompute löschen (O5, DASH-5).
- Tab Score-Analyse löschen (O7, DASH-9); Tab Post Trade Performance löschen (DASH-8).
- Recent-Tab in Trades zusammenlegen, Horizont-Filter nur für Renditewerte (O19, DASH-4); `staging_export` löschen (DASH-6).
- Ungenutzte Glossar-Einträge, Inline-CSS ins Theme (DASH-15/16).

## Befunde

DASH-4, DASH-5, DASH-6, DASH-8, DASH-9, DASH-15, DASH-16; Entscheidungen O5, O7, O19 (`docs/sanierung/audit.md`)

## Abnahme

- [ ] App startet lokal, alle verbleibenden Tabs rendern (Skill `run`)
- [ ] Score-Anzeige unverändert
- [ ] `tools/check.sh` grün; Obergrenzen in `CONSTRAINTS.md` auf den neuen Stand gesenkt

## Review

Kein Interim Review (mechanisch). Vor dem Abschluss grep nach den gelöschten Namen in Code und `organisation/`.

## Umfang

Geschätzte Netto-Änderung Produktivcode: −1.400 Zeilen. Teil der Spec „Sanierung Stufe 2“ (#1).

---

## #6 Zielschnittstellen entwerfen

Labels: wayfinder:grilling
Blockiert durch: #2, #3

## Auftrag

- Mit Skill `codebase-design` die Schnittstellen der Module aus Audit Abschnitt 6 entwerfen: ingest, instruments, members/reference, prices, signals, store, `analysis/event_study`, `app/data.py`, Orchestrator `run()`.
- Je Modul: Signatur, Seam, was dahinter verschwindet, was wegfällt, Testzugang ohne Netz.
- Ergebnis in `docs/sanierung/schnittstellen.md`; offene Designfragen mit Bene klären.
- `.importlinter` um die Zielgrenzen ergänzen (zunächst als gemessene Verträge).

## Befunde

Audit Abschnitt 6 (#1–#10), ARCH-1, PIPE-6, S1A-5, S1A-8 (`docs/sanierung/audit.md`)

## Abnahme

- [ ] Architektur-Fachprüfer hat die Schnittstellen freigegeben
- [ ] Bene hat offene Designfragen entschieden

## Review

Ein Interim Review (Generalist + Tests-Fachprüfer) plus Architektur-Fachprüfer (`agent-review`, `rollen/fachpruefer-architektur.md`).

## Umfang

Geschätzte Netto-Änderung Produktivcode: 0 Zeilen. Teil der Spec „Sanierung Stufe 2“ (#1).

---

## #7 Instrumente auflösen

Labels: wayfinder:task
Blockiert durch: #6

## Auftrag

- Schnittstelle aus „Zielschnittstellen entwerfen“ (`docs/sanierung/schnittstellen.md`) umsetzen; Abweichungen dort nachtragen und begründen.
- Modul `instruments.resolve(ticker, asset_name, tickertype_org, transaction_date)` → `asset_class` (equity/fund/option/bond/other/unresolved), `yf_symbol`, `issuer_key`, `sector`.
- Quellen: Quiver-Typcode → FIGI-Whitelist → Muni-/Fondsname → Namensabgleich und Datumstest → Regex nur für die Symbolform.
- Optionen aus (O9, TICK-2); Closed-End-Fonds aus, BDCs Aktie (O10, TICK-3); Munis (TICK-4); Auflösung zum Handelsdatum, sonst `unresolved` (O11, TICK-1, TICK-13); kleine Umbenennungstabelle (O12); Sektor-Null-Cache mit Recheck (TICK-7).
- Gate liest nur `asset_class`, Paket `gates/` entfällt (GATE-4); `ticker_routing`, Skip-/Bypass-Listen, OpenFIGI-Nebenpfade, eigene Retry-Queues und `utils/issuer_ticker.py` entfallen (TICK-8/9/10/12).
- Test-first je Klasse: Open-End-Fonds, OP-Option, Muni AONC + Negativprobe GCBC, FB-Trade 2015 → `unresolved` (TEST-1/4, TICK-15).
- Vor der Umsetzung erwartete Kennzahl-Änderung aufschreiben; danach auf der Vollauf-Stage (`/home/bene/Nextcloud/Projekte/bmb_trdprg/congress_tracker/data/processed/trades_stage.csv`, nur lesen) belegen.

## Befunde

TICK-1, TICK-2, TICK-3, TICK-4, TICK-7, TICK-8, TICK-9, TICK-10, TICK-12, TICK-13, GATE-1, GATE-2, GATE-3, GATE-4, S1A-2, S1A-9; Entscheidungen O9–O12 (`docs/sanierung/audit.md`)

## Abnahme

- [ ] Erwartete Deltas aus Audit 8.4 belegt: Optionen 0 im Universum, FB/PCLN/ANSS nicht ETF
- [ ] `tools/check.sh` grün; Obergrenzen in `CONSTRAINTS.md` auf den neuen Stand gesenkt

## Review

Ein Interim Review (Generalist + Tests-Fachprüfer). Weitere Runde nur nach ≥ 4 behobenen KRITISCH.

## Umfang

Geschätzte Netto-Änderung Produktivcode: −1.500 Zeilen. Teil der Spec „Sanierung Stufe 2“ (#1).

---

## #8 Ingest: eindeutige Zeilen, Richtung, Datumsherkunft

Labels: wayfinder:task
Blockiert durch: #6

## Auftrag

- Schnittstelle aus „Zielschnittstellen entwerfen“ (`docs/sanierung/schnittstellen.md`) umsetzen; Abweichungen dort nachtragen und begründen.
- ~20 Duplikatgruppen gegen die PTR prüfen (Netz), Ergebnis dokumentieren; danach `lot_index` in die trade_id (O3, PIPE-3).
- Spalte `direction` einführen, ersetzt `_direction_from_transaction_type` an 39 Stellen (PIPE-10).
- `date_fallback`-Flag (SIG-4).
- Vor der Umsetzung erwartete Kennzahl-Änderung aufschreiben; danach auf der Vollauf-Stage (`/home/bene/Nextcloud/Projekte/bmb_trdprg/congress_tracker/data/processed/trades_stage.csv`, nur lesen) belegen.

## Befunde

PIPE-3, PIPE-10, DASH-2, ES-15, TEST-10, SIG-4; Entscheidung O3 (`docs/sanierung/audit.md`)

## Abnahme

- [ ] Keine doppelten trade_ids im Master
- [ ] Test mit doppelten IDs in Signalen und Aggregaten
- [ ] `tools/check.sh` grün; Obergrenzen in `CONSTRAINTS.md` auf den neuen Stand gesenkt

## Review

Ein Interim Review (Generalist + Tests-Fachprüfer). Weitere Runde nur nach ≥ 4 behobenen KRITISCH.

## Umfang

Geschätzte Netto-Änderung Produktivcode: −100 Zeilen. Teil der Spec „Sanierung Stufe 2“ (#1).

---

## #9 Kurse: PriceHistory, Cache-Reset, Exit-Prüfung

Labels: wayfinder:task
Blockiert durch: #6

## Auftrag

- Schnittstelle aus „Zielschnittstellen entwerfen“ (`docs/sanierung/schnittstellen.md`) umsetzen; Abweichungen dort nachtragen und begründen.
- Öffentliche `PriceHistory` für Post-Trade, Paper und AR statt privater Funktionen in 6 Modulen (S1A-8, PIPE-11).
- Rückbefüllungs-Zweig und `price_history_full_fetch.json` löschen; Migration per Cache-Reset vor dem Vollauf (S1A-4).
- Plausibilitätsgate gegen Niveausprünge (ES-12, KLAC/CTVA als Testfälle); NaN-Marker der 18 Share-Classes entfernen.
- Offener Prüfpunkt: Exit-Erkennung und `effective_return_pct` (`_compute_exit_columns`) gegen eine manuelle FIFO-Stichprobe prüfen, Befunde beheben oder als Folgeticket anlegen.
- Kursabdeckung relativ zur Ticker-Obergrenze je Jahr messen (O13).
- Vor der Umsetzung erwartete Kennzahl-Änderung aufschreiben; danach auf der Vollauf-Stage (`/home/bene/Nextcloud/Projekte/bmb_trdprg/congress_tracker/data/processed/trades_stage.csv`, nur lesen) belegen.

## Befunde

S1A-4, S1A-8, PIPE-11, ES-12, TICK-5, ES-1, ES-11; offener Prüfpunkt Audit 11.3; Entscheidung O13 (`docs/sanierung/audit.md`)

## Abnahme

- [ ] Ein Kurs-Lookup für alle Return-Arten
- [ ] FIFO-Stichprobe dokumentiert
- [ ] `tools/check.sh` grün; Obergrenzen in `CONSTRAINTS.md` auf den neuen Stand gesenkt

## Review

Ein Interim Review (Generalist + Tests-Fachprüfer). Weitere Runde nur nach ≥ 4 behobenen KRITISCH.

## Umfang

Geschätzte Netto-Änderung Produktivcode: −300 Zeilen. Teil der Spec „Sanierung Stufe 2“ (#1).

---

## #10 Vote-Daten reparieren

Labels: wayfinder:task
Blockiert durch: #4

## Auftrag

- Listen-Aliasing in `fetch_congress_data.py:1116-1117` beheben, 724 Bills ohne actions neu laden (SCR-2).
- Erst danach: Lock mit Stale-Erkennung in den Fetcher verlegen, verwaiste Lock-Datei entfernen (SCR-4).
- Außerhalb der Abdeckung NA statt False für `Vote_Timing_Hit` (SCR-3, PIPE-1, O8: nur ab Kongress 119).
- Ungenutzte Endpunkte committees/summaries löschen (SCR-5).
- Fetcher-Test mit 51 Bills und Stale-Lock (SCR-15).
- Vor der Umsetzung erwartete Kennzahl-Änderung aufschreiben; danach auf der Vollauf-Stage (`/home/bene/Nextcloud/Projekte/bmb_trdprg/congress_tracker/data/processed/trades_stage.csv`, nur lesen) belegen.

## Befunde

SCR-2, SCR-3, SCR-4, SCR-5, SCR-15, PIPE-1; Entscheidung O8 (`docs/sanierung/audit.md`)

## Abnahme

- [ ] 0 Bills ohne actions
- [ ] Vote-Quoten nur über abgedeckte Trades
- [ ] `tools/check.sh` grün; Obergrenzen in `CONSTRAINTS.md` auf den neuen Stand gesenkt

## Review

Ein Interim Review (Generalist + Tests-Fachprüfer). Weitere Runde nur nach ≥ 4 behobenen KRITISCH.

## Umfang

Geschätzte Netto-Änderung Produktivcode: −400 Zeilen. Teil der Spec „Sanierung Stufe 2“ (#1).

---

## #11 Ausschüsse und Referenzdaten

Labels: wayfinder:task
Blockiert durch: #2, #6

## Auftrag

- Schnittstelle aus „Zielschnittstellen entwerfen“ (`docs/sanierung/schnittstellen.md`) umsetzen; Abweichungen dort nachtragen und begründen.
- Ein Committee-Modul statt drei, Signal `committee` entfällt, `committee_ex_officio` stützt sich auf committee_full, Match-Modi und MATCH_MODE weg (E2, CM-6, CFG-2).
- Chair und Ranking Member erben die Branchen aller Unterausschüsse eines Elternausschusses ohne eigene Branchen (E4, CM-5).
- Pipeline bricht laut ab bei Trades außerhalb der abgedeckten Kongresse; `build_reference_data` prüft vor dem Schreiben (E3, CM-2). 118 bleibt mit 119er-Mitgliedschaften, in der Doku vermerkt (CM-3).
- Sitze nach Handelsdatum filtern (CM-15); Sitz-IDs statt Namens-Roundtrip (CM-11).
- App liest Branchen aus dem Master statt aus einer Referenzkopie (S1A-5); `Insider_Sectors` nicht im Master speichern (O17, PIPE-5).
- Vor der Umsetzung erwartete Kennzahl-Änderung aufschreiben; danach auf der Vollauf-Stage (`/home/bene/Nextcloud/Projekte/bmb_trdprg/congress_tracker/data/processed/trades_stage.csv`, nur lesen) belegen.

## Befunde

CM-2, CM-3, CM-5, CM-6, CM-11, CM-15, CFG-2, S1A-5, PIPE-5; Entscheidungen E2, E3, E4, O17 (`docs/sanierung/audit.md`)

## Abnahme

- [ ] Keine doppelten Committee-Events
- [ ] Kongress-Gate getestet
- [ ] `tools/check.sh` grün; Obergrenzen in `CONSTRAINTS.md` auf den neuen Stand gesenkt

## Review

Ein Interim Review (Generalist + Tests-Fachprüfer). Weitere Runde nur nach ≥ 4 behobenen KRITISCH.

## Umfang

Geschätzte Netto-Änderung Produktivcode: −350 Zeilen. Teil der Spec „Sanierung Stufe 2“ (#1).

---

## #12 Signale und Score

Labels: wayfinder:task
Blockiert durch: #3, #7, #8, #11

## Auftrag

- Schnittstelle aus „Zielschnittstellen entwerfen“ (`docs/sanierung/schnittstellen.md`) umsetzen; Abweichungen dort nachtragen und begründen.
- `signals.evaluate()` vektorisiert, Bool-Spalten bzw. NA bei fehlender Abdeckung, Gate als Maske (ARCH-1, PIPE-9).
- trade_volume löschen; swarm_conflict und swarm_paradox zusammenlegen; extreme_report in late/fast trennen (O6, SIG-4/5/7).
- Score als lineare Summe (O5, SCORE-1); Swarm-Cluster auf das qualifizierende Fenster beschränken (SIG-1); floor_action mit Kammerfilter bis zum Hypothesentest (SIG-6).
- Vertrag `signale-unabhaengig` in `.importlinter` erfüllt.
- Vor der Umsetzung erwartete Kennzahl-Änderung aufschreiben; danach auf der Vollauf-Stage (`/home/bene/Nextcloud/Projekte/bmb_trdprg/congress_tracker/data/processed/trades_stage.csv`, nur lesen) belegen.

## Befunde

ARCH-1, PIPE-9, SIG-1, SIG-3, SIG-4, SIG-5, SIG-6, SIG-7, SCORE-1, CFG-2; Entscheidungen O5, O6 (`docs/sanierung/audit.md`)

## Abnahme

- [ ] Ausgeschlossene Zeilen mit Signal = 0
- [ ] `lint-imports --contract signale-unabhaengig` grün
- [ ] `tools/check.sh` grün; Obergrenzen in `CONSTRAINTS.md` auf den neuen Stand gesenkt

## Review

Ein Interim Review (Generalist + Tests-Fachprüfer) plus Architektur-Fachprüfer (`agent-review`, `rollen/fachpruefer-architektur.md`).

## Umfang

Geschätzte Netto-Änderung Produktivcode: −900 Zeilen. Teil der Spec „Sanierung Stufe 2“ (#1).

---

## #13 Speichern und Datenvertrag

Labels: wayfinder:task
Blockiert durch: #6

## Auftrag

- Schnittstelle aus „Zielschnittstellen entwerfen“ (`docs/sanierung/schnittstellen.md`) umsetzen; Abweichungen dort nachtragen und begründen.
- `MASTER_SCHEMA` als einzige Spaltenliste, ungelesene Spalten weg (PIPE-7, TICK-14).
- Ein `atomic_write` für alle Schreibstellen einschließlich Master (S1A-6).
- `scripts/report_integrity.py` wird `check(df)` vor `--save`: Frische der Quelldaten (SCR-1), Asset-Klasse, Abdeckung je Jahr, Duplikate, Plausibilität der Returns (TEST-5, SIG-T1).
- Code-Revision im Manifest (PIPE-2); Direktmodus ohne Flag löschen, Timer nur Dry-Run (O14, SCR-14).

## Befunde

PIPE-2, PIPE-7, S1A-6, TEST-5, SCR-14, TICK-14; Entscheidung O14 (`docs/sanierung/audit.md`)

## Abnahme

- [ ] `--save` verweigert bei verletztem Datenvertrag (Test)
- [ ] `tools/check.sh` grün; Obergrenzen in `CONSTRAINTS.md` auf den neuen Stand gesenkt

## Review

Ein Interim Review (Generalist + Tests-Fachprüfer). Weitere Runde nur nach ≥ 4 behobenen KRITISCH.

## Umfang

Geschätzte Netto-Änderung Produktivcode: −200 Zeilen. Teil der Spec „Sanierung Stufe 2“ (#1).

---

## #14 Pipeline-Orchestrator und Golden-Path-Test

Labels: wayfinder:task
Blockiert durch: #7, #8, #9, #10, #11, #12, #13

## Auftrag

- Schnittstelle aus „Zielschnittstellen entwerfen“ (`docs/sanierung/schnittstellen.md`) umsetzen; Abweichungen dort nachtragen und begründen.
- `scripts/import_trades.py` auf `run()` mit ~80 Zeilen über die Module reduzieren.
- Je Stage ein Config-Objekt statt Modul-Globals und `getattr`-Defaults; Konstanten zu ihrem einzigen Konsumenten (CFG-1/3/4/6, PIPE-6/12).
- Golden-Path-Test `--dry_run` offline mit aufgezeichneten OpenFIGI-Antworten und synthetischen Kursen, Prüfung gegen handgerechnete Werte (TEST-2).
- Vertrag `netz-nur-in-adaptern` erfüllt.

## Befunde

PIPE-6, PIPE-12, CFG-1, CFG-3, CFG-4, CFG-6, TEST-2 (`docs/sanierung/audit.md`)

## Abnahme

- [ ] Golden-Path-Test grün
- [ ] `lint-imports` alle Verträge grün
- [ ] `tools/check.sh` grün; Obergrenzen in `CONSTRAINTS.md` auf den neuen Stand gesenkt

## Review

Ein Interim Review (Generalist + Tests-Fachprüfer) plus Architektur-Fachprüfer (`agent-review`, `rollen/fachpruefer-architektur.md`).

## Umfang

Geschätzte Netto-Änderung Produktivcode: −1.000 Zeilen. Teil der Spec „Sanierung Stufe 2“ (#1).

---

## #15 Modul analysis/event_study

Labels: wayfinder:task
Blockiert durch: #6, #12

## Auftrag

- Schnittstelle aus „Zielschnittstellen entwerfen“ (`docs/sanierung/schnittstellen.md`) umsetzen; Abweichungen dort nachtragen und begründen.
- Population, `signal_eligible_mask`, Schichtung nach Richtung, zweifaches Clustering (Mitglied × Ticker-Monat), Holm-Korrektur (ES-4/5/9/13, SCR-8, TICK-6).
- `scripts/analyse_event_study.py` nutzt das Modul, ohne feste Befundtexte, mit `--master/--out` (ES-8); Gewichtsexploration (O5); Ausnahme W2 entfällt.
- Tests auf Fixture-Stage: Kauf, Verkauf, fehlender Benchmark, Einheit als Bruchteil (TEST-3).
- Vor der Umsetzung erwartete Kennzahl-Änderung aufschreiben; danach auf der Vollauf-Stage (`/home/bene/Nextcloud/Projekte/bmb_trdprg/congress_tracker/data/processed/trades_stage.csv`, nur lesen) belegen.

## Befunde

ES-4, ES-5, ES-6, ES-8, ES-9, ES-13, SCR-8, TICK-6, TEST-3; Entscheidungen O5, O7 (`docs/sanierung/audit.md`)

## Abnahme

- [ ] Ausnahme W2 aus `CONSTRAINTS.md` und `.importlinter` entfernt
- [ ] `tools/check.sh` grün; Obergrenzen in `CONSTRAINTS.md` auf den neuen Stand gesenkt

## Review

Ein Interim Review (Generalist + Tests-Fachprüfer). Weitere Runde nur nach ≥ 4 behobenen KRITISCH.

## Umfang

Geschätzte Netto-Änderung Produktivcode: −300 Zeilen. Teil der Spec „Sanierung Stufe 2“ (#1).

---

## #16 Read-Model der App

Labels: wayfinder:task
Blockiert durch: #5, #11, #13

## Auftrag

- Schnittstelle aus „Zielschnittstellen entwerfen“ (`docs/sanierung/schnittstellen.md`) umsetzen; Abweichungen dort nachtragen und begründen.
- `app/data.py` als einzige Seam zur Pipeline: Parquet mit Spaltenliste, `st.cache_resource`, öffentliche Kursreihe (DASH-3/10/12).
- Mitglieder nach `bio_guide_id` gruppieren (DASH-1); Asset-Name mit Rückgriff auf `asset_name` (DASH-7).
- Ausnahme W1 entfällt.

## Befunde

DASH-1, DASH-3, DASH-7, DASH-10, DASH-12 (`docs/sanierung/audit.md`)

## Abnahme

- [ ] Ausnahme W1 aus `CONSTRAINTS.md` und `.importlinter` entfernt
- [ ] App läuft lokal (Skill `run`)
- [ ] `tools/check.sh` grün; Obergrenzen in `CONSTRAINTS.md` auf den neuen Stand gesenkt

## Review

Ein Interim Review (Generalist + Tests-Fachprüfer). Weitere Runde nur nach ≥ 4 behobenen KRITISCH.

## Umfang

Geschätzte Netto-Änderung Produktivcode: −500 Zeilen. Teil der Spec „Sanierung Stufe 2“ (#1).

---

## #17 Trade-Quelle Quiver reparieren

Labels: wayfinder:task

## Auftrag

- **HITL:** Bene prüft, ob das Quiver-Abo mit Export-Funktion aktiv ist, und meldet das Ergebnis im Ticket.
- Abo aktiv: Export-Selektor im Scraper reparieren, Probelauf mit neuen Trades nach 2026-07-21.
- Abo inaktiv: Ergebnis im Ticket festhalten; die Wahl der Ersatzquelle ist ein neues Entscheidungsticket.

## Befunde

SCR-1; Entscheidung O1 (`docs/sanierung/audit.md`)

## Abnahme

- [ ] Probelauf liefert Trades jünger als 60 Tage, oder dokumentierte Entscheidung zur Quelle

## Review

Ein Interim Review (Generalist + Tests-Fachprüfer). Weitere Runde nur nach ≥ 4 behobenen KRITISCH.

## Umfang

Geschätzte Netto-Änderung Produktivcode: ±0 Zeilen. Teil der Spec „Sanierung Stufe 2“ (#1).

---

## #18 Hypothesen der Event-Study festlegen

Labels: wayfinder:grilling
Blockiert durch: #15

## Auftrag

- Höchstens 6 Hypothesen mit Richtung, Horizont (A90/A120), Population und Sensitivitäten (Audit 8.4) mit Bene festlegen.
- Ergebnis in `docs/sanierung/hypothesen.md`; Freigabe durch Bene vor dem Vollauf.

## Befunde

Audit 8.3, 8.4; Entscheidungen O6, O8 (`docs/sanierung/audit.md`)

## Abnahme

- [ ] Bene hat die Hypothesenliste freigegeben

## Review

Kein Review (Entscheidung mit Bene).

## Umfang

Geschätzte Netto-Änderung Produktivcode: 0 Zeilen. Teil der Spec „Sanierung Stufe 2“ (#1).

---

## #19 Doku-Schnitt

Labels: wayfinder:task
Blockiert durch: #14, #16

## Auftrag

- Zielsatz aus Audit 10.2: AGENTS.md ≤ 60 Zeilen, ARCHITEKTUR.md (ersetzt Pipelines.md), DATA_STRUCTURE.md aus dem Schema, CLI.md nur Produktivskripte, `deploy/README_deployment.md`, 3 Fachdokus.
- Pläne, Reviews und alte CHANGELOG-Einträge ins Archiv; Doku-Regel O16 in AGENTS.md und `organisation/DOKU_INDEX.md`.

## Befunde

DOKU-1 bis DOKU-9; Entscheidung O16 (`docs/sanierung/audit.md`)

## Abnahme

- [ ] grep nach gelöschten Namen in `organisation/` und AGENTS.md leer
- [ ] `tools/check.sh` grün; Obergrenzen in `CONSTRAINTS.md` auf den neuen Stand gesenkt

## Review

Kein Interim Review (mechanisch). Vor dem Abschluss grep nach den gelöschten Namen in Code und `organisation/`.

## Umfang

Geschätzte Netto-Änderung Produktivcode: Doku −11.000 Zeilen. Teil der Spec „Sanierung Stufe 2“ (#1).

---

## #20 Vollauf, Kennzahlen, Event-Study und Abschluss

Labels: wayfinder:task
Blockiert durch: #14, #15, #17, #18, #19

## Auftrag

- Kurs-Cache leeren, Dry-Run mit saniertem Code, `check(df)`, Kennzahlen je Jahr als CSV/JSON nach Messplan (Audit 8.4).
- Event-Study mit den freigegebenen Hypothesen, Sensitivitäten laut Audit 8.4.
- Skill `simplify` auf den Gesamtdiff `sanierung-anker..HEAD`, danach Final Review (`agent-review`) mit Architektur-Fachprüfer.
- `--save`, Deploy, Push und Merge nur nach ausdrücklicher Freigabe durch Bene.

## Befunde

Audit 8.4; Kernziel 3 (`docs/sanierung/audit.md`)

## Abnahme

- [ ] Kennzahlen und Event-Study-Bericht liegen vor
- [ ] Final Review abgenommen
- [ ] Ziel ≤ 15000 Zeilen Produktivcode erreicht oder Abweichung begründet

## Review

Final Review mit Architektur-Fachprüfer nach `simplify`.

## Umfang

Geschätzte Netto-Änderung Produktivcode: ±0 Zeilen. Teil der Spec „Sanierung Stufe 2“ (#1).
