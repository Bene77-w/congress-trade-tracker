# Review: Referenzdaten je Kongress (Branch `t3/1b43496c`)

## Final Review

- Anker: `b8f4988`
- Endstand Runde 1: `392a571`
- Besetzung: 1 Generalist (eine Planquelle, `plan.md`)
- Fachprüfer: Architektur. Auslöser: Die Referenztabellen und ihre Schlüssel sind die Schnittstelle, auf der der 120. Kongress und der weitere Umbau aufsetzen.
- Releasezusatz: gilt nicht. Das Ziel-Repo ist `PRIVATE` (`gh repo view --json visibility`).
- Tests am Endstand `392a571`: `pytest -q` im Worktree, 2369 bestanden.

| Prüfer | Runde | Modell/Effort | Verdikt | Status |
|---|---|---|---|---|
| Generalist | 1 | Sonnet 5.5 / medium | abgenommen | abgeschlossen |
| Fachprüfer Architektur | 1 | Sonnet 5.5 / medium | abgenommen | abgeschlossen |

Beide Prüfer sind nach Runde 1 abgeschlossen: Kein Befund war kritisch oder hoch, und die Korrekturen
sind einzelne Prüfungen, Tests und Doku ohne Eingriff in die zentralen Abläufe (als trivial beurteilt).
Tests nach den Korrekturen: `pytest -q`, 2371 bestanden.

| Befund | Schweregrad laut Prüfer | Prüfer, Runde | Stand |
|---|---|---|---|
| Anzeigename eines Ausschusses kann je Kongress doppelt sein; Industries würden still falsch zugeordnet | MITTEL | Architektur, 1 | behoben (Prüfung in `check()`, Test) |
| Kongress ohne `mapping_revision` bleibt still ohne Industries | MITTEL | Architektur, 1 | behoben (Fehler im Build-Aufruf) |
| Kein Test für den Einbau in `TradeImporter` | MITTEL | Architektur, 1; Generalist, 1 | behoben (`test_pipeline_stage_assigns_and_lists_open_members`) |
| Doku an mehreren Stellen auf altem Stand (NameMatcher, Checkpoint, Stage-Diagramm) | MITTEL | Generalist, 1 | behoben |
| Delta-Lauf vor dem Neuaufbau mischt altes und neues Format im Master | MITTEL | Generalist, 1 | behoben (Hinweis in CLI-Anleitung und CHANGELOG) |
| Zuordnung gilt je Kongress, nicht je Tag | NIEDRIG | Generalist, 1 | erfasst (→ `entscheidungen.md`) |
| Leere `bio_guide_id` fiele aus der Triage-Liste | NIEDRIG | Generalist, 1 | behoben (`dropna=False`) |
| `ReferenceMapper` liest private Attribute von `ReferenceData` | NIEDRIG | Generalist, 1; Architektur, 1 | erfasst |
| Referenztest hängt am Arbeitsverzeichnis | NIEDRIG | Generalist, 1; Architektur, 1 | behoben (Pfad aus den Settings) |
| Build setzt `senate.MANIFEST` als Modulglobal um | NIEDRIG | Architektur, 1 | erfasst |
| `revision` am `ReferenceMapper` ist ein verstecktes Signal für `CommitteeTag` | NIEDRIG | Architektur, 1 | erfasst |
| Der 118. Kongress ist in den Trades allein nicht als Übernahme erkennbar | NIEDRIG | Architektur, 1 | erfasst |
| `members.csv` hat zur Laufzeit keinen Leser | NIEDRIG | Architektur, 1 | behoben (Hinweis in `DATA_STRUCTURE.md`) |
| `REFERENCE_CONGRESS_DATA_FROM` und `congresses.csv` sind zwei Pflegeorte | NIEDRIG | Architektur, 1 | erfasst |
| `scripts/reset_pipeline.py` räumt die nicht mehr erzeugte Checkpoint-Datei auf | MITTEL (Teil des Doku-Befunds) | Generalist, 1 | erfasst |
