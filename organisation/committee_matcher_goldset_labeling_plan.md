# Committee Matcher V2 — Agentengestütztes Labeln des Entwicklungs-Gold-Sets

Stand 2026-08-28. Werkzeug, das die 22 Units aus
`organisation/committee_matcher_goldset_stichprobe.md` von einem starken Agenten
labeln lässt — headless über Codex oder Claude Code, gleiche Eingabe, gleiches
Ausgabeformat. Zielartefakt: `data/committee_matcher_v2/goldset/development_goldset.xlsx`.

Evaluation, die darauf aufsetzt: `organisation/committee_matcher_v2_implementierungsplan.md`, P9.

## 1. Entscheidungen vom 2026-08-28

| Punkt | Entscheidung |
|---|---|
| Status der Labels | Vorschlag; Gold-Set entsteht erst durch Benes Abnahme |
| Engines | gleichwertig und austauschbar, per `--engine` gewählt; Doppelvergabe optional |
| Modell und Effort | freie Parameter ohne Whitelist, Defaults in `config/settings.py` |
| Abrechnung | beide CLIs über Abo; keine USD-Wache, stattdessen Laufzeitdeckel je Unit |
| Deckel | großzügig: 30 min je Unit, greift nur im Fehlerfall, nicht bei langem Nachdenken |
| Konsistenzpass | entfällt |
| Batchstrategie | eine Unit je Lauf |
| Trägerformat | XLSX für die Handarbeit, CSV nur maschinell; Begründung Abschnitt 3 |
| Satzformat | Long-Format nach dem Review-Vertrag, siehe Abschnitt 3 |
| Fehlerausgabe | benannte Codes mit Ursache, Abhilfe und Codestelle, siehe Abschnitt 7 |

## 2. Methodisches Kernrisiko

Ein von einem LLM erzeugtes Gold-Set misst Übereinstimmung mit einem starken
LLM, nicht Korrektheit. Gegenmaßnahmen:

- Abnahme durch Bene ist konstitutiv; ohne sie existiert kein Gold-Set.
- Isolation: der labelnde Agent sieht weder Repo noch Produktivartefakte,
  insbesondere nicht die Mappings aus `p8-run2` — sonst kopiert er den Prüfling.
- Optionale Doppelvergabe: beide Engines labeln denselben Satz, der
  Dissensbericht zeigt, wo die Aufgabe strittig ist, und begrenzt Benes
  Prüfaufwand auf diese Fälle. Empfohlen, da die Abo-Abrechnung den doppelten
  Lauf nur mit Zeit und Ratenlimit bezahlt.

Der Recherchezugang ist der Unterschied zum Prüfling: das Gold-Set darf
nachschlagen, der Produktivlauf sieht nur den Prompt.

## 3. Dateiformat des Gold-Sets

Der Matcher hat noch kein Gold-Set-Format: `evaluate goldset` ist in `cli.py`
registriert, aber unimplementiert (P9). Das Format wird deshalb hier festgelegt
und der spätere Import darauf gebaut. Maßstab ist der bestehende
Artefakt- und Reviewvertrag, nicht die bisherige Vorlage.

### Trägerformat: XLSX für die Handarbeit

Entschieden nach dem Versagensrisiko der Bearbeitung, nicht dem des Schreibens.

- CSV verliert beim Bearbeiten mehr, als sie beim Schreiben gewinnt: wird sie in
  Excel oder LibreOffice geöffnet und gespeichert, handelt das Programm im
  deutschen Locale Trennzeichen (Semikolon statt Komma) und Encoding neu aus.
  Die `evidence_excerpt`-Zitate enthalten Kommas und Anführungszeichen, die
  Umlaute brechen ohne BOM. Der Schaden ist still und trifft die ganze Datei.
- XLSX hält Struktur, Encoding und Zellinhalte verlustfrei. Das bekannte Risiko
  ist die Autokorrektur, und die ist gezielt abfangbar.

Drei Sperren gegen den `packaged-foods`-Vorfall, zwei davon aus dem Reviewpfad
bewährt (`committee_matcher/review.py:215` und `:235`):

1. Jede Zelle wird als Textzelle geschrieben (`data_type = "s"`); ein führendes
   `=` wird nie zur Formel.
2. Die Spalte `industry_id` bekommt die Auswahlliste aus dem Blatt `taxonomy`;
   Bene wählt, statt zu tippen. Die Liste warnt, sperrt aber nicht, damit
   Einfügen aus der Zwischenablage möglich bleibt.
