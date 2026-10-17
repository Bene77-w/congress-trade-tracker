# 07 Committee Matcher (Subcommittee zu Industry) und Legacy-Mapping

Stand 2026-10-17, Audit read-only. Tests ausgeführt: `pytest tests/test_committee_matcher_* tests/test_submapper.py` (inkl. weiterer committee-Tests) ergab 1285 passed in 170 s.

## 1. Features

Fachliches Produkt: Je Unit (Ausschuss-Kern oder Unterausschuss, 225 Stück) eine Liste von 0 bis 5 Yahoo-Finance-Industries mit Confidence 1 bis 4, wörtlichem Textbeleg (`evidence_excerpt`) und kurzer Begründung. Konsument: `tags/committee_tag.py` über `config/committee_mapping.py`.

| Feature | Fundort |
| --- | --- |
| Legacy-`CommitteeMapper`, O(1)-Lookup, heute Fassade über aktive V2-Revision | `config/committee_mapping.py:62`, `config/committee_revision.py:137,310` |
| Aktive Revision wählen (`COMMITTEE_MAPPING_REVIEW_ID`, Parent-Core additiv) | `config/settings.py:434-451` |
| Produktiver Mappingweg per CLI-Abo (Codex/Claude): Submapper `--fullrun`, `--combined N` | `committee_matcher/submapper.py` (4871 Z., 152 Defs) |
| Import des Fullrun-Ergebnisses in eine Review-Revision | `committee_matcher/fullrun_import.py` |
| Input-Korpus (Jurisdiktions-Markdown mit Frontmatter), Units, Roster-Abdeckung | `input_corpus.py`, `units.py`, `roster_coverage.py` |
| Taxonomie-Snapshot aus yfinance (Sektoren/Industries) und Freigabe | `taxonomy_snapshot.py`, `taxonomy_source.py` |
| API-Matcher (OpenRouter): Provider, Prompts, Workflow, Run/Reconcile | `provider.py`, `prompts.py`, `workflow.py`, `mapping.py` |
| Review-Export/-Import, Artefakte, Validierung, Terminalstatus | `review.py`, `artifacts.py`, `validation.py`, `status.py` |
| Gold-Set, Evaluation, Metriken (aufgegeben) | `evaluation.py`, `metrics.py`, `AUFGEGEBENER_PLANTEIL.md` |
| CLI mit vielen Subbefehlen | `cli.py` (2337 Z.) |
| 40+ `COMMITTEE_MATCHER_*`-Settings | `config/settings.py:774-870` |

## 2. Funktionsprüfung

Belegt:
- Tests grün (1285).
- Aktive Revision `map-fullrun-20261016` (`data/committee_matcher_v2/reviews/…`): 225 Units, alle `reviewed`; 204 mapped, 21 no_mapping (begründet: z. B. Ethics, House Admin, Appropriations-Kern); 522 Assignments, 87 verschiedene Industries; Verteilung assignment_count 2: 128, 3: 23, 5: 27. Modell `gpt-6.1-sol` über codex-cli, Prompt `mapping-system-v3`, Hashes und Taxonomie-Hash gespeichert (gute Provenienz).
- Abdeckung vollständig (225/225), keine leeren Units ohne Begründung. Übertreibung begrenzt: 62 Units mit `broad_scope_exception` (explizit begründet).

Stichprobe (15 Assignments, Zufallsseed 11):
- Plausibel (9): Natural Resources Energy zu Oil & Gas E&P; E&C Environment zu Water Utilities (Safe Drinking Water Act); EPW Chemical Safety zu Chemicals; Surface Transportation zu Utilities Gas (PHMSA); Fin. Services Illicit Finance zu Banks; Senate Finance zu Medical Care Facilities (c4); Senate Ag Livestock zu Packaged Foods; EPW Clean Air zu IPPs (Nuklear); Workforce Protections zu Coking Coal (Mine Safety, schwach aber belegt).
- Schwach/fragwürdig (6): Senate Budget zu Oil & Gas Integrated und Senate Approps Legislative Branch zu Entertainment (Beleg nur „Recherche“, nicht zitiert); Foreign Relations East Asia zu Software-Infrastructure (Cyber-Randsatz); Higher Education zu Staffing & Employment; Oversight Health zu Packaged Foods; Senate HSGAC Investigations zu Banks (Offshore-Banking-Satz).
- Struktureller Befund: 123 von 522 Assignments (24 %) haben als `evidence_excerpt` nur das Wort „Recherche“ (91 davon c3), also keinen überprüfbaren Textbeleg; hier ist die Zuordnung Modellwissen/Websuche. Die Mehrheit der Fragwürdigen stammt aus dieser Gruppe. Confidence ist fast uniform (381 von 522 = 3), trennt also kaum.
- Legacy-CSV `data/mappings/subcommittee_sector_industry_mapping.csv` (861 Zeilen, 143 Units, 134 Industries/11 Sektoren, Konfidenz Hoch/Mittel) ist ein älterer Stand und mit der aktiven Revision nicht identisch; aktuelle Wahrheit ist die Revision. 27 Zeilen ohne Subcommittee. Beispiele aus der CSV mit offensichtlicher Überdehnung: E&C Commerce/Manufacturing mit 35 Industries; Senate Judiciary Courts zu Regulated Electric.

