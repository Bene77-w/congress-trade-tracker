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

Weitere Blätter: `jurisdiction` (`unit_id, chamber, committee_name,
unit_name, input_basis, jurisdiction_text` — Lesehilfe), `taxonomy`
(`industry_id, industry_name, sector_name, description`; zugleich Auswahlliste
der Spalte `industry_id`), `agent_notes` (`unit_id, engine, model,
label_uncertain, uncertainty_note, broad_scope, broad_scope_reason, sources,
dissens` — Begründungs- und Adjudikationsmaterial, außerhalb der
Arbeitstabelle), `info` (Provenienz je Unit: `unit_id, snapshot_id,
taxonomy_content_hash, mapping_input_hash, prompt_sha256, prompt_version,
engine, model, effort, prompt_delivery, created_at`).

Die maschinellen Zwischentabellen liegen unter `labeling/collected/` als
`labels_<engine>.csv`, `notes_<engine>.csv` und `info_<engine>.csv`; `merge`
schreibt dieselben drei Dateien unter `merged` sowie `labeling/disagreements.md`.
Die bearbeitete Datei ist `goldset/goldset_review.xlsx`.

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
| `merge --base … --against …` | zwei Engineläufe vergleichen, `disagreements.md` erzeugen, `labels/notes/info_merged.csv` schreiben | nein |
| `to-xlsx [--source …] [--out …] [--force]` | neue Reviewmappe aus den CSV erzeugen, alle Zellen als Text | nein |
| `verify [--source …] [--file …] [--fix-case]` | bearbeitete XLSX gegen Snapshot, Vertrag und Zwischentabelle prüfen | nein |

`--source` ist bei `to-xlsx` und `verify` gleichbedeutend: `merged` (Default)
oder ein Enginename.

`to-xlsx` baut eine neue Mappe mit fünf Blättern (`labels`, `jurisdiction`,
`taxonomy`, `agent_notes`, `info`); es füllt keine bestehende Vorlage. Eine vorhandene
Zieldatei blockt den Aufruf (`GS_XLSX_OVERWRITE`), da sie Benes Abnahmearbeit
trägt; `--force` sichert sie vorher als `<name>.<zeitstempel>.bak.xlsx`.

`verify` ist die Wache gegen den `packaged-foods`-Vorfall und zugleich die
zweite fachliche Prüfung — Bene darf jede Zeile ändern, löschen und ergänzen,
weshalb der Vertrag hier erneut gilt und nicht nur bei `collect`. Geprüft wird:

- Schreibweise (`GS_INDUSTRY_CASE`) und Randleerraum (`GS_XLSX_WHITESPACE`),
  beide mit `--fix-case` behebbar; unbekannte IDs mit Hinweis auf eine nur in
  der Unicodeform abweichende ID;
- Formelzellen, Doppelzeilen — auch zwei `no_mapping`-Zeilen derselben Unit —,
  Zuordnung und `no_mapping_reason` zugleich, fehlende Units,
  Confidencebereich;
- Belegregel, Anzahlgrenze, Broad-Scope-Bedingungen und Pflichtfelder je Unit
  — auch für Units ohne Zuordnung, bei denen die Ausnahme gar nicht zulässig
  ist;
- Rücklesevergleich gegen `labels_<source>.csv`: die Änderungsbilanz nennt je
  Zelle alt und neu sowie neue und entfernte Zeilen. Gleiche Zeilen werden
  einzeln gezählt, damit eine Dublette nicht als Änderung der ersten erscheint;
  innerhalb einer Schlüsselgruppe werden unveränderte Zeilen zuerst gepaart,
  sodass bloßes Umsortieren keine Änderung meldet. Sie ist Auskunft, kein
  Befund — ohne sie blieben Änderungen an `evidence_excerpt`,
  `rationale_short` und `no_mapping_reason` ungeprüft.