3. `verify` liest zurück und vergleicht zeichengenau gegen den Snapshot. Weicht
   ein Wert nur in der Groß- und Kleinschreibung ab, meldet es ausdrücklich
   Autokorrektur und bietet `--fix-case` an. Genau diese dritte Sperre fehlte im
   P8-Lauf, weshalb `Packaged-foods` erst der Import bemerkte.

CSV bleibt als maschinelles Zwischenformat zwischen `collect`, `merge` und
`to-xlsx`; Bene bearbeitet sie nicht.

Blatt `labels`, eine Zeile je Zuordnung, Spalten wie `review.ASSIGNMENT_SHEET_COLUMNS`:

`unit_id, industry_id, confidence, evidence_excerpt, rationale_short, no_mapping_reason`

- Leere `industry_id` plus gefüllter `no_mapping_reason` heißt `no_mapping`;
  eine Unit hat entweder Zuordnungszeilen oder genau eine `no_mapping`-Zeile.
- Bedienung identisch zum erprobten Review-Workbook: Wert ändern, Zeile
  löschen, Zeile anhängen. Keine Steuerspalten.
- Verworfen: das bisherige Wide-Format `industry_id_1..5`. Es kann Confidence,
  Evidence und Rationale je Zuordnung nicht aufnehmen, obwohl der Modellvertrag
  sie je Zuordnung verlangt, und erzeugt mit den Slots eine Ordnung, die
  fachlich nicht existiert. Mengenmetriken lesen aus Long-Format direkt.

Weitere Blätter: `jurisdiction` (Volltext je Unit, Lesehilfe), `taxonomy`
(145 Industries, zugleich Auswahlliste der Spalte `industry_id`), `agent_notes`
(`unit_id, engine, model, label_uncertain, sources, dissens` — Begründungs- und
Adjudikationsmaterial, außerhalb der Arbeitstabelle), `info` (Provenienz:
`snapshot_id`, `taxonomy_content_hash`, `mapping_input_hash` je Unit,
Prompt-SHA-256, Engine, Modell, Effort, Erzeugungszeit).

Die bisherige Vorlage wird ersetzt; ihre Kopfdaten (`chamber`,
`committee_name`, `unit_name`, `input_basis`) sind aus der `unit_id` und dem
Korpus ableitbar und entfallen aus demselben Grund wie im Review-Workbook.

## 4. Batchstrategie — eine Unit je Lauf

- P9 vergleicht Batchgröße 1 gegen 3. Ein mit Batch 3 gelabeltes Gold-Set trüge
  denselben Kontexteffekt wie eine der Varianten und bevorzugte sie
  systematisch. Batch 1 ist die kontextärmste und damit neutralste Referenz.
- Kein Reihenfolge- und kein Nachbarschaftseffekt zwischen Units.
- Fehler bleiben unkorreliert; ein schlechter Lauf beschädigt eine Zeile.
- Recherchebudget je Unit voll verfügbar statt über 22 Units geteilt.
- Kosten sprechen nicht dagegen: Taxonomie 37.698 Zeichen ≈ 9,4k Token je Lauf.

Verworfen: je Committee ein Lauf (nur drei Committees stellen zwei Units,
Gewinn null, Kontextkopplung real); ein Lauf über alle 22 Units (korrelierte
Fehler, Ermüdung, Reihenfolgeeffekt, ein Abbruch kostet den ganzen Satz).

## 5. Architektur

`scripts/committee_matcher_goldset_label.py`

| Kommando | Wirkung | Bezahlt |
|---|---|---|
| `prepare` | Aufgabenpaket je Unit nach `data/committee_matcher_v2/goldset/labeling/tasks/` | nein |
| `preflight --engine … --model …` | Ein-Token-Lauf; prüft, ob Engine, Modell und Effort existieren | minimal |
| `run --engine … [--model …] [--effort …]` | je Unit ein headless Lauf, Rohantwort und Log je Unit | ja |
| `collect --engine …` | Rohantworten nach `labels_<engine>.csv`, formale Prüfung | nein |
| `merge` | zwei Engineläufe vergleichen, `disagreements.md` erzeugen | nein |
| `to-xlsx` | CSV in die Vorlage schreiben, Zellen als Text | nein |
| `verify` | bearbeitete XLSX gegen Snapshot und Vertrag prüfen | nein |

