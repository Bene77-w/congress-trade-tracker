# Implementierungsplan: Committee Matcher V2 – vollständige Neuplanung

Stand: 2026-08-20  
Status: P0–P4 abgeschlossen (providerfreier Kern steht); nächster Schritt ist Gate B, das bis zur Freigabe durch Bene gesperrt bleibt  
Verbindliche Grundlage: `organisation/committee_matcher_planung_entscheidungen_codex.md`  
Scope: Matching-Phase bis zu manuell reviewten V2-Artefakten

## 1. Ziel und Nicht-Ziele

Der alte Committee Matcher wird vollständig und separat wiederherstellbar
entfernt. Danach entsteht ein neuer Batch-Workflow:

```text
kanonische Committee-MDs
  -> Input- und Roster-Coverage-Prüfung
  -> frischer yfinance-Industry-Snapshot
  -> direktes Industry-Mapping über OpenRouter
  -> deterministische Validierung und gezielter Repair
  -> manueller Review
  -> Gold-Set-Evaluation und unabhängige Holdout-Qualifikation
  -> qualifizierter Full-Run und finaler Review
```

Primärziel ist hohe Precision beziehungsweise geringe False-Discovery-Rate.
Eine Unit darf regulär `no_mapping` erhalten.

Nicht Bestandteil dieser Planung sind:

- Beschaffung oder redaktionelle Aufbereitung der Committee-Quelltexte;
- Relevance, Market Cap, Market Weight, Börsenpräsenz oder Kurswirkung;
- vorgelagerte Sektorauswahl, LLM-Overview, Research-Agent oder LLM-Review;
- Promotion auf den produktiven Mapping-Master;
- Integration in `import_trades.py`, Committee-Tag, Signale, Scoring oder UI;
- produktive Nutzung der Roster-Mitgliedschaftsstatus.

## 2. Verbindliche Verträge

### 2.1 Inputbaseline und Migration

Der Corpus umfasst:

```text
46 MD   mapping_input
2  MD   source_only
46 TXT  raw_source
1  CSV  administrative_excluded
```

`data/committee_matcher_inputs/corpus_manifest.yaml` führt jede reguläre Datei
unter dem Inputroot genau einmal mit Rolle und SHA-256. Nur `mapping_input`
erzeugt Units. `source_files` darf nur `raw_source` oder `source_only`
referenzieren; alle 46 TXTs und beide Sammel-MDs müssen referenziert sein.

Vor der Schutzbaseline migriert ein versioniertes, idempotentes Skript mit
`--check`, `--apply` und `--report` die 46 Mapping-MDs auf das Template:

- `unit_input_basis.committee = jurisdiction_text`;
- Subcommittees standardmäßig `jurisdiction_text`, ausschließlich die acht
  markierten Fälle `name_only`;
- zwölf alte `input_basis:`-Felder und acht HTML-Ersatzmarker entfernen;
- Jurisdiktionstexte und Identitäten nicht inhaltlich verändern.

Erst der validierte Post-Migrations-Stand wird gehasht. Der Matcher liest die
Inputs danach ausschließlich read-only. `Zusätzliche Kommentare` und eindeutig
markierte Fremdanweisungen sind kein Mappinginput; Fremdanweisungen außerhalb
des Kommentarabschnitts blockieren mit Pfad und Zeile.

Der Parser nutzt begrenzte Datei-/Frontmattergrößen und `yaml.safe_load`,
blockiert Pfadtraversal, unbekannte Corpusdateien und widersprüchliche
Identitäten und korrigiert nichts durch Raten.

### 2.2 Units, Identität und Evidence

- Ohne Subcommittees entsteht `committee_direct`.
- Mit Subcommittees entstehen je H2 eine `subcommittee`-Unit und zusätzlich
  `committee_parent_core`; Parent-Core wird nie aus Child-Mappings aggregiert.
- Der aktuelle Sollbestand umfasst 225 Unit-IDs: 179 Subcommittees,
  31 Parent-Core und 15 Direct. Vollständigkeit wird über exakte Setgleichheit,
  nicht nur über die Anzahl geprüft.
- IDs und fachliche Inputhashes verwenden eine versionierte, deterministische
  Unicode-/Whitespace-/Slug-Kanonisierung. Kollisionen blockieren; exakte
  Quelldateibytes erhalten einen separaten Hash.
- Subcommittees erhalten Parent-Kontext, müssen eine Zuordnung aber mit eigenem
  Namen beziehungsweise eigenem H2-Text belegen. `name_only` darf nur den
  offiziellen Unit-Namen als Evidence verwenden. Kommentare und fremde Units
  sind ausgeschlossen.
- `committee_direct` und `committee_parent_core` belegen Zuordnungen mit dem
  eigenen Committee-Namen beziehungsweise Parent-Jurisdiktionstext.

Die Kanonisierung ist als `unit-id-v1`/`mapping-input-v1` festgeschrieben, in
`committee_matcher/units.py` implementiert und per Golden Test eingefroren;
Regelbeschreibung: `CLI_Bedienungsanleitung.md` 15a.

### 2.3 Roster-Coverage

Die vorhandenen House-/Senate-Roster werden read-only über
`utils.committee_parser.parse_committees_full` eingelesen. Roster-Daten prüfen
nur Coverage und beeinflussen keine fachliche Mappingentscheidung.

```text
coverage_status = mapped | excluded | missing
match_basis     = exact | alias | manual_exclusion | null
```

- `mapped`: Unit-ID vorhanden, Matchbasis `exact` oder `alias`;
- `excluded`: Unit-ID `null`, Matchbasis `manual_exclusion`;
- `missing`: Unit-ID und Matchbasis `null`, Handlungshinweis Pflicht.