Ein Rückschreiben durch `--fix-case` geht über eine Nebendatei und hängt sie
erst am Ende um; vorher entsteht eine Sicherung. Liegt der Sperrvermerk
`.~lock.<name>#` daneben, blockt der Aufruf (`GS_XLSX_LOCKED`), da das
geöffnete Programm die Änderung sonst beim nächsten Speichern verwürfe.

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
- Claude (umgesetzt): `-p --system-prompt <vertrag> --json-schema <schema>
  --output-format json --model … --effort … --tools WebSearch,WebFetch
  --safe-mode --no-session-persistence --strict-mcp-config`. `--safe-mode` hält
  CLAUDE.md, Skills, Plugins, Hooks und MCP-Server draußen, `--tools` lässt
  genau die Websuche übrig: keine Datei-, Bash- oder Editierwerkzeuge.
- Codex (umgesetzt): `codex exec --output-schema <schema>
  --output-last-message <datei> --json --sandbox read-only
  --skip-git-repo-check --ignore-user-config -C <tmpdir> -m … -c
  model_reasoning_effort="…" -c tools.web_search=true`, dazu ein eigenes
  `CODEX_HOME` je Lauf im Arbeitsverzeichnis mit `auth.json` als Symlink.
  `--search` kennt `codex exec` in Fassung 0.150 nicht; die Websuche wirkt
  ausschließlich über den Konfigurationsschlüssel, nachgewiesen an den
  `web_search`-Ereignissen im Protokoll.

