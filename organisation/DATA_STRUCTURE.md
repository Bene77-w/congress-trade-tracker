# Congress Tracker - Datenstruktur-Dokumentation

Vollständige Übersicht aller relevanten Dateien, ihrer Struktur und Verwendung.

---

## 1. Trade-Daten (Scraped)

### `data/raw/congress-trading-all.xlsx`
**Quelle**: Quiver Quantitative (Auto-Download via `scrapers/quiver_scraper.py` oder manueller Download)
**Format**: XLSX
**Update**: Täglich 03:00 via systemd-User-Timer + Quiver-Auto-Download-Scraper
**Credentials**: `QUIVER_EMAIL`, `QUIVER_PASSWORD` (nur in `.env`, gitignored; Vorlage in `.env.example`)

**Beschreibung**: Primäre Quelle aller Congress-Trades (House + Senate). Der Auto-Scraper
(`quiver_scraper.py`) loggt sich täglich ein, lädt die aktuelle Snapshot-Datei herunter und
ersetzt sie atomar an diesem Pfad (via `os.replace` nach Download in Temp-Datei). Input für
Pipeline 5 (`scripts/import_trades.py`). Plausibilitäts-Check: Dateigröße > 0 erforderlich.

**Scheduler (Verkettung Download + Import):** systemd-User-Timer `quiver-import.timer`
(täglich 03:00, `Persistent=true` holt verpasste Läufe nach). Service-Unit führt
Scraper + Import als zwei `ExecStart`-Schritte aus; bei Scraper-Exit ≠ 0 läuft der
Import nicht. Einrichtung/Bedienung: **`deploy/systemd/README.md`**.

### `data/raw/house_trades.csv`
**Quelle**: `scrapers/house_scraper.py` (ZIP + PDF-Parsing von disclosures-clerk.house.gov)
**Format**: CSV
**Update**: Bei jedem Scraper-Lauf

**Spalten**:
```csv
name,ticker,asset_name,asset_type,transaction_type,amount,transaction_date,owner,subholding,comment,disclosure_date,ptr_link,csv_upload_time
```

### `data/raw/senate_trades.csv`
**Quelle**: `scrapers/senate_scraper.py` (Playwright + DataTables API von efdsearch.senate.gov)
**Format**: CSV
**Update**: Bei jedem Scraper-Lauf

**Spalten**:
```csv
name,ticker,asset_name,asset_type,transaction_type,amount,transaction_date,owner,comment,disclosure_date,ptr_link,csv_upload_time
```

**Verwendung**: Hauptdatenquellen für alle Trades. Werden von `scripts/import_trades.py` importiert.

---

## 2. Committee-Mitgliedschaften

> **F2-Format (2026-06-12):** Beide Roster-CSVs unten enthalten
> `Member_Committees_Full`-kompatible Strings im **F2-Format** — `;` trennt
> Committees, `:` leitet die Sub-Liste ein, `~` trennt Subcommittees, `|`
> trennt Name von Rolle (Committee-Ebene: `Chairman`/`Vice Chairman`/
> `Ranking Member`/`Ex Officio`/`Cochairman`/leer; Sub-Ebene: `Chair`/
> `Vice Chair`/`Ranking Member`/`Ex Officio`/leer). Vollständige Spec inkl.
> Beispiele und Begründung: `scrapers/senate_pdf_parsing_tool/csv_zielstruktur.md`
> (Single-Source — hier nicht duplizieren). Zentraler Reader für das Format:
> `utils/committee_parser.py`.

### `data/mappings/house_member&(sub)committees.csv`
**Quelle**: `scrapers/house_pdf_parsing_tool/parse_house_committee_pdf.py`
(Extraktion aus `scsoal.pdf`, clerk.house.gov, committet)
**Format**: CSV
**Zeilen**: 437 (436 Mitglieder + Header)
**Update**: Bei neuen Wahlen (alle 2 Jahre) oder Committee-Umstrukturierungen,
neu erzeugt via `parse_house_committee_pdf.py --pdf <pfad> [--out <pfad>]`

**Spalten**:
```csv
Name,Party,State,Committees,ExOfficio
```

**Struktur**:
- `Name` - Vorname Nachname (z.B. "John Smith")
- `Party` - 1 Buchstabe: `D` (Democrat), `R` (Republican)
- `State` - 2 Buchstaben: `CA`, `TX`, etc.
- `Committees` - F2-Format-String (siehe oben / `csv_zielstruktur.md`)
- `ExOfficio` - `true` oder `false` (Mitglied hat ≥1 Ex-Officio-Position)

**Abdeckung**: alle 20 Standing + 2 Select Committees (Intelligence mit 6
Subs, China) + 4 Joint Committees (House-Hälfte; Senatoren kommen aus dem
Senate-XML). 26 Committees insgesamt.

**Verwendung**: Name-Matching in `utils/name_matcher.py` → Anreicherung in `scripts/import_trades.py`

---

### `data/mappings/senate_member&(sub)committees.csv`
**Quelle**: `scrapers/senate_committee_xml/parse_senate_xml.py`
(XML→F2-CSV aus `scrapers/senate_committee_xml/raw/*.xml`)
**Format**: CSV
**Zeilen**: 101 (100 Senatoren + Header)
**Update**: Bei neuen Wahlen (alle 2 Jahre) oder Committee-Umstrukturierungen —
zuerst `fetch_senate_xml.py` (neue XML-Snapshots), dann `parse_senate_xml.py
[out_pfad]`

**Spalten**: Gleiche Struktur wie House CSV (F2-Format)

**Unterschiede zu House**:
- Committee-Namen beginnen oft mit "Committee on ..." (wird normalisiert,
  s. `config/committee_mapping.py::_normalize`)
- Ausführlichere Subcommittee-Namen
- Name-Format: „Last, First"

**Verwendung**: Gleich wie House CSV

---

### `scrapers/senate_committee_xml/raw/*.xml` + `_manifest.json`
**Quelle**: `scrapers/senate_committee_xml/fetch_senate_xml.py` — dynamische
Committee-Discovery aus dem senate.gov-Dropdown (kein Hardcoding), lädt pro
Committee `committee_memberships_<CODE>.xml` via curl_cffi
(Chrome-Impersonation gegen Akamai-403)
**Format**: XML-Snapshots (26 Dateien), **committet** für Determinismus
**Inhalt `_manifest.json`**: Snapshot-Metadaten + Change-Report (+NEU/−ENTFALLEN
gegenüber vorherigem Snapshot)
**Verwendung**: Input für `parse_senate_xml.py` → `senate_member&(sub)committees.csv`

---

### `data/processed/committees_structure.csv` (gitignored)
**Quelle**: `scrapers/generate_committee_structure.py`
**Spalten**: `Chamber, Committee, Subcommittee`
**Inhalt**: Strukturliste (Committee/Subcommittee-Paare) aus beiden Roster-
CSVs (House + Senate), für Validierung/Vollständigkeitsprüfung.
**Update**: Nach jeder Roster-Neuerzeugung

---

### `data/mappings/us_house_all_committees.csv` / `us_senate_all_committees.csv` (untracked)
**Quelle**: Manuell/extern erstelltes Struktur-Orakel (offizielle
Committee/Subcommittee-Listen) für die Validierung der Parser-Outputs gegen
`committees_structure.csv`.
**Verwendung**: Validierungslauf `scrapers/house_pdf_parsing_tool/validate_house.py`
(House: 0 Abweichungen bei 26 Committees, Joint-Headcounts handverifiziert;
Senate: 70/70 Sub-Paare, Stand 2026-06-12).

---

## 3. Committee → Sektor Mapping

### `data/raw/subcommittee_sector_industry_mapping_v3.xlsx`
**Quelle**: Manuell erstellt (Mapping-Projekt)
**Format**: Excel (.xlsx) mit 6 Sheets
**Größe**: 76 KB
**Update**: Bei Bedarf (neue Committees, Korrekturen)

**Sheets**:

#### Sheet 1: Overview (180 Zeilen)
Breite Ansicht: Ein Subcommittee pro Zeile, bis zu 6 Sektoren in Spalten

**Spalten**:
```
Chamber, Committee, Subcommittee,
Sektor_1, Industries_1, Sektor_2, Industries_2, ..., Sektor_6, Industries_6,
Region_Relevance, Konfidenz, Notiz, Bemerkung
```

#### Sheet 2: Industry_Mapping (861 Zeilen) ⭐ **HAUPTSHEET**
Normalisierte Ansicht: Ein Sektor/Industry pro Zeile

**Spalten**:
```csv
Chamber,Committee,Subcommittee,Sector,Industry,Sector_Rank,Konfidenz
```

**Beispiel**:
```csv
House,Agriculture,"Commodity Markets, Digital Assets, And Rural Development",Financial Services,Capital Markets,1,Hoch
House,Agriculture,"Commodity Markets, Digital Assets, And Rural Development",Financial Services,Financial Data & Stock Exchanges,1,Hoch
House,Agriculture,"Commodity Markets, Digital Assets, And Rural Development",Technology,Software - Application,2,Hoch
```

**Sector_Rank** (wichtig für Scoring):
- `1` = Primärer Sektor (höchste Relevanz) → 100% Gewichtung
- `2` = Sekundärer Sektor → 75% Gewichtung
- `3` = Tertiärer Sektor → 50% Gewichtung
- `4+` = Weitere Sektoren → 25% Gewichtung

**Konfidenz**:
- `Hoch` = High confidence (1.0x Multiplier)
- `Mittel` = Medium confidence (0.7x Multiplier)
- `Niedrig` = Low confidence (0.4x Multiplier)

**Verwendung**: Wird von `config/committee_mapping.py` geladen → Lookup in `tags/committee_tag.py`

#### Sheet 3: Region_Mapping (238 Zeilen)
Geografische Relevanz von Committees

#### Sheet 4: Korrekturen (36 Zeilen)
Audit Trail für Mapping-Änderungen

#### Sheet 5: Statistik (67 Zeilen)
Validierungsstatistiken

#### Sheet 6: Manuelle Prüfung (6 Zeilen)
Foreign Affairs Committees mit schwacher Sektor-Zuordnung

---

### `data/raw/subcommittee_sector_industry_mapping.csv` ⭐
**Quelle**: Automatisch generiert aus xlsx (Industry_Mapping Sheet)
**Format**: CSV
**Größe**: 99 KB
**Zeilen**: 862 (861 Mappings + Header)
**Status**: geschützter Produktionsmaster; der frühere Generator wurde entfernt. Eine Neugenerierung erfolgt erst nach separater Freigabe der geplanten V2-Integration (siehe Abschnitt 3a).

**Spalten**: Gleich wie Industry_Mapping Sheet (siehe oben)

**Verwendung**: **EMPFOHLEN** für Code (schneller als xlsx)
Wird von `CommitteeMapper` geladen.

---

## 3a. Committee Matcher V2 (in Arbeit)

Der frühere Committee-Mapping-Agent unter `scripts/committee_mapping_agent/` wurde
vollständig entfernt. Der bestehende Produktionsmaster
`data/mappings/subcommittee_sector_industry_mapping.csv` bleibt bis zur späteren,
separat freizugebenden Integration von V2 unverändert in Benutzung.

Verbindlicher Entscheidungsstand und Implementierungsreihenfolge stehen in
`committee_matcher_planung_entscheidungen_codex.md` und
`committee_matcher_v2_implementierungsplan.md`. Der Mappingkern selbst ist noch
nicht implementiert; verbindlich sind bereits die Inputartefakte aus P0
(Inputbaseline) sowie die in P3 eingefrorenen Unit-IDs.

### `data/committee_matcher_inputs/md/*.md` (46 Mapping-Inputs + 2 Sammelquellen)

Kanonische Committee-Jurisdiktionstexte im Format aus
`committee_jurisdiction_input_template_codex.md`. Nach der P0-Migration
(`input-basis-v1`) traegt jede der 46 Mapping-MDs ein `unit_input_basis` im
Frontmatter; die alten `input_basis:`-Felder (12) und HTML-Ersatzmarker (8) sind
entfernt.

| Frontmatter-Feld | Typ | Bedeutung |
|---|---|---|
| `schema_version` | int | Version des Inputvertrags (aktuell 1) |
| `congress` | int | Kongressnummer (119) |
| `chamber` | enum | `house` \| `senate` \| `joint`; muss zum Dateinamenpraefix passen |
| `committee` | str | Offizieller Committee-Name (Identitaet) |
| `committee_type` | str | z. B. `standing`, `select`, `joint` |
| `source_url` | str \| null | Quell-URL; bei `pending` zulaessig `null`, wird nie erfunden |
| `retrieved_at` | str \| null | Abrufzeit (ISO-8601) oder `null` |
| `source_metadata_status` | enum | `complete` \| `pending`; `complete` verlangt URL **und** Abrufzeit |
| `source_files` | list[str] | Referenzen auf manifestierte `raw_source`/`source_only`-Dateien |
| `unit_input_basis.committee` | enum | immer `jurisdiction_text` |
| `unit_input_basis.subcommittees` | map | H2-Name → `jurisdiction_text` \| `name_only`; Keys exakt gleich der H2-Menge ohne Kommentarabschnitt, ausnahmslos doppelt gequotet |

Body: genau eine H1 `# Committee jurisdiction`, je Subcommittee ein
`## <offizieller Name>`, optional H3-Untergliederungen im Jurisdiktionsabschnitt
und als letzter Abschnitt `## Zusätzliche Kommentare` — dieser ist reserviert und
wird nie Mapping-Unit oder Promptkontext.

Abgeleitete Unit-Sollmenge (eingefroren): **179** `subcommittee` + **31**
`committee_parent_core` + **15** `committee_direct` = **225**; davon 8 Units
`name_only`. Die kanonischen IDs dazu stehen seit P3 in
`data/config/committee_matcher_unit_ids_v1.yaml`.

### `data/committee_matcher_inputs/corpus_manifest.yaml`

Rollen- und Hashmanifest jeder regulaeren Datei unter dem Inputroot; erzeugt von
`scripts/committee_matcher_baseline.py manifest --write`, nicht von Hand pflegen.
Das Manifest ist Kontrollmetadata und deshalb selbst kein Corpusmitglied.

| Feld | Typ | Bedeutung |
|---|---|---|
| `manifest_version` | str | `corpus-manifest-v1` |
| `migration_version` | str | Version der Inputmigration (`input-basis-v1`) |
| `input_root` | str | Projektrelativer Inputroot |
| `role_counts` | map | `mapping_input=46`, `source_only=2`, `raw_source=46`, `administrative_excluded=1` |
| `unit_counts` | map | Eingefrorene Unit-Sollmenge (s. oben) |
| `files[].path` | str | Inputroot-relativer Pfad |
| `files[].role` | enum | Corpusrolle; nur `mapping_input` erzeugt Units |
| `files[].bytes` | int | Dateigroesse |
| `files[].sha256` | str | SHA-256 der exakten Dateibytes (Post-Migrations-Stand) |

`source_files` darf ausschliesslich Dateien der Rollen `raw_source` und
`source_only` referenzieren; alle 46 TXTs und beide Sammel-MDs sind mindestens
einmal referenziert.

### `data/config/committee_matcher_protected_baseline.yaml`

SHA-256-Baseline der neun matcherbezogenen Produktionspfade, die V2
ausschliesslich liest (Produktionsmaster + XLSX, House-/Senate-Roster,
yfinance-Mappingdateien, Committee-Listen). Erzeugt von
`scripts/committee_matcher_baseline.py protect --write`; der Schutztest in
`tests/test_committee_matcher_input_baseline.py` prueft die Unveraendertheit.
Die Liste ist matcherbezogen und **nicht** aus der globalen
`reset_pipeline.py`-Schutzliste abgeleitet.

| Feld | Typ | Bedeutung |
|---|---|---|
| `baseline_version` | str | `protected-paths-v1` |
| `scope` | str | `committee_matcher_v2` |
| `paths[].path` | str | Projektrelativer Pfad |
| `paths[].bytes` | int | Dateigroesse |
| `paths[].sha256` | str | SHA-256 zum Zeitpunkt der Baseline |

Beide YAML-Artefakte enthalten bewusst keine Zeitstempel; `--write` ist damit
reproduzierbar und die Provenienz laeuft ueber Git.

### `data/config/committee_matcher_unit_ids_v1.yaml` (P3, eingefroren)

Die 225 kanonischen Unit-IDs als Sollmenge. `input validate` vergleicht die aus
dem Corpus gebildeten IDs mengengleich dagegen; jede Abweichung blockiert
(`UNIT_MISSING`, `UNIT_UNEXPECTED`). Erzeugt von
`scripts/committee_matcher_baseline.py units --write`.

| Feld | Typ | Bedeutung |
|---|---|---|
| `canonicalization_version` | str | `unit-id-v1`; Regeländerung erfordert neue Version |
| `mapping_input_version` | str | `mapping-input-v1` (Hashvertrag des Modellinputs) |
| `unit_count` | int | 225 |
| `unit_counts` | map | Aufschlüsselung nach `kind` |
| `units[].unit_id` | str | `<kammer>--<committee-slug>[--<subcommittee-slug>]` |
| `units[].kind` | enum | `subcommittee` \| `committee_parent_core` \| `committee_direct` |
| `units[].chamber` | enum | `house` \| `senate` \| `joint` |

Die Kanonisierungsregeln selbst sind in `CLI_Bedienungsanleitung.md` 15a
beschrieben und durch Golden-Tests eingefroren.

### `data/config/committee_matcher_roster_rules.yaml` (P3)

Explizite Zuordnungsregeln für `roster validate`; ohne Eintrag wird
ausschließlich kanonisch gematcht. Inhaltlich noch leer — die drei offenen
Rostervorkommen entscheidet Gate C.

| Feld | Typ | Bedeutung |
|---|---|---|
| `rules_version` | str | `roster-rules-v1` |
| `aliases[].chamber` / `.committee` / `.subcommittee` | str | Rosterschreibweise |
| `aliases[].unit_id` | str | Ziel-Unit; muss existieren |
| `aliases[].reason` | str | Pflicht; ohne Begründung blockiert der Lauf |
| `exclusions[].chamber` / `.committee` / `.subcommittee` | str | Vorkommen ohne Unit |
| `exclusions[].reason` | str | Pflicht |

### `data/committee_matcher_v2/` (reservierte Artefaktwurzel)

Einzige Schreibwurzel des Matchers. Ab P4 entstehen darunter unveränderliche
Run-, Snapshot-, Review-, Evaluations- und Qualifikationsrevisionen; die
Feldlisten der Long-CSV und des Review-Workbooks werden erst mit den Artefakten
in P7/P8 eingefroren.