Aliasse und Ausschlüsse liegen versioniert und begründet in
`data/config/committee_matcher_roster_rules.yaml`. Fuzzy Matching erzeugt nur
Vorschläge. House-/Senate-Vorkommen desselben Joint Committees werden auf eine
Joint-Unit normalisiert, behalten aber getrennte Provenienz.

### 2.4 yfinance-Snapshot

Jeder neue Run fragt die Taxonomie frisch ab; Resume verwendet den eingefrorenen
Snapshot desselben Runs:

1. gepinnte yfinance-Konstanten liefern nur Sector-Seeds;
2. `Sector(...).industries` entdeckt den Live-Bestand;
3. `Industry(...).overview` liefert Identität und Beschreibung;
4. Requestpause, begrenzte Retries und run-lokale Checkpoints sichern den Abruf;
5. erst ein vollständiger, kollisionsfreier Snapshot darf Modellcalls speisen.

Persistiert werden Industry-ID/-Name, Sector-ID/-Name und Description.
Mitgelieferte Market-Cap-/Weight-Felder werden am Adapterrand verworfen und
weder ausgewertet noch gespeichert oder an das Modell gesendet. Der Snapshot
erzeugt JSON, einen nach Sektoren gruppierten Markdown-Katalog und einen
`taxonomy_content_hash` ohne volatile Abrufzeiten.

Ohne freigegebene Baseline stoppt der erste Run mit `no_baseline`; relevante
ID-, Namens- oder Sector-Drift stoppt ebenfalls in
`awaiting_taxonomy_approval`. Freigabe und Resume sind an dieselbe `run_id` und
denselben Snapshot gebunden. Description-Änderungen werden protokolliert; ihre
Freigabepolicy wird vor Gate C festgelegt. Für Qualifikation und Full-Run zählt
jede Description-Änderung als neuer Taxonomieinhalt.

### 2.5 Mappingvertrag

Das Modell wählt direkt aus allen Industries des eingefrorenen Snapshots. Eine
Zuordnung ist nur zulässig, wenn die Jurisdiktion unmittelbar wesentliche
Produkte, Tätigkeiten oder Regulierung der Industry umfasst. Lieferkettennähe,
allgemeine Auswirkungen oder thematische Nachbarschaft reichen nicht.

```text
MappingBatchResult
  requested_batch_id
  units[]                   1..3; jede angeforderte Unit exakt einmal

MappingUnitResult
  unit_id
  decision                 mapped | no_mapping
  no_mapping_reason        string | null
  broad_scope_exception    bool
  broad_scope_reason       string | null
  assignments[]

Assignment
  industry_id
  confidence               1..4
  evidence_excerpt
  rationale_short
```

Regeln:

- 0 Zuordnungen ist regulär und verlangt `no_mapping_reason`;
- `no_mapping` besitzt keine Assignments und keine Broad-Scope-Ausnahme;
- 1–2 Zuordnungen sind der Normalfall;
- 3–5 sind nur für `subcommittee` oder `committee_direct` mit
  `broad_scope_exception`, getrennten Kernzuständigkeiten, eigener Evidence und
  Rationale je Industry sowie Confidence mindestens 3 zulässig;
- `committee_parent_core`, `name_only` und jede reguläre Unit bleiben bei
  maximal 2; mehr als 5 ist immer ungültig;
- Industry-IDs müssen exakt im Snapshot vorkommen; Sector und Namen werden
  lokal ergänzt;
- Evidence muss nach definierter Normalisierung als zusammenhängender
  Substring im erlaubten Kontext vorkommen;
- Python prüft nur formale Regeln. Fachliche Direktheit wird durch Prompt,
  Gold-Set, Holdout und manuellen Review abgesichert;
- ungültige Units gehen vollständig in einen gezielten Repair. Es gibt keine
  automatische Kappung, Löschung oder Confidence-Änderung.
- Confidence wird vor abgeschlossener Kalibrierung niemals als automatischer
  Lösch- oder Mappingfilter verwendet.

Mappingcalls enthalten 1 bis 3 Units. Konfigurierbar sind Batchgröße 1 oder 3;
ein Context-Split darf 3→2→1 erzeugen. Texte und Taxonomie werden nie still
gekürzt. Passt eine einzelne Unit einschließlich Output-/Reasoningreserve nicht
in das freigegebene Endpoint-Kontextfenster, blockiert der Call.

### 2.6 Provider, Kosten und Betriebszustand

Der Fachcode verwendet ein schmales providerneutrales Structured-Output-
Interface; V1 implementiert es ausschließlich über das offizielle, gepinnte
OpenRouter-Paket. SDK-Typen verlassen den Adapter nicht.

Vor dem ersten Call werden Modell, Routingbedingungen, Datenschutz, Preise und
Kontextgrenzen im Runmanifest eingefroren. Den Endpoint wählt seit dem
Gate-B-Beschluss (2026-08-21) OpenRouter innerhalb dieser Bedingungen; er wird
je Call protokolliert, aber nicht gebunden. V1 setzt:

- `require_parameters=true`;
- `allow_fallbacks=true`, kein `provider.only` und kein `provider.order`;
- Quantisierungsfilter `COMMITTEE_MATCHER_QUANTIZATIONS`, damit ein Lauf nicht
  4-Bit- und 16-Bit-Endpoints mischt;
- explizites `data_collection`/ZDR und `max_price`;
- keine SDK-Retries und genau einen gleichzeitigen Providercall.

