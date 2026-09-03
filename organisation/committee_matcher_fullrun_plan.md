# Fullrun ueber die CLI-Abos - Implementierungsplan

Stand 2026-09-02. Betrifft `committee_matcher/submapper.py` und ein neues
Modul `committee_matcher/fullrun_import.py`.

## Betriebsprofil

- **Wie oft:** ein- bis viermal im Jahr, je Korpus- und Taxonomiestand. Kein
  Dauerbetrieb, keine Nebenlaeufigkeit.
- **Wer schaut zu:** Bene, live am Terminal. Der Lauf laeuft ueber Stunden bis
  Tage; er darf jederzeit abgebrochen und erneut gestartet werden.
- **Was kostet ein Abbruch:** nichts. `run` ueberspringt vorhandene Antworten
  und labelt nur den Rest - der Wiederaufsatz steht bereits und wird nicht neu
  gebaut.

Daraus folgt, was **nicht** gebaut wird: keine Auditspur ueber Wartezeiten,
keine Kostenwache (das Abo rechnet nicht je Aufruf ab), keine Reconciliation,
kein Fortschrittsjournal auf Platte. Die Konsolenausgabe ist die Spur.

**Zeilenbudget Kern:** rund 250 Zeilen in `submapper.py`, rund 230 im neuen
Exportmodul. Was darueber hinausgeht, braucht einen Grund.

## Ausgangslage

| Sache | Stand |
| --- | --- |
| Unitmenge Gold-Set | 22 aus `sample_units.json` |
| Unitmenge Korpus | 225 (31 committee_parent_core, 15 committee_direct, 179 subcommittee) |
| Wiederaufsatz | vorhanden; `run` ueberspringt vorhandene Antworten |
| Ratenlimit | wird als `GS_RATE_LIMITED` erkannt, bricht den Satz heute ab |
| Turn-Limit | existiert nicht; `--max-turns` wird nie gesetzt |
| Zeitdeckel | 1800 s je Unit, 43200 s gesamt |

### Ratenlimit-Meldungen der beiden CLIs (Recherche 2026-09-02)

- **Claude Code 2.1.258:** HTTP 429, Fehlerkategorie `rate_limit`. Die CLI
  wiederholt zuerst selbst und meldet das als `system/api_retry`-Ereignis mit
  `retry_delay_ms`; erst danach gibt sie auf. Einen eigenen Exitcode fuer das
  Ratenlimit gibt es nicht - die Doku verlangt ausdruecklich die Pruefung auf
  null/nicht-null plus Auswertung der strukturierten Ausgabe.
- **Codex 0.151.0:** HTTP 429 mit Typ `rate_limit_exceeded`, Klartext
  "You've hit your usage limit ... try again at <Zeit>". Das Feld `rate_limits`
  im JSONL des exec-Modus ist laut offenem Issue openai/codex#14728 immer
  `null`; die Auslastung ist bei Codex nicht auslesbar, nur die Fehlermeldung.

**Folge:** Die vorhandene Erkennung ueber Status 429 und die Textmarken
`rate limit`, `rate_limit`, `too many requests`, `usage limit`,
`insufficient_quota`, `exceeded your current quota` bleibt der einzige
belastbare Weg und deckt beide Formulierungen ab. Es wird keine Exitcode-Logik
ergaenzt, die es nicht gibt.

Die Marke `quota` allein waere zu weit: sie traefe auch `Disk quota exceeded`
und schickte den Fullrun wegen eines lokalen Plattenfehlers in eine
Warteschleife, die nie endet.

## Soll

### 1. Getrennte Ablage

Der Fullrun schreibt nach `data/committee_matcher_v2/fullrun/`, das Gold-Set
unveraendert nach `data/committee_matcher_v2/goldset/labeling/`. Ein Fullrun
darf die Referenz nicht ueberschreiben.

Umgesetzt ueber `use_fullrun_paths()`, das die Pfad-Globals einmal in `main()`
ersetzt. Kein Parameter an 25 Funktionen: das waeren 25 Gelegenheiten, ihn zu
vergessen, und der Fehlerfall waere ein Fullrun in der Gold-Set-Ablage.

### 2. Unitmenge