Der Schreibschutz ist technisch durchgesetzt (`committee_matcher/paths.py`):
Schreiben außerhalb dieser Wurzel, in die Inputbaseline oder in einen der neun
Pfade der P0-Schutzbaseline blockiert mit Exit 4; ebenso ein Symlink- oder
Hardlink-Schreibziel. Fehlt die Schutzbaseline, ist sie kaputt oder trägt sie
falsche Kopffelder (`baseline_version`, `scope`) beziehungsweise eine leere
Pfadliste, blockiert die Wache ebenfalls (fail closed). Geschrieben wird
ausschließlich über `write_text_atomic` (Temporärdatei im Zielverzeichnis,
`O_NOFOLLOW`, `os.replace`).

#### `data/committee_matcher_v2/runs/<run_id>/` (P4, je Runrevision)

| Datei | Zweck |
|---|---|
| `taxonomy_snapshot.json` | eingefrorene Taxonomie des Runs; nur vollständig und kollisionsfrei geschrieben |
| `taxonomy_catalog.md` | nach Sektoren gruppierter Markdown-Katalog desselben Inhalts |
| `taxonomy_checkpoint.json` | volatiler Abruffortschritt für `snapshot resume`; an die eigene `run_id` gebunden, nie freigabefähig; enthält nur Beschreibungen, die Identität holt auch der Resume-Lauf frisch |

`taxonomy_snapshot.json`:

| Feld | Typ | Bedeutung |
|---|---|---|
| `content_version` | str | `taxonomy-content-v1` |
| `snapshot_id` | str | Run-Revision, an die der Snapshot gebunden ist |
| `yfinance_version` | str | Version der Abrufbibliothek (gepinnt `1.3.0`) |
| `taxonomy_content_hash` | str | SHA-256 über die sortierten Industries, ohne Abrufzeiten |
| `industries[].industry_id` / `.industry_name` | str | Identität der Industry |
| `industries[].sector_id` / `.sector_name` | str | Sektorzuordnung |
| `industries[].description` | str | Beschreibung als fachliche Mappinggrundlage |

Börsengrößen (Market Cap, Market Weight, Firmen- und Mitarbeiterzahlen, Symbole)
liefert yfinance mit, sie werden aber am Adapterrand verworfen und tauchen in
keinem Artefakt auf — eine Zuordnung soll fachliche Nähe belegen, nicht
Marktgröße.

#### `data/committee_matcher_v2/runs/<run_id>/mapping/` (P7a, je Runrevision)

| Datei | Zweck |
|---|---|
| `run_manifest.json` | eingefrorene Laufbedingungen; Grundlage der Resume-Prüfung |
| `run_state.json` | Workflowstatus plus Stand je Unit; Grundlage von Cache und Resume |
| `calls/<call_id>.json` | Provenienz und rohe Antwortnutzlast eines Providercalls |
| `calls/<call_id>.intent.json` | Vormerkung vor dem Senden; bleibt liegen, solange der Call keine Auditdatei hat |

`run_manifest.json` (`version: run-manifest-v1`):

| Feld | Typ | Bedeutung |
|---|---|---|
| `run_id` / `snapshot_id` | str | Run- und Snapshotrevision |
| `input_manifest_hash` / `taxonomy_content_hash` | str | fachliche Bindung an Input und Taxonomie |
| `unit_count` | int | Sollmenge der Units dieses Laufs |
| `model` | str | eingefrorenes Modell |
| `routing` | obj | `require_parameters`, `allow_fallbacks`, `quantizations`, `data_collection`, `zdr`, `max_price_usd_per_mtok`, `sdk_retries`, `max_concurrent_calls` |
| `context` | obj | `endpoint_context_tokens`, `max_output_tokens`, `reasoning_reserve_tokens`, `request_timeout_s` |
| `batching` | obj | `batch_size`, `max_repair_attempts`, `chars_per_token`, `workflow_retries` |
| `prompts[]` | obj | `prompt_id`, `prompt_version`, `schema_version`, `prompt_sha256`, `path` |
| `schema` | obj | `schema_name`, `schema_sha256` |
| `cache_key_version` | str | `cache-key-v1` |
| `budget_usd` / `cost_guard` | float / bool | Kostendeckel des Laufs und Zustand der Wache |

Kein Prompttext und kein Zugangsdatum: das Manifest wird versioniert abgelegt
und ist damit so öffentlich wie das Repository; der Prompt ist über
`prompt_sha256` eindeutig belegt.

`run_state.json` (`version: run-state-v2`): `run_id`, `workflow_state` sowie je
Unit `unit_id`, `status` (`not_decided | mapped | no_mapping | failed`),
`cache_key`, das vollständig geprüfte `result`, die `violations`-Codes eines
Fehlschlags und die Callprovenienz `call_id`, `provider`, `generation_id`,
`from_repair`. Die Provenienz gehört zum Urteil, nicht zum Lauf: nur so trägt
auch ein Cache-Treffer aus einer früheren Runde seine Herkunft in die Artefakte.
Ein Zustand der Vorversion `run-state-v1` blockiert mit `RUN_STATE_MALFORMED`
(Exit 4) und wird nicht überschrieben. Workflowstatus und Unitstatus bleiben getrennt — ein Lauf in
`outcome_unknown` fällt nie in ein Mappingurteil zurück.

`calls/<call_id>.json` (`version: call-audit-v1`): `call_id`, `model`,
`provider`, `prompt_tokens`, `completion_tokens`, `cost_usd`, `finish_reason`,
`generation_id`, `schema_name`, `max_output_tokens` und die rohe `payload`.
Geschrieben **vor** der fachlichen Prüfung. Systemprompt und Nutzdaten stehen
bewusst nicht darin.

`calls/<call_id>.intent.json` (`version: call-intent-v1`) entsteht **vor** dem
Netzaufruf mit `run_id`, `call_id`, `schema_name` und `worst_case_usd`. Sie wird
gelöscht, sobald die Auditdatei steht oder feststeht, dass OpenRouter die
Anfrage abgewiesen hat. Bleibt sie liegen, nimmt sie auf, was bekannt ist
(`reason`, `generation_id`, `cost_usd`) — sie ist die einzige Spur, die einen
bezahlten Call nach einem Prozessabsturz noch belegt. `workflow.open_intents`
liefert diese Call-IDs. Eine Call-ID, die bereits eine Auditdatei trägt, wird
nie ein zweites Mal gesendet (`CALL_ID_REUSED`, Exit 4); je physischem Versuch
gehört eine eigene ID.

Ergebnisartefakte desselben Ordners (P7b, geschrieben nach dem Lauf):

| Datei | Zweck |
|---|---|
| `assignments_long.csv` | eine Zeile je Unit-Industry-Zuordnung |
| `unit_coverage.csv` | jede erwartete Unit genau einmal mit ihrem fachlichen Stand |
| `boundary_conflicts.csv` | gleiche Industry in Geschwister-Units; Reviewposten, kein Fehler |
| `roster_coverage.csv` | Roster-Coverage des Laufs; Pflicht, je Rostervorkommen genau eine Zeile |
| `cost_report.json` | Kostenstand und die Calls mit offenem Ausgang |
| `artifacts_manifest.json` | Abschlussmarker; erst er belegt einen vollständigen Satz |

`assignments_long.csv` (Spalten in dieser Reihenfolge, durch Golden Test
gebunden): `run_id`, `revision_id`, `unit_id`, `chamber`, `committee_name`,
`unit_name`, `unit_kind`, `input_basis`, `mapping_input_hash`, `decision`,
`broad_scope_exception`, `industry_id`, `industry_name`, `sector_id`,
`sector_name`, `confidence`, `evidence_excerpt`, `rationale_short`,
`assignment_source`, `broad_scope_reason`, `call_id`, `provider`,
`generation_id`, `from_repair`, `model`, `taxonomy_content_hash`, `snapshot_id`, `prompt_id`,
`prompt_version`, `prompt_sha256`, `schema_name`, `schema_sha256`.

Industry- und Sektornamen stammen aus dem Snapshot des Laufs, Modell- und
Promptangaben aus dem Runmanifest; das Modell liefert nur die `industry_id`.
`assignment_source` ist in P7b immer `model` — `manual` entsteht erst beim
Reviewimport. `no_mapping`-Units erzeugen hier keine Zeile und stehen
vollzählig in der Unit-Coverage.

`unit_coverage.csv`: `run_id`, `revision_id`, `unit_id`, `chamber`,
`committee_name`, `unit_name`, `unit_kind`, `status`
(`not_decided | mapped | no_mapping | failed`), `assignment_count`,
`no_mapping_reason`, `broad_scope_exception`, `broad_scope_reason`, `call_id`,
`provider`, `generation_id`, `from_repair`, `violations`. Eine Unit ohne Zustandseintrag ist
`not_decided`; mehrere Werte in einer Zelle trennt `|`. Enthält ein Wert selbst
ein `|`, blockiert `ART_SEPARATOR_IN_VALUE` — maskieren hieße, den Wert still zu
ändern.

`boundary_conflicts.csv`: `run_id`, `family_id`, `industry_id`, `industry_name`,
`unit_count`, `unit_ids`. `cost_report.json` (`version: cost-report-v1`):
`run_id`, `audited_calls`, `budget_usd`, `spent_usd`, `reserved_usd`,
`committed_usd`, `remaining_usd`, `calls`, `unknown_calls`, `guard_enabled`
sowie `unknown_calls_detail` je offenem Call.

Callprovenienz (`call_id`, `provider`, `generation_id`) ist bei jeder
entschiedenen Unit Pflicht — auch bei `no_mapping` —, `mapping_input_hash` bei
jeder Zuordnungszeile; sie stehen im Laufzustand je Unit und
überleben damit auch einen Cache-Treffer. `assignment_source` ist in P7b immer
`model`.

Das Schreiben ist ein Alles-oder-nichts. Alle Prüfungen und alle Zeilen entstehen
vor dem ersten Schreibvorgang; scheitert danach ein Schreibvorgang, werden die
bereits geschriebenen Dateien entfernt. Ein Prozessabbruch dazwischen lässt den
Abschlussmarker `artifacts_manifest.json` (`version: artifact-index-v1`) fehlen —
`artifacts.is_complete(run_id)` ist die einzige zulässige Vollständigkeitsprobe.

Blockierende Prüfungen, jeweils Exit 4: `ART_PROVENANCE_INCOMPLETE` (Manifest,
Inputhash oder Callprovenienz unvollständig), `ART_RUN_MISMATCH` (Manifest oder
Laufzustand gehört zu einem anderen Run), `ART_SNAPSHOT_MISMATCH` (fremde
Snapshotrevision oder fremder Content-Hash), `ART_UNIT_COUNT_MISMATCH` (Unitmenge
weicht vom Manifest ab), `ART_UNEXPECTED_UNIT`, `ART_DUPLICATE_UNIT`,
`ART_STATE_INCONSISTENT` (Status und Ergebnis widersprechen sich in einer der
beiden Richtungen), `ART_UNKNOWN_INDUSTRY`, `ART_SEPARATOR_IN_VALUE`,
`ART_ROSTER_MISSING`, `ART_ROSTER_INCOMPLETE` (weniger Zeilen als
Rostervorkommen), `ART_ROSTER_DUPLICATE`, `ART_COST_INCONSISTENT` (Bericht
und Kostenwache zählen verschieden viele Calls; jeder gebuchte Call ist
entweder auditiert oder vorgemerkt) und `ART_REVISION_EXISTS` (die
Runrevision trägt bereits einen **vollständigen** Satz; nur der ist
unveränderlich — Reste eines abgebrochenen Schreibvorgangs dürfen ersetzt
werden, sonst könnte derselbe Lauf seinen bezahlten Zustand nie exportieren).

#### `data/config/committee_matcher_prompts/` (P5, versionierter Kontrollinput)

Die beiden editierbaren Systemprompts `mapping_system.md` und
`repair_system.md` (UTF-8-Markdown mit YAML-Frontmatter). Pflichtfelder:
`prompt_id`, `prompt_version`, `schema_version`. Die `schema_version` bindet
den Prompt an den Ausgabevertrag des Codestands (`mapping-output-v1`); weicht
sie ab, blockiert der Lauf mit `PRM_SCHEMA_MISMATCH`. Der Inhaltshash (SHA-256)
deckt nur den Text ohne Frontmatter ab und wird zusammen mit der Version im
Runmanifest eingefroren.

#### `data/committee_matcher_v2/taxonomy/approved_baseline.json` (P4)

Die freigegebene Taxonomie-Baseline; erzeugt von `snapshot approve --run-id ...`.
Felder wie im Snapshot, zusätzlich `approved_run_id`. Fehlt sie, stoppt jeder
Snapshotlauf mit `no_baseline` in `awaiting_taxonomy_approval` (Exit 5). Jeder
Lauf rechnet ihren `taxonomy_content_hash` aus den Einträgen nach und prüft
deren Feldmenge; eine am Hash vorbei editierte Baseline blockiert mit
`TAX_BASELINE_TAMPERED`, weil sie sonst genau die Drift verdecken könnte, die
sie melden soll. `snapshot approve` prüft spiegelbildlich Inhaltsvertrag,
Feldmenge, Hash und die Bindung `snapshot_id == run_id`.

Bereits erzeugte Inhalte: `reports/<kommando>/errors.json` (strukturierter
Fehlerbericht je CLI-Lauf, Plan 2.7 — darunter `reports/provider_check/errors.json`
mit den Befunden des Routing-Preflights), `reports/input_validate/units.json` (alle
225 Units mit `unit_id`, `kind`, `input_basis`, `parent_unit_id`,
`mapping_input_hash`, `source_bytes_sha256`) und
`reports/roster_validate/roster_coverage.json` (je Rostervorkommen `chamber`,
`committee_name`, `unit_name`, `coverage_status`, `unit_id`, `match_basis`,
`hint`, `fuzzy_suggestion`, dazu die Liste `units_without_roster_occurrence`).

Versioniert ist von den Laufartefakten nur `taxonomy/approved_baseline.json` —
sie ist der Bezugspunkt der Drifterkennung und muss in jedem Klon identisch
vorliegen. `reports/` und `runs/` sind gitignoriert: beide entstehen bei jedem
CLI-Lauf neu, und der freigegebene Snapshotinhalt steht vollständig in der
Baseline, sodass eine zweite versionierte Kopie nur Duplikate erzeugen würde.

Das Paket `committee_matcher/` selbst hält seit P2 den providerfreien Kern:
Zustände und Exit-Codes (`status.py`), Befundsammlung und Terminalbericht
(`errors.py`), Pfadwache (`paths.py`), typisierte Settingssicht mit
Providergate (`settings_view.py`), Pydantic-Datenverträge (`models.py`) und das
CLI-Skelett (`cli.py`). Seit P3 kommen der strikte Inputparser
(`input_corpus.py`), die Unitbildung samt Kanonisierung (`units.py`) und die
Roster-Coverage (`roster_coverage.py`) hinzu, seit P4 der yfinance-Adapter
(`taxonomy_source.py`) und die Snapshotlogik mit Katalog, Content-Hash sowie
Drift- und Freigabegate (`taxonomy_snapshot.py`). Mit dem Gate-B-Beschluss kommt
der Routing-Preflight (`provider.py`) hinzu: er prüft den öffentlichen
OpenRouter-Endpointkatalog auf einen zulässigen Endpoint, ohne Key, ohne Kosten
und ohne Modellaufruf. Seit P5 hält dasselbe Modul zusätzlich das
providerneutrale Structured-Output-Interface, den `FakeProvider` für
netzwerkfreie Tests und den OpenRouter-Adapter als einzigen Ort mit SDK-Kontakt;
`prompts.py` lädt und prüft die beiden Systemprompts. Seit P6 kommt der
Mappingkern hinzu: `mapping.py` (Ausgabeschema, Batching mit Context-Split,
Requestbau, Ausführung und gezielter Repair) und `validation.py` (formale
Prüfung gegen Vertrag, Unit und Snapshot; Verstoßliste und Boundary-Erkennung).
Die Ownership-Karte aller 18
Zielmodule steht im Docstring von `committee_matcher/__init__.py`. Bedienung, Familien, Exit-Codes
und die vollständige Settingstabelle: `CLI_Bedienungsanleitung.md` Abschnitt 15.

## 4. Yahoo Finance Sektoren

### `data/raw/yf_secotors_with_industries.xlsx`
**Quelle**: Yahoo Finance Sektor-Klassifikation
**Format**: Excel (.xlsx)
**Größe**: 16 KB
**Zeilen**: 167

**Struktur**: Hierarchisch organisiert

**Spalten**:
```csv
Industry,Market Weight
```

**Format**:
- Sektoren haben `100.00%` Market Weight (z.B. "Technology")
- Industries darunter haben prozentuale Anteile (z.B. "Semiconductors, 36.04%")

**Beispiel**:
```
Technology,100.00%
Semiconductors,36.04%
Software - Infrastructure,21.59%
Consumer Electronics,16.90%
...
Financial Services,100.00%
Banks - Diversified,22.88%
Credit Services,14.38%
...
```

**Inhalt**:
- 11 Haupt-Sektoren
- ~176 Industries

---

### `data/raw/yf_sectors_with_industries.csv` (veraltet)
**Status**: Datei existiert nicht mehr. Ersetzt durch JSON-Version.

### `data/mappings/yf_sectors_with_industries.json` ⭐
**Quelle**: Yahoo Finance (validiert via `yf.Sector()` API)
**Format**: JSON (hierarchisch: Sector → Industries)
**Inhalt**: 11 Sektoren, 145 Industries
**Control-Datei**: Alle Industry-Namen im Projekt muessen mit dieser Datei uebereinstimmen.

**Struktur**:
```json
{"sectors": [
  {"sector": "Technology", "industries": ["Semiconductors", "Software - Infrastructure", ...]},
  {"sector": "Financial Services", "industries": ["Banks - Diversified", ...]}
]}
```

### `data/mappings/yf_sectors_with_industries.xlsx`
**Quelle**: Automatisch generiert aus JSON
**Format**: Excel (flache Struktur: Sector, Industry)
**Zeilen**: 145

### `data/mappings/yf_sectors_with_industries_enriched.json`
**Quelle**: historisches V1-Artefakt; der zugehörige Generator wurde entfernt
**Format**: JSON (hierarchisch: Sector → Industries, wie die Quelle, zusaetzlich je Sektor/Industry eine deutsche Kurzbeschreibung)
**Inhalt**: identische Mengen wie `yf_sectors_with_industries.json` (11 Sektoren, 145 Industries) + `description`-Feld je Sektor/Industry

**Struktur**:
```json
{"sectors": [
  {"sector": "Technology", "description": "...", "industries": [
    {"industry": "Semiconductors", "description": "..."},
    ...
  ]}
]}
```

**Verwendung**: derzeit keine aktive Generator-Nutzung. Die Datei bleibt als geschütztes historisches Datenartefakt erhalten; V2 bezieht seine Taxonomie später aus einem frischen, run-lokal eingefrorenen yfinance-Snapshot.