`verify` ist die Wache gegen den `packaged-foods`-Vorfall: es liest die von Bene
bearbeitete Datei zurück und meldet unbekannte oder umgeschriebene IDs,
Doppelzeilen, Units mit Zuordnung und `no_mapping_reason` zugleich sowie fehlende
Units.

### Modell- und Effortwahl

Reine Durchreichung ohne Whitelist, damit künftige Modelle ohne Codeänderung
funktionieren:

```
--engine claude --model opus-5.5    --effort high
--engine codex  --model gpt-5.6-sol --effort high
```

- Claude: `--model <wert> --effort <wert>`
- Codex: `-m <wert> -c model_reasoning_effort="<wert>"`

Defaults stehen in `config/settings.py` (`GOLDSET_LABEL_ENGINE`,
`GOLDSET_LABEL_MODEL_CLAUDE`, `GOLDSET_LABEL_MODEL_CODEX`,
`GOLDSET_LABEL_EFFORT`, `GOLDSET_LABEL_TIMEOUT_SECONDS`,
`GOLDSET_LABEL_TOTAL_TIMEOUT_SECONDS`). Ohne Whitelist fällt ein Tippfehler erst
beim Aufruf auf; genau dagegen steht `preflight` vor den 22 Läufen.

Die globale Codex-Konfiguration wird nicht angefasst und nicht gelesen:
`--ignore-user-config` schaltet `~/.codex/config.toml` für den Lauf ab, Modell
und Effort kommen ausschließlich aus den Skriptparametern. Deine
Standardeinstellung `gpt-5.6-luna` bleibt für die normale Codex-Nutzung
unverändert. Bei Claude überschreiben `--model` und `--effort` die
Sitzungseinstellung ebenfalls nur für den Lauf.

### Isolation gegen den Prüfling

- Erlaubte Importe: `committee_matcher.input_corpus`, `units`,
  `taxonomy_snapshot`, `prompts`, `settings_view`, `errors`, `paths` — Lader und
  Provenienzfunktionen, kein Netzwerk.
- Die Grenze verläuft zwischen Provenienz und Rendering, nicht entlang der
  Modulnamen. Inhaltshash der Taxonomie (`taxonomy_snapshot.hash_records`),
  Vertragshash und Frontmatterprüfung (`prompts.load_prompt`) sowie die
  Entschärfung der Datenblockrahmen (`prompts.untrusted_block`) werden bewusst
  geteilt: wären sie es nicht, könnte die Evaluation Gold-Set und Lauf nicht an
  denselben Stand binden, und ein abweichender Prompt-Rahmen wäre eine
  zusätzliche Variable im Vergleich. In Kauf genommen wird dafür, dass ein
  Fehler in diesen drei Funktionen beide Seiten gleich träfe — er beträfe
  Provenienz und Rahmung, nicht die fachliche Zuordnung, die gemessen wird.
- Verbotene Importe: `provider`, `workflow`, `mapping`, `artifacts`, `review`.
  Taxonomie- und Unit-Rendering baut das Skript selbst; ein Rendering-Fehler des
  Prüflings darf sich nicht in die Referenz vererben. Ein Selbsttest vergleicht
  den gerenderten Jurisdiktionstext zeichengenau gegen die Korpus-Markdown.
  Spaltennamen des Reviewvertrags werden als Literale geführt, nicht importiert.
- Regelvertrag: Text aus `data/config/committee_matcher_prompts/mapping_system.md`,
  vollständig weitergegeben, SHA-256 im `info`-Blatt; ergänzt um den
  Recherche- und Sorgfaltsauftrag.

### Isolation der Läufe

- Arbeitsverzeichnis ist ein temporäres Verzeichnis außerhalb des Repos, damit
  weder `CLAUDE.md`/`AGENTS.md` noch Projektartefakte in den Kontext geraten.
- Claude: `-p --output-format json --json-schema <datei> --tools WebSearch,WebFetch
  --system-prompt <vertrag> --add-dir <tmpdir>`; keine Datei- und keine
  Bash-Werkzeuge.
- Codex: `codex exec --output-schema <datei> -o <antwort.json> --json
  --sandbox read-only --skip-git-repo-check --ignore-user-config -C <tmpdir>`,
  Websuche über `--search` beziehungsweise `-c tools.web_search=true`; welche
  Form Version 0.150.1 in `exec` annimmt, klärt Phase 1.
### Deckel

