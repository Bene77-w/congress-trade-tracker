# Committee Matcher – Architektur- und Planungsentscheidungen (Codex)

Stand: 2026-08-18  
Status: Bedingt umsetzungsbereit für die providerfreien Phasen P0–P4; separate Gates vor OpenRouter-, Holdout-, Full-Run- und finalen Reviewphasen bleiben verbindlich.  
Zweck: Verbindliche Entscheidungsgrundlage für die vollständige Neuplanung des Committee Matchers. Überholte Relevance-, Börsenpräsenz- und Altarchitekturannahmen sind entfernt.

## Ziel und Qualitätsmaßstab

- Ziel ist ausschließlich die möglichst präzise fachliche Zuordnung von Committees beziehungsweise Subcommittees zu yfinance-Industries. False Positives sollen vorrangig minimiert werden.
- Börsengröße, Market Cap, Marktgewicht, politische Bekanntheit, Lobbying-Salienz oder vermutete Kurswirkung sind keine Mappingkriterien.
- Ein Committee oder Subcommittee muss keiner Industry zugeordnet werden. `no_mapping` ist ein reguläres fachliches Ergebnis und kein Fehler.
- Der Matcher ist eine Batch-Pipeline. Zur Trade-Laufzeit erfolgen keine LLM-Aufrufe.
- Die Matching-Phase endet bei manuell reviewten V2-Artefakten. Integration in `import_trades.py`, Committee-Tag, Signale und Scoring werden separat geplant.

## Verbindliche Implementierungs-Gates

- **Gate A – Providerfreier Kern:** Rückbau, Input-Validierung, Roster-/Coverage-Modell, yfinance-Adapter und Snapshots, providerfreie Datenmodelle, grundlegende CLI-Zustände sowie zugehörige Tests dürfen implementiert werden, ohne OpenRouter aufzurufen. P4 darf den yfinance-Live-Contract prüfen. Workflowstatus und Exit-Code-Matrix werden vor P2 verbindlich festgelegt. Provider-, Mapping- und nachgelagerte Review-/Evaluationsphasen folgen erst nach den im Implementierungsplan definierten Gates.
- **Gate B – Provider und Mapping:** Vor dem ersten OpenRouter-Call müssen Modell, Confidence-Rubrik, Structured-Output-Schema, Review-Workbook-Schema, Token-/Timeout-/Retrygrenzen, exakter Provider-/Endpoint-Slug, Routing-/Datenschutzregeln sowie Run-, Evaluations- und Qualifikationsbudget verbindlich festgelegt sein. V1 arbeitet seriell mit genau einem Call gleichzeitig.
- **Gate C – Holdout und vollständiger Lauf:** Vor `holdout-export` müssen Roster-Aliasse/Ausschlüsse, Taxonomie-Freigabepolicy, disjunktes Holdoutdesign und fachliche Abnahmeschwellen festgelegt sein. Ohne bestandenes Holdout gibt es keinen Full-Run.
- Ein technisch funktionierender Offline-Kern ist nicht automatisch fachlich für den Full-Run qualifiziert.

## Vollständiger Rückbau vor dem Neubau

- Vor der Neuimplementierung wird der alte Committee Matcher als geschlossene Einheit entfernt: Agentcode, alte Research-/Datenbeschaffungslogik, Mapping-/LLM-Review-/Finalize-Rollen, Inline-Prompts, Tests, Caches, temporäre Generated-Artefakte, DeepSeek-spezifische Konfiguration sowie ausschließlich dafür benötigte Dependencies und veraltete Dokumentation.
- Vor dem Rückbau wird der vollständige Stand in Git gesichert. Der Rückbau erhält einen eigenen Commit; dadurch bleibt er wiederherstellbar, liegt aber nicht mehr im aktiven Codebestand.
- Die neuen beziehungsweise noch untracked kanonischen Inputs und Planungsdokumente werden vor jeder Migration pfadbezogen gesichert und committet. Für diese Sicherung und spätere Baselines wird niemals `git add -A` verwendet, damit fremde Löschungen oder Änderungen nicht versehentlich aufgenommen werden. Der freigegebene Implementierungsplan erhält in P0 die kanonische, versionierte Repository-Kopie `organisation/committee_matcher_v2_implementierungsplan.md` und einen Eintrag in `organisation/DOKU_INDEX.md`; eine Datei unter `~/.claude/plans/` ist nur Arbeitskopie.
- Nicht gelöscht werden die 46 kanonischen Committee-MDs, ihre TXT-Quellen und der derzeit noch produktiv gelesene Mapping-Master. Der Produktionsmaster bleibt bis zur späteren Integrationsphase unverändert, damit Committee-Tag und Dashboard weiter funktionieren.
- `data/mappings/yf_sectors_with_industries_enriched.json` bleibt unverändert, wird vom neuen Matcher aber nicht verwendet.
- Nach dem Rückbau wird der Matcher unter einem sauberen neuen Paketbaum vollständig neu implementiert; Rollen, Prompts, Datenmodelle, Abläufe und Modulgrenzen des Altagenten werden nicht übernommen.
- Die Rückbau-Schutzmenge ist matcherbezogen und wird aus exakten Löschzielen, direkten Abhängigkeiten und weiter produktiven Committee-Pfaden gebildet. Die projektweite Schutzliste aus `reset_pipeline.py` definiert nicht den Matcher-Scope; Außer-Scope-Sicherheit entsteht durch explizite Löschziele und vollständige Git-Diffs.