`label_units(args)` liefert bei `--fullrun` alle Korpus-Units als `SampleUnit`,
sonst `load_sample()`. Die Reihenfolge ist die sortierte `unit_id`, damit ein
Wiederaufsatz dieselbe Folge sieht. Jedes Kommando, das heute `load_sample()`
ruft, ruft kuenftig `label_units(args)`.

### 3. Deckel

Bei `--fullrun` entfaellt der Gesamtdeckel ersatzlos. Der Unitdeckel bleibt: er
ist die einzige Notbremse gegen einen haengenden Unterprozess, und die
Ratenlimit-Wartezeit laeuft ausserhalb von ihm.

### 4. Warteschleife bei Ratenlimit

Ergibt die Fehlerklassifikation `GS_RATE_LIMITED` und laeuft ein Fullrun, wird
die Unit nicht als fehlgeschlagen verbucht: das Werkzeug wartet 600 s und ruft
dieselbe Unit erneut auf, unbegrenzt, bis eine Antwort kommt. Ohne `--fullrun`
bleibt es beim Abbruch des Satzes.

Das Log haelt den letzten Versuch. Die Konsole zaehlt die Wartevorgaenge, der
Schlussbericht nennt die Gesamtwartezeit.

### 5. Uebernahme ins Mapping

Neues Modul `committee_matcher/fullrun_import.py`. Es darf die Matcher-Module
importieren - die Unabhaengigkeitsregel gilt dem Gold-Set, das unabhaengig vom
Prueflung entstehen muss; ein Fullrun ist Produktivmapping und soll dessen
Vertraege benutzen. `submapper.py` ruft das Modul als Unterprozess auf und
bleibt selbst unabhaengig.

Das Modul liest die abgenommene Fullrun-Mappe, baut daraus
`review.EffectiveUnit`/`EffectiveAssignment` und schreibt ueber die vorhandenen
Funktionen `review.reviewed_rows()`, `review.reviewed_coverage_rows()` und
`review._write_review_files()` nach
`data/committee_matcher_v2/reviews/<review-id>/`:

- `reviewed_assignments.csv` - die Datei, die `import_trades` liest
- `reviewed_unit_coverage.csv`

Herkunft je Zeile: `provider` traegt `claude-cli` beziehungsweise `codex-cli`,
`model`, `prompt_id`, `prompt_version` und `prompt_sha256` stammen aus der
Bindung des Aufgabenpakets. `call_id`, `generation_id`, `from_repair` bleiben
leer - ein CLI-Lauf hat sie nicht, und ein erfundener Wert waere eine
Falschaussage. `assignment_source` ist `model` bei einer unveraenderten Zeile
und `manual`, sobald Bene sie in der Mappe angefasst hat; verglichen wird gegen
die gesammelte CSV.

Bei `--compared` traegt `info_merged.csv` beide Laeufe, `labels_merged.csv`
dagegen nur die Basis. Die Herkunft wird deshalb an die Basisengine gebunden
(`MERGE_BASE_ENGINE`); ohne diese Bindung nennte sie die alphabetisch letzte
Engine und behauptete ein Modell, das die Zeile nie erzeugt hat.

`run_id` und `review_id` tragen denselben Wert: es gab keinen Matcherlauf, dem
ein eigener `run_id` zustuende.

### 6. Bedienung

```
submapper.py label  --fullrun [--engine ...|--compared] [--model ...] [--effort ...]
submapper.py finish --fullrun [--engine ...|--compared] --review-id <name>
```

`--eval-id` bleibt der Gold-Set-Weg, `--review-id` der Fullrun-Weg. Beide
zugleich ist ein Bedienfehler und wird gemeldet.

## Nicht-Ziele

- Kein Nachbau einer Matcher-Runrevision (`assignments_long.csv`,
  `run_manifest.json`, Abschlussmarker). Das Manifest verlangt Provider-,
  Routing- und Budgetfelder, die ein CLI-Lauf nicht hat.
- Kein Auslesen der Abo-Auslastung bei Codex - die CLI meldet sie nicht.
- Kein Parsen des Reset-Zeitpunkts aus der Fehlermeldung. Feste 600 s.
- Keine Obergrenze der Wartevorgaenge. Der Lauf soll durchhalten; abbrechen
  kann Bene selbst.