Vor jedem Providerlauf prüft der Routing-Preflight (`committee_matcher/provider.py`,
Kommando `provider check`) gegen den öffentlichen Endpointkatalog, ob unter
diesen Bedingungen überhaupt ein Endpoint existiert, und blockiert sonst mit
Exit 4, bevor Kosten entstehen.

Alle veränderbaren Laufparameter liegen unter `COMMITTEE_MATCHER_*` in
`config/settings.py`; dazu gehören Modell, Tokens/Kontext, Timeout, Workflow-
Retries, yfinance-Pause, Routing/Datenschutz sowie:

- `COMMITTEE_MATCHER_MAX_RUN_COST_USD`;
- `COMMITTEE_MATCHER_MAX_EVALUATION_COST_USD`;
- `COMMITTEE_MATCHER_MAX_QUALIFICATION_COST_USD`.

Vor Run und vor jedem Call müssen konservative Worst-Case-Kosten ins jeweilige
Budget passen. Tatsächliche `usage.cost` und reservierte Kosten ungeklärter
Calls werden addiert. Ein Timeout mit unbekannter Providerannahme wird
`outcome_unknown` und nie automatisch wiederholt. Reconciliation nutzt nur eine
vorhandene OpenRouter-`generation_id`; ein erneutes Senden benötigt Benes
ausdrückliche Bestätigung mit Doppelabrechnungswarnung.

Lokale Call-ID, OpenRouter-Generation-ID, Request-ID und Upstream-ID werden
getrennt gespeichert; nur die Generation-ID ist für den Generation-Lookup
zulässig.

**Offen bis P7c-Reconciliation (Stand 2026-08-26):** Die Antwort des
Chat-Endpoints trägt im gepinnten SDK `openrouter==1.1.71` weder Request- noch
Upstream-ID; `openrouter_metadata` liefert nur `attempt`, `endpoints`, `region`,
`strategy` und `pipeline`. P7a speichert deshalb lokale Call-ID und
Generation-ID, der Lookup nutzt ausschließlich die Generation-ID. Zwei Quellen
bleiben ungenutzt und werden mit der Reconciliation in P7c bewertet:
`GenerationResponse.upstream_id` aus dem Generation-Lookup selbst
(`openrouter/components/generationresponse.py:119`) und die
HTTP-Antwortkopfzeilen, die den SDK-Fehlerobjekten als `raw_response` anhängen.
Beide werden dann nullable und getrennt gespeichert; für den Lookup bleibt die
Generation-ID die einzige zulässige Kennung.

Responses werden vor Fachvalidierung atomar als bereinigte Auditdaten
persistiert. Cache/Resume verwenden nur vollständig validierte Ergebnisse mit
identischem Input, Taxonomie-Content-Hash, Prompt, Schema, Modell, Endpoint und
Inferenzparametern. Volatile Run-, Snapshot-, Call-, Evaluations- und
Wiederholungs-IDs bleiben Auditprovenienz, verändern den fachlichen Cache-Key
aber nicht.

Workflowstatus und fachlicher Unitstatus werden getrennt gespeichert.
Workflowzustände decken mindestens `pending`, `running`,
`awaiting_taxonomy_approval`, `awaiting_development_goldset`,
`awaiting_holdout`, `awaiting_manual_review`, `failed`, `outcome_unknown` und
`completed` ab; `outcome_unknown` ist niemals ein Mappingurteil.

### 2.7 Prompts, Fehler und CLI

Die editierbaren UTF-8-Systemprompts liegen separat als
`mapping_system.md` und `repair_system.md`. Version, kompatible Schemaversion
und Hash werden validiert und eingefroren. Committee- und Industry-Daten werden
als nicht vertrauenswürdige Datenblöcke abgegrenzt.

Jeder CLI-Lauf persistiert Fehler strukturiert und zeigt am Ende immer einen
nach Schweregrad und Stage gruppierten Terminalbericht mit Fehlercode,
betroffenem Objekt, Handlungshinweis, Kosten und Artefaktpfaden – auch bei
Abbruch oder null Fehlern.

Verbindliche Exit-Codes:

```text
0    erfolgreich oder erwartete Human-Übergabe
2    erfolgreich mit Warnungen
3    finale fehlgeschlagene Mapping-Units
4    Input-/Prompt-/Config-Preflight blockiert
5    awaiting_taxonomy_approval
6    Kostenlimit oder cost_unknown blockiert
7    Review-/Gold-Set-/Holdout-Import abgelehnt
8    outcome_unknown benötigt Reconciliation
9    nicht-retrybarer Providerfehler
10   unerwarteter interner Fehler
130  Benutzerabbruch
```

CLI-Familien: Input/Roster validieren, Snapshot erzeugen/freigeben/resumen,
Entwicklungsrun planen/ausführen/reconcilen, Review exportieren/importieren,
Gold-Set evaluieren, Holdout qualifizieren, Full-Run und Bericht. Es gibt kein
`promote`, `finalize`, `research` oder LLM-`review`. Chamber-/Committee-/Unit-
Auswahl ist nur für Entwicklungsruns erlaubt und wird im Manifest festgehalten;
`full-run` ist filterlos.

### 2.8 Artefakte und manueller Review

Generierte Artefakte liegen ausschließlich unter
`data/committee_matcher_v2/` in unveränderlichen Run-, Snapshot-, Review-,
Evaluations- und Qualifikationsrevisionen. Inputmanifest und manuelle
Roster-Regeln bleiben getrennte, versionierte Kontrollinputs.

Pflichtartefakte:

- Long-CSV: eine Zeile je Unit–Industry-Zuordnung, einschließlich Inputhash,
  `revision_id`, Assignment-Provenienz, Confidence, Evidence, Rationale,
  Prompt-/Providerprovenienz;
