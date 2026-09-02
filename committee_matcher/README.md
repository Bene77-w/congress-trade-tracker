# Committee Matcher V2 – Kurzbedienung

Der Workflow wird über die CLI gesteuert. Der Mappingweg ist vollständig:
Inputprüfung, Roster-Coverage, Taxonomie-Snapshots, Providerpreflight,
Mappinglauf und manueller XLSX-Roundtrip.

## Schnellstart

```bash
venv/bin/python -m committee_matcher --help
venv/bin/python -m committee_matcher input validate
venv/bin/python -m committee_matcher roster validate
```

Exit-Code `2` bedeutet erfolgreicher Lauf mit Warnungen; Details stehen im
Terminalbericht und unter
`data/committee_matcher_v2/reports/<kommando>/errors.json`.

## Provider prüfen

```bash
venv/bin/python -m committee_matcher provider check
venv/bin/python -m committee_matcher provider smoke
```

`provider check` fragt den Endpointkatalog des konfigurierten Modells ab und
prüft ihn gegen die Providervorgaben: Structured Output, Datenschutzpolitik,
Quantisierung, Preisgrenze, Contextfenster. **ZDR weist der Katalog nicht aus
und wird deshalb nicht geprüft** — ein grüner Preflight ist kein ZDR-Nachweis.
Bleibt kein Endpoint übrig, endet der Lauf mit Exit-Code `4` und nennt die
zulässigen Stellschrauben: Modellwechsel oder `COMMITTEE_MATCHER_ZDR = False`.

`provider smoke` ist der einzige kostenpflichtige Prüfbefehl (< 0,001 USD). Er
führt einen echten Call gegen das erzwungene Ausgabeschema aus und schlägt
fehl, wenn das Modell Structured Output nur nominell unterstützt.

## Taxonomie abrufen und freigeben

```bash
RUN_ID=run-2026-08-20
venv/bin/python -m committee_matcher snapshot build --run-id "$RUN_ID"
```

Der erste vollständige Snapshot endet erwartungsgemäß mit Exit-Code `5`
(`awaiting_taxonomy_approval`). Vor der Freigabe prüfen:

```text
data/committee_matcher_v2/runs/<run_id>/taxonomy_snapshot.json
data/committee_matcher_v2/runs/<run_id>/taxonomy_catalog.md
```

Danach freigeben:

```bash
venv/bin/python -m committee_matcher snapshot approve --run-id "$RUN_ID"
```

Abgebrochenen Abruf aus dem Checkpoint fortsetzen:

```bash
venv/bin/python -m committee_matcher snapshot resume --run-id "$RUN_ID"
```

## Mappen

```bash
venv/bin/python -m committee_matcher run plan    --run-id "$RUN_ID" --all-units
venv/bin/python -m committee_matcher run execute --run-id "$RUN_ID"
```

`run plan` ist providerfrei und nennt den Worst Case in USD; erst `run execute`
sendet kostenpflichtige Calls. Statt `--all-units` schneiden `--chamber`,
`--committee` und `--unit-id` die Zielmenge zu; beides zugleich ist ein
Bedienfehler. Ohne Zielangabe läuft nichts.

Bleibt ein Call mit offenem Ausgang, klärt ihn
`run reconcile --run-id "$RUN_ID"` über die Generation-ID nach.

## Review-Roundtrip

```bash
venv/bin/python -m committee_matcher review export --run-id "$RUN_ID"
venv/bin/python -m committee_matcher review import --run-id "$RUN_ID"
```

Der Import schreibt `reviewed_assignments.csv` — die Datei, die
`scripts/import_trades.py` liest. Der produktive Mapping-Master wird durch
diesen Workflow nicht verändert.

## Nicht gebaut

Evaluationslauf (`evaluate run`), Holdout-Qualifikation (`qualify holdout`),
eine eigene `full-run`-Familie und der Auswertungsbericht (`report`) entfallen.
Begründung und Ersatzwege: `AUFGEGEBENER_PLANTEIL.md`.

Das produktive Mapping aller 225 Units läuft über die CLI-Abos; siehe
`GOLDSET_ANLEITUNG.md` und `organisation/committee_matcher_fullrun_plan.md`.

## Konfiguration und Referenzen

- Einstellungen: `config/settings.py`, Präfix `COMMITTEE_MATCHER_*`
- Vollständige CLI: `organisation/CLI_Bedienungsanleitung.md`, Abschnitt 15
- Gold-Set-Creator (P9): `GOLDSET_ANLEITUNG.md`
- Entscheidungen: `organisation/committee_matcher_planung_entscheidungen_codex.md`
- Phasen und Gates: `organisation/committee_matcher_v2_implementierungsplan.md`
- Aufgegebener Planteil: `AUFGEGEBENER_PLANTEIL.md`