Vermutet (nicht geprüft): ob `tags/committee_tag.py` alle Revisionsfelder (Evidenz, Confidence) wirklich nutzt; Qualität gegenüber einem Gold-Set (Gold-Set unfertig, P9 offen).

## 3. Code-Qualität

| Block | Zeilen |
| --- | --- |
| `committee_matcher/*.py` gesamt | ca. 19 900 (inkl. config/committee_*: 20 500) |
| davon CLI `cli.py` | 2337 |
| davon `submapper.py` | 4871 |
| Tests (committee + submapper) | 15 500 |
| Doku `organisation/committee_matcher_*.md` + README | ca. 3 500 |
| Skripte `committee_matcher_baseline/migrate_inputs` | 1 800 |

Produktiv zur Laufzeit nötig (Lesen der Revision, Lookup): `config/committee_mapping.py` 341 + `config/committee_revision.py` 312 = ca. 650 Zeilen (3 %). Für das Erzeugen nötig (einmal je Kongress): Submapper-Kern + fullrun_import + Input/Units/Taxonomie, realistisch 3 000 bis 4 000 Zeilen. Aufgegeben oder redundant: API-Mapper (provider, workflow, mapping, prompts, Teile von review/artifacts/errors/status, ca. 5 000), Evaluation/Metrics/Gold-Set (ca. 1 300 plus Labeling-Daten), settings_view/CLI-Fassade (ca. 2 800). Overengineering-Quote: grob 80 bis 85 % des Codes nicht für das Produkt nötig. Verhältnis Infrastruktur : Nutzen ca. 30 : 1 (20k Zeilen für eine Tabelle mit 522 Zeilen). Ein File mit 4871 Zeilen, zwei parallele Mapperwege (API und CLI), 34 MB Laufartefakte in `data/committee_matcher_v2/` (runs, fullrun_test, Backups) und `.bak.xlsx` sind Ballast. Wiederholte Plan-/Gate-Dokumente deuten auf Prozessaufwand, der das Ergebnis nicht verbessert hat.

## 4. Wiederverwendbares Wissen / Assets

1. Jurisdiktions-Inputs: `data/committee_matcher_inputs/md/` (48 Markdown-Dateien, ca. 284 kB, Frontmatter pro Committee mit Unit-Basis) plus `txt/` und `corpus_manifest.yaml`. Der wertvollste Rohstoff; Quellen: House Rule X, Senate-Jurisdiktionen. `source_url` ist teils `null` (Quellenmetadaten „pending“).
2. Finale Zuordnung: `data/committee_matcher_v2/reviews/map-fullrun-20261016/reviewed_assignments.csv` (522 Zeilen mit Evidenz/Begründung/Provenienz) und `reviewed_unit_coverage.csv` (225 Units, no_mapping-Gründe, Broad-Scope-Begründungen). Die Legacy-CSV ist nur noch Altlast.
3. Prompt `mapping-system-v3`: `committee_matcher/prompts.py` (249 Z.) und `organisation/committee_matcher_mapping_prompt_v3_plan.md`; Regeln „wesentlicher, vorhersehbarer Einfluss auf Kerngeschäft“, no_mapping-Option, Confidence-Skala.
4. Taxonomie: `data/committee_matcher_v2/taxonomy/approved_baseline.json`, `data/yf_sectors_with_industries*.json` (yfinance, 11 Sektoren).
5. Roster: `data/mappings/house_member&(sub)committees.csv`, `senate_member&(sub)committees.csv`, `us_*_all_committees.csv` (Unit-Liste).
6. Entscheidungswissen: `committee_matcher/AUFGEGEBENER_PLANTEIL.md`, `SUBMAPPER_ANLEITUNG.md` (CLI-Abo statt API, `--combined`).

## 5. Rebuild-Empfehlung

Minimal: Ein Skript (ca. 300 bis 500 Zeilen) plus eine Datei.
- Eingabe: die 48 Jurisdiktions-Markdowns und Industry-Liste; Roster-CSV als Unit-Liste.
- Prompt v3 übernehmen; je Unit ein Aufruf (Claude/Codex-Abo), JSON-Schema: `industries[{industry, confidence, evidence_quote, rationale}]` oder `no_mapping`. Validierung nur: Industry in Taxonomie, Zitat ist wörtlicher Teilstring der Quelle (hätte die 123 „Recherche“-Fälle verhindert), max. 5.
- Ausgabe: eine CSV (unit_id, industry, confidence, evidence, rationale), per Hand im Excel prüfbar; Commit statt Review-Revisionsframework.
- Laufzeit: ein Loader (ca. 80 Zeilen) ersetzt `committee_mapping.py` + `committee_revision.py`; Parent-Core-Additiv als eine Regel.
- Streichen: API-Mapper, Gold-Set/Evaluation, Taxonomie-Snapshot-Workflow (statische JSON reicht), settings_view, die meisten CLI-Subbefehle, 40 Settings (3 bis 4 genügen).
- Vorab: die 123 „Recherche“-Assignments und Confidence-Kalibrierung gezielt nachprüfen oder mit Pflichtzitat neu laufen lassen (geringe Kosten, ca. 225 Calls).