Keine der beiden CLIs kennt ein Turn-Limit: `claude --help` führt keines,
`codex exec --help` ebenfalls nicht. Der einzige wirksame Deckel ist die
Wall-Clock-Zeit des Unterprozesses.

- `GOLDSET_LABEL_TIMEOUT_SECONDS = 1800` (30 min je Unit). Ein Lauf mit
  Recherche und hoher Denktiefe braucht erfahrungsgemäß 3 bis 10 min; der
  Deckel liegt bewusst um das Drei- bis Zehnfache darüber und schlägt nur bei
  Hänger, Endlosschleife oder wartendem Ratenlimit an.
- `GOLDSET_LABEL_TOTAL_TIMEOUT_SECONDS = 43200` (12 h für den ganzen Satz), rein
  als Notbremse für den unbeaufsichtigten Lauf.
- Ein Lauf im Deckel wird als `timeout` protokolliert und einzeln
  wiederholbar; der übrige Satz bleibt erhalten.
- `num_turns` und der von Claude gemeldete `total_cost_usd` werden
  protokolliert, nicht bewacht — eine USD-Wache entfällt bei Abo-Abrechnung.

Beide CLIs erzwingen dasselbe JSON-Schema; identisches Ausgabeformat ist damit
Vertrag, nicht Konvention. Schema je Unit: `unit_id, decision,
no_mapping_reason, broad_scope_exception, broad_scope_reason,
assignments[{industry_id, confidence, evidence_excerpt, rationale_short}],
sources[], label_uncertain`. Als `label_uncertain` markierte Units gehen
unabhängig vom Konsens in die Adjudikation.

Zwischenformat ist CSV mit UTF-8 und `QUOTE_ALL`; XLSX schreibt allein
`to-xlsx`, `industry_id`-Zellen als Text.

## 6. Phasen und Gates

### P1 — Aufgabenpakete und Trockenlauf (kostenfrei)

Skriptgerüst, Renderer, Schema, Settings-Defaults, `prepare`, `collect`,
`merge`, `to-xlsx`, `verify`. Ein Fake-Engine-Modus schreibt synthetische
Antworten und durchläuft die gesamte Kette ohne bezahlten Call.

Abnahme: 22 Aufgabenpakete; `unit_id`-Menge deckungsgleich mit der Stichprobe;
jeder Jurisdiktionstext zeichengenau identisch mit dem Korpus; alle 145
Industries im Paket; die beiden `name_only`-Units ohne Jurisdiktionstext;
Fake-Durchlauf erzeugt eine gültige XLSX, die `verify` besteht; Test auf die
verbotenen Importe grün; je Fehlercode aus Abschnitt 7 ein Test, der die
Störung herbeiführt und die Meldung samt Codestelle prüft — insbesondere
`GS_INDUSTRY_UNKNOWN` mit `Packaged-foods` und die `--fix-case`-Reparatur.

### Gate A — erster bezahlter Lauf

`preflight`, danach zwei Probeunits je gewählter Engine: Nr. 9 (`no_mapping`
erwartet) und Nr. 19 (vier bis fünf Zuordnungen). Vorgelegt werden Labels,
Rechercheverhalten, Turns und Laufzeit. Erst danach der Vollauf.

### P2 — Vollauf (je gewählter Engine)

22 Läufe, seriell, mit Wiederaufnahme: vorhandene Antworten werden nicht erneut
erzeugt. Abbruch bei Turn- oder Zeitüberschreitung, ohne den Satz zu verwerfen.

Abnahme: 22 schemakonforme Antworten; jede `industry_id` im Snapshot vorhanden;
jedes `evidence_excerpt` wörtlich im Jurisdiktionstext der Unit enthalten, bei
`name_only` im Unitnamen; Anzahlgrenzen und Broad-Scope-Regel eingehalten.

### P3 — Merge, nur bei Doppelvergabe

`merge` vergleicht je Unit beide Engines: identische Menge, Teilmenge,
Widerspruch, `decision`-Konflikt. `disagreements.md` stellt beide Begründungen
nebeneinander. Ohne Doppelvergabe entfällt die Phase; dann prüft Bene alle 22
Units statt nur der strittigen.

### Gate B — Abnahme durch Bene

Vorlage der befüllten XLSX. Bene entscheidet Dissens- und
`label_uncertain`-Fälle und prüft die übrigen. Ohne diesen Schritt entsteht kein
Gold-Set.

### P4 — Übernahme