### `data/mappings/ticker_industry_cache.json`
**Quelle**: Automatisch von `scripts/import_trades.py` (yfinance API)
**Format**: JSON (Ticker → {sector, industry})
**Update**: Bei jedem Import (nur neue Ticker werden abgefragt)

**Struktur**:
```json
{
  "AAPL": {"sector": "Technology", "industry": "Consumer Electronics", "quoteType": "EQUITY"},
  "SPY": {"sector": "", "industry": "", "quoteType": "ETF"},
  "VFIAX": {"sector": "", "industry": "", "quoteType": "MUTUALFUND"}
}
```

**Hinweis**: Enthaelt positive Eintraege — Ticker mit Sector/Industry (inkl. ETFs, Fonds). Null-Eintraege sind in `ticker_0_cache.json` separiert.

**Verwendung**: Cache fuer Ticker-Industry Lookup (Sektor/Industrie) in der Import-Pipeline. Erstlauf ~42 Min (5000+ Ticker), danach nur neue Ticker. Wird NICHT mehr fuer ticker_type verwendet (siehe `data/cache/ticker_type_cache.db`).

### `data/mappings/ticker_0_cache.json`
**Quelle**: Automatisch von `scripts/import_trades.py` (yfinance API + Pre-Filter)
**Format**: JSON (sortierte Liste von Ticker-Strings)
**Update**: Bei jedem Import

**Struktur**:
```json
[
  "07/01/28",
  "AABA",
  "BACWSA",
  "SPY"
]
```

**Hinweis**: Enthaelt Ticker fuer die yfinance kein Sector/Industry liefert (ETFs, delistete Aktien, Fonds) PLUS Ticker die vom Pre-Filter als Non-Standard erkannt wurden (Warrants, CUSIPs, Bonds, Auslandsnotierungen etc.) und gar nicht erst bei yfinance abgefragt werden. Pre-Filter-Patterns konfigurierbar in `config/settings.py` (`TICKER_FILTER_PATTERNS`).

**Verwendung**: Verhindert wiederholte yfinance-API-Calls fuer bekannt unresolvbare Ticker (Sektor/Industrie). Wird NICHT fuer ticker_type verwendet.

### `data/cache/ticker_type_cache.db`
**Quelle**: Automatisch von `scripts/import_trades.py`. Enthaelt zwei Tabellen parallel:
- `ticker_types` (Legacy, via `utils/openfigi_client.py` — nicht mehr aktiv genutzt)
- `ticker_normalizer` (Rebuild Teil 2/3, via `utils/ticker_normalizer.py` — aktive Pipeline)
**Format**: SQLite-Datenbank
**Update**: Bei jedem Import (nur Cache-Misses werden bei der API abgefragt)

**Schema (aktiv) — `ticker_normalizer`** (seit 2026-05-14: `_figi`-Suffix):
```sql
CREATE TABLE ticker_normalizer (
    cache_key           TEXT PRIMARY KEY,   -- "<ticker_base>:<id_type>:<exch_code|*>"
    classification_type TEXT NOT NULL,      -- "TICKER" | "SEARCH" | "ID_CUSIP"
    figi                TEXT,               -- FIGI (Bloomberg, z.B. "BBG000B9XRY4")
    ticker_figi         TEXT,               -- Canonical Ticker laut OpenFIGI
    asset_name_figi           TEXT,               -- Canonical Name laut OpenFIGI
    security_type_figi  TEXT,               -- OpenFIGI securityType
    raw_security_type   TEXT,               -- OpenFIGI securityType (Roh-Spiegel)
    raw_security_type2  TEXT,               -- OpenFIGI securityType2 (z.B. "ETP" → Type-Korrektur)
    market_sector_figi  TEXT,               -- OpenFIGI marketSector (z.B. "Equity", "Pfd", "Muni")
    source              TEXT NOT NULL,      -- "mapping" | "search" | "search_empty" | "empty"
    checked_date        TEXT NOT NULL       -- ISO-Zeitstempel
);
```

**Schema (Legacy) — `ticker_types`**:
```sql
CREATE TABLE ticker_types (
    ticker TEXT PRIMARY KEY,        -- Ticker-Symbol (z.B. "SPY", "AAPL")
    security_type TEXT NOT NULL,    -- Interner Typ: "Stock", "ETF", "Mutual Fund" etc.
    openfigi_raw_type TEXT,         -- OpenFIGI securityType (z.B. "ETP", "Common Stock")
    openfigi_raw_type2 TEXT,        -- OpenFIGI securityType2
    openfigi_market_sector TEXT,    -- OpenFIGI marketSector (z.B. "Equity", "Corp")
    source TEXT NOT NULL,           -- Herkunft: "openfigi", "regex", "whitelist", "fallback", "error"
    checked_date TEXT NOT NULL,     -- ISO-Datum des letzten Lookups
    figi TEXT                       -- FIGI-Nummer (Bloomberg-Kennung)
);
```

**TTL**: Normale Eintraege 90 Tage, "Unknown"/"empty" 7 Tage. Bei Offline: alle Cache-Eintraege gueltig.

**Verwendung**: `ticker_normalizer` ist die aktive Quelle der OpenFIGI-Felder im Master (Spalten `figi`, `ticker_figi`, `asset_name_figi`, `security_type_figi`, `market_sector_figi`). `ticker_types` ist Legacy und wird nicht mehr beschrieben.

### `data/cache/price_history.parquet`
**Quelle**: Automatisch von `scoring/post_trade_returns.py` (`compute_post_trade_returns`) via yfinance.
**Format**: Parquet (Pandas MultiIndex)
**Update**: Bei jedem Import; Cache-TTL = 1 Tag (`POST_TRADE_PRICE_CACHE_TTL_DAYS = 1`, seit 2026-06-16). Nie gecachte Ticker werden immer geladen; bei stale-Tickern greift das Refetch-Fenster `POST_TRADE_REFRESH_WINDOW_DAYS = 384` (= größter Horizont 365 + 19 Puffer; nur Positionen ≤ 384d alt). Details: `organisation/Preis_Aktualisierung.md` §3.
**Atomare Speicherung**: tmp + rename verhindert Korruption bei Abbruch.

**Struktur**:
```
MultiIndex: (ticker: str, date: datetime64[ns])
Spalte:     close: float64
```

**Beispiel**:
```
ticker    date
AAPL      2025-01-02    243.85
          2025-01-03    243.04
MSFT      2025-01-02    421.10
          2025-01-03    423.35
```

**Marker-Konvention fuer delistete Ticker** (Schema seit 2026-06-14, Netz-Robustheit-Haertung):
```
ticker    date
AAPL      2026-06-14    NaN
```
Ein Eintrag mit `close=NaN` signalisiert, dass yfinance fuer diesen Ticker keine Historie liefert
(delisted oder unbekannt). `date` = `last_checked_date` (Tag des letzten Checks, nicht mehr fix
`1900-01-01`). Erkennung erfolgt ueber **NaN-close** (nicht mehr ueber `date==1900-01-01`).
Verhindert wiederholte API-Calls, solange `last_checked_date` innerhalb
`POST_TRADE_DELISTED_RECHECK_DAYS` (30 Tage, `config/settings.py`) liegt. Alte Marker mit
`date='1900-01-01'` werden automatisch migriert (NaN-close wird weiterhin erkannt; `date < cutoff`
fuehrt zu einmaligem Re-Check). Sobald echte Kurse fuer den Ticker eintreffen, wird der NaN-Marker
physisch geprunt; je Ticker wird zudem nur der juengste NaN-Marker behalten (Marker-Kollaps).
Details und Begründung stehen in diesem Abschnitt.

**Verwendung**: Wird von Stage `post_trade_returns` (Pipeline Stage 9) gelesen/geschrieben. Alle yfinance-basierten Preisdaten (5 Jahre Daily Close via `POST_TRADE_PRICE_FETCH_YEARS`). Wird geladen bevor neue Ticker abgefragt werden (Cache-Hit-Erkennung); neue Daten werden appendiert und in einem Rutsch atomar gespeichert. **Incremental-Save**: alle 100 Ticker wird der Cache zwischenpersistiert (Crash-Schutz fuer 1-2h Erstabruf).

**Netz-Robustheit (seit 2026-06-14)**: Schreibvorgaenge in diesen Cache durchlaufen die
Variante-B-Fenster-State-Machine (`scoring/post_trade_returns.py`), abgesichert durch
`utils/network_canary.py` (NetworkCanary-Verdikt) + `utils/network_retry.py` (zentraler
Retry/Backoff). Die Modulübersicht und Datenverträge stehen in diesem Dokument.

---

## 5. House PDF Extractor (Hand-Delivered PTRs)

### `data/raw/house_scraper_fehler.csv`
**Quelle**: Output von `scrapers/house_scraper.py` (automatisch erzeugt)
**Format**: CSV
**Update**: Bei jedem House Scraper Lauf

**Spalten**:
```csv
doc_id, year, disclosure_date, name, ptr_link, reason
```

**Wichtige Werte für `reason`**:
- `empty_text` — PDF hat keinen Textlayer (gescanntes Papierformular) → Input für House PDF Extractor
- `download_error` — HTTP-Fehler beim PDF-Download (wird vom Extractor übersprungen)

**Verwendung**: Input-CSV für `scrapers/house_pdf_extractor.py`

### `logs/house_pdf_extractor_done.csv`
**Quelle**: Automatisch von `scrapers/house_pdf_extractor.py` erzeugt
**Format**: CSV
**Update**: Nach jedem Extractor-Lauf (auch bei 0 Trades)

**Spalten**:
```csv
doc_id, processed_at
```

**Verwendung**: Delta-Check — bereits verarbeitete DocIDs werden beim naechsten Lauf uebersprungen. `--no-delta` ignoriert diese Datei.

### `data/raw/senate_pdf_extractor_done.csv`
**Quelle**: Automatisch von `scrapers/senate_pdf_extractor.py` erzeugt
**Format**: CSV
**Update**: Nach jedem Extractor-Lauf (auch bei 0 Trades)

**Spalten**:
```csv
ptr_link, processed_at
```

**Verwendung**: Delta-Check — bereits verarbeitete ptr_links werden beim naechsten Lauf uebersprungen. `--no-delta` ignoriert diese Datei.

### `logs/ocr_uncertain.csv`
**Quelle**: `scrapers/house_pdf_extractor_v2.py` (readable=false) + `scripts/import_trades.py` (Datum-Validierung)
**Format**: CSV
**Update**: Nach jedem Extractor-Lauf / Import-Lauf (kumulativ)

**Spalten**:
```csv
name, ticker, asset_name, asset_type, transaction_type, amount, transaction_date,
owner, subholding, comment, disclosure_date, ptr_link, csv_upload_time,
source, rejection_reason
```

**source**: `"extractor"` (Vision API als unleserlich markiert) oder `"import"` (Datum-Validierung fehlgeschlagen)
**rejection_reason**: `"low_confidence"` | `"date_too_old"` | `"date_in_future"` | `"missing_date"`

**Verwendung**: Manuelle Pruefung von Trades die nicht automatisch verarbeitet werden konnten. Trades in dieser Datei sind NICHT in house_trades.csv bzw. trades_master.csv enthalten.

---

### `data/debug/mccaul_verification/` *(Verifikations-Artefakte)*
**Quelle**: Manuell erzeugt für QA-Zwecke
**Inhalt**:
- `page_01_rotated.png` bis `page_09.png` — Seitenbilder (mit Rotation korrigiert, 200 DPI)
- `trades_extracted.csv` — Extrahierte Trades für manuellen Abgleich

**Verwendung**: Manueller Vergleich extrahierter Trades gegen Originalseiten (QA)

---

## 5b. House PDF Extractor v2 (Grid-basiert)

### `scrapers/house_pdf_extractor_v2.py`
**Quelle**: Eigenentwicklung (OpenCV + Anthropic Vision API)
**Zweck**: Verbesserte OCR für gescannte House-PTR-PDFs — löst Merge-, Truncation- und Amount-NaN-Probleme von v1
**Input**: `data/raw/house_scraper_fehler.csv` (reason="empty_text")
**Output**: Ergänzt `data/raw/house_trades.csv`

**Architektur (3-stufig):**
1. **OpenCV Grid-Detection** — Morphologische Linienerkennung (horizontal/vertical Kernels) auf Raw-Graustufenbild → Zeilen- und Spalten-Bounding-Boxes
2. **Pixel-Density Amount-Detection** — Zählt dunkle Pixel pro Zelle in den letzten 11 Spalten (A-K Checkboxen). Zelle mit meisten Pixeln = markierte Checkbox. Kein OCR/LLM nötig.
3. **Vision API pro 5-Zeilen-Batch** — Nur für Text-Felder (Owner, Asset, Tx Type, Dates). Vereinfachter Prompt ohne Amount-Extraktion.

**Fallback**: Wenn Grid nicht erkannt wird (< 3 H-Lines oder < 3 V-Lines) → ganzes Seitenbild an Vision API (wie v1). Wird im Terminal geloggt.

**Unterstützte Formulartypen:**
| Typ | Beschreibung | Beispiel |
|-----|-------------|---------|
| A | Rotiert, handschriftlich, 1 Trade/Seite | Bilirakis, Wagner |
| B | Landscape Tabelle, getippt, ~25 Trades/Seite, manchmal 90° gedreht | McCaul |
| C | Hybrid rotiert getippt, ~7 Trades/Seite | Fleischmann |
| D | Broker Statement, rotiert, ~15-20 Trades/Seite | Khanna |

**Konfiguration** (in `config/settings.py`):
- `PDF_EXTRACTOR_DPI = 300` — Render-Auflösung
- `PDF_EXTRACTOR_AMOUNT_PIXEL_THRESHOLD = 50` — Min. dunkle Pixel für Checkbox-Erkennung
- `PDF_EXTRACTOR_USE_GRID_DETECTION = True` — Grid-Modus an/aus
- `PDF_EXTRACTOR_ROW_BATCH_SIZE = 5` — Zeilen pro Vision-API-Call

**CLI** (gleiche Optionen wie v1):
```bash
python scrapers/house_pdf_extractor_v2.py                              # Alle fehler-PDFs
python scrapers/house_pdf_extractor_v2.py --doc-id 8221228 --year 2025 # Einzelne PDF
python scrapers/house_pdf_extractor_v2.py --dry-run                    # Nicht speichern
python scrapers/house_pdf_extractor_v2.py --model claude-sonnet-4-6    # Anderes Modell
```

**Dependencies**: `opencv-python-headless`, `numpy`, `pdfplumber`, `Pillow`

**Fehlerraten-Vergleich v1 vs v2:**
| Metrik | v1 (Vision API) | v2 (Grid + Pixel) |
|--------|----------------|-------------------|
| Amount NaN (Typ D) | 37.8% | ~0.5% |
| Amount NaN (Typ B) | 12.2% | ~0% |
| Merged Names | 65 | 0 |
| Truncated Names | 23 | 0 |

---

## 6. Processed Data (Output)

### `data/processed/trades_master.csv` (kuratierte Sicht)
**Quelle**: Output von `scripts/import_trades.py` (Stage `save`)
**Format**: CSV
**Sortierung**: Absteigend nach `transaction_date` (NaT ans Ende); erbt Reihenfolge aus `trades_master_complete.csv` — s. dort.
**Update**: Bei jedem Import (kumulativ; vollstaendige Spalten in `trades_master_complete.csv`)

**Hinweis (Rebuild Teil 3, 26.04.2026)**: `trades_master.csv` enthaelt jetzt
nur die **kuratierten Spalten** aus `settings.CURATED_COLUMNS`. Die
vollstaendige Spalten-Sicht liegt in `trades_master_complete.csv`. Der
Wechsel passiert beim Speichern automatisch — fuer Stage-Inputs liest
die Pipeline immer aus `trades_master_complete.csv`.

**Kuratierte Spalten** (Default in `settings.CURATED_COLUMNS`, vom
User erweiterbar):
- `trade_id` — 16-Hex Hash-ID (utils/trade_id.compute_trade_ids)
- `name`, `ticker`, `issuer_ticker`, `issuer_group_key`, `ticker_type`, `asset_name`
- `transaction_date`, `transaction_type`, `amount`
- `chamber`, `Member_Party`, `Member_State`
- `security_type_figi`, `sector_yf`, `industry_yf`
- `Member_Committees` (via `CURATED_COALESCE` ← `Member_Committees_Full`, Bugfix 2026-07-02 — vorher immer leer), `Vote_Timing_Hit`
- **Modul 8**: `trade_volume_usd`, `trade_amount_max_usd`, `asset_tradingvolume_usd`, `trade_score`, `trade_score_breakdown`, `trade_warnings`

`issuer_ticker` (P5.5): Fallback-Kette `share_class_figi → ticker_base
→ ticker`. Garantiert 100% gefuellt solange mindestens eine der drei
Quellen gesetzt ist. Wird als Gruppierungs-Key in Sequenz-Signalen
(Stage 10) und Suspicious-Asset-Aggregation (Stage 12) verwendet
(Share-Class-Level).
Hinweis: shareClassFIGI gruppiert Exchange-Listings derselben
Aktienklasse, jedoch NICHT Cross-Class wie GOOGL+GOOG (separate
Class-A/Class-C-FIGIs).

`issuer_group_key` (P5.6): Issuer-Level (Cross-Class) via SEC EDGAR
CIK. Praefix-Konvention `CIK:<cik>` / `TICKER:<id>` / `SKIP:<type>`.
Wird als SWARM_GROUPING_KEY verwendet. GOOGL und GOOG bekommen
denselben `CIK:1652044`. Vollstaendige Architektur und
Design-Entscheidungen: `organisation/Ticker_Architektur.md`.

**adj_-Prefix-Regel**: Spalten mit `adj_X` lesen den Wert aus der
Quellspalte `X` der complete-Datei und schreiben ihn unter dem
`adj_X`-Namen (signalisiert: Pipeline-modifizierter Wert).

---

### `data/processed/trades_master_complete.csv` (vollstaendige Persistierung)
**Quelle**: Output von `scripts/import_trades.py` (Stage `save`)
**Format**: CSV
**Sortierung**: Absteigend nach `transaction_date` (NaT ans Ende, Quell-Reihenfolge als Tiebreak
innerhalb desselben Datums). Gilt fuer alle Master-Schreibvorgaenge (staged + monolithisch + Delta)
via `_sort_master_by_date` in `scripts/import_trades.py`.
Abschnitt „Master-Sortierung".
**Update**: Bei jedem Import (kumulativ; jeder Stage schreibt hier ein)