- Unit-Coverage: jede erwartete Unit genau einmal mit
  `not_decided | mapped | no_mapping | failed`;
- Roster-Coverage gemäß Abschnitt 2.3;
- Boundary-Konflikte für gleiche Industries in Geschwister-Units;
- Runmanifest, Fehler-/Kostenbericht und Review-XLSX.

Die XLSX trennt unveränderliche `model_*`-Felder von editierbaren
`review_*`-Feldern. Bene darf Zuordnungen, Confidence, Evidence und Rationale
ändern, hinzufügen oder löschen sowie `mapped ↔ no_mapping` wechseln.

- `mapped` verlangt mindestens einen lückenlosen Slot und keinen
  No-Mapping-Grund;
- `no_mapping` verlangt Grund und vollständig leere Industry-, Confidence-,
  Evidence-, Rationale- und Broad-Scope-Reviewfelder;
- neue/geänderte Industry verlangt Review-Evidence und -Rationale; reine
  Confidenceänderung verlangt Review-Rationale;
- gelöschte Zuordnungen benötigen eine Reviewnotiz, sofern eine konsistente
  Boundary-Aktion den Grund nicht bereits dokumentiert;
- jede manuelle Feldänderung setzt die effektive Assignment-Provenienz auf
  `manual`, während der Modelldraft erhalten bleibt;
- Sektorwerte werden beim Import aus dem Snapshot abgeleitet und nicht aus
  Excel vertraut; Unitzeilen dürfen weder fehlen noch hinzukommen;
- stale Input-, Snapshot- oder Exportbindung blockiert den gesamten Import;
- read-only Felder werden gegen das Exportmanifest geprüft, nicht nur durch
  Excel-Zellschutz;
- Boundary-Aktionen `keep_a`, `keep_b`, `remove_both` und
  `legitimate_overlap` müssen mit den tatsächlich bearbeiteten Slots
  übereinstimmen; neue Konflikte erzeugen eine neue Reconciliation-XLSX. Offen
  bis P8: die Abbildung eines Konflikts mit mehr als zwei beteiligten Units auf
  `keep_a`/`keep_b`; die Erkennung liefert seit P6 eine sortierte Unitliste
  beliebiger Länge;
- reviewed Artefakte entstehen erst ohne offene Konflikte, `not_decided`,
  `failed` oder `needs_followup`. Drafts und alte Revisionen bleiben erhalten.
- Der Import erzwingt dieselben 0/2/5- und Broad-Scope-Regeln wie das
  Modellresultat; jede Unit benötigt den finalen Reviewstatus `reviewed`.

## 3. Implementierungsphasen und Gates

### Umsetzungsstand (2026-08-27)