`to-xlsx` schreibt in `development_goldset.xlsx`; die leere Altvorlage wird
vorher gesichert. Nach Benes Bearbeitung läuft `verify`.

Abnahme: alle 22 Units vertreten; jede `industry_id` zeichengenau im Snapshot;
keine Unit mit Zuordnung und `no_mapping_reason` zugleich; Rücklesetest
bestätigt die CSV-Werte unverändert.

## 7. Fehlerbild

Jede vorhersehbare Störung bekommt einen benannten Code und eine Meldung nach
demselben Muster wie `committee_matcher/errors.py`, aber ohne dessen Import:

```
[GS_INDUSTRY_UNKNOWN] labels!B14
  Was: 'Packaged-foods' steht nicht im Snapshot run-p4-spike.
  Warum: Excel-Autokorrektur hat den ersten Buchstaben groß gesetzt.
  Abhilfe: 'verify --fix-case' korrigiert die Schreibweise; oder Zelle aus dem
           Blatt taxonomy auswählen.
  Code: scripts/committee_matcher_goldset_label.py:412
```

Die Codestelle wird zur Laufzeit aus dem Modul und der Zeilennummer der
prüfenden Funktion ermittelt, nicht von Hand gepflegt.

| Code | Anlass | Kern der Abhilfe |
|---|---|---|
| `GS_CLI_MISSING` | `claude` oder `codex` nicht im PATH | Installationspfad prüfen |
| `GS_CLI_UNAUTHORIZED` | CLI meldet fehlende Anmeldung | interaktiv anmelden, dann erneut |
| `GS_MODEL_UNKNOWN` | Modellname von der CLI abgelehnt | `preflight` mit korrigiertem `--model` |
| `GS_EFFORT_REJECTED` | Effortwert abgelehnt | zulässige Stufe der jeweiligen CLI setzen |
| `GS_WEBSEARCH_OFF` | Antwort ohne `sources`, Websuche nicht aktiv | Codex-Flagvariante prüfen (Phase 1 klärt sie) |
| `GS_TIMEOUT` | Deckel je Unit erreicht | Unit einzeln wiederholen; Satz bleibt erhalten |
| `GS_RATE_LIMITED` | Abo-Ratenlimit | Lauf später fortsetzen, `run` nimmt wieder auf |
| `GS_SCHEMA_INVALID` | Antwort verletzt das JSON-Schema | Rohantwort im Log, Unit wiederholen |
| `GS_UNIT_ID_MISMATCH` | Antwort trägt fremde oder veränderte `unit_id` | Unit wiederholen |
| `GS_INDUSTRY_UNKNOWN` | `industry_id` nicht im Snapshot | Auswahlliste oder `--fix-case` |
| `GS_EVIDENCE_NOT_VERBATIM` | Zitat steht nicht wörtlich im Jurisdiktionstext | Unit wiederholen, Zitat prüfen |
| `GS_COUNT_EXCEEDED` | mehr als 2 ohne Broad-Scope oder mehr als 5 | Zeilen streichen oder Ausnahme begründen |
| `GS_DECISION_CONFLICT` | Unit mit Zuordnung und `no_mapping_reason` zugleich | eine der beiden Angaben entfernen |
| `GS_UNIT_MISSING` | Unit fehlt in der bearbeiteten Datei | Zeile ergänzen |
| `GS_UNIT_DUPLICATE` | identische Zuordnung doppelt | Zeile löschen |
| `GS_SNAPSHOT_DRIFT` | `taxonomy_content_hash` weicht vom Aufgabenpaket ab | `prepare` neu ausführen |
| `GS_CORPUS_DRIFT` | Jurisdiktionstext seit `prepare` verändert | `prepare` neu, betroffene Units erneut labeln |

`GS_SNAPSHOT_DRIFT` und `GS_CORPUS_DRIFT` sind die stillen Risikostellen: ohne
Prüfung labelt der Agent gegen einen Stand, den die Evaluation später nicht mehr
kennt. Beide werden bei jedem `run` und jedem `collect` geprüft.

## 8. Dokumentation

Nach P4: `organisation/DATA_STRUCTURE.md` (Verzeichnis `goldset/labeling/`,
neues Gold-Set-Format), `organisation/CLI_Bedienungsanleitung.md` (Skript mit
sieben Unterkommandos), `organisation/committee_matcher_goldset_stichprobe.md`
(Abschnitt Labelvorlage auf das Long-Format umstellen), `CHANGELOG.md`.
