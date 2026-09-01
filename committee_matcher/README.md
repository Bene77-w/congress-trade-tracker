# Committee Matcher V2 – Kurzbedienung

Der Workflow wird über die CLI gesteuert. Verfügbar sind Inputprüfung,
Roster-Coverage, Taxonomie-Snapshots (P0–P4) sowie Routing-Preflight und
Contract-Smoke des Providers (P5). Mapping-, Review- und Evaluationsläufe
folgen ab P6.

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

## Noch nicht implementiert

Die folgenden Familien sind bereits sichtbar, aber bis zu ihrer Phase
gesperrt:

```text
run plan|execute|reconcile  → Entwicklungsrun und Kostenkontrolle
review export|import        → manueller XLSX-Roundtrip
evaluate goldset            → Entwicklungsevaluation
qualify holdout             → unabhängige Qualifikation
full-run                    → qualifizierter Lauf über alle 225 Units
report                      → Revisionsbericht
```

Nicht verfügbare Stages enden mit Exit-Code `4`. Der produktive Mapping-Master
und `scripts/import_trades.py` werden durch diesen Workflow nicht verändert.

## Konfiguration und Referenzen

- Einstellungen: `config/settings.py`, Präfix `COMMITTEE_MATCHER_*`
- Vollständige CLI: `organisation/CLI_Bedienungsanleitung.md`, Abschnitt 15
- Gold-Set-Creator (P9): `GOLDSET_ANLEITUNG.md`
- Entscheidungen: `organisation/committee_matcher_planung_entscheidungen_codex.md`
- Phasen und Gates: `organisation/committee_matcher_v2_implementierungsplan.md`