## Scope und Committee-Inputs

- Der Matcher beginnt beim Einlesen und Validieren der bereits beschafften Committee-Inputs. Beschaffung und Rohtextaufbereitung dieser Quellen gehören nicht zur Matching-Phase.
- Der vollständige Corpus besteht aus 48 MDs, 46 TXTs und einer administrativen CSV. `data/committee_matcher_inputs/corpus_manifest.yaml` klassifiziert und hasht jede dieser Dateien: 46 MDs als `mapping_input`, zwei Sammel-MDs als `source_only`, 46 TXTs als `raw_source` und `Extrahierungsanleitung.csv` als `administrative_excluded`. Das Manifest selbst ist Kontrollmetadata, kein selbst gehashtes Corpusmitglied. Nur `mapping_input` erzeugt Units.
- `source_files` darf ausschließlich manifestierte `raw_source`- oder `source_only`-Dateien referenzieren. Alle 46 Roh-TXTs und beide Sammel-MDs müssen mindestens einmal referenziert sein; andere Rollen blockieren.
- Die 46 finalen Committee-MDs umfassen 22 House, 20 Senate und 4 Joint. Davon besitzen 31 Subcommittee-Abschnitte und 15 keine.
- Jede der 46 `mapping_input`-Dateien folgt `committee_jurisdiction_input_template_codex.md`: YAML-Frontmatter mit strukturierter `unit_input_basis`, genau ein `# Committee jurisdiction`, je Subcommittee ein `## <offizieller Name>` und optional `## Zusätzliche Kommentare`. Die Subcommittee-Keys in `unit_input_basis` müssen exakt der H2-Menge ohne Kommentarabschnitt entsprechen; fehlende, zusätzliche oder doppelte Keys blockieren.
- Kanonische Werte der `unit_input_basis` sind `jurisdiction_text` und `name_only`. Ein versioniertes, idempotentes Migrationsskript mit `--check`, `--apply` und `--report` setzt bei allen 46 MDs `unit_input_basis.committee = jurisdiction_text`, klassifiziert jeden H2-Subcommittee-Abschnitt standardmäßig als `jurisdiction_text` und ausschließlich die acht explizit markierten Units als `name_only`. Es entfernt dabei ausdrücklich zwölf alte `input_basis:`-Frontmatter-Felder (`committee_direct` beziehungsweise `summarized_mandate`) und acht `name_only`-HTML-Marker; die Legacy-Werte werden nicht als neue Enums übernommen. Danach werden alle 46 MDs strukturell und fachlich validiert. Erst dieser validierte Post-Migrationsstand wird als unveränderliche Input-Baseline gehasht und geschützt. Der Matcher verändert die Inputs nach dieser Baseline nicht.
- `## Zusätzliche Kommentare` wird vollständig von Mapping-Units und Prompts ausgeschlossen. H3-Untergliederungen innerhalb eines Jurisdiktionsabschnitts bleiben erlaubt.
- Bereits in Quellen enthaltene Sektor-, Industry-, Gewichtungs-, Scoring- oder Mappingempfehlungen sind keine Jurisdiktionsgrundlage. Eindeutig markierte Fremdanweisungen außerhalb des Kommentarabschnitts blockieren die Datei mit festem Fehlercode, Pfad und Zeile. Erkannt werden versionierte eindeutige Marker; eine allgemeine semantische Prompt-Injection-Erkennung wird nicht vorgetäuscht. Legitimer Jurisdiktionstext wird nicht per Branchenwort-Heuristik gelöscht.
- Fehlende URL oder Abrufzeit bei `source_metadata_status: pending` bleibt sichtbar, blockiert die inhaltlich vollständigen Dateien aber nicht und wird niemals erfunden.
- Eindeutige Abweichungen zwischen Dateiname, Chamber, Committee-Identität und Inhalt werden sofort gemeldet. Es gibt kein Raten und kein stilles Überspringen.

## Unit- und Roster-Modell