Verbleibende Grenze bei Codex, offen: die globalen Skills aus
`~/.agents/skills/` werden auch mit eigenem `CODEX_HOME` geladen; ein
Abschalter existiert in Fassung 0.150 nicht (`--disable skills` meldet
„Unknown feature flag"). Das eigene Home entfernt `~/.codex/AGENTS.md` und
`config.toml` — messbar an den Eingabetokens (14.384 auf 13.022) —, die Skills
bleiben. `--sandbox read-only` erlaubt außerdem Lesezugriff außerhalb des
Arbeitsverzeichnisses. Bei Claude besteht die Lücke nicht. Ob Codex damit als
unabhängige Zweitengine taugt, entscheidet Bene.
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
- Der Gesamtdeckel kappt den nächsten Unterprozess auf die Restzeit, statt erst
  zwischen zwei Units zu greifen; sonst wäre er um einen vollen Unitdeckel
  überziehbar. Die Restzeit wird aufgerundet, damit `Gesamtdeckel ==
  Unitdeckel` nicht null Läufe ergibt. Ein so gekappter Abbruch wird als
  Restzeit des Gesamtdeckels gemeldet und nicht als Hänger. Offene Units
  ergeben Exit 2, damit ein unvollständiger Satz nicht als Erfolg gilt.
- Systemische Fehler (`GS_MODEL_UNKNOWN`, `GS_CLI_UNAUTHORIZED`, `GS_CLI_FLAG`,
  `GS_RATE_LIMITED`) brechen den Satz nach dem ersten Auftreten ab; sie treffen
  jeden weiteren Aufruf gleichermaßen.
- Die Denktiefe lehnt keine der beiden CLIs ab: Claude warnt auf stderr und
  rechnet mit der Standardstufe weiter (Exit 0), Codex nimmt jeden Wert
  kommentarlos an. Der Lauf blockt deshalb auf Claudes Warnung
  (`GS_EFFORT_REJECTED`). Wie belastbar die Stufe ist, leitet
  `effort_confirmation` aus dem Protokoll des jeweiligen Laufs ab und nicht
  aus dem Enginenamen: `bestaetigt` (die CLI nennt die Stufe selbst zurück),
  `ohne-warnung` (Claude hätte eine unbekannte Stufe sichtbar verworfen),
  `unbestaetigt` (Codex nimmt jeden Wert an) oder `verworfen`. Der Wert steht
  in der Provenienz jeder Antwort; `preflight` gibt bei `unbestaetigt` Exit 2.
- Die Wiederaufnahme prüft die vorhandene Antwort auf Fassung, Engine, Modell,
  Denktiefe, Zustellweg und die Bindungshashes, statt sich auf die Existenz der
  Datei zu verlassen; eine Abweichung meldet `GS_RESUME_MISMATCH` und labelt
  nicht ungefragt neu.
- `num_turns` und der von Claude gemeldete `total_cost_usd` werden
  protokolliert, nicht bewacht — eine USD-Wache entfällt bei Abo-Abrechnung.

Beide CLIs erzwingen dasselbe JSON-Schema; identisches Ausgabeformat ist damit
Vertrag, nicht Konvention. Schema je Unit: `unit_id, decision,
no_mapping_reason, broad_scope_exception, broad_scope_reason,
assignments[{industry_id, confidence, evidence_excerpt, rationale_short}],
sources[{url, belegt}], label_uncertain, uncertainty_note`. Als
`label_uncertain` markierte Units gehen unabhängig vom Konsens in die
Adjudikation; `uncertainty_note` nennt den Grund und steht im Blatt `agent_notes`.

Die Anzahl- und Broad-Scope-Regel des Modellvertrags wird nachgebaut, nicht
importiert — die Wache soll nicht dieselbe Auslegung teilen wie der Prüfling.
Nachgebaut sind: Obergrenze aus `unit_kind` und `input_basis` der Unit (zwei
bei `committee_parent_core` und bei `name_only`, sonst fünf), Ausnahme erst ab
drei Zuordnungen, gar nicht bei `no_mapping` und gar nicht bei den engen
Unittypen, `broad_scope_reason` mit sichtbarem Text, Confidence mindestens 3
sowie eigene Evidence und eigene Rationale je Industry. `collect` liest die
Vertragsfelder dabei so streng wie Pydantic: `broad_scope_exception` muss ein
echter Wahrheitswert sein (`bool("")` machte aus einem Textfeld sonst klaglos
ein Nein), Textfelder müssen Text sein (`str(None)` ergäbe sonst den sichtbaren
Wert `None`), die beiden Begründungsfelder sind Text oder leer (`False` wäre
sonst falsy und ergäbe stillschweigend „keine Begründung"), Pflichttexte gelten
nur mit sichtbarem Inhalt — unsichtbare
Formatzeichen bestünden ein `strip()` —, und `mapped` duldet keinen
`no_mapping_reason`, auch keinen leeren.

Zwischenformat ist CSV mit UTF-8 und `QUOTE_ALL`; XLSX schreibt allein
`to-xlsx`, `industry_id`-Zellen als Text. Die Auswahlliste des Blatts `labels`
deckt 200 Zeilen über den Datenbestand hinaus ab, damit angehängte Zeilen sie
noch tragen.

Der Unterprozess bekommt nur eine Positivliste von Umgebungsvariablen
(`ENV_ALLOWED`): ein geerbtes `OPENAI_BASE_URL` lenkte den Lauf sonst still auf
einen anderen Endpunkt um, während das Manifest weiterhin den angefragten
Modellnamen trüge.

## 6. Phasen und Gates

Stand 2026-08-28:

| Phase | Stand |
|---|---|
| P1 Aufgabenpakete und Trockenlauf | umgesetzt; Testsatz grün, netzwerk- und CLI-frei |
| Gate A erster bezahlter Lauf | offen, wartet auf Benes Freigabe |
| P2 Vollauf | offen |
| P3 Merge | offen, nur bei Doppelvergabe |
| Gate B Abnahme | offen |
| P4 Übernahme | offen |

Reviewstand:

| Themenbereich | Ergebnis | Stand der Befunde |
|---|---|---|
| 1 Fundament, Lader, `prepare` | mit Auflagen abgenommen | erledigt |
| 2 Engines, `preflight`, `run` | nicht abgenommen | alle 13 Befunde behoben |
| 3 `collect`, `merge`, `to-xlsx`, `verify`, Trockenlauf, Tests | nicht abgenommen | vier Auflagen abgearbeitet (siehe unten) |

Auflagen des Reviews zu Themenbereich 3, Stand 2026-08-29 erledigt: die vier
HOCH-Befunde (Überschreibschutz für `to-xlsx`, `verify` ohne Trimmen,
Rücklesevergleich gegen `labels_*.csv`, vollständige Anzahl- und
Broad-Scope-Regel), `system_prompt_sha256` in der Wiederaufnahmeprüfung, der
Vorrang des HTTP-Status vor den Textmarken, Tests für Gesamtdeckel,
`run_codex`-Verdrahtung und `to-xlsx` ohne `--source` sowie dieser Abschnitt
und §5. Zusätzlich behoben: fachliche Nachprüfung in `verify`, Provenienz
beider Engines im Merge, unteilbares Rückschreiben mit Sperrvermerkprüfung,
`effort_confirmation` aus dem Protokoll, Positivliste der Umgebung, Reserve der
Auswahlliste, Unicode-Ähnlichkeitshinweis, Exit-Codes von `preflight` und
`merge`.

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

`to-xlsx --out data/committee_matcher_v2/goldset/development_goldset.xlsx
--force` erzeugt die Reviewmappe und sichert die leere Altvorlage dabei selbst
als `<name>.<zeitstempel>.bak.xlsx`. Nach Benes Bearbeitung läuft `verify
--source <quelle>`; ein `verify --fix-case` repariert Schreibweise und
Randleerraum und sichert die Datei vorher ebenfalls.

Abnahme: alle 22 Units vertreten; jede `industry_id` zeichengenau im Snapshot;
keine Unit mit Zuordnung und `no_mapping_reason` zugleich; Belegregel,
Anzahlgrenze, Broad-Scope-Bedingungen und Pflichtfelder auch für die von Hand
geänderten Zeilen eingehalten; die Änderungsbilanz des Rücklesevergleichs
gegen `labels_<source>.csv` nennt ausschließlich Änderungen, die Bene bewusst
vorgenommen hat.

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
| `GS_EFFORT_REJECTED` | Claude verwirft den Effortwert und rechnet mit der Standardstufe | zulässige Stufe setzen; die Warnung nennt sie |
| `GS_EFFORT_UNCONFIRMED` | Die CLI nennt die Denktiefe nicht zurück (Codex) | Stufe gegen die CLI-Dokumentation prüfen; Provenienz weist sie als unbestätigt aus |
| `GS_WEBSEARCH_OFF` | Antwort ohne `sources` | Log der Unit auf `web_search`-Ereignisse prüfen; fehlen sie, greift der Suchschalter nicht und der Satz ist zu wiederholen |
| `GS_TIMEOUT` | Deckel je Unit erreicht | Unit einzeln wiederholen; Satz bleibt erhalten |
| `GS_RATE_LIMITED` | Abo-Ratenlimit | Lauf später fortsetzen, `run` nimmt wieder auf |
| `GS_SCHEMA_INVALID` | Antwort verletzt das JSON-Schema | Rohantwort im Log, Unit wiederholen |
| `GS_UNIT_ID_MISMATCH` | Antwort trägt fremde oder veränderte `unit_id` | Unit wiederholen |
| `GS_INDUSTRY_UNKNOWN` | `industry_id` nicht im Snapshot | Auswahlliste oder `--fix-case` |
| `GS_EVIDENCE_NOT_VERBATIM` | Zitat steht nicht wörtlich im Jurisdiktionstext | Unit wiederholen, Zitat prüfen |
| `GS_COUNT_EXCEEDED` | mehr Zuordnungen, als der Unittyp zulässt (2 bei `committee_parent_core` und `name_only`, sonst 5) | Zeilen streichen |
| `GS_BROAD_SCOPE_INVALID` | Ausnahme ohne alle ihre Bedingungen, bei `no_mapping` oder bei einem engen Unittyp | Bedingung erfüllen oder auf 2 Zuordnungen kürzen |
| `GS_DECISION_CONFLICT` | Unit mit Zuordnung und `no_mapping_reason` zugleich | eine der beiden Angaben entfernen |
| `GS_UNIT_MISSING` | Unit fehlt in der bearbeiteten Datei | Zeile ergänzen |
| `GS_UNIT_DUPLICATE` | identische Zuordnung doppelt | Zeile löschen |
| `GS_SNAPSHOT_DRIFT` | `taxonomy_content_hash` weicht vom Aufgabenpaket ab | `prepare` neu ausführen |
| `GS_CORPUS_DRIFT` | Jurisdiktionstext seit `prepare` verändert | `prepare` neu, betroffene Units erneut labeln |
| `GS_RUN_FAILED` | Fehlschlag ohne bekanntes Merkmal | Rohausgabe im Log der Unit lesen |
| `GS_ANSWER_AMBIGUOUS` | Abschlussdatei enthält mehr als ein JSON-Objekt | Unit wiederholen; welches die Antwort ist, wäre geraten |
| `GS_ANSWER_FORMAT` | abgelegte Antwort in fremder Fassung | Unit mit `--force` wiederholen |
| `GS_RESUME_MISMATCH` | vorhandene Antwort passt nicht zum aktuellen Lauf | Antwort prüfen, mit `--force` neu erzeugen |
| `GS_INDUSTRY_CASE` | `industry_id` weicht nur in der Schreibweise ab | `verify --fix-case` |
| `GS_XLSX_MISSING` / `GS_XLSX_LAYOUT` / `GS_XLSX_FORMULA` | Reviewdatei fehlt, hat fremdes Blattbild oder eine Formelzelle | `to-xlsx` neu erzeugen; Formelzelle als Text eintragen |
| `GS_XLSX_WHITESPACE` | Zellwert mit Leerraum am Rand | `verify --fix-case` |
| `GS_XLSX_OVERWRITE` | `to-xlsx` träfe eine vorhandene Reviewdatei | Datei umbenennen oder `--force` (sichert vorher) |
| `GS_XLSX_LOCKED` | Sperrvermerk `.~lock.<name>#` daneben; Datei geöffnet | Datei schließen und wiederholen |
| `GS_LABELS_MISSING` / `GS_LABELS_FORMAT` | Zwischentabelle fehlt oder hat fremde Spaltenfolge | vorherigen Schritt wiederholen |
| `GS_MERGE_SCOPE` | Vergleich zweier Läufe nicht durchführbar | zwei verschiedene Engines angeben |
| `GS_FAKE_MODE` | `GOLDSET_LABEL_FAKE_DIR` zeigt ins Leere | Variable auf ein Verzeichnis setzen oder löschen |
| `GS_SETTING_INVALID` | aufgelöster Laufparameter unbrauchbar | Wert an der genannten Stelle korrigieren |
| `GS_TASK_MISSING` / `GS_TASK_FORMAT` / `GS_TASK_LAYOUT` | Aufgabenpaket fehlt, hat fremde Fassung oder das Verzeichnis fremden Inhalt | `prepare` neu ausführen |
| `GS_CONTRACT_INVALID` / `GS_TAXONOMY_INVALID` / `GS_CORPUS_BLOCKED` | Modellvertrag, Taxonomie oder Korpus unbrauchbar | Quelle an der genannten Stelle prüfen |
| `GS_SAMPLE_INVALID` / `GS_SAMPLE_STALE` / `GS_UNIT_UNKNOWN` | Stichprobendatei unbrauchbar oder gegen den Korpus veraltet | Stichprobe gegen den Korpus abgleichen |
| `GS_EVIDENCE_MISMATCH` | geladener Jurisdiktionstext deckt sich nicht mit der Quelldatei | Abschnitt in der Korpusdatei gegen den Parser prüfen |

`GS_SNAPSHOT_DRIFT` und `GS_CORPUS_DRIFT` sind die stillen Risikostellen: ohne
Prüfung labelt der Agent gegen einen Stand, den die Evaluation später nicht mehr
kennt. Beide werden bei jedem `run` und jedem `collect` geprüft.

## 8. Dokumentation

Erledigt: `organisation/DATA_STRUCTURE.md` (Abschnitt
`data/committee_matcher_v2/goldset/`) und
`organisation/CLI_Bedienungsanleitung.md` (Bedienablauf, Kommandotabelle,
`GS_`-Exit-Codes, Trockenlauf).

Offen bis nach P4: `organisation/committee_matcher_goldset_stichprobe.md`
(Abschnitt Labelvorlage auf das Long-Format umstellen), `CHANGELOG.md`.
