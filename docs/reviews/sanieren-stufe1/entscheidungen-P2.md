# Entscheidungen — Ticket P2
Startcommit: 0296f08

## Implementierungsentscheidungen
| Entscheidung/Annahme | Begründung | Bezug (Spec/Ticket/Datei) |
|---|---|---|
| Umsetzung begonnen durch Sonnet-Umsetzer, auf Wunsch des Nutzers abgebrochen und vom Implementierungsagenten fertiggestellt; Teiländerungen geprüft und übernommen. | Nutzervorgabe „mach P2 selbst“. | P2 |
| Entfernt: `SCRAPER_*`, `SENATE_SCRAPE_*`, `PDF_EXTRACTOR_*`, `FUZZY_MATCH_THRESHOLD`, `NAME_ALIASES_FILE`. | grep über alle `*.py`, `*.sh`, `*.yml`, `*.service` und Dockerfiles findet keinen Leser, auch keinen String-Zugriff per `getattr`. | P2; `config/settings.py` |
| `HOUSE_COMMITTEES_FILE`, `SENATE_COMMITTEES_FILE`, `COMMITTEE_MAPPING_FILE` bleiben samt Schutzlisteneintrag. | Roster-CSVs bleiben (Matcher-Roster-Coverage); `COMMITTEE_MAPPING_FILE` fällt laut Plan erst in P4. | P2, P4 |
| `pipeline_state.json` in `reset_pipeline.py` bleibt vorerst. | Laut Plan Teil von P3. | P3 |
| `TestNameMatcherMemoEquivalence` gelöscht (1 Test); Testanzahl 2127 → 2126. | Testet nur das gelöschte Modul. | `tests/test_intervall4_utils_fixes.py` |

## Reviewbefunde
| Befund und Auswirkung | Schweregrad laut Prüfer | Prüfer, Review, Runde | Bezug (Spec/Ticket/`datei:zeile`) | Umgang | Begründung/Belege |
|---|---|---|---|---|---|