- Ohne Subcommittee-H2 entsteht eine `committee_direct`-Unit.
- Mit Subcommittee-H2 entsteht je Subcommittee eine `subcommittee`-Unit sowie eine eigene schmale `committee_parent_core`-Unit.
- Der aktuelle Corpus ergibt als abgeleitete Sollmenge 225 Units: 179 `subcommittee`, 31 `committee_parent_core` und 15 `committee_direct`. Maßgeblich ist stets die im Manifest eingefrorene exakte Unit-ID-Menge, nicht nur die Zahl.
- Subcommittees werden immer mit Parent-Jurisdiktion bewertet. `name_only` erhält nur offiziellen Namen plus Parent-Kontext; es wird kein fehlender Text erfunden.
- `committee_parent_core` wird ausschließlich aus der Parent-Jurisdiktion bestimmt und niemals aus Child-Mappings aggregiert. Der alte Committee-Rollup entfällt als produktive Match-Quelle.
- Der MD-Bestand bestimmt `committee_has_subcommittees`. Mitglied-zu-Subcommittee-Zuordnungen stammen später aus den House-/Senate-Rostern.
- Unit-IDs und fachliche Inputhashes verwenden versionierte, deterministische Unicode-/Whitespace-/Slug-Kanonisierung; Slugkollisionen blockieren statt automatisch nummeriert zu werden. Der fachliche Inhaltshash bleibt vom SHA-256 der exakten Quelldateibytes getrennt.
- Ein Roster ist die Mitgliedschaftsliste, die zeigt, welche Personen welchen Committees und Subcommittees zugeordnet sind. Es ist keine fachliche Mappingquelle.
- Bereits in der Matching-Phase gleicht ein separater Roster-Coverage-Audit alle dort vorkommenden Gremien gegen die Mapping-Units ab und erzeugt `mapped | excluded | missing` samt Matchbasis `exact | alias | manual_exclusion`. Kein Roster-Gremium darf still verschwinden.
- Bei Roster-Status `missing` sind `matched_unit_id` und `match_basis` zwingend `null` und ein Handlungshinweis ist Pflicht; bei `excluded` gilt `matched_unit_id=null` und `match_basis=manual_exclusion`.
- Alias-/Ausschlussregeln liegen versioniert mit Pflichtbegründung in `data/config/committee_matcher_roster_rules.yaml`; Fuzzy Matching schlägt nur Kandidaten vor und bestätigt nie automatisch.
- Für das F2-Mitgliedschaftsformat wird der bestehende allgemeine Parser `utils/committee_parser.py::parse_committees_full` wiederverwendet; er ist kein Bestandteil des Altagenten. House- und Senate-Rostervorkommen desselben Joint Committees werden auf genau eine kanonische Joint-Unit normalisiert und nicht doppelt gezählt.
- Die produktive Behandlung von `sub_assignment_status = explicit | confirmed_none | unknown` erfolgt erst in der späteren `import_trades.py`-Integration.

## Frischer yfinance-Industry-Snapshot

- Vor jedem neuen Mapping-Run werden aktuelle Industry-IDs, Namen, Beschreibungen und Sektorzuordnungen live aus yfinance abgefragt. Ein Resume desselben Runs verwendet dessen eingefrorenen Snapshot; ein neuer Run fragt erneut live ab.
- Die lokal gepinnten yfinance-Konstanten liefern nur bekannte Sector-Seeds und dienen zum Vergleich/als Diagnosehilfe. Für jeden Seed werden die aktuellen Industries live über `yf.Sector(sector_key).industries` ermittelt; Details und Beschreibungen kommen live über `yf.Industry(industry_key).overview`. Die heute bekannten 11 Sektoren/145 Industries sind kein unveränderlicher Hardcode.
- Nach jedem Request gilt eine konfigurierbare Pause; temporäre Fehler werden begrenzt wiederholt. Fehlende, leere, doppelte oder widersprüchliche Industry-Daten blockieren alle Modellaufrufe und erscheinen im Abschlussbericht.
- Erfolgreiche yfinance-Antworten werden erst nach Adapterfilterung run-lokal als Checkpoints gespeichert; Rohpayloads und Market-Cap-/Weight-Felder werden nicht persistiert. Ein partieller Snapshot bleibt ungültig und darf nicht für Modellaufrufe verwendet werden, kann aber innerhalb desselben Runs ohne erneuten Komplettdownload fortgesetzt werden.
- Der Lauf erzeugt einen datierten maschinenlesbaren JSON-Snapshot und einen stabil nach Sektoren gruppierten Markdown-Katalog für den Prompt.
- Market Cap und `market_weight` können technisch im yfinance-Rohpayload enthalten sein, werden aber an der Adaptergrenze verworfen: Sie werden nicht ausgewertet, abgeleitet, persistiert, validiert, in Prompts gesendet oder für Mapping/Evaluation verwendet. Börsenpräsenz-Tiers existieren im neuen Workflow nicht.
- Industry → Sektor wird nach der LLM-Auswahl ausschließlich deterministisch aus dem Snapshot ergänzt.

### Snapshot-Freigabe und Run-Zustand

