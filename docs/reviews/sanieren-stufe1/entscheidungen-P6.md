# Entscheidungen — Ticket P6
Startcommit: 7cae4d9

## Implementierungsentscheidungen
| Entscheidung/Annahme | Begründung | Bezug (Spec/Ticket/Datei) |
|---|---|---|
| H6-Testfall mit der echten Richtung des Audit-Falls (McCaul EOG 2016-05-18 sind Verkäufe, im Master 46 Zeilen, Audit nennt 44) und zusätzlich mit Käufen, parametrisiert. | Der Plan spricht von „ein Kauf an einem zweiten Handelstag“; ein Kauf neben 44 Verkäufen wäre eine andere Richtung und könnte nie auslösen. Gemeint ist dieselbe Richtung, die Regel ist richtungsunabhängig. | P6 Gate; `tests/test_sequence_signals.py` |
| H6 in `AccumulativeSignal._scan_direction`: das Gleitfenster läuft über Handelstage (`_td.normalize()`), nicht über Zeilen. Ein qualifizierendes Fenster emittiert ein Event-Set für alle Zeilen seiner Tage; `triggering_date` ist der Handelstag, der die Mindestzahl erreicht, `window_start_iso` der älteste Handelstag. `ACCUMULATIVE_MIN_COUNT` zählt damit Handelstage (Kommentar in den Settings angepasst, Wert unverändert 2). | Plan: Lots desselben Mitglieds, Issuers, Handelstags und derselben Richtung zählen als ein Ereignis. Mitglied und Issuer legt schon die Gruppierung `(bio_guide_id, issuer_ticker)` in `_apply_sequence_signals` fest, die Richtung `evaluate_group`. | P6 H6; `signals/accumulative_signal.py` |
| contrarian bleibt unverändert. | Der Plan nennt für H6 nur accumulative; Audit 03 Nr. 6 („contrarian zählt nur über verschiedene Tage“) ist eine Empfehlung für Stufe 2, nicht Teil des Tickets. | P6 H6 |
| H2 als Maske direkt in `run()` vor Schritt 12: Signal-Schritte 12–14 laufen nur auf `~asset_class_excluded`; die ausgeschlossenen Zeilen werden danach mit `signal_count=0`, `signals_triggered=''` wieder angefügt (`reindex(df.index)`, Reihenfolge und Zeilenzahl bleiben, ein doppelter Index bricht laut ab). Kein neuer Helfer. | Plan: „Umgesetzt als Maske vor den Signal-Schritten in run()“. Die drei Signal-Methoden bleiben unverändert. | P6 H2; `scripts/import_trades.py` |
| „Kein Score“ heißt `trade_score = 0` mit leerem Breakdown, nicht NaN. Das Scoring läuft weiter über alle Zeilen; ohne Signal greift das bestehende Hard-Gate (kein Primärsignal → 0). | So steht jeder Trade ohne Primärsignal im Master; NaN würde die int64-Spalte und die App-Leser ändern. Der Bericht zählt „ausgeschlossen mit Score > 0“. | P6 H2; `scoring/trade_score.py` |
| Committee-Relevanz (`Insider_Sectors`, `Relevant_Sectors`, `ex_officio_role_keys`) wird für ausgeschlossene Zeilen weiter berechnet. | Das sind Tags, keine Signale; der Plan setzt die Maske vor die Signal-Schritte. Ohne Signal wirken sie weder auf Score noch auf Events. | P6 H2 |
| H2-Test als ganzer `--dry_run` über die vorhandene Fixture `pipeline_paths` (`tests/test_suspicious_export.py::TestAssetGateHarterAusschluss`): Apple-Anleihen (Typ `bond` über einen Fake-Normalizer, wie es OpenFIGI liefert) teilen den Issuer mit einer Apple-Aktie. Ohne Gate entstünden Schwarm (B) und Sequenz (A) und Score 20 für alle drei Zeilen (Rotlauf vor der Änderung belegt). | Prüft die Maske dort, wo der Plan sie verlangt (in `run()`), samt Score und Event-Stage-Dateien. | P6 Gate |
| `scripts/report_integrity.py`: Defaults aus den Settings (`DATA_PROCESSED_PATH`/`TRADES_MASTER_COMPLETE_CSV`, `DATA_RAW_PATH`/`QUIVER_TRADES_FILE`, `POST_TRADE_PRICE_CACHE`, je unter `BASE_DIR`); keine neuen Settings. Fehlende Master-Spalten brechen laut ab (`usecols`). | Kein eigener Filterparameter nötig; der Kurs-Abstand kommt über `POST_TRADE_MAX_PRICE_GAP_DAYS`. | P6; P7 Punkt 5 |
| Return-Anteile je Jahr als disjunkte Aufteilung: `null` (= 0), `nan`, `gueltig` (nicht NaN und ≠ 0); Summe 100 %, Nenner alle Trades des Jahres. | Plan: „Anteil 0, Anteil NaN und Anteil gültig, immer mit demselben Nenner“. Disjunkt ist eindeutig; das P7-Gate „gültig ≥ 80 %“ wird dadurch höchstens um den Nullanteil (< 2 %) strenger. | P7 Punkt 5 und Gate |
| Kurs-Cache-Abdeckung je Jahr = Anteil der Trades, für deren `ticker` der Cache einen echten Close am Handelstag oder bis `POST_TRADE_MAX_PRICE_GAP_DAYS` danach hat. Wiederverwendet `_load_price_cache`, `_build_price_lookup`, `_price_at_or_after_fast` aus `scoring/post_trade_returns.py`. | Dieselbe Regel und derselbe Cache-Schlüssel wie die Baseline der Post-Trade-Returns; keine zweite Implementierung. | P7 Punkt 5; Audit 6.2 |
| „Ohne Sektor“ = `sector_yf` leer, „ohne Ausschuss“ = `Member_Committees_Full` leer oder `[]`; Nenner alle Trades des Jahres (auch ausgeschlossene). | Einheitlicher Nenner wie im Plan verlangt. | P7 Punkt 5 |
| Zusatzzeilen im Abschnitt Asset-Klassen: „ausgeschlossen“, „ausgeschlossen mit Score > 0“, Share-Class-Zeilen gesamt, ausgeschlossene Zeilen nach `ticker_type` und die zehn häufigsten ausgeschlossenen Ticker. | Das P7-Gate prüft „ausgeschlossene Zeilen ≈ 1.140 + 3.500, alle ohne Score“ und „Ziel nahe 898“. Die Ticker-Liste macht die rund 549 FB-Zeilen (K4, als ETF ausgeschlossen) sichtbar, ohne Sonderregel. | P7 Gate; P5 bekannte Abweichung |