**Spalten** (alle, deutlich mehr als `trades_master.csv`):
```csv
# Original Scraped Trade-Daten (Reihenfolge wie in CSV)
name,                   # Politiker-Name
ticker,                 # Stock Symbol (leer bei Bonds etc.)
ticker_type,            # Asset-Typ (abgeleitet aus Ticker, NICHT aus Quiver-Daten)
asset_name,             # Name des Assets
asset_type,             # ST/OP/GS/OT/CS/DC/EF/MF/Corporate Bond etc.
transaction_type,       # Purchase/Sale (Full)/Sale (Partial)/Exchange
amount,                 # Dollar-Range ("$1,001 - $15,000" etc.)
transaction_date,       # Transaktionsdatum
owner,                  # Self/Spouse/Joint/Child
comment,                # Freitext (Bond-Metadaten etc.)
disclosure_date,        # Meldedatum (bis 45 Tage spaeter)
ptr_link,               # URL zum Original-PTR
csv_upload_time,        # Zeitstempel des Scraper-Laufs
chamber,                # House/Senate (von import_trades.py gesetzt)
subholding,             # Depot/Trust-Name (nur House, aus "S O:" Zeilen)

# Anreicherung durch Name Matching
Member_Party,           # D oder R
Member_State,           # CA, TX, etc.
Member_ExOfficio,       # True/False
Member_Chamber,         # House/Senate

# Anreicherung durch Committee Filter (Modul 4)
Member_Committees_Full, # Vollstaendiger Committee-String, F2-Format (siehe Abschnitt 2 oben
                        # bzw. scrapers/senate_pdf_parsing_tool/csv_zielstruktur.md)
Insider_Sectors,        # JSON: ALLE (sub)committee-Sektoren des Members
                        # [{sector, industry, rank, confidence, committee_role, subcommittee_role, committee, subcommittee}, ...]
                        # committee_role/subcommittee_role (NEU 2026-06-12, ersetzen das vermischte `role`):
                        # je ebenen-spezifische Rolle des Members (Werte siehe csv_zielstruktur.md);
                        # `subcommittee` leer = Committee-Ebene-Treffer (dann ist subcommittee_role leer)
Relevant_Sectors,       # JSON: Intersection von Insider_Sectors mit sector_yf/industry_yf (Match-Mode konfigurierbar in settings.RELEVANT_SECTORS_MATCH_MODE)
                        # Jeder Eintrag hat Feld match_type: 'full' (Sector+Industry treffen), 'sector' (nur Sector trifft)

# Anreicherung durch Filter 0 (Asset-Klasse)
asset_class_excluded,   # True = Nicht-Aktie (ETF/Option/Bond/Crypto/...) -> wird von Scoring ignoriert

# Anreicherung durch OpenFIGI (Stage tickeropenfigi) — Suffix _figi
figi,                   # FIGI-ID Security-Ebene (z.B. "BBG000B9XRY4")
ticker_figi,            # Canonical Ticker laut OpenFIGI (Bloomberg-Form, z.B. "WRB 7.375 PERP +A")
asset_name_figi,        # Canonical Name laut OpenFIGI (z.B. "W.R. BERKLEY CORPORATION")
security_type_figi,     # OpenFIGI securityType (z.B. "Common Stock", "PUBLIC", "FIXED, OID")
market_sector_figi,     # OpenFIGI marketSector (z.B. "Equity", "Pfd", "Corp", "Muni", "Govt")
share_class_figi,       # FIGI-ID auf Issuer x Aktienklassen-Ebene (z.B. "BBG009S39JY5" GOOGL Class A).
                        # P5: Coverage 82.67% gesamt / 83.1% YFINANCE_DIRECT.
                        # WICHTIG: shareClassFIGI = Issuer x SHARE-CLASS, NICHT reiner Issuer.
                        # GOOGL Class A und GOOG Class C bekommen UNTERSCHIEDLICHE share_class_figi
                        # (BBG009S39JY5 vs BBG009S3NB21). FIGI gruppiert Exchange-Listings derselben
                        # Aktienklasse, NICHT Cross-Class. Cross-Class-Mapping ist offenes Paket.

issuer_ticker,          # Abgeleitete Spalte (P5.5): share_class_figi -> ticker_base -> ticker.
                        # Garantiert 100% nicht-leer. Share-Class-Level. Wird als Gruppierungs-Key
                        # fuer Sequenz-Signale (Contrarian, Accumulative) und Suspicious-Asset-
                        # Aggregation verwendet. Auch in kuratierter trades_master.csv.

issuer_group_key,       # Abgeleitete Spalte (P5.6): Issuer-Level via SEC EDGAR CIK.
                        # Praefix-Konvention: CIK:<cik>, TICKER:<id>, SKIP:<type>.
                        # GOOGL und GOOG bekommen denselben CIK:1652044 (Cross-Class).
                        # ETFs/Foreign-Stocks bypassen den CIK-Lookup -> TICKER:<issuer_ticker>.
                        # Wird als SWARM_GROUPING_KEY verwendet.
                        # Doku: organisation/Ticker_Architektur.md

# Anreicherung durch yfinance (Stage tickeryfinance) — Suffix _yf
sector_yf,              # YF Sector (z.B. "Technology") — leer bei ETFs/Bonds/unbekannten Tickern
industry_yf,            # YF Industry (z.B. "Consumer Electronics") — leer bei ETFs/Bonds/unbekannten Tickern
quote_type_yf,          # YF quoteType (z.B. "EQUITY", "ETF", "MUTUALFUND")

# Post-Trade-Performance (Stage post_trade_returns, Modul 9.x) — NUR in trades_master_complete.csv
post_trade_baseline_price, # float: Close-Preis am transaction_date (naechster Handelstag bei Wochenende)
post_price_30d,            # float: Close-Preis 30 Tage nach Trade
post_price_60d,            # float: Close-Preis 60 Tage nach Trade
post_price_90d,            # float: Close-Preis 90 Tage nach Trade
post_price_120d,           # float: Close-Preis 120 Tage nach Trade
post_price_180d,           # float: **NEU (2026-06-04):** Close-Preis 180 Tage nach Trade. Dynamisch mit POST_TRADE_HORIZONS_DAYS.
post_price_270d,           # float: **NEU (2026-07-04):** Close-Preis 270 Tage nach Trade.
post_price_365d,           # float: **NEU (2026-07-04):** Close-Preis 365 Tage nach Trade.
post_return_30d,           # float: (post_price_30d/baseline - 1) * 100 in %. RICHTUNGSBEWUSST: Käufe wie üblich,
                           # Verkäufe mit invertiertem Vorzeichen (sell_sign = -1). Positiv = gutes Timing.
post_return_60d,           # float: dito fuer 60d
post_return_90d,           # float: dito fuer 90d
post_return_120d,          # float: dito fuer 120d.
post_return_180d,          # float: **NEU (2026-06-04):** dito fuer 180d. Für Verkäufe: Vorzeichen invertiert. Details: scoring/post_trade_returns.py.
post_return_270d,          # float: **NEU (2026-07-04):** dito fuer 270d. Nur für Trades ≥ 270 Tage alt befüllt, sonst NaN.
post_return_365d,          # float: **NEU (2026-07-04):** dito fuer 365d. Nur für Trades ≥ 365 Tage alt befüllt, sonst NaN.
first_close_offset_days,   # float: **NEU (2026-06-04):** Kalendertage vom transaction_date bis zum ERSTEN gegensätzlichen Trade
                           # (egal ob closed_full oder closed_partial). Hilfs-Spalte für Horizont-Sichtbarkeitsfilter im Dashboard.
                           # NaN bei not_applicable / delisted / unknown / held. Beispiel: Kauf 2022-09-01, erstes Verkauf 2023-03-22 → 203 Tage.
paper_return_pct,          # float: **NEU (2026-06-06):** Mark-to-Market Return gegen den AKTUELLEN jüngsten Close.
                           # Formel: `(latest_close / baseline_live − 1) × 100 × sell_sign`.
                           # Nur befüllt für Trades im RECENT_MONITORING_WINDOW_DAYS-Fenster (Bezug `transaction_date`).
                           # Außerdem: nur wenn Post-Trade-Baseline existiert (not_applicable/delisted → NaN).
                           # Richtungsbewusst: positive Werte = Gewinn (Kauf) oder gutes Verkaufs-Timing, negative = Verlust/schlechtes Timing.
                           # UNTERSCHIED zu effective_return_pct: `effective` ist realisiert (bis exit_date),
                           # `paper` ist unverwirklicht (bis jüngstes verfügbares Close-Datum). Siehe `price_as_of`.
                           # **SPLIT-IMMUN (Härtung d1a2cf5, 2026-06-13):** `baseline_live` wird NICHT aus der
                           # gespeicherten Master-Spalte `post_trade_baseline_price` gelesen, sondern live aus dem
                           # AKTUELLEN Preis-Cache abgeleitet (erster Close ≥ `transaction_date`, via
                           # `_price_at_or_after_fast`). Zähler (`latest_close`) und Nenner (`baseline_live`)
                           # stammen damit aus demselben Cache-Stand — ein rückwirkender Split kürzt sich heraus.
                           # Details/Mechanik: Abschnitt 6 dieses Dokuments,
                           # Lauf-Modus-Frage: `organisation/Preis_Aktualisierung.md`.
price_as_of,               # datetime: **NEU (2026-06-06):** Datum des für `paper_return_pct` verwendeten jüngsten Close.
                           # Macht die Staleness des Mark-to-Market sichtbar. NaT wenn kein Preis verfügbar.
                           # Beispiel: Trade 2023-06-01, jüngstes Close in Cache: 2026-06-05 → `price_as_of=2026-06-05`.
exit_status,               # str: held / closed_full / closed_partial / delisted / unknown / not_applicable
exit_date,                 # datetime: Datum des ersten gegensätzlichen Trades (bei closed_*)
exit_price,                # float: Close-Preis am exit_date
exit_amount_max_usd,       # float: Kumulierte Max-USD der gegensätzlichen Trades
effective_return_pct,      # float: **GEÄNDERT (2026-06-04) — REALISIERT-ONLY:** Bei closed_full/closed_partial: 
                           # (exit_price/baseline-1)*100 mit sell_sign-Multiplikation. 
                           # Bei held/delisted/unknown/not_applicable: NaN (nicht: längster Horizont-Snapshot).
                           # Richtungsbewusst: positive Werte = Gewinn (Kauf) oder gutes Verkaufs-Timing,
                           # negative Werte = Verlust (Kauf) oder entgangener Gewinn.
effective_return_horizon_days,  # float: **GEÄNDERT (2026-06-04):**
                           # Bei closed_*: Tage zwischen transaction_date und exit_date.
                           # Bei held/delisted/unknown/not_applicable: NaN (nicht: Horizont-Tage des Snapshots).
opposing_trade_ids,        # str: Semikolon-getrennte trade_ids der gegensätzlichen Trades
# exit_status-Werte: not_applicable — ticker_type nicht in {stock,foreign_stock,shareclass,etf} oder
#                    issuer_group_key beginnt mit SKIP: oder Exchange-Trade (kanonische Richtungserkennung
#                    via _direction_from_transaction_type, handling Sale/Sale (Full)/Sale (Partial)/SALE)
#                    delisted — yfinance liefert leere History
#                    unknown — yfinance Network/Rate-Limit-Error
#                    held — keine gegensätzlichen Trades im Fenster
#                    closed_full — kumulierte opposing amount >= Schwellwert
#                    closed_partial — opposing vorhanden aber Schwellwert nicht erreicht

# Modul 8 — Volume-Spalten. Edge-Cases: tests/test_amount_parser.py,
# tests/test_asset_volume.py, tests/test_trade_volume_signal.py.
trade_volume_usd,       # int64: Mittelpunkt der Disclosure-Amount-Range (utils/amount_parser.midpoint_usd).
                        # Genutzt fuer asset_tradingvolume_usd und total_volume_usd in den View-CSVs.
trade_amount_max_usd,   # int64: OBERGRENZE der Disclosure-Amount-Range (utils/amount_parser.max_usd).
                        # Score-Basis fuer das trade_volume-Signal. Beispiele:
                        # '$1,001 - $15,000' -> 15000; 'Over $50M' -> 50_000_000; leer/NaN -> 0.
asset_tradingvolume_usd,# int64: Cross-Class rolling-window Sum(trade_volume_usd) gruppiert nach
                        # issuer_group_key, Fenster Z = settings.SCORING_VOLUME_WINDOW_DAYS_Z.
                        # SKIP-Buckets / NaT-Datum -> 0. Delta-Mode liest Master-Historie mit.
                        # HINWEIS: Floesst NICHT mehr direkt in trade_score ein (Volume ist
                        # jetzt ein Tier-Signal auf trade_amount_max_usd). Bleibt fuer
                        # total_volume_usd in suspicious_assets.csv + Streamlit Asset-Tab.

ex_officio_role_keys,   # str ";"-getrennt: NEU (2026-06-12). Fuehrungsrollen-Keys der relevanten
                        # Sektor-Treffer eines Trades (aus Insider_Sectors/Relevant_Sectors
                        # committee_role/subcommittee_role), z.B. "committee_chairman;subcommittee_ranking_member".
                        # Erzeugt vom CommitteeTag in Stage gates_tags, durchgereicht von
                        # scripts/import_trades.py, konsumiert vom Scoring (SCORING_EX_OFFICIO_ROLE_BONUSES).

# Anreicherung durch Branch-1 Signale (Stage branch1_signals)
signal_count,           # int 0-4: Anzahl ausgeloester Branch-1 Signale pro Trade
signals_triggered,      # str ";"-getrennt: Liste der Signal-Typen (z.B. "committee;floor_action")

# Modul 8 — Score-Spalten. Formel + Beispiele: organisation/Filter_Instanz_Parameter.md.
# Edge-Cases: tests/test_trade_score.py, tests/test_trade_volume_signal.py.
# Primär-/Hint-Gating (2026-06-27): trade_score ist 0, wenn kein Primärsignal
# (SCORING_PRIMARY_SIGNALS) triggert. Die Master-Spalte signals_triggered bleibt
# UNVERÄNDERT (enthält alle getriggerten Signale); der Primär/Hint-Split erfolgt
# nur im App-Frontend (s. Filter_Instanz_Parameter.md).
trade_score,            # int: gewichteter Trade-Score (kein Cap). Quelle: scoring/trade_score.compute_trade_scores.
trade_score_breakdown,  # str ";"-getrennt: deterministische Komponenten-Aufschluesselung in fester Reihenfolge
                        # (Branch-1 -> Sequenz -> Schwarm -> trade_volume -> ex_officio).
                        # trade_volume erscheint nur wenn das Signal getriggert hat.
trade_warnings,         # str ";"-getrennt: Warning-Tags (initial nur "paradox_swarm").

# Metadata
ImportDate              # YYYY-MM-DD HH:MM:SS
```

**Hinweis**: `subholding` steht nach `chamber` (nicht nach `owner`), weil Senate-Trades kein `subholding` haben — pandas fuegt die Spalte beim Concat ans Ende an.

**Naming-Konvention (seit 2026-05-14, Spalten-Refactoring)**: Felder, die aus
externen APIs stammen, tragen Suffixe zur eindeutigen Quellzuordnung:
- `_figi` — alle aus OpenFIGI abgeleiteten Felder (Stage `tickeropenfigi`)
- `_yf` — alle aus yfinance abgeleiteten Felder (Stage `tickeryfinance`)
- ohne Suffix — Classifier-Outputs (`ticker_type`, `ticker_base`, …) oder Original-Scrape-Daten.

Die ID-Spalte `figi` selbst behält keinen Suffix (Eigenname/Akronym).

**Stage `tickerclassify` (Rebuild Teil 3) ergaenzt zusaetzliche Spalten**:
- `ticker_base` — normalisierte Form (z.B. BRK-B fuer BRK.B)
- `classification_reason` — welche Regex/Regel hat gefeuert
- `classification_confidence` — high/medium/low
- `parent_ticker` — interne Fallback-Spalte fuer PREFERRED-Notationen (z.B. `EP$C` → `parent_ticker='EP'`). NICHT in Master persistiert, verbleibt in `TickerClassification`-Dataclass und wird intern in der Sektor-Stage fuer yfinance-Lookup genutzt.
- `exch_code`, `series_letter`, `status_suffix`
- `manual_review_flag` — bool

**Stage `tickeropenfigi` (Rebuild T3 + T5/P5) ergaenzt zusaetzliche Spalten**:
- `figi`, `ticker_figi`, `asset_name_figi`
- `security_type_figi`, `market_sector_figi`
- `share_class_figi` (P5) — FIGI auf Issuer-Ebene (Cross-Share-Class-Anker)

**P5 — Router-Erweiterung (2026-05-18)**: Auch Trades mit Classifier-internem
Routing-Wert `YFINANCE_DIRECT` (ETFs, NUMERIC_SUFFIX, manche SHARECLASS) und
`MANUAL_REVIEW` (UNKNOWN, LONG_ALPHA_SUSPECT) durchlaufen einen optimistischen
OpenFIGI-Mapping-Versuch, damit ihre `share_class_figi`-Spalte gefuellt werden
kann. Bei Miss wird ein Negativ-Cache-Eintrag mit `source='empty'` geschrieben
(verhindert Wiederholungen). Klassen `SKIP` (OPTION, TREASURY, CRYPTO,
WHEN_ISSUED, INVALID) bleiben unveraendert ohne API-Call.

Der Routing-Wert (`api_strategy`) ist intern im `TickerClassification`-
Dataclass und wird **nicht** als Spalte in `trades_master_complete.csv` 
persistiert (Refactor 2026-05-28).

**Stage `tickeryfinance` ergaenzt**:
- `sector_yf`, `industry_yf`, `quote_type_yf`

**Stage `branch1_signals` (P2) ergaenzt**:
- `signal_count` — int (0-4): Anzahl ausgeloester Branch-1 Signale pro Trade
- `signals_triggered` — str (";"-getrennt): Liste der Signal-Typen pro Trade
- Schreibt parallel die Datei `data/processed/branch1_events_stage.csv`
  (siehe Abschnitt unten) — wird in P4 (`_stage_suspicious_export`) in
  `suspicious_events.csv` promoted.

**Stage `sequence_signals` (P3) ergaenzt**:
- Akkumuliert `signal_count` und `signals_triggered` additiv: Sequenz-Signale
  (`contrarian`, `accumulative`) werden zu den bestehenden Branch-1-Werten
  hinzugefuegt. Pro Trade wird ein Signal-Typ nur einmal gezaehlt
  (Deduplikation), auch wenn er mehrfach getriggert wuerde.
- Schreibt parallel die Datei `data/processed/sequence_events_stage.csv`
  (gleiche 21-Spalten-Form wie `branch1_events_stage.csv` ab P6, inkl. `issuer_group_key`).

**Bekannter Bestand:** Master enthaelt nicht-unique `trade_id`s
(99.706 unique vs. 111.986 Rows, Stand Mai 2026). Ursache liegt im
Hash-Schema `utils.trade_id.compute_trade_ids` und ist ausserhalb des
Signal-Scopes. In `branch1_events_stage.csv` tauchen diese Duplikate als
mehrfache Events mit identischer `(signal_event_id, trade_id)` auf.
Der Duplikat-Schutz in P4 reduziert das beim Promoten auf unique
Eintraege.