- `run` erzeugt immer einen eigenen neuen Snapshot. Bei relevanter Taxonomie-Drift stoppt der Run mit `awaiting_taxonomy_approval`; es werden keine Modellaufrufe gestartet.
- Existiert noch keine freigegebene Taxonomie, stoppt der erste Snapshot analog mit `drift_status=no_baseline` und `awaiting_taxonomy_approval`. Nur eine explizite Run-ID-Freigabe etabliert die erste Baseline; es gibt keine automatische Erstfreigabe.
- Blockierende Drift umfasst Industry-ID-Zugänge/-Abgänge, Industry-Umbenennungen, Industry→Sector-Verschiebungen sowie Sector-ID-Zugänge/-Abgänge oder Sector-Umbenennungen. Reine Beschreibungsänderungen werden protokolliert und nach der festgelegten Driftpolicy behandelt; für die Qualifikationsgleichheit verändern auch sie den `taxonomy_content_hash`.
- `approve-taxonomy --run-id <id> --reason <text>` gibt ausschließlich den Snapshot dieses Runs frei. `resume --run-id <id>` setzt genau diesen Run mit genau diesem eingefrorenen Snapshot fort.
- Ein separater `snapshot`-Befehl ist nur Diagnosewerkzeug und wird nicht stillschweigend von einem anderen Run übernommen.
- Jede Evaluation beginnt mit genau einer frischen Live-Abfrage, besitzt eine eigene `evaluation_id` und referenziert einen eingefrorenen Snapshot, Input-Baseline-, Prompt-, Modell- und Providerstand. Varianten/Wiederholungen sind Child-Executions dieses Evaluationskontexts und keine neuen Produktionsruns; sie verwenden diese unveränderten Referenzen und fragen nicht erneut live ab.
- Jeder Run-, Evaluations-, Qualifikations- und Full-Run-Kontext besitzt zusätzlich eine `run_id` für Snapshot-, Drift-, Approval- und Resume-Zustand. `evaluation_id`/`qualification_id` ersetzen diese technische Run-ID nicht.
- Eine `qualification_id` bindet zusätzlich den deterministischen `taxonomy_content_hash` über sortierte Industry-ID, Industry-Name, Sector-ID, Sector-Name und Description; volatile Abrufzeiten gehören nicht in diesen Hash. Der Full-Run fragt zwar zwingend frisch ab, darf aber nur starten, wenn sein Content-Hash dem Qualifikationssnapshot entspricht. Jede Inhaltsänderung – einschließlich Description – stoppt den Full-Run und verlangt eine neue Qualifikation auf dem neuen Snapshot; eine bloße Taxonomie-Freigabe ersetzt die fachliche Requalifikation nicht.

## Direkte Mappingentscheidung

- Der Mapping Agent wählt direkt aus allen Industries des validierten aktuellen Snapshots. Eine vorgelagerte Sektor-Auswahl darf keine Kandidaten ausschließen.
- Eine Zuordnung ist nur zulässig, wenn die Jurisdiktion unmittelbar wesentliche Produkte, Tätigkeiten oder Regulierung der Industry umfasst.
- Allgemeine Wirtschafts-, Haushalts-, Arbeitsmarkt- oder Verbraucherauswirkungen, Lieferkettennähe, bloße thematische Nachbarschaft und theoretische Berührung genügen nicht.
- Im Zweifel gilt `no_mapping`. Der Prompt enthält keine Ziel- oder Mindestzahl an Zuordnungen.
- Jede Zuordnung enthält:
  - exakte `industry_id` aus dem Snapshot;
  - `confidence` 1–4 als Einschätzung, wie zuverlässig die fachliche Zuordnung ist;
  - `evidence_excerpt` als kurzen wörtlichen Nachweis aus dem für die Unit erlaubten Kontext;
  - `rationale_short` als knappe Erklärung, warum der Nachweis die Industry direkt trägt.
- `evidence_excerpt` muss nach normalisierter Whitespace-/Unicode-Prüfung tatsächlich im erlaubten Input vorkommen. `rationale_short` ist nur Reviewhilfe und kein Ersatz für Evidenz.
- Erlaubte Evidence stammt bei `committee_direct`/`committee_parent_core` aus eigenem Committee-Namen beziehungsweise Parent-Text, bei textbasierten Subcommittees aus eigenem Namen/H2-Text und bei `name_only` ausschließlich aus dem offiziellen Unit-Namen. Parent-Kontext darf Subcommittee-Mappings interpretativ unterstützen, aber nicht allein belegen; Kommentare und fremde Units sind ausgeschlossen.
- Relevance wird vollständig gestrichen: kein Feld, kein Mindestwert, keine Promptregel, keine Reviewspalte, keine Score-Nutzung und kein Gold-Set-Label.

## Anzahl der Zuordnungen und präzise Sonderfallregel

- Zulässig sind 0 Zuordnungen. Im Normalfall sind höchstens 2 Industries je Unit erlaubt.
- 3 bis höchstens 5 Industries sind ausschließlich als `broad_scope_exception` zulässig. Mehr als 5 sind immer ungültig.
- Die Ausnahme gilt nur für `subcommittee` und `committee_direct`. `committee_parent_core` und `name_only` bleiben aus Präzisionsgründen immer auf höchstens 2 begrenzt.
- Für 3–5 Zuordnungen muss das strukturierte Unit-Ergebnis zusätzlich enthalten:
  - `broad_scope_exception: true`;
  - `broad_scope_reason`: knappe Erklärung, welche mehreren ausdrücklich getrennten Kernzuständigkeiten der Unit die größere Menge erfordern.