| Schritt | Status | Nachweis / Bedingung |
|---|---|---|
| P0.1–P0.3 Baselinesicherung | abgeschlossen | Commit `8ff7bc8`; Inputs + Planungsdokumente pfadbezogen committet, kanonische Plankopie versioniert |
| P1 Vollständiger Rückbau | abgeschlossen | Commit `d5a31fc`; `scripts/committee_mapping_agent/` entfernt, Altdoku und Altdependencies bereinigt; Rollback über Tag `committee-matcher-v1-final` |
| P0.4–P0.6 Migration + Schutzbaseline | abgeschlossen | Commits `5e683a9` + `235a5c8`; Migration `input-basis-v1` idempotent, `corpus_manifest.yaml` (95 Dateien) + `committee_matcher_protected_baseline.yaml` (9 Pfade) erzeugt und verifiziert; Phasenreview mit Auflagen abgenommen, alle fünf Befunde behoben |
| P2 Providerfreies Gerüst | abgeschlossen | Paket `committee_matcher/` mit Ownership-Karte für 17 Zielmodule; `status`, `errors`, `paths`, `settings_view`, `models`, `cli` implementiert; `COMMITTEE_MATCHER_*`-Block in `config/settings.py`; 127 netzwerkfreie Tests; Phasenreview mit Auflagen, alle sieben Befunde behoben |
| P3 Input, Identität, Roster | abgeschlossen | `input_corpus.py` (strikter Parser + Fremdanweisungsgate), `units.py` (`unit-id-v1`/`mapping-input-v1`, Golden Tests), `roster_coverage.py` (Joint-Cross-Chamber-Regel); `input validate` bestätigt 225 Units mengengleich zur eingefrorenen Baseline, `roster validate` 227/230 gemappt; 75 zusätzliche netzwerkfreie Tests |
| P4 yfinance-Snapshot | abgeschlossen | Contract-Spike gegen `yfinance==1.3.0` bestätigt; `taxonomy_source.py` (Adapterrand verwirft Börsengrößen) + `taxonomy_snapshot.py` (`taxonomy-content-v1`, Katalog, Checkpoint/Resume, `no_baseline`- und Driftgate); Livelauf: 145 Industries in 11 Sektoren, Erstfreigabe erteilt; 45 netzwerkfreie Tests; Phasenreview mit Auflagen, alle sechs Befunde plus der Nachprüfungsbefund behoben (Checkpoint-Runbindung, strikte Recordprüfung, Freigabebindung `snapshot_id == run_id`, nachgerechneter Baselinehash, live geholte Identität beim Resume, retrybare leere Overview) |
| Gate B Providerentscheidungen | beschlossen (2026-08-21) | Beschluss in `committee_matcher_gate_b_entscheidungsvorlage.md`; Modell `deepseek/deepseek-v4-flash-0731`, freie Providerwahl (`allow_fallbacks`, kein `only`/`order`), Filter über `require_parameters`, Quantisierung, `data_collection=deny`, `zdr=true`, Preisdeckel; Kostenwache abschaltbar; SDK `openrouter==1.1.71` installiert |
| Routing-Preflight | abgeschlossen | `provider.py` + `provider check`; prüft den öffentlichen Endpointkatalog ohne Key, Kosten und Modellaufruf und blockiert mit Exit 4, wenn kein Endpoint zur Konfiguration passt; live am 2026-08-22 7 von 30 Endpoints zulässig; 25 netzwerkfreie Tests |
| P5 Prompts und Provideradapter | implementiert, Contract-Smoke offen |  `prompts.py` (Frontmatter, Schemabindung `mapping-output-v1`, Inhaltshash, Datenblockabgrenzung) + beide Systemprompts unter `data/config/committee_matcher_prompts/`; `provider.py` um providerneutrales Interface, `FakeProvider` und `OpenRouterProvider` erweitert; `provider smoke` implementiert, aber noch nicht live ausgefuehrt (Gate: kostenpflichtiger Call); 60 netzwerkfreie Tests |
| P6 Mappingkern | abgeschlossen | `mapping.py` (Ausgabeschema als Golden Test, Batching mit Context-Split 3→2→1, Requestbau mit Datenblockabgrenzung, gezielter Repair) + `validation.py` (formale Prüfung gegen Vertrag/Unit/Snapshot, Verstoßliste, Boundary-Erkennung); neue Settings `MAX_REPAIR_ATTEMPTS` und `CHARS_PER_TOKEN`; 95 netzwerkfreie Tests; Phasenreview + zwei Nachprüfungen (GPT Sol) mit acht hohen Befunden, alle behoben: keine Fremdwerte mehr in der Verstoßliste (nur Vertragspositionen), Repair auf die beanstandeten Units begrenzt und der Auftrag über alle Versuche stabil, Kontextschätzung über UTF-8-Bytes inklusive gemessenem Schema mit Obergrenze 4.0 Zeichen/Token, Boundary-Mitglieder deterministisch sortiert |
| P7a Runzustand | abgeschlossen | `workflow.py`: Runmanifest, Laufzustand (Workflow-/Unitstatus getrennt), Auditspur und Kostenwache als Providerdekorator, Cache-Key ohne gerouteten Endpoint (Gate-B-Folge), Resume-Bindung an die ergebnisbestimmenden Manifestfelder; 72 netzwerkfreie Tests; Phasenreview (GPT Sol) mit fünf hohen Befunden, drei behoben (Kostenbuchung vor der Persistenz, offener Callausgang ohne Autoretry, beschädigter Laufzustand hält an statt neu zu starten), einer abgelehnt (Redaktion der Modellantwort im Audit), einer als SDK-Grenze dokumentiert (Request-/Upstream-ID) |
| P7b Ergebnisartefakte | implementiert, Review offen | `artifacts.py`: Long-CSV je Unit-Industry-Zuordnung, Unit-Coverage über die volle Sollmenge, Boundary-Konflikte, Roster-Coverage und Kostenbericht; Provenienz ausschließlich aus Runmanifest und Snapshot, Schreiben als Alles-oder-nichts mit sieben Blockerprüfungen; 27 netzwerkfreie Tests |
| P7c/P7d und alles danach | offen | `map run`, Terminalreporter S4, XLSX-Roundtrip, Evaluation |

P1 wurde vor P0.4–P0.6 ausgeführt, da der Rückbau ausschließlich Altcode und
Altdoku betrifft und den Inputroot nicht berührt. Der Schutzbezug bleibt
unverändert der Post-Migrations-Hash aus P0.5; die Invariante „Baseline vor
Migration“ gilt damit weiterhin.

### Gate A – Freigabe des providerfreien Kerns

Bene gibt den Rest von P0 sowie P2–P4 frei. Gate A autorisiert weder OpenRouter
noch Mapping-/Review-Live-Runs.

Vorbedingung Doku-/Worktree-Klärung:

- Die früheren Doku-Löschungen (`organisation/Pipelines.md`,
  `organisation/analyse_event_study.md`,
  `organisation/tool_specific_md/Briefing_stage_post_trade_performance.md`) sind
  über Commit `810d35a` geklärt und Teil des Branchstands.
- Offen bleiben drei fremde, uncommittete Löschungen im Worktree
  (`.agents/skills/frontend-design/LICENSE.txt`,
  `.agents/skills/frontend-design/SKILL.md`,
  `.claude/skills/skill-creator/SKILL.md`). Sie liegen außerhalb des
  Matcher-Scopes, werden in keinen Matcher-Commit aufgenommen und warten auf
  Benes Commit- oder Restore-Entscheidung.

### P0 – Baseline und Inputmigration *(abgeschlossen)*

1. **[erledigt, `8ff7bc8`]** Fremde Worktree-Änderungen mit `git status`,
   unstaged/staged Diff und Branch-Diff dokumentieren und überlappende
   Dokuänderungen vor Beginn klären.
2. **[erledigt, `8ff7bc8`]** Untracked Inputs und Planungsdokumente
   ausschließlich pfadbezogen sichern; niemals `git add -A`.
3. **[erledigt, `8ff7bc8`]** Diesen Plan als kanonische Repository-Kopie unter
   `organisation/committee_matcher_v2_implementierungsplan.md` versionieren.
4. **[erledigt, `5e683a9`]** Migrationsskript und Corpusmanifest erstellen, Dry-Run prüfen,
   Migration anwenden und 46/46 Mapping-MDs validieren.
5. **[erledigt, `5e683a9`]** Post-Migrations-Baseline committen und matcherbezogene
   Schutz-Hashes bilden.
6. **[erledigt, `5e683a9`]** Gitignorierte Altartefakte auf nicht reproduzierbare
   Reviewarbeit prüfen.