## Pruefung

Netzwerk- und CLI-frei ueber den vorhandenen Trockenlauf
(`SUBMAPPER_FAKE_DIR`), `time.sleep` wird im Test ersetzt.

| Was | Erwartung |
| --- | --- |
| `use_fullrun_paths()` | jeder Pfad-Global zeigt unter die Fullrun-Wurzel |
| `label_units` | ohne Flag 22 Units, mit Flag alle Korpus-Units, sortiert |
| Gesamtdeckel | ohne Flag greift er, mit Flag nie |
| Warteschleife | Ratenlimit einmal, dann Erfolg: Unit gilt als gelabelt, einmal gewartet |
| Warteschleife ohne Flag | Ratenlimit bricht den Satz weiter ab |
| anderer Fehler | wartet nicht, auch nicht im Fullrun |
| `label --fullrun` | schreibt ausschliesslich unter die Fullrun-Wurzel |
| `finish --fullrun` | ruft das Exportmodul, nicht `evaluate import` |
| `fullrun_import` | Spalten und Reihenfolge wie `reviewed_assignments.csv` |
| Herkunft | unveraenderte Zeile `model`, geaenderte `manual` |
| Herkunft bei `--compared` | Provenienz stammt vom Basislauf, nicht vom Vergleichslauf |
| Ratenlimit im Zeitdeckel | 429 plus Deckelriss gilt als Ratenlimit, nicht als Zeitueberlauf |
| lokaler Plattenfehler | `Disk quota exceeded` loest keine Warteschleife aus |
| unbekannte Industry-ID | Uebernahme blockiert, statt leere Namen zu schreiben |
| Modellzeile ohne Provenienz | Uebernahme blockiert |
| Broad-Scope-Zelle | nur `ja` zaehlt, wie in `verify` |
| beschaedigte Mappe | Blockiermeldung statt Traceback |
| abgewiesener Aufruf | laesst die Pfade des Prozesses unveraendert |

## Nachbesserung nach dem Funktionsreview (2026-09-02)

Behoben: Herkunft bei `--compared` an die Basisengine gebunden; Ratenlimit
auch bei gerissenem Unitdeckel erkannt; Textmarke `quota` praezisiert;
`industry_id` gegen den freigegebenen Snapshot geprueft; Modellzeilen ohne
Provenienz blockieren; Broad-Scope-Zelle wird gelesen wie in `verify`;
beschaedigte Mappe meldet statt abzustuerzen; ein abgewiesener Aufruf schaltet
die Ablage nicht mehr um.

Verworfen mit Grund:

- **Gesamtdeckel im Fullrun nicht mehr validieren.** Ein ungueltiger Wert in
  `settings.py` soll gemeldet werden, auch wenn er hier nicht wirkt.
- **`import_manifest.json` streichen.** Es ist der Marker, an dem ein zweiter
  Aufruf einen vorhandenen Stand erkennt.
- **`verify` zusaetzlich `true`/`1` akzeptieren lassen.** Vereinheitlicht wurde
  in die andere Richtung: eine Schreibweise, die `to-xlsx` auch erzeugt.
- **Prompt-Fassung aus der Aufgabenbindung statt aus der Provenienzdatei.** Die
  Provenienz haelt fest, was tatsaechlich lief; die Bindung nur, was geplant war.
- **Umschalter am Ende von `main()` zuruecksetzen.** Der Umschalter ersetzt
  Modulglobals, die Tests direkt umlenken; ein Ruecksetzer schriebe sie auf die
  echte Ablage zurueck. Ein Aufruf bedient eine Betriebsart, das genuegt.

Offen und Bene vorgelegt: `scripts/import_trades.py` liest weiterhin
`data/mappings/subcommittee_sector_industry_mapping.csv` ueber `CommitteeTag()`
und nicht `reviewed_assignments.csv`. Das ist ein vorbestehender offener Punkt
des Matchers (`CLI_Bedienungsanleitung.md`: "der genaue Aufruf fuer diese
Uebernahme bleibt offen") und liegt ausserhalb dieses Fullrun-Wegs.