- Jede der 3–5 Zuordnungen muss unabhängig dieselbe direkte Aufnahmeregel erfüllen und einen eigenen überprüfbaren `evidence_excerpt` sowie `rationale_short` besitzen. Eine pauschale Begründung für alle Industries reicht nicht.
- Die Ausnahme ist unzulässig, wenn zusätzliche Industries nur Varianten derselben Lieferkette, indirekte Betroffenheit oder allgemeine thematische Nähe darstellen.
- Für eine Broad-Scope-Ausnahme müssen alle Zuordnungen mindestens Confidence 3 tragen. Diese Regel wird am Gold-Set überprüft; sie ersetzt nicht die Einzelevidenz.
- Python validiert ausschließlich formal prüfbare Bedingungen deterministisch: Anzahl, Scope, Pflichtfelder, Evidenz als normalisierten Substring, IDs und Confidence. Ob Evidenz unmittelbare Zuständigkeit statt Lieferkettennähe belegt oder mehrere getrennte Kernzuständigkeiten fachlich rechtfertigt, wird nicht als deterministisch prüfbar dargestellt; dies sichern Prompt, Entwicklungs-Gold-Set, Holdout und manueller Review ab. Bei formal unvollständigem Ergebnis wird die gesamte betroffene Unit gezielt frisch repariert; Ergebnisse werden niemals automatisch gekürzt, umsortiert oder teilweise verworfen.
- Im manuellen Review darf Bene die Ausnahme setzen, entfernen oder ändern, muss bei 3–5 Zuordnungen aber ebenfalls einen Ausnahmegrund und Einzelnachweise hinterlegen.

## Calls und Batching

- Es gibt genau eine fachliche LLM-Schicht, ohne Research-Agent und ohne LLM-Review.
- Mapping-Calls sind zustandslos und enthalten höchstens drei Ziel-Units. Konfigurierbare Batchgrößen sind ausschließlich `{1, 3}`; ein deterministischer Tokensplit darf effektive Calls mit 2 Units erzeugen. Eine Variante „alle Units eines Committees“ gibt es nicht.
- Jeder Call erhält denselben System-/Regel-/Taxonomie-Prefix, den vollständigen Parent-Kontext und die vollständigen zulässigen Texte seiner Ziel-Units.
- Vor jedem Call muss der vollständige, niemals still gekürzte Kontext einschließlich Output-/Reasoningreserve und Sicherheitsmarge in das eingefrorene Endpoint-Kontextfenster passen. Der deterministische Split ist 3→2→1; passt eine Einzelunit nicht, blockiert der Call sichtbar.
- Ungültiges JSON, fremde/fehlende Unit-IDs, unbekannte Industries, Regelverstöße oder unbelegte Zuordnungen führen zu frischem Repair/Retry nur für betroffene Units. Gültige Units bleiben erhalten. Nach ausgeschöpften Versuchen wird die Unit `failed`; es gibt keine stille Korrektur.
- V1 besitzt keinen vorgeschalteten LLM-Overview, keine LLM-Zusammenfassung und keine zweite fachliche Agentenschicht. Mapping und gezielter Repair arbeiten direkt auf dem erlaubten Jurisdiktionskontext. Eine spätere Overview-Forschung wäre eine neue Architekturentscheidung mit eigenem Plan.
- Identische Industries in Geschwister-Subcommittees werden als mögliche Boundary-Konflikte markiert und im manuellen Review entschieden, niemals automatisch gelöscht.

## OpenRouter- und Kostengrenze

- Alle Modellaufrufe laufen zunächst ausschließlich über OpenRouter. Fachcode verwendet ein schmales providerneutrales Interface; nur der Adapter kennt SDK-, Routing- und Response-Details.
- Erste Implementierung ist das offizielle `openrouter`-Python-Paket. Vor Gate B wird eine Kandidatenversion gepinnt; erst nach Gate B prüft ein kostenpflichtiger Contract-Smoke Structured Output, Usage/Kosten und normalisierte Fehler und bestätigt oder verwirft diesen Pin.
- Structured Output verwendet striktes JSON-Schema und nur kompatible Endpoints; lokale Pydantic- und Fachvalidierung bleibt zwingend.
- Modell, Reasoning, Tokenlimit, Timeout, Routing, Datenschutz, Fallback und Workflow-Retry liegen unter `COMMITTEE_MATCHER_*` in `config/settings.py`. Parallelität ist in V1 keine Einstellung, sondern die feste Workflowinvariante 1.
- Für reproduzierbare Providerwahl werden `require_parameters = true`, `allow_fallbacks = false`, in V1 exakt ein freigegebener Provider-/Endpoint-Slug in `only` sowie die Datenschutz-/ZDR-Einstellung im Runmanifest eingefroren. Bei Nichtverfügbarkeit wird sichtbar abgebrochen; ein Provider-/Endpointwechsel invalidiert Evaluation/Qualifikation. `max_price` begrenzt zusätzlich den zulässigen Endpoint-Preis; es ersetzt nicht das lokale Runbudget.
- V1 ist strikt seriell (genau ein Providercall gleichzeitig); SDK-interne automatische Retries werden deaktiviert und alle Retries durch die eigene Workflowlogik kontrolliert. Parallelität samt atomarer Inflight-Reservierung ist ausdrücklich nicht Bestandteil von V1.
- `COMMITTEE_MATCHER_MAX_RUN_COST_USD` setzt ein hartes Laufbudget. Vor dem Run wird ein konservativer Gesamtpreis geschätzt; vor jedem Call gilt `actual_cost + cost_unknown_reserved + estimated_worst_case_next_call <= max_run_cost`. Für jedes ungeklärte Outcome reserviert `cost_unknown_reserved` die zuvor berechneten Worst-Case-Kosten dieses Calls vollständig.
- `COMMITTEE_MATCHER_MAX_EVALUATION_COST_USD` begrenzt die gesamte Entwicklungsevaluationsmatrix über alle Varianten, Wiederholungen, Repairs und unbekannten Kosten hinweg. `COMMITTEE_MATCHER_MAX_QUALIFICATION_COST_USD` begrenzt separat die Holdoutqualifikation. Das spätere Full-Run-Budget bleibt davon unabhängig.
- Nach jedem Call wird OpenRouters tatsächlich gemeldetes `usage.cost` einschließlich Mapping- und Repair-Calls addiert. Ein Call, der nicht vollständig ins Restbudget passt, wird nicht gestartet.
- Kosten nach einem unklaren Timeout werden als `cost_unknown` sichtbar geführt und verhindern weitere Calls, wenn das verbleibende Budget nicht sicher bestimmt werden kann.