Abnahme: 48 MD/46 TXT/1 CSV vollständig klassifiziert, 225 Unit-IDs ableitbar,
12 Legacyfelder und 8 Marker entfernt, keine fremde Änderung übernommen.

### P1 – Vollständiger Rückbau *(abgeschlossen, Commit `d5a31fc`)*

Den alten Paketinhalt unter `scripts/committee_mapping_agent/` einschließlich
Research-, Mapping-, LLM-Review-, Finalize-, Cache-, Prompt-, Test- und
Generated-Pfaden entfernen. DeepSeek-/Altprovider-Konfiguration und nur dort
verwendete Dependencies werden erst nach repo-weitem Importcheck entfernt.

Geschützt bleiben insbesondere Inputroot, produktiver Mapping-Master samt XLSX,
House-/Senate-Roster und bestehende yfinance-Mappingdateien. Die Schutzliste ist
matcherbezogen und wird nicht aus der globalen `reset_pipeline.py`-Liste
abgeleitet. Rückbau als eigener Commit; danach Import-/Test-Collection und
Vorher-/Nachher-Diff prüfen.

```text
data/committee_matcher_inputs/**
data/mappings/subcommittee_sector_industry_mapping.csv
data/mappings/subcommittee_sector_industry_mapping.xlsx
data/mappings/house_member&(sub)committees.csv
data/mappings/senate_member&(sub)committees.csv
data/mappings/yf_sectors_with_industries_enriched.json
data/mappings/yf_sectors_with_industries.json
data/mappings/yf_sectors_with_industries.xlsx
data/mappings/us_house_all_committees.csv
data/mappings/us_senate_all_committees.csv
```

### P2 – Providerfreies Gerüst *(abgeschlossen)*

Neuen Paketbaum mit eindeutiger Ownership für Input/Roster, Taxonomie,
Provider, Mapping, Workflow/Cache/Kosten, Artefakte/Review und Evaluation
anlegen. Zielgröße sind etwa 12–18 kohärente Laufzeitmodule, keine starre
Dateiliste. Pydantic-Modelle, Workflowstatus, Exit-Codes, Fehlercollector,
Pfadwache, typisierte Settingssicht und CLI-Skelett implementieren.

`PyYAML` wird gepinnt. Providerabhängige Settings dürfen im Offline-Gerüst
unset sein, müssen aber jeden Providerpfad blockieren.

### P3 – Input, Identität und Roster *(abgeschlossen)*

Corpusmanifest, strikter MD-Parser, Fremdanweisungsgate, Unitbildung,
Kanonisierung/Hashes, exakte 225-ID-Sollmenge und Roster-Coverage mit
Joint-Cross-Chamber-Regel sind implementiert; die Kanonisierung ist versioniert
und per Golden Test eingefroren.

Zwei bewusst offene Punkte, beide als Warnung sichtbar statt still korrigiert:

- drei Rostervorkommen ohne Unit (Select Subcommittee January 6, Commission on
  Security and Cooperation in Europe, Senate Caucus on International Narcotics
  Control) und zwei Units ohne Rostervorkommen (`house--ways-and-means--tax-policy`,
  `house--ways-and-means--trade`) — Alias, Ausschluss oder offener Punkt
  entscheidet Gate C;
- zwei abgeschnittene Jurisdiktionstexte im Corpus (House THUD, Senate Homeland
  Security) melden `INP_EVIDENCE_TOO_SHORT`; Corpuspflege, kein Codefehler.

### P4 – yfinance-Contract-Spike und Snapshot *(abgeschlossen)*

Der Contract-Spike gegen `yfinance==1.3.0` hat alle vier Punkte bestätigt:
`SECTOR_INDUSTY_MAPPING` liefert 11 Seeds, `Sector(key).industries` den
Live-Bestand (DataFrame mit `name`, `symbol`, `market weight`),
`Industry(key).overview` Identität und Beschreibung neben Börsengrößen, die der
Adapter verwirft. Checkpoint/Resume ist am Livelauf belegt: ein Abbruch wegen
gedrosselter Sectorantwort hinterließ nur den Checkpoint, `snapshot resume`
setzte darauf auf. Eine leere Sectorantwort wird seither als Drosselung
behandelt und fällt in die Retrylogik.

Ursprüngliche Nachweisliste:

- Sector-Seeds führen vollständig zur Live-Industry-Liste;
- Description/Identität sind stabil abrufbar;
- Checkpoint/Resume funktioniert ohne partielle Snapshotfreigabe;
- Market-Cap-/Weight-Felder werden am Adapterrand entfernt.

Danach Snapshot, Katalog, Content-Hash, `no_baseline` und Driftgate
implementieren. P4 darf Netzwerkzugriff auf yfinance, aber keinen OpenRouter-
Call ausführen.

### Gate B – vor erstem OpenRouter-Call

Verbindlich festlegen und im Plan-/Runvertrag einfrieren:

- Modell, Confidence-Rubrik und Kandidatenversion des OpenRouter-SDKs;
- Outputschema- und Workbook-Schemaversion;
- Routingbedingungen (Fähigkeit, Quantisierung, Datenschutz/ZDR, `max_price`)
  statt eines gebundenen Endpoint-Slugs;
- Endpoint-Kontextfenster, Token-Estimator, Output-/Reasoningreserve;
- Timeout und Workflow-Retries;
- Run-, Evaluations- und Qualifikationsbudget.

Erst danach führt ein einzelner kostenbegrenzter Contract-Smoke Auth,
Structured Output, tatsächliches Routing, Usage/Kosten, Provider-IDs,
Generation-Lookup und lokale Validierung aus.

