# Committee Matcher V2 – Kurzbedienung

Der Workflow wird über die CLI gesteuert. Aktuell sind Inputprüfung,
Roster-Coverage und yfinance-Snapshots (P0–P4) verfügbar. OpenRouter-, Mapping-
und Reviewläufe bleiben bis Gate B gesperrt.

## Schnellstart

```bash
venv/bin/python -m committee_matcher --help
venv/bin/python -m committee_matcher input validate
venv/bin/python -m committee_matcher roster validate
```

Exit-Code `2` bedeutet erfolgreicher Lauf mit Warnungen; Details stehen im
Terminalbericht und unter
`data/committee_matcher_v2/reports/<kommando>/errors.json`.

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

## Workflow nach Gate B

Die folgenden Familien sind bereits sichtbar, aber noch nicht implementiert
beziehungsweise bis zu ihrer Phase gesperrt:

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
- Entscheidungen: `organisation/committee_matcher_planung_entscheidungen_codex.md`
- Phasen und Gates: `organisation/committee_matcher_v2_implementierungsplan.md`