## Workflow- und Mappingstatus

- Technischer Workflowstatus und fachlicher Mappingstatus werden getrennt gespeichert. Workflowstatus umfasst mindestens `pending`, `running`, `awaiting_taxonomy_approval`, `awaiting_development_goldset`, `awaiting_holdout`, `awaiting_manual_review`, `failed`, `outcome_unknown` und `completed`.
- Der fachliche Unitstatus ist transient `not_decided` und final `mapped | no_mapping | failed`. `excluded` gehört ausschließlich zum Roster-Coverage-Modell. `not_decided` entsteht vor einer Entscheidung, `failed` erst nach ausgeschöpften/ausdrücklich finalisierten Versuchen; reviewed Endartefakte dürfen weder `not_decided` noch `failed` enthalten.
- `outcome_unknown` ist kein Mappingergebnis. Der Adapter speichert getrennt `local_call_id`, `openrouter_generation_id`, `openrouter_request_id` und `upstream_id`. OpenRouters Generation-Lookup darf nur mit einer vorhandenen `openrouter_generation_id` (`gen-...`) erfolgen; eine lokale Korrelations- oder Request-ID ist kein Ersatz. Ohne Generation-ID bleibt der Ausgang unbekannt, bis Bene einen kostenbewussten Retry oder endgültiges `failed` autorisiert.

## Prompts, Cache und Betriebsrobustheit

- Jeder System-Prompt liegt als eigene editierbare UTF-8-Markdown-Datei vor. Dynamische Committee- und Industry-Daten werden getrennt als nicht vertrauenswürdige Datenblöcke eingesetzt.
- Promptversion und SHA-256-Hash fließen in Manifest, Call-ID und Cache-Key ein. Manuelle Promptänderungen invalidieren automatisch betroffene Caches und Reviewprovenienz.
- Cache und Resume dürfen nur vollständig validierte Ergebnisse mit identischen Inputs, identischem Taxonomieinhalt, Prompt, Schema, Modell und Parametern wiederverwenden.
- Der fachliche Cache-Key verwendet den Taxonomie-Content-Hash und den kanonischen providerwirksamen Request. Volatile Auditwerte wie `snapshot_id`, `local_call_id`, Run-/Evaluations-IDs, Zeitstempel, Wiederholungsindex und Attempt bleiben Provenienz, verändern aber den Cache-Key nicht.
- Writes erfolgen atomar in einem neuen, vom Produktionsmaster getrennten Artefaktnamensraum. Bestehende Produktions- und Inputpfade sind schreibgeschützt beziehungsweise werden in Schutztests auf Unverändertheit geprüft.
- Alle Input-, yfinance-, Prompt-, Provider-, Schema-, Mapping-, Artefakt- und Reviewfehler verwenden ein einheitliches Fehlermodell.
- Kritische Fehler werden sofort gemeldet. Am Ende jedes CLI-Laufs erscheint immer ein nach Schweregrad und Stage gruppierter Terminalbericht mit Anzahl, Objekt, Fehlercode, Kurzbefund, Handlungshinweis, Kosten und Artefaktpfaden – auch bei Abbruch oder null Fehlern.

## Dependencies, Settings und Modulgrenzen