**ticker_type Werte** (Rebuild Teil 3 — neue Enum-Werte aus `utils.ticker_classifier.TickerType`,
kleinbuchstabig statt der alten Großbuchstaben-Strings):

| Wert | Quelle | Beschreibung | Beispiel-Ticker |
|------|--------|-------------|-----------------|
| `stock` | Classifier (1-5 Buchstaben) | Normale US-Aktie inkl. ADR/REIT/MLP | AAPL, MSFT, BABA |
| `foreign_stock` | Classifier (Suffix .L, .DE, ...) | Auslandsnotierung | NESN.SW, AIR.PA |
| `shareclass` | Classifier (-A, -B etc., NICHT Preferred) | Aktien-Klasse | BRK-B, GOOGL |
| `etf` | Type-Korrektur OpenFIGI | Exchange-Traded Fund | SPY, QQQ |
| `preferred` | Classifier ($, .PR, -P-X etc.) | Vorzugsaktie | EP$C, ATH.PRC |
| `warrant` | Classifier (-W, -WS, w-Suffix) | Optionsschein | KMB-W, BACWSA |
| `option` | Classifier (OCC-Format) | Option | SPY160219P00180000 |
| `bond` | Classifier (CUSIP-aehnlich) | Anleihe | 37045XEF9 |
| `treasury` | Classifier (T+8-9 alnum) | US-Treasury T-Bill | T912796U49 |
| `crypto` | Classifier (USD/USDT etc.) | Krypto-Pair | BTC-USD |
| `unit` | Classifier (-U) | SPAC-Unit | BLOC-U |
| `when_issued` | Classifier (-W I) | "When-Issued"-Aktie | XYZ-WI |
| `numeric_suffix` | Classifier (AGN1, PYPL1) | Numerisches Suffix (manual_review=True) | AGN1 |
| `long_alpha_suspect` | Classifier (>=6 Großbuchstaben) | Datenmuell-Verdacht | BELGIUM |
| `misc` | Classifier (Sonderzeichen) | Sonstige Sonder-Ticker | |
| `invalid` | Classifier (Datenmuell, leer) | Nicht klassifizierbar | |
| `unknown` | Fallback | Kein Pattern getroffen | |

**Asset-Class-Gate** (User-Sicht „Filter 0", Spalte `asset_class_excluded`): Setzt `True` fuer alle `ticker_type`-Werte **ausser** `stock`, `foreign_stock`, `shareclass` (siehe `config/settings.py::ASSET_CLASS_KEEP_TYPES`). Ausgefilterte Trades bleiben in der Datei, werden aber vom VoteTimingTag und nachgelagerten Scoring-Modulen uebersprungen.

**Verwendung**: Hauptdatenbank fuer Analyse, Filter und Scoring

**Display-Only Partial-Close-Zerlegung (2026-06-04, Modul 9):**
Die `app/chart_helpers.py`-Funktion `build_partial_close_rows` zerlegt im Post-Trade-Performance-Tab Eröffnungs-Trades mit `closed_partial`-Status in mehrere Display-Zeilen (eine pro Schließungs-Event). Diese Zeilen werden **nicht in `trades_master_complete.csv` persistiert**, sondern nur im UI angezeigt. Spalten:
- `trade_id`: `{parent_trade_id}-pc{event_n}` (Display-ID, nicht eindeutig in Master)
- `parent_trade_id`: Rückverweis auf original `trade_id`
- `close_date`: `exit_date` aus dem Event
- `exit_volume`: Volumen dieses einzelnen Schließungs-Trades
- `close_offset_days`: Tage zwischen `transaction_date` und dieses `close_date`
- `effective_return_pct`: Return bis zu diesem Close-Preis (nicht aggregiert über alle Closes)

**Limitierung (nicht-FIFO Partition):** Derselbe Verkauf kann bei mehreren Käufen als Gegen-Trade auftauchen. Volumen-Partitionierung erfolgt global NICHT sauber → Zerlegungs-Zeilen dürfen niemals in Volumen-Aggregate (Asset-Tab, Member-Tab) eingehen. Nur für Performance-Charts zulässig.

**Horizont-Sichtbarkeitsfilter (2026-06-04, Modul 9):**
Die vier historischen Tabs (Trades / Assets / Members / Post Trade Performance) filtern global nach `first_close_offset_days`. Sidebar-Toggle „Horizont-Filter aktiv" (Standard an) begrenzt Sichtbarkeit auf Trades mit `first_close_offset_days ≤ PRIMARY_HORIZON_DAYS` (benutzerwählter primärer Horizont aus 30/60/90/120/180/270/365). **Design: offene/gehaltene Positionen (held mit NaN) werden AUSGEBLENDET** (Filter-Bedingung `off.notna() & off <= horizon`). Sinn: Real geschlossene Positionen mit Exits >120d später zeigen sich erst wenn der Horizont aufgedreht wird. **Monitoring offener Positionen** erfolgt ausschließlich über den neuem 5. Tab „Recent Trades" (aktiviert über `build_recent_pool()`, überschreibt den Horizont-Filter für Fresh-Position-Überwachung mit aktuellen Papier-Returns). Auch der Tab **„Score-Analyse"** (6, Modul 9.x) umgeht den Horizont-Filter via `apply_prefilter(..., apply_horizon_filter=False)` — er validiert den `trade_score` gegen den signierten Objective Return `post_return_{X}d` **inklusive** gehaltener (held) Positionen (Details: Abschnitt „7d. App-Module").

---

### `data/processed/branch1_events_stage.csv` (Stage-Zwischen-Output, P2)

**Quelle**: `scripts/import_trades.py::_apply_branch1_signals` (Stage `branch1_signals`)
**Format**: CSV (UTF-8)
**Update**: Wird bei jedem Lauf der Stage komplett neu geschrieben.

**Zweck**: Snapshot aller Branch-1 Signal-Auslösungen eines Pipeline-Laufs.
Eine spaetere Suspicious-Export-Stage (P4) liest diese Datei ein, schreibt
sie append-only nach `suspicious_events.csv` und entfernt diese Stage-Datei.
Bis P4 fertig ist, dient sie auch der manuellen Inspektion.

**Spalten** (21 ab P6): `signal_event_id`, `trade_id`, `signal_type`,
`triggering_date`, `scope_key`, `member_id`, `member_name`, `chamber`,
`ticker`, `issuer_ticker`, `issuer_group_key`, `asset_name_figi`, `transaction_date`,
`disclosure_date`, `transaction_amount`, `direction`, `is_ex_officio`,
`match_count`, `vote_timing_hit_count`, `committee_subcommittee`,
`relevant_sectors`.

`match_count` = Anzahl Einträge in `Relevant_Sectors` zum Zeitpunkt des Triggers (Snapshot).

`issuer_ticker` wird über `utils/issuer_ticker.resolve_issuer_ticker`
aufgelöst (Fallback-Kette `share_class_figi → ticker_base → ticker`).
`asset_name_figi` fällt auf `asset_name` zurück, falls leer.

Detailbeschreibung pro Spalte: `organisation/tool_specific_md/signal_info.md`.

---

### `data/processed/sequence_events_stage.csv` (Stage-Zwischen-Output, P3)

**Quelle**: `scripts/import_trades.py::_apply_sequence_signals` (Stage `sequence_signals`)
**Format**: CSV (UTF-8)
**Update**: Wird bei jedem Lauf der Stage komplett neu geschrieben.

**Zweck**: Snapshot aller Sequenz-Signal-Auslösungen (`contrarian`,
`accumulative`) eines Pipeline-Laufs. Jede Auslösung erzeugt mehrere Event-
Zeilen mit identischer `signal_event_id` (eine pro beteiligtem Trade), um
die Gruppen-Zugehoerigkeit ueber den Hash zu transportieren. P4 promoted
sie zusammen mit `branch1_events_stage.csv` nach `suspicious_events.csv`.

**Spalten:** identisch zu `branch1_events_stage.csv` (21 Spalten ab P6,
inkl. `issuer_group_key`; `SIGNAL_EVENT_COLUMNS` in `scripts/import_trades.py`).

---

### `data/processed/swarm_events_stage.csv` (Stage-Zwischen-Output, P6)

**Quelle**: `scripts/import_trades.py::_apply_swarm_signals` (Stage `swarm_signals`)
**Format**: CSV (UTF-8)
**Update**: Wird bei jedem Lauf der Stage komplett neu geschrieben.

**Zweck**: Snapshot aller Schwarm-Signal-Auslösungen (`swarm_buy`,
`swarm_sell`, `swarm_conflict`, `swarm_paradox`). Gruppiert auf
**Issuer-Group-Ebene** (`issuer_group_key` aus P5.6 — CIK-basiert,
Cross-Class). Eine `signal_event_id` pro Cluster bzw. Paradox-Auslösung,
alle beteiligten Trades als Event-Zeilen. P4 promoted die Datei zusammen
mit `branch1_events_stage.csv` und `sequence_events_stage.csv` nach
`suspicious_events.csv`.

**Spalten:** identisch zu den anderen Event-Stage-CSVs (21 Spalten,
inkl. `issuer_group_key`). Vollständige Spalten-Doku siehe
`organisation/tool_specific_md/signal_info.md`.

**Smoke-Run-Beispiel (Mai 2026, 111.986 Trades):**
- 4.950 Issuer-Group-Buckets, davon 3.779 mit ≥ 2 Trades evaluiert
- 24.926 Event-Zeilen: `swarm_buy` 10.267 / `swarm_sell` 10.105 /
  `swarm_paradox` 3.214 / `swarm_conflict` 1.340
- Cross-Class-Beispiel: GOOGL und GOOG teilen sich `signal_event_id`s
  im Alphabet-Bucket (`CIK:1652044`).

---

### `data/processed/suspicious_events.csv` (Master, append-only, P4)

**Quelle**: `scripts/import_trades.py::_stage_suspicious_export` (Stage `suspicious_export`)
**Format**: CSV (UTF-8)
**Update**: Append-only. Dedup-Schlüssel `(signal_event_id, trade_id)` — Re-Runs sind idempotent.

**Zweck**: Master-Liste aller Signal-Auslösungen über die gesamte Historie. Aus dieser Datei werden die beiden Views `suspicious_assets.csv` und `suspicious_members.csv` bei jedem Pipeline-Lauf komplett neu materialisiert.

**Spalten** (21 ab P6): identisch zu `branch1_events_stage.csv` / `sequence_events_stage.csv` / `swarm_events_stage.csv`, inkl. `issuer_group_key`. Vollständige Spalten-Doku: `organisation/tool_specific_md/signal_info.md`.

---

### `data/processed/suspicious_assets.csv` (View, regeneriert, Y-Tage-Verfall, P4 + Modul 8.4)

**Quelle**: `scripts/import_trades.py::_materialize_suspicious_assets`
**Format**: CSV (UTF-8)
**Update**: Wird bei jedem Lauf komplett überschrieben. Filter: `last_event_date >= today - settings.SUSPICIOUS_ASSET_PERSISTENCE_DAYS` (Default 30 Tage).

**Zweck**: Asset-Aggregat aller aktuell verdächtigen Werte. Ein Asset, dessen jüngste Auslösung älter als 30 Tage ist, fällt aus dieser View, bleibt aber in `suspicious_events.csv` historisch erhalten.

**Spalten** (15): `issuer_ticker`, `asset_name_figi`, `event_count`, `member_count`, `signal_types`, `last_event_date`, `first_event_date`, `directions`, `chambers`, `last_signal_type`, `score_avg`, `score_max`, `score_total`, `total_volume_usd`, `warnings`.

Counter-Spalten (`signal_types`, `directions`, `chambers`): String-Format `"committee:5,accumulative:2"` (Count desc, Tie-Break alphabetisch).

Score-Aggregat-Spalten (Modul 8.4) joinen `trade_score`/`trade_volume_usd`/`trade_warnings` aus `trades_master_complete.csv` über distinct `trade_id`. Aggregations-Logik + Helper-Beschreibung: `organisation/Filter_Instanz_Parameter.md`. Schema + Beispiele: `organisation/tool_specific_md/signal_info.md`.

---

### `data/processed/suspicious_members.csv` (View, regeneriert, dauerhaft, P4 + Modul 8.4)

**Quelle**: `scripts/import_trades.py::_materialize_suspicious_members`
**Format**: CSV (UTF-8)
**Update**: Wird bei jedem Lauf komplett überschrieben. Kein Verfallfilter.

**Zweck**: Member-Aggregat aller je in einem Signal aufgetauchten Politiker, mit Rückverweis-Liste auf alle auslösenden Trades.

**Spalten** (13): `member_id`, `member_name`, `chamber`, `event_count`, `asset_count`, `signal_types`, `last_event_date`, `first_event_date`, `is_ex_officio_ever`, `event_trade_pairs`, `score_avg`, `score_max`, `score_total`.

`event_trade_pairs` ist eine JSON-encodierte Liste `[["signal_event_id","trade_id"], ...]`.

Score-Aggregate (Modul 8.4) analog `suspicious_assets.csv`, aber NUR drei Score-Spalten — kein `total_volume_usd` und kein `warnings`. Aggregations-Logik: `organisation/Filter_Instanz_Parameter.md`. Schema-Details: `organisation/tool_specific_md/signal_info.md`.

---

### Problem-Outputs: Stage-vs-Live-Verhalten (Mai 2026)

Die drei Diagnose-Dateien `unmatched_names.csv`, `delisted_trades.csv`, `manual_review_tickers.csv` folgen einem einheitlichen Stage→Live-Muster:

| Lauf-Typ | Verhalten | Wirkung |
|----------|-----------|---------|
| **Full-Run** (`python scripts/import_trades.py`) | **Overwrite**: Live-Datei wird mit aktuellem Stand überschrieben. Bei leerem Ergebnis → Datei wird gelöscht. | Auto-Purge: gelöste Probleme verschwinden automatisch. |
| **Delta-Run** (`--delta`) | **Akkumuliert + Re-Validiert**: alte Live-Datei + neue Probleme werden gemerged via natürlichem Schlüssel. Anschließend werden Einträge entfernt, die jetzt gelöst sind (z.B. Name jetzt in `name_aliases.csv`, Ticker in `ignored_tickers.csv` oder im positiven Cache). | Akkumuliert offene Probleme über Runs hinweg. |
| **Dry-Run** (`--dry_run`) | Schreibt nur `*_stage.csv` (Vorschau aktueller Run). Live-Dateien bleiben unangetastet. `manual_review_tickers.csv` zeigt die projizierte Endform (existing-master ∪ delta). | Sichere Vorschau ohne destruktive Änderung. |

**Stage-Dateien** (Zwischen-Output während des Laufs):
- `unmatched_names_stage.csv`
- `delisted_trades_stage.csv`

Sie werden nach erfolgreicher Promotion automatisch entfernt (`_promote_problem_outputs(delta)` in `scripts/import_trades.py`).

**Re-Validation-Quellen pro Datei**:
| Datei | Re-Validation gegen |
|-------|---------------------|
| `unmatched_names.csv` | `NameMatcher.match()` mit aktuellen `name_aliases.csv` + Member-CSVs |
| `delisted_trades.csv` | Positiver `ticker_industry_cache.json` (kein API-Call) |
| `manual_review_tickers.csv` | bestehende Filter (`ticker_type!='ignored'`, `manual_review_flag=True`, nicht in `ignored_tickers`) |

`count`-Spalte in `unmatched_names.csv` wird beim Delta-Merge **aufsummiert** (alt + neu), damit Sortierung nach Triage-Priorität bleibt.

---

### `data/processed/manual_review_tickers.csv` (Diagnose-Output, Rebuild Teil 3)
**Quelle**: Stage `save` und Bonus-Flag `--manual_review_only`
**Format**: CSV, sortiert nach `count_of_trades` absteigend
**Update**: Bei jedem Import neu geschrieben (kein Append)
**Daten-Basis**: Full-Run und Delta-Run aggregieren aus dem **vollen gemergten Master**. Im Delta-Dry-Run wird `existing-master ∪ delta_enriched` als Vorschau aggregiert.

**Spalten**:
- `raw_ticker` — wie aus dem Quiver-Feed
- `ticker_base` — normalisierte Form
- `ticker_type` — Classifier-Ergebnis
- `classification_reason` — welche Regel hat gefeuert
- `count_of_trades` — Anzahl Trades mit diesem Ticker
- `first_trade_date`, `last_trade_date`
- `example_member` — ein Politiker als Beispiel

**Verwendung**: Aufmerksame Pruefung von `manual_review=True`-Tickern, fuer die der Classifier keine eindeutige Regel hatte (z.B. NUMERIC_SUFFIX, LONG_ALPHA_SUSPECT). Regeneration ohne Master-Touch via `python scripts/import_trades.py --manual_review_only`.

---

### `data/processed/trades_new.csv`
**Quelle**: Output von `scripts/import_trades.py`
**Format**: CSV
**Update**: Bei jedem Import (nur neue Trades)

**Spalten**: Gleich wie trades_master.csv

**Verwendung**: Review der letzten Import-Batch

---

### `data/processed/unmatched_names.csv`
**Quelle**: Output von `scripts/import_trades.py`
**Format**: CSV
**Update**: Full-Run = Overwrite. Delta-Run = Akkumuliert + Re-Validiert (siehe Übersicht oben).
**Auto-Purge**: Sobald ein Name in `data/mappings/name_aliases.csv` (oder Member-CSV) auftaucht, wird er beim nächsten Run automatisch aus der Datei entfernt.

**Spalten**:
```csv
trade_name, count, chamber, best_match, best_match_score
```
- `count` wird beim Delta-Merge aufsummiert (alt + neu).

**Beispiel**:
```csv
Marjorie Taylor Greene,286,house,Al Green,72
Nancy Pelosi,30,house,Nancy Mace,63
```

**Verwendung**:
- Review von nicht-gematchten Namen
- Kandidaten für `data/mappings/name_aliases.csv`

---

### `data/processed/delisted_trades.csv`
**Quelle**: Output von `scripts/import_trades.py` (Ticker-Sector Stage via `utils/ticker_sector.py`)
**Format**: CSV
**Update**: Full-Run = Overwrite. Delta-Run = Akkumuliert + Re-Validiert (Cache-only).
**Auto-Purge**: Sobald ein Ticker im positiven `data/mappings/ticker_industry_cache.json` auftaucht, wird er beim nächsten Run entfernt (kein API-Call).

**Stage-Datei**: `data/processed/delisted_trades_stage.csv` (Zwischenpuffer, wird nach Promotion gelöscht).