### P5 – Prompts und Provideradapter

Mapping-/Repair-Prompts, Loader/Hashing, providerneutrales Interface,
OpenRouter-Adapter und Fake-Provider implementieren. Kein SDK-Typ gelangt in
Fachmodule; SDK-Retries bleiben aus. Providerfallbacks innerhalb desselben
Modells sind seit Gate B ausdrücklich erwünscht (`allow_fallbacks=true`).
Verbindliche Integrationsanforderung: Adapter und Contract-Smoke rufen
`provider.check_routing` vorgeschaltet auf und brechen mit Exit 4 ab, bevor ein
kostenpflichtiger Call abgesetzt wird.

### P6 – Mappingkern

Structured Output, Context-Preflight, Batching 1/3 mit Split, formale
Validierung, gezielten Repair, Broad-Scope-Regel und Boundary-Erkennung
implementieren. Ungültige Antworten werden nicht still verändert.

### P7 – Workflow, Kosten, Cache und Artefakte

Seriellen Runzustand, atomare Auditpersistenz, Resume, Reconciliation,
semantischen Cache, Kostenwachen, Terminalabschluss, CSVs und XLSX-Roundtrip
implementieren. Offline-E2E- und Crash-/Budgettests müssen bestanden sein.

### P8 – Entwicklungsrun und Review-Roundtrip

Einen kleinen, gefilterten Live-Run ausführen, Review-XLSX exportieren, von
Bene bearbeiten lassen und strikt rückimportieren. Beide Richtungen
`mapped ↔ no_mapping`, Add/Edit/Delete und mindestens ein Konfliktfall werden
im Roundtrip geprüft. Dies ist ausdrücklich kein Full-Run.

### P9 – Entwicklungs-Gold-Set und Evaluation

`goldset-export` eröffnet den Evaluationskontext mit genau einer frischen
Live-Abfrage, friert Snapshot, Stichprobe, Inputhashes, Variantenmatrix und
Gesamtbudget ein und stoppt in `awaiting_development_goldset`. Bene erstellt
15–20 vollständig gelabelte Units; erst der valide Import startet die
Evaluation. Jedes Label enthält die vollständige erwartete Industry-Menge oder
explizit `no_mapping`.

Verglichen werden Batchgröße 1/3 und gegebenenfalls vorab freigegebene Modelle.
Jede Variante läuft zunächst dreimal ohne LLM-Cache-Reads; bei uneindeutiger
Stabilität höchstens fünfmal nach erneuter Budgetprüfung. Primärmetriken sind
Precision/FDR; ergänzend Recall/F1, No-Mapping-Accuracy, Stabilität, Confidence,
Broad-Scope-Fehler, Kosten und Laufzeit. Das Entwicklungsset qualifiziert keinen
Full-Run.

### Gate C und P10 – unabhängige Holdout-Qualifikation

Vor `holdout-export` legt Bene Roster-Aliasse/Ausschlüsse,
Description-Driftpolicy, Holdoutdesign und fachliche Schwellen fest.
`holdout-export` fragt die Taxonomie frisch ab und friert Kandidat, disjunkte
Stichprobe, Inputs, Taxonomie und Schwellen vor Labels ein. Bene erstellt das
Holdout; Import und Auswertung dürfen den Kandidaten nicht verändern. Ein
offengelegtes Holdout wird für neue Kandidaten nicht wiederverwendet.

Nur ein bestandener Bericht setzt `qualified_for_full_run`.

### P11 – qualifizierter Full-Run

`full-run` akzeptiert nur die bestandene `qualification_id`, fragt yfinance
frisch ab und vergleicht den Content-Hash mit der Qualifikation. Bei Abweichung,
`failed`, `not_decided` oder `outcome_unknown` entsteht kein finales Workbook.
Nach Recovery verarbeitet der filterlose Run exakt alle erwarteten Unit-IDs und
endet in `awaiting_manual_review`.

### P12 – finaler Review und Abschluss

Bene reviewt das vollständige Workbook. Erst ein konfliktfreier, stale-sicherer
Rückimport erzeugt finale reviewed Revisionen und den Abschlussbericht.
Integration oder Promotion findet weiterhin nicht statt.

Neue/entfernte projektrelevante Dateien werden in derselben Phase gemäß
`DOKU_INDEX.md` dokumentiert. Große Architekturmeilensteine verwenden
`/doku-update`; P12 führt den abschließenden Konsistenzlauf aus.

## 4. Test- und Abnahmestrategie

Reguläre Tests sind netzwerkfrei; yfinance- und OpenRouter-Smokes sind getrennt
opt-in und hart begrenzt.

| Bereich | Verbindliche Abdeckung |
|---|---|
| Migration/Input | vollständiges Rollenmanifest; idempotente Migration; H1/H2/H3; Kommentar-/Fremdanweisungsausschluss; `source_files`; 225-ID-Set; Canonicalization-/Hash-Golden-Tests |
| Roster | exact/alias/excluded/missing; null-Regeln; Fuzzy nur Vorschlag; Joint Cross-Chamber ohne Doppelunit |
| Taxonomie | Seed→Live-Discovery; Retry/Pause/Checkpoint; leere/fehlende/duplizierte Daten blockieren; `no_baseline`; Drift; Content-Hash; keine Börsengrößenfelder |
| Provider/Workflow | Fake-SDK; Routing/Privacy; Context-Gate; 3→2→1; Retryklassen; `outcome_unknown`; Generation-ID; Cacheprovenienz; Crash-Resume; Kostenstop; Terminal-/Exit-Codes |
| Mapping | 0/1–2/3–5/>5; Scope-/Confidence-/Evidence-Regeln; unbekannte/duplizierte IDs; Repair ohne Silent-Fix; Boundary-Konflikte |
| Artefakte/Review | Golden CSVs; unveränderliche Drafts; Add/Edit/Delete; beide Decision-Richtungen; stale/read-only/Slot-/Broad-Scope-Prüfung; Konflikt-Reconciliation; keine Überschreibung alter Revisionen |
| Evaluation | handgerechnete Metrikfixtures; vollständiges `no_mapping`; getrennte Wiederholungen; eingefrorener Snapshot/Budget; disjunktes Holdout; Full-Run-Gate und exakte Unit-Coverage |