- Jede neue Laufzeitabhängigkeit wird versionsgepinnt dokumentiert; insbesondere das für Frontmatter verwendete YAML-Paket (`PyYAML`) wird in `requirements.txt` gepinnt.
- Jede Einstellung wird mit Name, Typ, Default, zulässigem Wertebereich und Verbraucher dokumentiert. Alle veränderbaren Laufparameter liegen unter `COMMITTEE_MATCHER_*` in der zentralen Settings-Datei.
- Chamber-, Committee- und Unit-ID-Auswahl sind einmalige, manifestierte CLI-Zielangaben für Entwicklungsruns und keine fachlichen Settings; `full-run` lehnt sie ab. Fachliche Schwellen, Provider-, Kosten-, Retry- und Datenparameter bleiben ausschließlich in `config/settings.py`.
- V1 verwendet kohärente Module mit eindeutiger Ownership (Zielgröße etwa 12–18 Laufzeitmodule). Es gibt genau eine Roster-Coverage-Implementierung; insbesondere keine doppelte `roster_coverage.py` in Input- und Artefaktbereich.

## Artefakte und manueller Review

- Kanonischer Mappingoutput ist eine Long-CSV mit einer Zeile je Unit–Industry-Zuordnung. Sie enthält keine Relevance- oder Börsenpräsenzfelder.
- Jede Long-Zeile enthält eine explizite `revision_id`. `assignment_source=model` gilt nur bei vollständig unverändert übernommener Industry, Confidence, Evidence und Rationale; jede manuelle Änderung oder Ergänzung ergibt `manual`, während der Modelldraft unverändert erhalten bleibt.
- Ein eigenes Unit-/Coverage-Artefakt enthält jede erwartete Unit genau einmal mit `mapping_status = not_decided | mapped | no_mapping | failed`. `not_decided` ist nur in Zwischenartefakten zulässig.
- Ein separates Roster-Coverage-Artefakt enthält jedes im Roster vorkommende Gremium mit `mapped | excluded | missing`.
- Die Wide-XLSX ist ausschließlich die Reviewansicht. Read-only `model_decision`, `model_no_mapping_reason`, `model_industry_*`, `model_confidence_*`, `model_evidence_*`, `model_rationale_*` und Modell-Broad-Scope-Felder bewahren den Entwurf. Davon getrennte editierbare `review_decision = mapped | no_mapping`, `review_no_mapping_reason`, `review_industry_1..5`, `review_confidence_1..5`, `review_evidence_*`, `review_rationale_*` und Review-Broad-Scope-Felder bilden die effektive Entscheidung. Decision/No-Mapping-Grund/Industry/Confidence/Broad Scope sind vorbelegt; Review-Evidence/-Rationale zunächst leer. Neue/geänderte Industry verlangt beides, reine Confidenceänderung Review-Rationale; unveränderte Slots verwenden die Modellfelder.
- Bene darf Industries und Confidence ändern, hinzufügen oder löschen sowie in beide Richtungen zwischen `mapped` und `no_mapping` wechseln. `mapped` verlangt mindestens einen Slot und keinen No-Mapping-Grund; `no_mapping` verlangt null Slots und einen Grund. Sektoren werden niemals aus Excel übernommen, sondern erneut per Snapshot-Lookup ergänzt.
- Gelöschte Einzelzuordnungen verlangen eine Reviewnotiz, sofern eine konsistente Konfliktaktion den Grund nicht bereits eindeutig dokumentiert. Read-only Felder werden beim Import gegen das unveränderliche Exportmanifest geprüft und nicht allein wegen Excel-Zellschutz vertraut.
- Konfliktaktionen sind deklarativ und werden nie automatisch auf Slots angewandt: `keep_a` ist nur gültig, wenn die Industry in A vorhanden und aus B entfernt ist; `keep_b` umgekehrt; `remove_both` verlangt Entfernung aus beiden; `legitimate_overlap` verlangt beide. Widersprechen Aktion und Slots einander, blockiert der Import.
- Der Import berechnet Konflikte nach allen Reviewänderungen neu. Neu entstandene, noch nicht entscheidbare Konflikte erzeugen ausschließlich eine neue Reconciliation-Workbook-Revision und erneut `awaiting_manual_review`; reviewed CSVs entstehen erst nach einem konfliktfreien Folgeimport.
- Der Rückimport validiert Unit-ID, Mapping-Input-Hash, Snapshot, Taxonomie, Wertebereiche, Duplikate, Slotgrenzen, Broad-Scope-Regel und Evidenz vollständig. Veraltete Reviews blockieren den Import; sie werden nicht nur gewarnt oder übernommen.
- Der manuelle Gate ist explizit: Export → Status `awaiting_manual_review` → Bene bearbeitet die XLSX → expliziter Import → Validierung → neue reviewed Revision. Ein Export ohne erfolgreichen Review-Import gilt nicht als abgeschlossen.
- Originale Draft-Artefakte bleiben unverändert. Reviewte Artefakte erhalten eigene Provenienz und überschreiben keine vorherige Revision.

## Gold-Set und Evaluation