**Schreibbedingung (seit 2026-06-14)**: Vor dem Schreiben des Stage-Files erzwingt
`utils/ticker_sector.py::_write_delisted_stage` eine **frische** `NetworkCanary.force_reprobe()` —
ist das Netz zu diesem Zeitpunkt ROT, wird die Datei **nicht** geschrieben (naechster Lauf erzeugt
sie neu). Verhindert, dass ein stale-GRUENes Verdikt zu Ausfall-Beginn falsche Delisted-Eintraege
verewigt. Details: Abschnitt 6 dieses Dokuments.

**Spalten**:
```csv
ticker, ticker_type, company_name, classification_reason, transaction_date
```

**Verwendung**:
- Übersicht aller Trades mit delisteten/unbekannten Tickern (kein Sector/Industry bei yfinance)
- Terminal-Report zeigt Verteilung nach Jahr

---

### `archive/trades_master_backup/trades_master_{YYYYMMDD-HHMMSS}.csv`
**Quelle**: Automatisch erstellt von `scripts/import_trades.py` (`_backup_master()`)
**Format**: CSV (identisch zu trades_master.csv)
**Update**: Vor jedem destruktiven Schreiben auf trades_master.csv (normal stage mode ohne --dry_run)

**Verwendung**:
- Wiederherstellung des letzten Zustands bei fehlgeschlagener Stage
- Kein automatisches Löschen — manuell aufräumen bei Bedarf
- Pfad konfigurierbar via `settings.ARCHIVE_BACKUP_PATH`

### `data/processed/trades_stage.csv`
**Quelle**: Zwischenpuffer von `scripts/import_trades.py`
**Format**: CSV (selbe Struktur wie trades_master.csv)
**Update**: Bei `--dry_run`, `--delta --stage_*`, oder `--stage_X --dry_run`

**Verwendung**:
- Output-Ziel für alle nicht-destruktiven Stage-Läufe
- Input für `--stage_save` (committet Stage → Master)
- Wird nach erfolgreichem `--stage_save` automatisch gelöscht

---

## 6. Configuration

### `data/config/name_aliases.csv`
**Quelle**: Manuell gepflegt
**Format**: CSV

**Spalten**:
```csv
trade_name, correct_name, chamber
```

**Beispiel**:
```csv
Rob Bresnahan,"Robert P. Bresnahan,Jr.",house
Thomas H Tuberville,"Tuberville, Tommy",senate
```

**Hinweis**: `correct_name` muss exakt dem Namen in der jeweiligen Member-CSV entsprechen (House: "First Last", Senate: "Last, First M.")

**Verwendung**: Name-Matching in `utils/name_matcher.py`
Überschreibt Fuzzy-Matching für bekannte Varianten.

---

### `data/mappings/ignored_tickers.csv`
**Quelle**: Manuell gepflegt
**Format**: CSV
**Zweck**: Allowlist-Gegenstueck — Trades, deren `raw_ticker` in dieser Liste
steht, werden in der Pipeline mit `ticker_type='ignored'` und
`manual_review_flag=False` markiert (Reconstruct leitet daraus intern
`SKIP`-Routing ab). Dadurch entfallen API-Lookups
(OpenFIGI/yfinance) und der Eintrag taucht nicht mehr in
`manual_review_tickers.csv` auf.

**Spalten**:
```csv
raw_ticker, category, reason
```

- `raw_ticker` (str, **exakter Match** auf Spalte `ticker` im Trade): z.B. `"GLAS FUNDS, LP"`
- `category` (str): freie Kategorisierung, z.B. `private_fund`, `non_public`, `obvious_typo`
- `reason` (str): Begruendung fuer den Eintrag (Audit-Trail)

**Beispiel**:
```csv
raw_ticker,category,reason
"GLAS FUNDS, LP",private_fund,Private investment platform - kein oeffentlicher Ticker
```

**Verwendung**: `scripts/import_trades.py` → `_load_ignored_tickers()` /
`_apply_ignore_list()`. Wird nach jeder Klassifikation angewendet (Stage
`tickerclassify` sowie in `run()` und `run_delta()`). Pipeline schreibt
diese Datei NIE — nur lesen.

**Pflege**: Eintraege aus `data/processed/manual_review_tickers.csv` koennen
hierher verschoben werden, wenn klar ist, dass das Asset keinen oeffentlichen
Ticker hat oder anderweitig ausgeschlossen werden soll.

---

## 7. Weitere Mappings

### `data/raw/subcommittee_region_mapping.csv`
**Quelle**: Automatisch generiert aus xlsx (Region_Mapping Sheet)
**Format**: CSV
**Größe**: 20 KB
**Zeilen**: 239

**Spalten**:
```csv
Chamber, Committee, Subcommittee, Region, Konfidenz
```

**Regionen**: USA, Global, Europe, Asia, Latin_America, Africa, Middle_East

**Verwendung**: Zukünftige Erweiterung (geografische Filter)

---

## 7b. App-Konfiguration (Streamlit-Dashboard)