## Probelauf `report_integrity.py` (gekürzt)
Lesend gegen den Haupt-Worktree: Master vom 2026-08-11 (`trades_master_complete.csv`, Stand vor P5/P6), XLSX vom 2026-08-03, Kurs-Cache vom 2026-11-18. Laufzeit 1:11 min.

```
== Zeilen ==
Master                                     114347
XLSX                                       114347
Master - XLSX                                   0
XLSX identische Zeilen (Wiederholungen)     11152

== post_return_90d je Jahr (%) ==
             n  null   nan  gueltig
2013       415  63.6  36.4      0.0
2016      7148  72.3  27.7      0.0
2020     12017  85.3  14.7      0.0
2021      7252  19.8  13.4     66.9
2022      9861   1.2  12.6     86.2
2025     13141   0.2   6.0     93.8
gesamt  114347  43.3  18.1     38.6
(signed_ar_90d entsprechend: gesamt 43.0 / 18.1 / 38.9)

== Kurs-Cache: Kurs zum Handelstag je Jahr (%) ==
2012–2020 je 0.0, 2021 47.4, 2022 86.2, 2025 93.8, gesamt 38.5

== Ohne Sektor / ohne Ausschuss je Jahr (%) ==
             n  ohne_sektor  ohne_ausschuss
2014      5220         35.7            41.7
2023      9526         12.5             4.7
gesamt  114347         20.8            19.2

== Trefferquote je Signal und Jahr (%), Zeile gesamt ==
accumulative 52.8, trade_volume 25.9, contrarian 16.8, committee 16.7,
committee_full 16.7, extreme_report 10.3, swarm_buy 10.1, swarm_sell 9.9,
committee_ex_officio 8.6, swarm_paradox 5.6, floor_action 3.7, swarm_conflict 1.1

== Asset-Klassen ==
etf                                0
ausgeschlossen                  1140
ausgeschlossen mit Signal        988
ausgeschlossen mit Score > 0      17
Share-Class-Zeilen               898
Share-Class-Zeilen mit FIGI        0

== Ausgeschlossen nach ticker_type ==
numeric_suffix 559, preferred 167, unknown 154, invalid 94, bond 65, ...

== Ausgeschlossen: haeufigste Ticker ==
WB1 131, DG1 73, TX1 48, NTEST 46, LLY-WD 29, ...
```

Die Werte decken sich mit dem Audit (accumulative 52,8 %, 1.139/1.140 ausgeschlossen, 987/988 mit Signal, 17 mit Score, 0 von 897/898 Share-Classes mit FIGI, 11.152 identische XLSX-Zeilen).

## Reviewbefunde
| Befund und Auswirkung | Schweregrad laut Prüfer | Prüfer, Review, Runde | Bezug (Spec/Ticket/`datei:zeile`) | Umgang | Begründung/Belege |
|---|---|---|---|---|---|
