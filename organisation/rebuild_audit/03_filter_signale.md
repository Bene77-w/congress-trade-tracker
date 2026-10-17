# Rebuild-Audit 03 — Filterstruktur, Tags und Signale

Stand 2026-10-17 · Basis: Worktree `agent/committe_matcher`, Master `data/processed/trades_master_complete.csv` (Stand 2026-07-31, 114.245 Zeilen, 101.916 eindeutige `trade_id`). Hilfsskripte: `/tmp/audit_filter/a1–a6.py`. Wichtig: Der Master stammt noch aus der Zeit vor der Bindung des V2-Mappings (`map-fullrun-20261016`). Die Committee-Trefferquoten unten spiegeln deshalb das Legacy-Mapping wider.

## 0. Kurzantwort: Welche Filterstufen gibt es wirklich?

Tatsächliche Ausführungsreihenfolge (Stage `gates_tags`, `scripts/import_trades.py:1140-1166`, danach eigene Stages):

| # | Stufe (Code) | User-Name | Typ | wirkt auf |
|---|---|---|---|---|
| 1 | CommitteeTag | „Filter 3" | Tag | schreibt `Insider_Sectors`, `Relevant_Sectors`, `ex_officio_role_keys` |
| 2 | AssetClassGate | „Filter 0" | Gate (nur Flag) | schreibt `asset_class_excluded` |
| 3 | VoteTimingTag | „Filter 4" | Tag | schreibt `Vote_Timing_*`, **hängt von Stufe 1 ab** |
| 4 | Branch-1-Signale | committee, committee_full, committee_ex_officio, floor_action (= Folge von Filter 4), extreme_report, trade_volume („Filter 1") | Per-Trade | `signals_triggered`, `signal_count` |
| 5 | Sequenz-Signale | contrarian („Filter 5"), accumulative | Member × `issuer_ticker` | additiv |
| 6 | Schwarm-Signale | swarm_buy/sell („Filter 2"), swarm_conflict, swarm_paradox | `issuer_group_key` | additiv |
| 7 | Scoring-Hard-Gate | `SCORING_PRIMARY_SIGNALS = (committee_full, swarm_buy)` | faktisch das eigentliche Filter | `trade_score` |

Die Nummerierung „Filter 0/1/2/3/4/5" ist historisch. „Filter 6/7" wurden verworfen, und „Filter 0" läuft **nach** „Filter 3". Eine „Filter 4A/4B"-Unterteilung gibt es im Code nicht. Das eigentlich wirksame Filter ist der Scoring-Hard-Gate (Stufe 7), der nirgends als Filter benannt ist.

## 1. Features

| Filter/Signal | Zweck | Logik (kurz) | Parameter (`config/settings.py`) | Code |
|---|---|---|---|---|
| AssetClassGate („Filter 0") | Nicht-Aktien ausschließen | `asset_class_excluded = ticker_type ∉ KEEP_TYPES`, setzt nur ein Flag | `ASSET_CLASS_KEEP_TYPES` {stock, foreign_stock, shareclass} (Z.29) | `gates/asset_class_gate.py:36-60` |
| CommitteeTag („Filter 3") | Ausschuss passt thematisch zum Asset | parst `Member_Committees_Full`, sammelt Sektor-/Industry-Einträge je (Sub-)Committee (optional Parent-Core additiv), dedupliziert und gleicht sie mit `sector_yf`/`industry_yf` ab; `match_type` full/sector | `RELEVANT_SECTORS_MATCH_MODE='sector_and_industry'` (Z.50), `COMMITTEE_PARENT_CORE_ADDITIVE` (Z.451), `COMMITTEE_MAPPING_REVIEW_ID` (Z.442) | `tags/committee_tag.py:105-240` |
| Rollen-Keys | Führungsrolle bei relevantem Treffer | Match-Pass über die nicht deduplizierte Liste, dann Rollen-Mapping | `COMMITTEE_ROLE_KEYS`/`SUBCOMMITTEE_ROLE_KEYS` (Code-Konstanten) | `tags/committee_tag.py:13-60, 226-232` |
| VoteTimingTag („Filter 4") | Trade nahe einer thematisch passenden Abstimmung | Roll-Calls aus `bill_actions.json`, Bill-Sektoren aus Subject-Mapping; Treffer, wenn ein Vote in [trade−5d, trade+30d] liegt und ein Sektor mit den Sektoren aus `Relevant_Sectors` übereinstimmt | `VOTE_TIMING_WINDOW_BEFORE=30`, `_AFTER=5`, `VOTE_MIN_SUBJECT_CONFIDENCE='Niedrig'`, `VOTE_DATA_TTL_HOURS` (Z.59-71) | `tags/vote_timing_tag.py:298-403` |
| committee | Hint | `len(Relevant_Sectors) > 0` | Match-Mode | `signals/committee_signal.py` |
| committee_full | **Primärsignal** | ein Eintrag mit `match_type=='full'`; im Strict-Modus identisch zu committee | Match-Mode | `signals/committee_full_signal.py` |
| committee_ex_officio | Hint | committee aktiv und `ex_officio_role_keys` nicht leer | Rollen-Boni (Z.215) | `signals/committee_ex_officio_signal.py` |
| floor_action | Hint | `Vote_Timing_Hit_Count >= 1` | `VOTE_TIMING_HIT_THRESHOLD=1` (Z.95) | `signals/floor_action_signal.py` |
| extreme_report | Hint | Meldeverzug > 60 d oder 0–5 d | `EXTREME_REPORT_DAYS=60`, `_FAST_DAYS=5` (Z.122f) | `signals/extreme_report_signal.py` |
| trade_volume („Filter 1") | Hint | `trade_amount_max_usd > 15.000`, Tier 1–6 | `SCORING_TRADE_VOLUME_*` (Z.256-269) | `signals/trade_volume_signal.py` |
| contrarian („Filter 5") | Richtungswechsel | zwei aufeinanderfolgende Trades desselben Members und `issuer_ticker` mit entgegengesetzter Richtung im Abstand ≤ 30 d | `CONTRARIAN_WINDOW_DAYS=30` | `signals/contrarian_signal.py` |
| accumulative | Positionsaufbau/-abbau | ≥ 2 Trades derselben Richtung im 30-d-Gleitfenster | `ACCUMULATIVE_WINDOW_DAYS=30`, `_MIN_COUNT=2` | `signals/accumulative_signal.py` |
| swarm_buy / swarm_sell („Filter 2") | mehrere Politiker im selben Issuer | Lauf aus Trades mit Lücke ≤ 7 d; qualifiziert, wenn ein 7-d-Teilfenster ≥ 2 distinkte Members enthält; danach wird **der ganze Lauf** geflaggt | `SWARM_WINDOW_DAYS=7`, `SWARM_MIN_MEMBERS=2` (Z.134f) | `signals/swarm_buy_signal.py:find_swarm_clusters` |
| swarm_conflict | Gegenindiz | genau 1 Gegen-Trade in [erster−3d, letzter+3d] | `SWARM_CONFLICT_WINDOW_DAYS_*=3` | `signals/swarm_conflict_signal.py` |
| swarm_paradox | starkes Gegenindiz | ≥ 2 Gegen-Trades; Event gilt für Cluster- **und** Gegen-Trades | `SWARM_PARADOX_MIN_CONFLICTS=2` | `signals/swarm_paradox_signal.py` |
| signal_event_id | stabile Event-ID | SHA256(triggering_date, signal_type, scope_key)[:16] | `TRADE_ID_HASH_LENGTH` | `utils/signal_event_id.py` |
| apply_prefilter (UI) | Sichtfilter, kein Signalfilter | Zeitfenster, Suche, Horizont-Filter (`first_close_offset_days ≤ H`) | Sidebar | `app/streamlit_app.py:993-1027` |

## 2. Funktionsprüfung

**Tests:** 129 von 129 grün (`test_committee_tag`, `test_vote_timing_tag`, `test_swarm_signals`, `test_sequence_signals`, `test_trade_volume_signal`, `test_branch1_signals`, `test_signal_event_id`; 3,2 s). Die Tests sind Unit-Tests auf synthetischen Fixtures und prüfen weder die Stage-Reihenfolge noch das Gate in nachgelagerten Stufen oder die Datenabdeckung.

**Trefferquoten im Master (Anteil an 114.245 Zeilen):** accumulative 52,8 % · trade_volume 25,9 % · contrarian 16,8 % · committee = committee_full 16,7 % (identisch) · extreme_report 10,3 % · swarm_buy 10,1 % · swarm_sell 9,9 % · ex_officio 8,6 % · swarm_paradox 5,6 % · floor_action 3,7 % · swarm_conflict 1,1 %. `trade_score > 0` bei 28.011 Trades, die alle ein Primärsignal tragen.

| Befund | Status | Beleg |
|---|---|---|
| **Gate wird nachgelagert fast nicht beachtet.** Nur VoteTimingTag prüft `asset_class_excluded`. Committee läuft vor dem Gate; Branch-1-, Sequenz-, Schwarm-Signale und Scoring prüfen es nicht. | belegt | `grep asset_class_excluded`: nur `vote_timing_tag.py:349`, `app/score_correlation.py:167`. Auf 1.139 ausgeschlossenen Trades: 730 accumulative, 597 trade_volume, 149 contrarian, 10 swarm_buy, 7 committee_full; **17 mit Score > 0**. `compute_trade_score` (`scoring/trade_score.py:129`) hat keinen Asset-Parameter. Die Doku nennt das Gate einen „binären Hard-Ausschluss vor dem Scoring" (`Filter_Instanz_Parameter.md:11`), das stimmt nicht. |
| **Committee-Zuordnung ist anachronistisch.** Ein aktueller Ausschuss-Snapshot (ohne Congress/Datum, Join über den Namen) wird auf Trades ab 2012 angewendet. | belegt | `house_member&(sub)committees.csv`: Spalten Name, Party, State, Committees, ExOfficio. Committees vorhanden: 2013 64 %, 2018 77 %, 2025 97 %. Committee-Trefferrate: 2013 6,5 % → 2025 25,1 %. Beispiel: Ro Khanna, PRGO 2018 und BAC 2017 per „Select Committee on the CCP" geflaggt, das erst 2023 gegründet wurde. |
| **Floor-Action deckt nur 2025-01 bis 2026-04 ab.** | belegt | 340 Roll-Calls, 2025-01-09 bis 2026-04-27 (`congress_tracker/data/congress_api`, Stand 2026-05-02). floor_action-Rate 2012–2023: 0,0 %, 2024: 0,3 %, 2025: 24,8 %. 1.800 Trades nach dem letzten Vote sind strukturell blind. |
| **Floor-Action trennt nicht.** Im abgedeckten Zeitraum tragen 4.133 von 4.166 committee-Treffern (99,2 %) auch floor_action, im Median mit 10 Votes pro Trade. | belegt | Ein Vote hat im Mittel 3,9 Sektoren, 30 % der Votes haben ≥ 5 Sektoren; Fenster 35 d. |
| floor_action ist eine echte Teilmenge von committee: Sektoren kommen aus `Relevant_Sectors`. | belegt | 0 Vote-Hits ohne committee (`vote_timing_tag.py:357`). |
| Vote-Timing prüft die Kammer nicht: Ein House-Member trifft auch auf Senats-Votes. | belegt | Code ohne Chamber-Vergleich. 242 von 3.793 Hit-Trades haben nur Votes der anderen Kammer. |
| Fehlende Vote-Daten fallen still auf „kein Treffer" zurück. | belegt | In diesem Worktree fehlt `data/congress_api/` ganz; `load_votes` gibt nur eine Warnung aus und setzt alle Werte auf False/0 (`import_trades.py:1012-1023`). |
| **accumulative ist durch Duplikate aufgebläht.** 33.084 von 60.278 accumulative-Trades haben einen Zwilling am selben Tag (gleicher Member, gleiches Asset, gleiche Richtung). | belegt | 12.329 Zeilen sind in den Kernfeldern identisch; `DEDUP_STRATEGY='keep_all'`. Mehrere Lots am selben Tag zählen als „wiederholter Aufbau". |
| Swarm-Verkettung (ganzer Lauf geflaggt) | geprüft, gering | 3.479 Buy-Cluster, Span median 5 d, max 63 d; 96,4 % der Cluster-Trades haben tatsächlich einen anderen Member innerhalb ±7 d, 3,6 % sind Mitläufer. |
| Recall Kernfälle Committee (Legacy-Mapping) | gut | Rüstungswerte × Armed Services: 214/214. Banken × FinServ/Banking: 81/83; die Fehltreffer betreffen Mitglieder nur in Unterausschüssen ohne Parent-Core (Garbarino, Steil). |
| Präzision Committee | fraglich | Breite Mappings, z. B. China-Select-Committee → „Internet Retail" (AMZN), Judiciary/IP → Semiconductors. |
| Sektor/Committee fehlen → stilles Falsch-negativ | belegt | 22.496 nicht ausgeschlossene Trades ohne `sector_yf`, 21.916 ohne Committees; beide werden still zu „kein Signal" ohne Markierung „nicht bewertbar". |
| extreme_report | plausibel | Verzug median 28 d; 8.455 > 60 d, 3.279 ≤ 5 d. Die Schnellmeldung als Verdachtsindiz ist fachlich fragwürdig. |
| Delta- und Full-Run sind nicht äquivalent | belegt (Code) | Im Delta-Modus bekommen nur neue Trades Events; ein historischer Trade, der durch einen neuen Trade Teil eines Schwarms wird, behält seine alten `signals_triggered` (`import_trades.py:1305-1310, 1448-1453`). Die Scope-Keys enthalten `window_start`, ein neuer früher Trade verschiebt die ID, im append-only `suspicious_events.csv` entstehen Dubletten. |
| `trade_id` ist nicht eindeutig (22.769 Zeilen teilen IDs) | belegt | Per-Trade-Events kollidieren in `signal_event_id`; `tid_to_indices` schreibt Signale auf alle Zwillinge. |

## 3. Code-Qualität und Ursachen der Unzuverlässigkeit

1. **Reihenfolgeabhängigkeit ohne Vertrag:** Vote-Timing liest `Relevant_Sectors` (Committee) und `asset_class_excluded` (Gate). Die Signale lesen Spalten aus vorigen Stages, Sequenz und Schwarm addieren sich auf `signals_triggered`. Die Reihenfolge steht nur als Kommentar im Code, und der Kommentar ist falsch (`import_trades.py:1143-1147` nennt ein nicht existentes „Match-Scoring", ein TODO „Signals anbinden" ist veraltet, der Docstring spricht von „vier Signalen, 0–4", es sind sechs).
2. **Gate als Flag statt Ausschluss:** Jeder Konsument muss selbst daran denken, fast keiner tut es (siehe §2).
3. **Stille Defaults:** 23 Aufrufe von `getattr(settings, X, default)` in `signals/`, `tags/`, `gates/`. Fehlt eine Einstellung oder ist sie vertippt, greift ein Code-Default. Dabei ist der Default in `committee_tag.py:84` (`'sector_or_industry'`) ein anderer als der dokumentierte (`'sector_and_industry'`).
4. **Tote Konfiguration:** `SWARM_TRACK_DIRECTIONS` und `SWARM_FLAG_CONFLICTS` werden nur in einem Test gelesen; im Code haben sie keine Wirkung.
5. **Fehlende Daten erzeugen still ein Falsch-negativ:** Es fehlen Vote-Daten, Sektor, Committee oder Kammer, und es gibt keinen Zustand „nicht bewertbar". Ein „kein Signal" ist deshalb nicht von „nicht prüfbar" zu unterscheiden.
6. **Zeitliche Gültigkeit fehlt:** Ausschuss-Snapshot statt Mitgliedschaft zum Handelsdatum, Vote-Abdeckung nur ab dem 119. Kongress. Für 2012–2024 messen Signale die Datenverfügbarkeit, nicht Verhalten.
7. **Inkonsistente Gruppierungsschlüssel:** Sequenz gruppiert nach `issuer_ticker`, Schwarm nach `issuer_group_key`; Richtung per Substring (`'purchase'`/`'sale'`), „Exchange" fällt still heraus.
8. **Duplikation:** Jedes Signal kopiert einen etwa 25-feldrigen Snapshot-Dict-Block (≈ 60 % des Signalcodes). Helfer liegen privat in `committee_signal.py` und werden quer importiert.
9. **Laufzeit-Overrides:** Das UI berechnet Scores mit Gewichts-/Parameter-Overrides und Signal-Exclusions neu (`streamlit_app.py:1031-1053`, `app/scoring_presets.py`). Die Signale selbst (Fenster, Schwellen) sind im UI aber nicht neu berechenbar, sondern im Master eingefroren. Das UI suggeriert also mehr Steuerbarkeit, als es gibt.
10. **Performance/Struktur:** `iterrows` über 114k Zeilen in Committee, Vote-Timing (verschachtelt über Votes) und Branch-1; `import_trades.py` hat 3.277 Zeilen und mischt Orchestrierung, IO und Logik.
11. **Doku–Code-Abweichungen:** Gate als „Hard-Ausschluss" (falsch), Reihenfolge 0→3→4 (falsch, real 3→0→4), Paradox-Event-Umfang im Docstring („nur Gegen-Trades") gegenüber Code und `signal_info.md:79` (Cluster- und Gegen-Trades).

## 4. Wiederverwendbares Wissen

- **Signal-Ideen mit fachlichem Wert:** Ausschuss–Sektor-Match mit Führungsrolle; Schwarm (≥ 2 Members, 7 d, Issuer-Ebene über CIK, Cross-Class GOOG/GOOGL); Schwarm-Konflikt/-Paradox als Gegenindiz; Meldeverzug > 45/60 d; Volumen-Tiers auf der Range-Obergrenze (≤ 15k = 73 % aller Trades, kein Signal); contrarian/accumulative.
- **Match-Semantik:** Industry impliziert Sector, `industry-only` ist ein Datenfehler; der strikte Modus (sector+industry) macht `committee` und `committee_full` deckungsgleich. Den Unterschied gibt es nur im Modus `sector_or_industry`.
- **Rollen:** Ex-Officio-Bonus vor der Deduplizierung ableiten (sonst verdrängt ein rollenloser Eintrag die Rolle); Committee- und Subcommittee-Ebene trennen; Cochair zählt als Chair.
- **Parent-Core additiv:** Die Zuständigkeit des Ausschusses gilt auch für Mitglieder in Unterausschüssen; ohne diese Regel fehlen Treffer (siehe Garbarino/Steil).
- **Vote-Timing-Erkenntnis:** Bei sektorgrobem Bill-Mapping und 35-d-Fenster ist fast jeder Committee-Trade ein Treffer. Brauchbar wird das Signal erst mit Kammerprüfung, eigener Teilnahme an der Abstimmung (Roll-Call-Position), Industry-Granularität, engerem Fenster und Basisraten-Normierung.
- **Konfidenz der Subject-Mappings:** 89 % „Hoch" (`vote_timing_audit.md`), also ist das Mapping selbst nicht der Engpass.
- **Befund aus dem Projektstand:** `trade_score` korreliert kaum mit dem Return; das passt zu den Präzisionsproblemen oben.

## 5. Rebuild-Empfehlung und Architekturskizze

**Empfehlung:** Die Signallogik nicht migrieren, sondern als kleine, deklarative Regel-Engine neu schreiben (geschätzt 600–900 Zeilen statt ≈ 2.400 in `signals/`, `tags/`, `gates/` plus Stage-Code). Die fachlichen Ideen und Parameter aus §4 übernehmen; Committee-Mapping-Daten (V2) und `issuer_group_key` weiterverwenden.

Architekturgrundsätze:

1. **Drei Phasen mit festen Verträgen** (Schema per pandera/pydantic):
   `Eligibility` (Ausschluss, entfernt Zeilen aus dem Signal-Universum) → `Features` (reine, zeitgültige Anreicherung: Sektor, Ausschuss **zum Handelsdatum**, Votes **der eigenen Kammer**) → `Rules` (Signale) → `Score` (separates Modul).
2. **Dreiwertige Ergebnisse je Regel:** `HIT | NO_HIT | NOT_EVALUABLE(reason)`. Fehlende Daten werden sichtbar und nicht zu einem Falsch-negativ.
3. **Deklarative Regeldefinition** (YAML/Python-Dataclass, eine Quelle, keine `getattr`-Defaults, beim Start validiert, unbekannte Keys führen zum Abbruch):
   ```yaml
   - id: committee_full
     scope: trade
     requires: [sector, industry, committees_at_date]
     when: any(m.match_type == 'full' for m in committee_matches)
     params: {match_mode: sector_and_industry}
   - id: swarm_buy
     scope: issuer_window          # issuer_group_key x Fenster
     requires: [issuer_group_key, member_id]
     params: {window_days: 7, min_members: 2, chaining: false}
   ```
4. **Begründung je Treffer (Evidence):** Jede Regel liefert `{rule_id, rule_version, params_hash, evidence:{…}}`, z. B. Ausschuss, Unterausschuss, Mapping-Revision, gematchte Industry bzw. beteiligte `trade_id`s und Fensterdaten. Gespeichert wird das in einer langen Tabelle `trade_flags(trade_id, rule_id, status, evidence_json, run_id)`. UI und Export lesen nur diese Tabelle, „warum geflaggt" ist dann ein Join.
5. **Idempotent und deterministisch:** Ein Full-Recompute der Flags pro Lauf ist bei ~100k Trades in Sekunden möglich (vektorisiert, kein `iterrows`); kein Delta-Sonderpfad, kein append-only-Eventlog mit instabilen IDs. Event-IDs aus (rule_id, Gruppenschlüssel, Ankerdatum = frühester Trade) bilden.
6. **Eindeutige Trade-Identität vor allem anderen:** Lots am selben Tag zusammenfassen oder mit einer Sequenznummer versehen; accumulative und contrarian zählen nur über verschiedene Tage.
7. **Tests auf drei Ebenen:** Regel-Unit-Tests (Tabellenfälle), Property-Tests (z. B. ein ausgeschlossener Trade hat nie ein HIT; floor_action ⊆ eigene Kammer), Golden-Set aus ≈ 50 handverifizierten Trades mit erwarteten Flags und Begründungen. Dazu ein Reporting-Test zu Trefferquoten je Regel und Jahr, der bei Sprüngen warnt (er hätte die Abdeckungslücken 2012–2024 sofort gezeigt).
8. **Benennung:** Statt „Filter 0/3/4" sprechende IDs (`eligible_asset`, `committee_full`, `vote_proximity`); die UI-Labels werden aus der Regeldefinition erzeugt.