### `data/config/scoring_presets.json`
**Quelle**: Vom User im Streamlit-Dashboard (Sidebar-Form „Score-Gewichte" →
„Speichern") angelegt. Datei + Ordner werden bei Bedarf automatisch erzeugt;
nicht in Git versioniert.
**Format**: JSON, Top-Level-Objekt mit benannten Presets.
**Verwendung**: `app/scoring_presets.py` (`load_presets`, `save_preset`,
`delete_preset`). Ein Preset namens `Default` ist synthetisch (Snapshot aus
`config/settings.py`) und immer verfügbar; er erscheint nicht in der Datei und
ist reserviert (nicht überschreib-/löschbar).

**Payload-Schema pro Preset:**
```json
{
  "Aggressiver Schwarm": {
    "weights": {
      "committee": 10,
      "committee_full": 5,
      "floor_action": 15,
      "extreme_report": 5,
      "contrarian": 8,
      "accumulative": 10,
      "swarm_buy": 40,
      "swarm_sell": 40,
      "swarm_conflict": 10
    },
    "paradox_damper": 0.0,
    "volume_enabled": true,
    "volume_threshold_u": 100000.0,
    "volume_slope": 0.00002,
    "ex_officio_role_bonuses": {
      "committee_chairman": 15,
      "committee_vice_chairman": 12,
      "committee_ranking_member": 10,
      "committee_ex_officio": 10,
      "subcommittee_chair": 12,
      "subcommittee_vice_chair": 9,
      "subcommittee_ranking_member": 8,
      "subcommittee_ex_officio": 8
    },
    "ex_officio_committee_global": true
  }
}
```

**Felder:**
- `weights` — Dict mit allen Keys aus `SCORING_WEIGHTS`. Fehlende Keys werden
  beim Laden mit Defaults aufgefüllt.
- `paradox_damper` — Float 0.0–1.0 (Schwarm-Multiplikator bei `swarm_paradox`).
- `volume_enabled` — Bool, schaltet den Volume-Score komplett aus.
- `volume_threshold_u` — Schwellenwert U in USD (linearer Anteil ab vol > U).
- `volume_slope` — Punkte pro USD über U.
- `ex_officio_role_bonuses` — Dict mit den 8 Keys aus
  `SCORING_EX_OFFICIO_ROLE_BONUSES` (Default-Werte 8–15). Bonus = MAX über die
  Boni der in `ex_officio_role_keys` vorhandenen Keys, nur wenn ein
  Trigger-Signal getriggert hat. **NEU (2026-06-12)**, ersetzt das frühere
  einzelne `ex_officio_bonus`.
- `ex_officio_committee_global` — Bool, entspricht
  `SCORING_EX_OFFICIO_COMMITTEE_GLOBAL`: wenn `false`, zählen
  `committee_*`-Role-Keys nicht für den Bonus. **NEU (2026-06-12)**.

**Recompute-Pfad:** Diffs gegen `default_payload()` werden als
`weights_override` / `params_override` an `app/score_recompute.recompute_subset()`
gereicht; nur tatsächlich veränderte Felder landen im Override-Dict.

**Korruptionsschutz (Bugfix 2026-07-02):** Ist die Datei nicht parsebar (korruptes JSON),
sichert `app/scoring_presets.py` sie einmalig nach `scoring_presets.json.bak`
(überschreibt eine ältere `.bak` — letzter Stand zählt) und lädt mit leeren Presets weiter;
die UI zeigt einmalig eine Warnung an (`consume_corrupt_warning()`). Vorher drohte beim
nächsten Speichern stiller Totalverlust.

### `config/settings.py::DETAIL_RETURN_THRESHOLD_PCT`
**Default**: `10.0` (Prozent). Reiner **Anzeige-Schwellwert** für das
Member-Detail-Popup (kein Scoring-Parameter, daher nicht in
`Filter_Instanz_Parameter.md`): bestimmt den Anteil „großer Treffer" — Trades
mit `paper_return_terminal > x` — in den Diagrammen D1/D7. Im Popup per Slider
live überschreibbar, ohne Recompute oder Pipeline-Lauf.

### `config/settings.py::DETAIL_RED_FLAG_*`
**NEU (Bugfix 2026-07-02):** Schwellen der Red-Flag-Ampel im Member-Detail-Popup
(`app/member_detail.py::_count_red_flags`), reine **Anzeige-Parameter** (kein
Scoring-Input, daher nicht in `Filter_Instanz_Parameter.md`):
- `DETAIL_RED_FLAG_VOTE_TIMING_RATE` — Default `0.20`; Schwelle Anteil
  Vote-Timing-Trades.
- `DETAIL_RED_FLAG_COMMITTEE_FULL_RATE` — Default `0.20`; Schwelle Anteil
  `committee_full`-Trades.
- `DETAIL_RED_FLAG_OUTPERFORMANCE_PP` — Default `5.0` (Prozentpunkte);
  Schwelle Outperformance vs. Vergleichsgruppe.

Ampel-Level (`RED_FLAG_LEVELS` in `app/member_detail.py`) sind seit diesem Fix
**emoji-frei** — reine Streamlit-Farbsyntax (`:green[Niedrig]`,
`:orange[Erhöht]`, ...).

### `config/settings.py::DETAIL_RELATED_RETURN_COLOR_PCT`
**Default**: `10.0` (Prozent). Reiner **Anzeige-Schwellwert** (kein Scoring-Parameter,
daher nicht in `Filter_Instanz_Parameter.md`), **NEU (2026-07-02)**: ±Schwelle der
Grün-/Rot-Färbung der Related-Trades-Tabelle im Trade-Detail-Popup
(`app/streamlit_app.py`) — Return `>= +x` grün, `<= -x` rot.

### `app/score_recompute.py` — Signal-Ausschluss & Recompute-Performance
**Primärsignal-Exclude-Semantik (Bugfix 2026-07-02):** Ausgeschlossene Signale (Sidebar
„Signale ausschließen") fliegen jetzt auch aus dem `SCORING_PRIMARY_SIGNALS`-Tuple des
Settings-Proxys (`build_settings_proxy`/`recompute_subset`) — ein ausgeschlossenes
Primärsignal (`committee_full`/`swarm_buy`) kann das Hard-Gate nicht mehr öffnen. Werden
**beide** Primärsignale ausgeschlossen, ist das Tuple leer → Gate komplett aus
(Rückwärts-Kompat-Semantik in `compute_trade_score`) → alle Trades werden nach
Hint-Gewichten gescort (bewusst, für Was-wäre-wenn-Analysen).

**Recompute-Ableitung (`app/streamlit_app.py`, Performance-Fix 2026-07-02):** `prefilt`
(Trades-/Assets-/Members-Tabs) wird bei aktiven Score-Overrides nicht mehr separat
rekomputet, sondern per Horizont-Schritt aus dem bereits einmal rekomputeten `corr_base`
abgeleitet (`apply_prefilter(corr_base, apply_window=False, ...)`) — 1 statt 2
Voll-Recomputes pro Rerun, zeilenidentisches Ergebnis. `min_score`/`primary_horizon` wirken
auf `corr_base` bewusst nicht.

**Zeitfenster-Anker (`_window_anchor`/`apply_prefilter`, finale Semantik
2026-07-07, User-Entscheid):** Das globale Sidebar-Zeitfenster (7/30/365 Tage)
schneidet auf der gewählten Datumsbasis (`transaction_date` = Quiver-Spalte
*Traded* / `disclosure_date` = Quiver-Spalte *Filed*); der **Fenster-Anker ist
für BEIDE Basen `max(disclosure_date)`** (= Datenstand; Helper
`_window_anchor`, Fallback `max(Basis-Spalte)`, falls `disclosure_date` fehlt
oder komplett NaT ist). Damit gilt die Invariante **Transaction-Treffermenge ⊆
Disclosure-Treffermenge** (da `disclosure_date >= transaction_date`);
Kurzfenster auf Transaction-Basis dürfen wegen der 45-Tage-Meldefrist **bewusst
leer sein** (jüngste Transaktionen sind noch nicht gemeldet — kein Bug, nicht
erneut „fixen"). Die datenrelative Alternative (Anker = Max der jeweils
gewählten Basis-Spalte, kurzzeitig aktiv am 2026-07-06) wurde verworfen, weil
die Trefferzahlen der Basen damit nicht vergleichbar sind. Eine Sidebar-Caption
unter der Fenster-Auswahl zeigt das wirksame Datumsfenster (von–bis, „Anker:
Datenstand"). Zeilen mit NaT in der Basis-Spalte fallen aus dem Fenster; ohne
bestimmbaren Anker wird nicht gefiltert. Tests:
`tests/test_streamlit_app_fixes.py` (W1-Block).

### `config/settings.py::SCORE_CORR_*` (Score-Analyse-Tab)
Reine **Anzeige-/Analyse-Parameter** für den Tab „Score-Analyse" (kein
Scoring-Parameter, daher nicht in
`Filter_Instanz_Parameter.md`). Block in `config/settings.py` unter
„Score-Korrelation (Modul 9.x)":
- `SCORE_CORR_DEFAULT_HORIZON_DAYS` — Default `180`; Default-Horizont X des
  Tabs. Muss in `POST_TRADE_HORIZONS_DAYS` (30/60/90/120/180/270/365) enthalten
  sein.
- `SCORE_CORR_DEFAULT_METHOD` — `'spearman'`; Leitkennzahl (Pearson zusätzlich).
  Spearman wird **ohne scipy** als Pearson auf Rängen berechnet
  (`a.rank().corr(b.rank())`, identisch inkl. Tie-Korrektur).
- Score-Buckets: **fixes Schema, nicht mehr konfigurierbar** (Selectbox +
  `SCORE_CORR_BUCKET_OPTIONS`/`SCORE_CORR_BUCKET_DEFAULT` entfernt, 2026-07-04).
  Score `0` (Hard-Gate: kein Primärsignal → `trade_score = 0`) bildet ein
  **eigenes** Bucket, alle Scores `> 0` laufen in **festen 5er-Schritten**
  (`1–5`, `6–10`, `11–15` …). Grund: das alte erste gleich-breite Bucket (`0–5`)
  vermischte unverdächtige Nuller-Trades mit `committee_full`-Treffern.
- `SCORE_CORR_SCATTER_MAX_POINTS` / `SCORE_CORR_SCATTER_OPACITY` — `20000` /
  `0.3`; Sample-Cap + Opazität des Scatters (nur Visualisierung; die Kennzahlen
  Spearman/Pearson/n rechnen immer über die volle Stichprobe).

### `config/settings.py::UI_SCOPE_PREFIX` / `UI_SCOPE_HELP`
Reine **Anzeige-Config** (kein Scoring-Parameter): Grundtexte für die
Scope-Kennzeichnung der Zeit-/Horizont-Widgets im Dashboard. Jedes
Zeit-/Horizont-Widget trägt ein Label-Präfix aus `UI_SCOPE_PREFIX` plus einen
Tooltip-Grundtext aus `UI_SCOPE_HELP` (Helfer `_scope_label`/`_scope_help`,
in `app/streamlit_app.py` definiert, in `app/member_detail.py` lokal
dupliziert gegen Circular-Import), damit von außen erkennbar ist, wie weit
eine Einstellung reicht (die Zeitfenster beschränkten sich vorher unsichtbar
gegenseitig). Drei Scope-Stufen, je ein Key in beiden Dicts:
- `global` (`[Global]`) — wirkt auf Trades-/Assets-/Members-/Post-Trade-
  Performance-Tab. Betrifft die Sidebar: Datumsbasis, Zeitfenster, Primärer
  Return-Horizont, Horizont-Filter aktiv.
- `tab` (`[Tab]`) — wirkt nur im jeweiligen Tab, umgeht die globalen
  Einstellungen. Betrifft: Recent-Trades-Fenster, Score-Analyse-Horizont
  (s. Abschnitt „7d").
- `detail` (`[Detail]`) — wirkt nur im geöffneten Detail-Popup. Betrifft:
  Trade-Detail „Alle Trades", Member-Detail Quartal-Bereich + „Nur ≤180d" +
  AR-Horizont-Radios (s. Abschnitte „7c"/„7e").

---

## 7c. App-Module: Member-Detail-Popup (Modul 9)

Live-Berechnung pro Kongress-Mitglied — **keine neuen Master-Spalten, kein
Pipeline-Lauf**. Datenbasis ist die **volle Member-Historie**
(`apply_prefilter(trades, apply_horizon_filter=False, apply_window=False)`,
neuer Parameter `apply_window: bool = True` an `apply_prefilter`); weder
Sidebar-Zeitfenster noch Horizont-Filter wirken im Popup — der
Quartal-Range-Slider ist das **einzige Zeitfilter**. Nur die globale Suche
bleibt aktiv. Eine Caption oben im Dialog macht die Datenbasis transparent
(„volle Member-Historie … Sidebar-Zeitfenster und Horizont-Filter wirken hier
nicht; ggf. globale Suche aktiv"). Bei aktivem Signal-Ausschluss wird pro
Member per `recompute_subset` neu gescort.
**Primär-/Hint-Anzeige (2026-06-27):** Tabellen (Members, Related-Tabelle im
Popup) zeigen in der `signals`-Spalte nur Primärsignale; Hints erscheinen
ausschließlich im Trade-Detail-Popup als eigene Zeile „hints". Keine neuen
Master-Spalten.

### `app/member_metrics.py`
Reine Metrik-/Aggregations-Logik (kein Streamlit-Code), ~563 Zeilen.
Funktionsgruppen:

- **Committee-Parsing**: `parse_committees_full`, `_split_name_role` — eigener,
  verschachtelnder Parser für `Member_Committees_Full`
  (Grammatik: `;`=Eintrag, `:`=neues Committee, sonst Subcommittee,
  `|`=Name/Rolle). **Bewusst NICHT** der kanonische
  `tags.committee_tag.CommitteeParser`, weil dieser Subcommittees
  verlustbehaftet abflacht (s. `project_committee_parser_lossy`-Notiz).
- **Horizont-Attribution**: `nearest_horizon`, `qualifying_horizons`,
  `_nearest_horizon_array` (vektorisiert via `np.searchsorted`),
  `qualifies_for_horizon`, `paper_return_terminal`. Ein Trade zählt zu seinem
  „nearest horizon" über `first_close_offset_days`, mit Mittelpunkts-Schwellen
  45/75/105/150 Tagen (für Standard-Horizonte `DEFAULT_HORIZONS = [30, 60, 90,
  120, 180]`). `post_return_{h}d` ist im Master bereits richtungsbereinigt
  (Sell → ×−1) — wird hier unverändert übernommen.
- **Verläufe**: `trajectory_end_offset`, `compute_member_trajectories`. Die
  Spaghetti-/Verlaufslinien enden am Voll-Close (`exit_date`,
  `exit_status ∈ FULLY_CLOSED_STATES = {closed_full, delisted}`), gecappt bei
  180 Tagen; offene Positionen laufen bis zum Cap. **Bewusste Divergenz** zur
  Horizont-Attribution oben (andere Endpunkt-Logik, gleiche Datenbasis).
  **SPLIT-IMMUN (Härtung c10c5ec, 2026-06-13):** Die Baseline jeder
  Trajektorie wird live aus dem Preis-Cache abgeleitet — erster Cache-Close
  pro Trade via `merged.groupby('trade_id')['close'].transform('first')`
  — NICHT mehr aus der Master-Spalte `post_trade_baseline_price`. Jede
  Trajektorie startet damit bei 0 % (r0 ≡ 0) und ist split-immun (Zähler
  und Nenner stammen aus demselben Cache-Stand). Die Master-Spalte
  `post_trade_baseline_price` wird nur noch als Applicability-Filter
  genutzt (vorhanden & > 0), nicht mehr als Nenner.
- **Sektoren**: `parse_insider_sectors`, `member_oversight_sectors`,
  `committee_full_mask`, `sector_concentration`. Aufsichts-Sektoren stammen aus
  der Member-konstanten Spalte `Insider_Sectors`; die „committee_full"-Gruppe
  und der Red-Flag-Overlap werden strukturell aus `signals_triggered`
  abgeleitet (Substring-Match) — nicht rekomputiert.
- **Chart-Aggregationen**: `horizon_mean_returns`, `horizon_bucket_shares`,
  `terminal_distribution`, `score_bins`.
- **Controls/Helfer**: `quarter_options`, `filter_by_quarter_range`,
  `filter_long_trades_out`, `resolve_highlight_from_selection`
  (Plotly-Selection → `trade_id`, robust gegen skalare ODER Listen-customdata),
  `red_flag_summary`.
- **Konstanten**: `DEFAULT_HORIZONS`, `FULLY_CLOSED_STATES`,
  `_PRICE_CACHE_SENTINEL`.

Beispiele/Verhalten je Funktion: `tests/test_member_metrics.py` (29 Tests).

### `app/member_detail.py`
UI- und Plotly-Schicht (~498 Zeilen), Einstieg
`render_member_detail(member_df, bench_df, name, chamber)`. Wird als
`@st.dialog` aus dem Members-Tab in `app/streamlit_app.py` bei Zeilenklick
geöffnet.

Inhalt:
- Header mit verschachteltem Gremien-Panel (`parse_committees_full`) und
  Google-Namenssuche-Button.
- Controls: Quartal-Range-Slider (`[Detail]`, einziges Zeitfilter des
  Popups), Toggle „Nur ≤180d" (`[Detail]`, `filter_long_trades_out`),
  Schwellwert-x-Slider (Default `DETAIL_RETURN_THRESHOLD_PCT`).
- **Abnormal-Return-Horizont** (`st.radio`, `[Detail]`): Optionen = im Master
  vorhandene `AR_HORIZONS_DAYS`-Horizonte, Default-Index über
  `member_metrics.ar_default_index(present, AR_HEADLINE_HORIZON_DAYS)`
  (defensiver Fallback Index 0, falls die Headline-Spalte fehlt). Median-AR
  und Ausreißer-Quote (s. Abschnitt „7e") folgen der Auswahl live. `_scope_label`/
  `_scope_help` sind lokal in `member_detail.py` dupliziert (Circular-Import
  gegen `streamlit_app.py` vermeiden).
- 4 Tabs: **Übersicht / Performance / Verläufe / Timing** mit Diagrammen
  D1/D2/D5/D6/D7/Z3 sowie Spaghetti-Charts D3b/D4.
- **Bidirektionales Highlight** zwischen Trade-Liste und Spaghetti-Plot
  (`on_select`-Callbacks via `resolve_highlight_from_selection`,
  most-recent-wins). **Highlight-Fix (Bugfix 2026-07-02):** ein verwaister
  `session_state`-Highlight-Key (vom vorigen Member-Popup, oder ein Trade,
  der durch Quartal-/≤180d-Filter herausfällt) wird gegen die aktuellen
  Trajektorien validiert (`_resolve_highlight` — vorher blieben dadurch alle
  Linien gedimmt); ein Doppelklick auf die highlighte Linie hebt das
  Highlight jetzt wirklich wieder auf.
- **Terminal-Return-Fallback in der Spaghetti-Liste** (`_render_spaghetti_section`,
  2026-06-13): Ist `post_return_180d` für einen Trade NaN (junge, noch offene
  Position ohne 180d-Horizont), fällt die Liste auf den letzten verfügbaren
  Trajektorien-Punkt zurück (= aktueller Mark-to-Market-Paper-Return,
  konsistent zur gezeichneten Linie) statt einen leeren Wert anzuzeigen.
- **Caption** zu den Spaghetti-Charts erklärt die Linien-Endpunkt-Semantik:
  Linien laufen vom Transaktionstag bis zum Voll-Close, höchstens aber bis
  zum letzten verfügbaren Kurstag — junge Trades enden daher vor Tag 180.
- Timeline (Z1+Z2): Scatter über Transaktionsdatum, ★ markiert
  Vote-Timing-Treffer, Farbe kodiert Kauf/Verkauf.
- Red-Flag-Ampel (`red_flag_summary`; emoji-freie, konfigurierbare Schwellen —
  s. `config/settings.py::DETAIL_RED_FLAG_*` oben).
- Ticker-Zellen als Google-Links („Aktienkurs von {Ticker}",
  `_ticker_google_url`).

---

## Data Flow Übersicht

```
1. SCRAPING
   house_scraper.py → house_trades.csv
   senate_scraper.py → senate_trades.csv
   ↓

2. IMPORT + NAME MATCHING (--stage_committees / enrich_member_data)
   scripts/import_trades.py
   + house_member&(sub)committees.csv
   + senate_member&(sub)committees.csv
   + name_aliases.csv
   ↓
   utils/name_matcher.py (Senate-Normalisierung + Fuzzy 85%)
   ↓
   [Member_Party, Member_State, Member_ExOfficio,
    Member_Chamber, Member_Committees_Full]

3. TICKER-TYPE + TICKER-INDUSTRY
   + OpenFIGI API (Stage tickeropenfigi, Suffix _figi)
   + yfinance (Stage tickeryfinance, Suffix _yf)
   ↓
   [ticker_type, figi, ticker_figi, asset_name_figi, security_type_figi,
    market_sector_figi, sector_yf, industry_yf, quote_type_yf]

4. GATES + TAGS (--stage_gates_tags / _apply_gates_and_tags)
   + subcommittee_sector_industry_mapping.csv (CommitteeTag)
   + bill_actions.json + bill_subjects.json (VoteTimingTag)
   ↓
   _apply_committee_relevance → [Insider_Sectors, Relevant_Sectors]
   AssetClassGate → [asset_class_excluded]
   VoteTimingTag → [Vote_Timing_Hit, Vote_Timing_Hits, Vote_Timing_Hit_Count]

5. OUTPUT
   ↓
   trades_master.csv (Backup in archive/trades_master_backup/ vor jedem destruktiven Schreiben)
   trades_stage.csv (Zwischenpuffer bei --dry_run / --delta --stage_*)
   unmatched_names.csv (nicht gematchte Namen)
```

---

## 7d. App-Module: Score-Analyse (Modul 9.x)

Validiert objektiv, wie gut der `trade_score` den **signierten** Objective
Return `post_return_{X}d` vorhersagt — **keine neuen Master-Spalten, kein
Pipeline-Lauf**, alles live aus dem Master. Ein einzelner Tab „Score-Analyse"
(bis 2026-07-03 zwei Tabs „Score-Korrelation" + „Score-Analyse", zusammengeführt
bei vollem Funktionserhalt). **Primär-/Hint-Anzeige
(2026-06-27):** Trades-Tab, Score-Analyse-Tab und Recent-Tab
zeigen in der `signals_triggered`-Spalte nur Primärsignale; Hints erscheinen
ausschließlich im Trade-Detail-Popup. Datenbasis ist
`apply_prefilter(trades, apply_horizon_filter=False)` (umgeht den
Horizont-Filter → held-Positionen inklusive), bei aktivem Signal-Ausschluss /
Gewichts-Override einmalig per `recompute_subset` neu gescort (Basis
`corr_base`). Der Return ist exit-agnostisch und bereits
`sell_sign`-bereinigt (positiv = Trade lag in seiner Richtung richtig, für Kauf
UND Verkauf).

### `app/score_correlation.py`
Reine pandas-Helfer (kein Streamlit/Plotly), testbar
(`tests/test_score_correlation.py`):
- `return_col(x)` → Spaltenname `post_return_{x}d`.
- `correlation_stats(scores, returns)` → `{spearman, pearson, n}` (paarweiser
  NaN-Drop). Spearman = Pearson auf Rängen (kein scipy nötig).
- `score_bucket_returns(df, score_col, ret_col)` → mittlerer/median Return je
  Score-Bucket mit **Sonder-Bucket `0`** (Hard-Gate-Trades) und **festen
  5er-Schritten** für Scores `> 0` (`1–5`, `6–10`, … Intervall `((k-1)*5, k*5]`);
  leere Buckets bis zum Maximum bleiben als Balken erhalten. Kein `n_buckets`-
  Parameter mehr (fixes Schema statt konfigurierbarer gleich-breiter Bins).
- `per_signal_return_table(df, ret_col)` → pro Signal binärer „gefeuert"-
  Indikator gegen den Return: `[signal, n_fired, mean_fired, mean_not, delta,
  spearman, pearson]`, sortiert nach |spearman|. Signal-Universum
  **datengetrieben** aus den `signals_triggered`-Tokens plus Pseudo-Signal
  `committee_ex_officio` (aus dem Breakdown-Token `ex_officio:+N`, N>0). Werte
  sind **marginal/univariat** (Signale ko-okkurrieren — keine isolierten
  Beiträge). **Hinweis Kollinearität:** Im Match-Modus `sector_and_industry`
  (`RELEVANT_SECTORS_MATCH_MODE`, s. Abschnitt „Committee-Relevanz") gibt es
  keine reinen Halb-Treffer → `committee` und `committee_full` feuern identisch
  und erscheinen mit deckungsgleichen Werten; in `sector_or_industry`
  divergieren sie.

### Tab „Score-Analyse" in `app/streamlit_app.py`
Aufbau flach untereinander (ein `with tab_analysis`-Block):
- **Kontrollzeile** (3 Spalten): Horizont-Selectbox (Widget-Key `an_horizon`,
  Default 180, Optionen `POST_TRADE_HORIZONS_DAYS` 30…365, Scope `[Tab]`,
  unabhängig vom globalen Primär-Horizont; Tooltip erklärt, dass der Horizont
  ALLE Kennzahlen des Tabs bestimmt) · **Kammer**- und **Partei**-Multiselect.
  Die Kammer-/Partei-Filter wirken auf ALLE Auswertungen des Tabs (Kennzahlen,
  Scatter, Buckets, Pro-Signal-Tabelle); Default = alles ausgewählt. Der
  Partei-Filter blendet Trades ohne hinterlegte Partei aus.
- **Kennzahlen**: Spearman-ρ / Pearson-r / n / davon held.
- **Scatter**: `go.Scattergl` `trade_score` × `post_return_{X}d` (Sample-Cap,
  Y-Achse auf 1.–99.-Perzentil geklippt — nur Darstellung). Caption nennt die
  Limitationen **Delisting-Bias** (Survivorship: Ticker delistet vor Tag X →
  Return NaN → fällt aus der Stichprobe → Verzerrung nach oben) und
  **Reife-Beschränkung** (Trades < X Tage haben per Konstruktion keinen
  X-Tage-Return; kein Bias).
- **Score-Buckets**: Balken mit Ø Return je Score-Bucket; Score `0` separat,
  Rest in festen 5er-Schritten (s. `score_bucket_returns` oben).
- **Pro-Signal-Korrelation**: Tabelle (s. `per_signal_return_table` oben).

Konfiguration: `config/settings.py::SCORE_CORR_*` (s. Abschnitt „7b.
App-Konfiguration").

---

## 7e. Abnormal Return: Master-Stage + App-Anzeige + Event-Study (Discovery)

Marktbereinigtes Lineal pro Trade: der **signierte Abnormal Return (signed BHAR)** —
rohe Rendite minus Markt (SPY) über dasselbe Fenster, Verkauf invertiert (positiv = besser
als der Markt). **Deskriptiv, KEIN Scoring-Input.** Zwei Nutzungen aus EINEM Berechnungs-Modul
(`scoring/abnormal_returns.py`):

1. **Pipeline-Stage `abnormal_returns`** (Modul 9b, `scripts/import_trades.py`): schreibt die
   kontinuierlichen **Konstrukt-A-Spalten** `signed_ar_{30,60,90,120,180}d` in
   `trades_master_complete.csv`. Outlier-Flag und Perzentil-Rang werden **NICHT** persistiert
   (schwellen-/populationsabhängig → in der App bei der Anzeige abgeleitet); Konstrukt B
   (`signed_ar_disc_*`) wird nicht in den Master geschrieben.
2. **Standalone-Analyse** `scripts/analyse_event_study.py` (Discovery): validiert `trade_score`
   gegen den AR, rechnet zusätzlich Konstrukt B + Outlier-Flag + member-geclusterten Bootstrap —
   alles nur **in-memory**, kein Master-Schreiben. Settings-Änderung = Skript neu starten.

### Metrik (pro Trade, Horizont H, t0 = Ankerdatum)

```
signed_ar = sell_sign × ( P_ticker(t0+H)/P_ticker(t0) − 1
                         − ( P_SPY(t0+H)/P_SPY(t0) − 1 ) )
```
- `sell_sign` (Verkauf → −1) wird **genau einmal** auf das Abnormal angewandt.
- Wert ist ein **Bruchteil** (0.03 = 3 %), nicht Prozent.
- Right-Censoring: liegt t0+H hinter dem Cache-Ende (2026-06-24), ist der Wert NaN.
- Benchmark = SPY (im Preis-Cache vorhanden).

### Zwei Konstrukte

| Konstrukt | Anker | Spalten | Bedeutung |
|-----------|-------|---------|-----------|
| A (primär) | `transaction_date` | `signed_ar_{30,60,90,120,180}d` | Insider-Edge (Kauf/Verkauf selbst) |
| B (sekundär) | `disclosure_date` | `signed_ar_disc_{30,90}d` | Copycat/Follower-Edge nach Bekanntmachung |

### `scoring/abnormal_returns.py`

Berechnungs-Modul. Öffentliche API:
- `compute_abnormal_returns(df, cache_path, benchmark_ticker, horizons_days, headline_horizon_days, disclosure_horizons_days, outlier_mode, outlier_upper, outlier_lower, outlier_pct_upper, outlier_pct_lower) -> pd.DataFrame`
  Fügt Spalten hinzu: `signed_ar_{H}d`, `signed_ar_disc_{H}d`, `ar_outlier_flag` (bool), `ar_outlier_direction` ('positive'|'negative'|'').
- `clustered_bootstrap_mean(df, value_col, group_col, n_iter, seed)` — Bootstrap-Konfidenzintervall, member-geclustert (resamplet ganze Member über `bio_guide_id`).
- `clustered_bootstrap_diff(df, value_col, group_col, split_col, n_iter, seed)` — Bootstrap-Differenz zweier Gruppen.

Reuse der Preis-Primitive aus `scoring/post_trade_returns.py`
(`_load_price_cache`, `_build_price_lookup`, `_sell_sign_series`) — nicht reimplementiert.

### Spalten

**Im Master (`trades_master_complete.csv`, via Stage `abnormal_returns`)** — Konstrukt A:

| Spalte | Typ | Beschreibung |
|--------|-----|--------------|
| `signed_ar_30d` | float | Abnormal Return nach 30 Tagen ab `transaction_date` |
| `signed_ar_60d` | float | Abnormal Return nach 60 Tagen |
| `signed_ar_90d` | float | Abnormal Return nach 90 Tagen |
| `signed_ar_120d` | float | Abnormal Return nach 120 Tagen (**Headline-Horizont**, `AR_HEADLINE_HORIZON_DAYS`) |
| `signed_ar_180d` | float | Abnormal Return nach 180 Tagen |

**Nur in-memory (Standalone-Skript, NICHT im Master)** — Konstrukt B + Outlier-Markierung:

| Spalte | Typ | Konstrukt | Beschreibung |
|--------|-----|-----------|--------------|
| `signed_ar_disc_30d` | float | B | Abnormal Return nach 30 Tagen ab `disclosure_date` |
| `signed_ar_disc_90d` | float | B | Abnormal Return nach 90 Tagen ab `disclosure_date` |
| `ar_outlier_flag` | bool | — | True wenn Trade außerhalb Ausreißer-Band (auf `signed_ar_{headline}d`) |
| `ar_outlier_direction` | str | — | 'positive', 'negative' oder '' |

### App-Anzeige (Detail-Popups, NUR dort — keine Tabellen-Spalte)

Outlier-Flag und Perzentil-Rang werden **bei der Anzeige** aus den Master-Spalten + den
`AR_OUTLIER_*`-Schwellen abgeleitet (nicht im Master gefroren).

**AR-Horizont im UI wählbar** (statt fix): In BEIDEN Detail-Popups ein `st.radio`
(horizontal, Scope `[Detail]`) — Optionen = die im Master vorhandenen
`AR_HORIZONS_DAYS`-Horizonte, Default = `AR_HEADLINE_HORIZON_DAYS` (120d) über
den Pure-Helper `member_metrics.ar_default_index(present, headline)`
(defensiver Fallback Index 0, falls die Headline-Spalte fehlt). Fehlt die
Spalte / ist der Wert NaN → bestehende Hinweis-Caption bleibt (kein Radio,
kein Crash).

- **Trade-Detail** (`app/streamlit_app.py`): Sektion „Abnormal Return (marktbereinigt vs. SPY)" —
  Headline-Metrik `AR {H}d` + **zweiseitiger Perzentil-Rang** gegen alle evaluierbaren Trades
  („besser als X %" / „schlechter als Y % der Trades") folgen der Radio-Auswahl live (Rang
  weiterhin gegen ALLE Trades im Master, ungefiltert) + Tabelle Horizont × {roher `post_return` %, AR %}
  über die **Vereinigung** `POST_TRADE_HORIZONS_DAYS | AR_HORIZONS_DAYS` (zukunftssicher, falls die
  Listen künftig divergieren; heute identisch → optisch unverändert).
- **Member-Detail** (`app/member_detail.py`): **Median-AR** + **Ausreißer-Quote** (positiv/negativ
  getrennt) folgen der Radio-Auswahl live, mit Member-Konzentrations-Caveat.
  **Perzentil-Modus respektiert (Bugfix 2026-07-02):** `_resolve_ar_outlier_thresholds`
  löst bei `AR_OUTLIER_MODE='percentile'` die Ausreißer-Schwellen jetzt aus den
  Quantilen (`AR_OUTLIER_PCT_UPPER/LOWER`) der vollen **sichtbaren** Verteilung
  (Member + Benchmark aller anderen Politiker im Popup, global — nicht
  member-relativ) auf, statt weiterhin die absoluten `AR_OUTLIER_UPPER/LOWER`-
  Schwellen zu verwenden. Bei < 2 Werten in der Referenzverteilung Fallback auf
  die absoluten Schwellen.
- Pure Logik (unit-getestet, `app/member_metrics.py`): `abnormal_return_summary`, `ar_percentile_rank`,
  `format_ar_rank`, `ar_default_index` (Default-Position des Headline-Horizonts in der
  Optionsliste des Radios).

### Output-Datei `data/processed/ar_outliers.csv` (gitignored)

Gefilterte Ausreißer-Trades, sortiert nach `signed_ar_90d` absteigend. Spalten:

```
trade_id, bio_guide_id, name, chamber, sector_yf, ticker,
transaction_type, transaction_date, disclosure_date,
trade_score, signals_triggered, ar_outlier_direction,
signed_ar_30d, signed_ar_60d, signed_ar_90d, signed_ar_120d, signed_ar_180d,
post_return_90d
```

### Analyse-Skript `scripts/analyse_event_study.py`

Eigenständiges Analyse-Skript (keine Flags): liest `data/processed/trades_master_complete.csv`,
ruft `compute_abnormal_returns`, schreibt Report `organisation/analyse_event_study.md` +
`data/processed/ar_outliers.csv`.

Lauf: `python scripts/analyse_event_study.py`

### Konfiguration (`config/settings.py`, `AR_*`-Block)

| Parameter | Default | Beschreibung |
|-----------|---------|--------------|
| `AR_BENCHMARK_TICKER` | `'SPY'` | Benchmark für Marktbereinigung |
| `AR_HORIZONS_DAYS` | `[30,60,90,120,180]` | Horizonte Konstrukt A |
| `AR_HEADLINE_HORIZON_DAYS` | `90` | Primär-Horizont für Report-Headlines |
| `AR_DISCLOSURE_HORIZONS_DAYS` | `[30,90]` | Horizonte Konstrukt B |
| `AR_OUTLIER_MODE` | `'absolute'` | `'absolute'` oder `'percentile'` |
| `AR_OUTLIER_UPPER` | `0.03` | Obere Ausreißer-Schwelle (absolut) |
| `AR_OUTLIER_LOWER` | `-0.03` | Untere Ausreißer-Schwelle (absolut) |
| `AR_OUTLIER_PCT_UPPER` | `0.95` | Oberes Perzentil (Modus percentile) |
| `AR_OUTLIER_PCT_LOWER` | `0.05` | Unteres Perzentil (Modus percentile) |
| `AR_BOOTSTRAP_ITERS` | `2000` | Bootstrap-Iterationen |
| `AR_BOOTSTRAP_SEED` | `0` | Zufalls-Seed |

Befund-Report: `organisation/analyse_event_study.md`.

---

## 7f. Optionaler Trade-CSV-Export

### Datei `data/agent_exports/trade_selection_<timestamp>.csv` (gitignored)

Neutraler dateibasierter Export aus dem Recent-Trades-Tab der Streamlit-App.
Congress Tracker schreibt ausschließlich in seinen eigenen Exportordner; externe
Programme können die CSV optional übernehmen, ohne Code-, Pfad- oder
Laufzeitabhängigkeit zwischen den Projekten.

- **Schreiber:** Haupt-App `app/streamlit_app.py` (Recent-Trades-Tab) via
  `app/staging_export.py:export_selection`.
- **Leser:** nicht festgelegt; die Datei ist ein generischer Exportvertrag für
  optionale externe Verbraucher.
- **Konfiguration:** `config/settings.py` definiert `TRADE_EXPORT_DIR`
  (`BASE_DIR / 'data' / 'agent_exports'`), `TRADE_EXPORT_FILENAME_PREFIX`
  (`'trade_selection'`) und `TRADE_EXPORT_TIMESTAMP_FORMAT`
  (`'%Y%m%d-%H%M%S'`). Das Zielverzeichnis wird beim Export angelegt.

**Festes CSV-Spaltenschema** (22 Spalten, Reihenfolge bindend =
`app/staging_export.py:STAGING_COLUMNS`):

```
trade_id, name, chamber, Member_Party,
ticker, issuer_ticker, asset_name,
transaction_type, direction,
transaction_date, disclosure_date, days_since_trade,
amount, trade_volume_usd, trade_amount_max_usd,
sector_yf, industry_yf, relevant_sectors,
committee_match, Member_Committees_Full,
exit_status, export_ts
```

- Die meisten Quellspalten stammen aus dem Master. `days_since_trade` wird beim
  Aufbau des `recent_pool` berechnet; `relevant_sectors` und `committee_match`
  werden beim App-Load aus den Masterdaten abgeleitet. Fehlt eine Quellspalte,
  wird sie schema-stabil leer geschrieben (Warnung im Log).
- `direction`: falls in der Zeile leer/fehlend, lokal in
  `app/staging_export.py` aus `transaction_type` als `'buy'`, `'sell'` oder
  `'other'` abgeleitet; das Exportmodul importiert kein Signal- oder externes
  Workflow-Package.
- `export_ts`: ISO-Zeitpunkt des Exports.

---

## Committee-Relevanz (Modul 4)

`_apply_committee_relevance` schreibt `Insider_Sectors` und `Relevant_Sectors` in den Master.
`Relevance_Score` wurde entfernt (Refactor 2026-05-21).

**Score-Beitrag** läuft über `SCORING_WEIGHTS` in `config/settings.py`:
- `committee = 10` — für jeden Trade mit `len(Relevant_Sectors) > 0`
- `committee_full = 5` — zusätzlich, wenn mind. ein Eintrag `match_type='full'`
- Voll-Treffer ergibt 10 + 5 = 15 (entspricht dem Wert vor dem Refactor)

`COMMITTEE_EX_OFFICIO_ROLES` (Default: Chairman, Ranking Member, Vice Chairman, Cochairman, EXOfficio) bleibt konfigurierbar.
`RELEVANT_SECTORS_MATCH_MODE` steuert welche Einträge in `Relevant_Sectors` landen (Default `'sector_and_industry'`).

### Relevant_Sectors (Intersection) vs. Insider_Sectors

- **`Insider_Sectors`** = JSON-Liste aller (sub)committee-Sektoren des Members
  (frueher: `Relevant_Sectors`). Inhalt haengt nur am Member, nicht am Ticker.
- **`Relevant_Sectors`** (neu) = Teilmenge von `Insider_Sectors`, deren Eintraege
  zum gehandelten Ticker passen (Match gegen `sector_yf` / `industry_yf`).
  Match-Modus konfigurierbar in `config/settings.py`:
  - `RELEVANT_SECTORS_MATCH_MODE = 'sector_or_industry'` (Default): Eintrag matcht,
    wenn Sektor ODER Industry passt.
  - `'sector_and_industry'`: nur Eintraege mit Sektor UND Industry-Match.
  - `'hybrid'`: bevorzuge Sektor+Industry-Matches, sonst Fallback auf
    sector_or_industry.

`Confidence_Level` wurde entfernt — die Info liegt pro Eintrag in `Insider_Sectors`
unter dem `confidence`-Key.

**Beispiele**:
- Member im Energy-Committee, Trade in NEE (Utilities/Utilities-Regulated Electric): kein Sektor-Match → **0**
- Rick Larsen (Transportation) + WAB (Industrials/Railroads): Sektor matcht über Subcommittee → **33**
- Jennifer McClellan (Energy & Commerce) + VZ (Communication Services/Telecom Services): Sektor + Industry matchen → **66**
- Chairman des Energy-Committees + ENPH (Technology/Solar): Sektor + Industry matchen, ex officio → **(33+33) × 1.5 = 99**

---

## Datei-Generierung

### CSV aus xlsx erzeugen (Skript entfernt, Debug-Intervall 4):
Das fruehere `scripts/convert_xlsx_to_csv.py` existiert nicht mehr; der
geschützte Mapping-Master bleibt bis zur freigegebenen V2-Integration unverändert (Abschnitt 3a).

Erzeugte damals:
- `yf_sectors_with_industries.csv`
- `subcommittee_sector_industry_mapping.csv`
- `subcommittee_region_mapping.csv`

---

## Update-Frequenzen

| Datei | Update | Quelle |
|-------|--------|--------|
| house_trades.csv / senate_trades.csv | Bei Scraper-Lauf | house_scraper.py / senate_scraper.py |
| house/senate committees.csv | Alle 2 Jahre | PDF-Extraktion (Wahlen) |
| subcommittee_sector_mapping.xlsx | Bei Bedarf | Manuell (neue Committees) |
| trades_master.csv | Bei jedem Import | Automatisch |
| name_aliases.csv | Bei Bedarf | Manuell (neue Varianten) |

---

## Wichtige Hinweise

### 1. Committee String Parsing
- **Komplex**: Verschachtelte Struktur mit 3 Separatoren (`;`, `|`, `:`)
- **Chamber-spezifisch**: Senate hat "Committee on ..." Prefix
- **Normalisierung**: Lowercase, Strip, Prefix-Handling

### 2. Sector_Rank Bedeutung
- **NICHT** die Qualität des Mappings (das ist `Konfidenz`)
- **SONDERN** die Priorität des Sektors für dieses Subcommittee
- Beispiel: Agriculture/Livestock → Consumer Defensive (Rank 1), Healthcare (Rank 3)

### 3. Ex Officio Status
- **Definition**: Chairman oder Ranking Member eines **Hauptcommittees** (nicht Subcommittee)
- **Automatisch berechnet** beim PDF-Import
- **Gewichtung**: Alle Ex Officio Rollen bekommen 1.5x Multiplier

### 4. JSON in CSV
- `Insider_Sectors`, `Relevant_Sectors` und `Vote_Timing_Hits` sind JSON-Strings in CSV
- **Beim Lesen**: `json.loads(row['Insider_Sectors'])` / `json.loads(row['Vote_Timing_Hits'])`
- **Beim Schreiben**: `json.dumps(list_or_dict)`
- `Vote_Timing_Hits` enthaelt **alle** Treffer im Fenster (frueher nur der naechstgelegene
  Vote). Anzahl liegt in `Vote_Timing_Hit_Count`.

---

## 8. Dokumentation

### `organisation/Filter_Instanz_Parameter.md`
**Quelle**: Zusammenstellung aus config/settings.py
**Format**: Markdown
**Erstellt**: 2026-02-20 (umbenannt 2026-05-17)

**Zweck**: Dokumentation der konkreten Filter-Instanzen (Gates / Tags / Signals) mit Parametern, Schwellen und Implementierungsstatus

**Inhalt**:
- Filter 1-7: Alle Parameter mit Implementierungsstatus
- Committee Scoring: Rank Weights, Confidence, Role Multiplier
- Gesamt-Scoring: Gewichtungen (Summe = 100%)
- Score-Schwellen: 0-29, 30-49, 50-74, 75-100
- Email-Alert Threshold: 50

**Status-Übersicht**:
- ✅ Filter 3 (Committee): Code vollständig implementiert
- ✅ Filter 1, 2, 6, 7: Parameter definiert, Code ausstehend
- ⏸️ Filter 4, 5: Vorerst übersprungen

**Verwendung**: Referenz für Filter-Implementierung und Scoring-System

---

---

## 8. Congress API Daten (Modul 6)

**Quelle**: `scrapers/fetch_congress_data.py` (Congress.gov API v3)
**Pfad**: `data/congress_api/`
**Update**: Erstlauf (~7-8h), danach wöchentlich `--update` (~5-30 Min)

```bash
python scrapers/fetch_congress_data.py           # Erstlauf (alle 6 Schritte)
python scrapers/fetch_congress_data.py --update  # Delta (nur geänderte Bills)
python scrapers/fetch_congress_data.py --force   # Erstlauf erzwingen
```

---

### `data/congress_api/bills_list.json`
**Schritt 1** (~30 Min) — Übersicht aller Bills des Kongresses
**Felder**: `congress, number, type, bill_type, title, originChamber, updateDate, latestAction`

---

### `data/congress_api/bill_details.json`
**Schritt 2** (~1.5-2h) — Details pro Bill inkl. policyArea
**Felder**: `bill_id, bill_type, number, title, short_title, policy_area, introduced_date, latest_action, sponsors, cosponsors_count`

**Hinweis**: `policy_area` ist nur über den Bill-Detail-Endpoint verfügbar, nicht in der paginierten Bill-Liste.

---

### `data/congress_api/bill_actions.json`
**Schritt 3** (~1-2h) — Alle Actions pro Bill inkl. Floor Votes
**Felder pro Action**: `date, text, type, action_code, source_system, committee, roll_call`

**Wichtig**: `roll_call` enthält `[{chamber, congress, date, rollNumber, url}]` für namentliche Abstimmungen (Vote-Timing-Filter nutzt `type="Floor"` + vorhandenes `roll_call`).

---

### `data/congress_api/bill_committees.json`
**Schritt 4** (~1h) — Committee-Zuweisungen pro Bill
**Felder**: `bill_id, policy_area, committees: [{name, chamber, system_code, subcommittees}]`

---

### `data/congress_api/bill_summaries.json`
**Schritt 5** (~1.5h) — CRS Summaries pro Bill (für AI-Agent geplant)
**Felder**: `bill_id, policy_area, summaries: [{action_date, text, version_code}], has_summary`

---

### `data/congress_api/bill_subjects.json` ⭐
**Schritt 6** (~2.5h) — Legislative Subjects pro Bill (**löst Rider-Problem**)
**Felder**: `bill_id, policy_area, legislative_subjects: [str], subject_count`

**Zweck**: Omnibus-Bills (z.B. HR 1) haben eine einzelne `policy_area` aber decken viele Themen ab.
Legislative Subjects listen alle Themen explizit auf und erfassen auch Rider.

**Beispiel HR 1**:
```json
{
  "bill_id": "hr-119-1",
  "policy_area": "Economics and Public Finance",
  "legislative_subjects": ["Energy policy", "Oil and gas", "Medicaid",
                            "Immigration enforcement", "Defense spending", ...],
  "subject_count": 238
}
```

---

### `data/congress_api/fetch_checkpoint.json`
**Felder**:
- `completed_steps` — abgeschlossene Schritte (für Resume bei Abbruch)
- `last_full_run` — Timestamp des letzten vollständigen Erstlaufs
- `last_update_run` — Timestamp des letzten `--update` Laufs
- `update_count` — Anzahl Delta-Updates seit Erstlauf

---

### `tags/vote_timing_tag.py`
**Klasse**: `VoteTimingTag` (User-Sicht „Filter 4")

**Methoden**:
- `load_votes()` → laedt Vote-Daten aus `bill_actions.json`/`bill_subjects.json`
- `apply(trades_df, votes_df)` → reichert DataFrame mit Vote_Timing_* Spalten an

**Matching-Logik**:
1. Trade-Sektoren aus `Relevant_Sectors` (Intersection Insider × Ticker aus Modul 4) extrahieren
2. Votes mit uebereinstimmendem `mapped_sectors` finden
3. Zeitfenster: `trade_date ZWISCHEN (vote_date - AFTER) UND (vote_date + BEFORE)`
4. **Alle** Treffer im Fenster werden in `Vote_Timing_Hits` (JSON-Liste) gesammelt,
   `Vote_Timing_Hit_Count` enthaelt die Anzahl. Pro Treffer:
   `{date, bill_title, policy_area, days_to_vote, konfidenz}`.

**Parameter** (aus settings.py):
- `VOTE_TIMING_WINDOW_BEFORE = 30` — max Tage VOR der Abstimmung
- `VOTE_TIMING_WINDOW_AFTER = 5` — max Tage NACH der Abstimmung (Toleranz)
- `VOTE_DATA_TTL_HOURS = 24` — Auto-Refresh-Schwelle fuer bill_actions/bill_subjects

Hinweis: `Vote_Timing_Score`/`Vote_Date`/`Vote_Bill_Title`/`Vote_Policy_Area`/`Days_To_Vote`
existieren nicht mehr — alle Treffer liegen jetzt in `Vote_Timing_Hits`.

---

## Zukünftige Erweiterungen

### Senate-Vote-Parsing
**Zweck**: Senate-Votes aus XML-Roll-Call-Dateien parsen
**Quelle**: clerk.senate.gov XML-Dateien (separater Endpoint)

### Modul 5: Ticker → Sector Lookup
**Benoetigt**:
- yfinance Integration ODER
- Pre-built ticker→sector mapping CSV

**Ziel**: Filter `Relevant_Sectors` auf tatsaechlichen Ticker-Sector → Boost bei Exact Match

---

Erstellt: 2026-02-20
Letzte Aktualisierung: 2026-05-14 (Spalten-Refactoring: API-Provider-Suffixe)

### Aenderungs-Historie

- **2026-05-14** — Provider-Suffixe an externe-API-Spalten:
  - OpenFIGI-Spalten bekommen Suffix `_figi`:
    `canonical_ticker → ticker_figi`,
    `canonical_name → asset_name_figi`,
    `security_type → security_type_figi`,
    `market_sector → market_sector_figi`.
    Die ID-Spalte `figi` selbst bleibt unveraendert.
  - yfinance-Spalten bekommen Suffix `_yf`:
    `Ticker_Sector → sector_yf`,
    `Ticker_Industry → industry_yf`,
    `quote_type → quote_type_yf`.
  - Konsistent auf drei Ebenen: CSV-Output, Python-Dataclasses
    (`NormalizedTicker`, `SectorInfo`), SQLite-Tabelle `ticker_normalizer`
    (DROP+CREATE-Migration; Backup liegt in `data/cache/`).
  - Curated-Schema (`settings.CURATED_COLUMNS` + `CURATED_COALESCE`)
    referenziert die neuen Namen.
  - Geaendert: `utils/ticker_normalizer.py`, `utils/ticker_sector.py`,
    `scripts/import_trades.py`, `config/settings.py`,
    `tests/test_ticker_*.py` (98 Tests gruen).
- **2026-03-13** — House PDF Extractor v2 hinzugefuegt.