## 5. Abnahmestufen

### `technically_complete`

- Altagent vollständig entfernt und separat wiederherstellbar;
- providerfreier Kern, Providervertrag, Mapping, Review-Roundtrip und
  Entwicklungsevaluation funktionieren;
- alle formalen Regeln, Kosten-/Resume-/Fehlerpfade und Schutztests bestehen;
- kein Promote und keine `import_trades.py`-Integration.

### `qualified_for_full_run`

- Kandidat, Schwellen und disjunktes Holdout wurden vor Ergebnissen eingefroren;
- der unveränderte Kandidat erfüllt die vorab festgelegten Precision/FDR-,
  No-Mapping-, Stabilitäts-, Broad-Scope- und gegebenenfalls Confidence-Grenzen.

### `matching_phase_complete`

- qualifizierter, filterloser Full-Run über die exakte Unit-Sollmenge;
- keine offenen `failed`, `not_decided`, `outcome_unknown` oder Konflikte;
- vollständiger manueller Review und valider Rückimport;
- unveränderliche reviewed V2-Artefakte, Abschlussbericht und konsistente Doku;
- Inputbaseline und produktiver Mapping-Master unverändert.

## 6. Migration, Schutz und Rollback

- P0 sichert Inputs und Planungsdokumente pfadbezogen vor jeder Migration.
- Die Post-Migrations-Baseline ist der Schutzbezug; nicht der Vormigrationshash.
- P1 erhält einen eigenen Commit, optional einen lokalen V1-Tag. Rückbau und
  Neubau werden nicht vermischt.
- Löschziele sind explizit; die globale `reset_pipeline.py`-Schutzliste wird
  nicht als Matcher-Scope missverstanden.
- Vor/nach P1 und vor Live-E2E werden matcherbezogene Produktionspfade gehasht
  und vollständige Git-Diffs geprüft.
- V2 schreibt ausschließlich in seinen Artefaktraum. Der produktive Master wird
  weder gelöscht noch überschrieben.
- Rollback bedeutet Git-Rückkehr zum gesicherten V1-Commit; V2-Artefakte sind
  revisionsgebunden und können separat archiviert werden.

## 7. Kritische Risiken

| Risiko | Gegenmaßnahme |
|---|---|
| Rückbau erfasst fremde Arbeit oder manuelle Reviewdaten | Worktree-/Ignore-Inventar, pfadbezogene Commits, explizite Löschziele, eigener Rückbaucommit |
| Corpus oder Unitmenge wird unvollständig verarbeitet | vollständiges Rollenmanifest, exakte 225-ID-Setprüfung, unbekannte Dateien blockieren |
| yfinance liefert unvollständige oder geänderte Taxonomie | P4-Spike, Checkpoints, All-or-nothing-Snapshot, Drift-/Erstfreigabe |
| Modell erzeugt False Positives oder überbreite Zuordnungen | direkte Evidenzregel, `no_mapping`, max2/enge Ausnahme5, Repair, Review, Precision/FDR-Holdout |
| Context- oder Providerroute weicht unbemerkt ab | Gate-B-Freeze der Routingbedingungen, Context-Preflight, Routing-Preflight vor jedem Call, Quantisierungsfilter, protokollierter Ist-Endpoint je Call, Contract-Smoke |
| Retry verursacht Doppelabrechnung | serieller Betrieb, Kostenreserve, `outcome_unknown`, Generation-Reconciliation, manueller Retry |
| Stale oder manipulierte XLSX überschreibt Ergebnisse | Exportmanifest, Hash-/Snapshotbindung, read-only-Vergleich, neue Revision statt Überschreiben |
| Entwicklungsset führt zu Overfitting | getrenntes Holdout, vorab fixierte Schwellen, verbrauchtes Holdout nicht wiederverwenden |

## 8. Noch offene Umsetzungsentscheidungen

Diese Punkte sind absichtlich nicht vorgetäuscht präzisiert:

### P3

- exakte `unit-id-v1`-/`mapping-input-v1`-Kanonisierungsfunktion; sie muss die
  verbindlichen Invarianten aus 2.2 erfüllen und wird vor Persistenz eingefroren.

### P4-Contract-Spike

- konkrete yfinance-Version und notwendige Adapteranpassungen, sofern der
  dokumentierte Live-Contract von der installierten Version abweicht.

### Gate B

- Modell und Confidence-Rubrik;
- OpenRouter-SDK-Version und Routingbedingungen statt eines Endpoint-Slugs;
- Context-/Token-Estimator und Token-, Timeout-, Retry- und Preisgrenzen;
- Run-, Evaluations- und Qualifikationsbudget;
- finale Schema-/Workbook-Version und Datenschutz/ZDR.

### Gate C

- Roster-Aliasse und Ausschlussliste;
- Description-Driftfreigabe;
- Holdoutumfang/-Strata und fachliche Abnahmeschwellen;
- empirische Bestätigung oder Anpassung der Broad-Scope-Regel.

Erst Benes Gate-A-`OK` startet den Rest von P0 (Schritte 4–6).