- `goldset-export` eröffnet den Evaluationskontext mit frischem Snapshot, friert Stichprobe, Variantenmatrix und Budget ein, erzeugt `evaluation_id` und ein daran gebundenes Template und stoppt mit `awaiting_development_goldset`. Bene erstellt 15–20 vollständig bewertete Units, und erst ein erfolgreicher Gold-Set-Import setzt dieselbe Evaluation fort. Je Unit enthält das Set `evaluation_id`, `taxonomy_content_hash`, Inputhash und die vollständige erwartete Industry-Menge oder explizit `no_mapping`. Dieses Set kalibriert und entwickelt, qualifiziert aber allein keinen Full-Run.
- Für `qualified_for_full_run` eröffnet `holdout-export` nach Gate C einen Qualifikationskontext: Kandidatenmanifest, Stichprobe, Metriken/Schwellen und frischer Snapshot werden vor Ergebnissen unter `qualification_id` eingefroren; Status `awaiting_holdout`. Holdout-Units müssen disjunkt zu allen Entwicklungs-/Tuningunits sein. Bene erstellt das separate Holdout, der Import bindet es an Input- und Taxonomiehashes, und erst danach wertet `qualify` den unveränderten Kandidaten aus. Schwellen, Stichprobe und Holdout dürfen nicht nachträglich angepasst werden; nach Offenlegung der Metriken ist das Holdout für neue Kandidaten verbraucht.
- `technically_complete` bezeichnet ausschließlich eine funktionierende Pipeline samt Tests, kleinem Entwicklungs-/Review-Roundtrip und funktionsfähiger Entwicklungsevaluation. `qualified_for_full_run` ist der zusätzliche fachliche Status nach bestandenem Holdout. `matching_phase_complete` wird erst nach qualifiziertem Full-Run über alle Units, finalem Reviewexport, Benes vollständigem Review, validiertem Rückimport und Abschlussbericht der reviewed V2-Artefakte erreicht.
- Primärmetrik ist Precision beziehungsweise False-Discovery-Rate. Recall, F1, No-Mapping-Accuracy, Zuordnungszahl, Fehlerquote und Lauf-zu-Lauf-Stabilität sind ergänzend.
- Confidence wird gegen die beobachtete Precision je Stufe kalibriert. Es gibt keine Relevance-Auswertung.
- V1 implementiert ausschließlich direktes Industry-Mapping. Eine separate Sektor→Industry-Mappingarchitektur und ein vorgeschalteter Overview werden weder produktiv noch als Evaluationsvarianten gebaut. Verglichen werden Batchgrößen 1/3 und gegebenenfalls vorab freigegebene Modellkandidaten unter identischen Verträgen. Produktionsziel bleibt direktes Mapping mit Batchgröße 3, sofern die Evaluation keine relevante Precisionverschlechterung zeigt.
- Varianten laufen zunächst dreimal ohne Cache-Reads für sämtliche LLM-Ergebnisse einschließlich Mapping und Repair; Audit-Writes bleiben aktiv. Nur bei uneindeutigen Ergebnissen und erneut bestandener Budgetprüfung wird auf fünf Wiederholungen erhöht.
- Broad-Scope-Ausnahmen werden als eigenes Stratum ausgewertet: Precision der Zuordnungen 3–5, Ausnahmehäufigkeit und falsche Ausnahmeaktivierung müssen separat sichtbar sein.
- Confidence darf vor abgeschlossener Kalibrierung niemals automatisch Mappingzeilen löschen oder fachliche Entscheidungen verändern; dies wird explizit getestet.

## Abschlussreihenfolge

- Ein kleiner Entwicklungs-/Roundtrip-Run prüft Draft, Reviewexport und Rückimport, ist aber kein Full-Run.
- Nach Entwicklungs-Gold-Set, Variantenentscheidung und eingefrorener Kandidatenkonfiguration folgt der unabhängige Holdout.
- Erst nach `qualified_for_full_run` wird ein neuer Full-Run über alle erwarteten Units mit frischem Snapshot ausgeführt.
- Dieser Full-Run endet in `awaiting_manual_review`. Erst finaler Review, Rückimport, konfliktfreie reviewed Artefakte und Abschlussbericht setzen `matching_phase_complete`.
- Projektdokumentation wird an den Architekturmeilensteinen Rückbau, Taxonomievertrag, Reviewvertrag und finaler Abschluss aktualisiert; ein abschließender Konsistenzcheck bleibt Pflicht.

## Vor Gate B/C noch verbindlich festzulegen

- Exakte Confidence-Definitionen und die spätere Kalibrierungs-/Nutzungsregel; bis zur Gold-Set-Kalibrierung werden Werte gespeichert, aber nicht zur automatischen Löschung von Mappingzeilen verwendet.
- Konkretes OpenRouter-Modell sowie Token-, Timeout-, Retry- und Kostengrenzen; Parallelität bleibt in V1 fest auf 1.
- Fachliche In-/Out-of-Scope-Liste und Aliasregeln für zusätzliche Roster-Gremien.
- Gate B bestätigt Version/Hashes des im Implementierungsplan bereits verbindlich spezifizierten Structured-Output- und Review-Workbook-Schemas. Workflowstatus und Exit-Code-Matrix sind bereits vor P2 verbindlich.
- Gold-Set-Zusammensetzung und verbindliche Abnahmeschwellen.
- Empirische Bestätigung der Broad-Scope-Regel, insbesondere Confidence ≥3 und Ausschluss von `name_only`/`committee_parent_core`.
