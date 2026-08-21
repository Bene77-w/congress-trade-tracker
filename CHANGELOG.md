# Changelog

Historie abgeschlossener Module/Phasen. **Aktiver WIP-Modul-Stand** steht im „Aktueller Stand (WIP)"-Block von `AGENTS.md`; Datei-/Modul-Historie via `git log --oneline -- <pfad>`. Detail-Specs zu offenen Arbeiten in `~/.claude/plans/`.

## Konvention

- Eine Phase pro Block, Datum (ISO), Kurz-Beschreibung, Test-Stand.
- Keine Wiederholung der laufenden Doku — Code-Referenzen nur, wenn dauerhaft sichtbar (z. B. neue Util-Datei).
- Reihenfolge: neuestes oben.


---

## 2026-08-22 — Committee Matcher V2, P6: Mappingkern

Ein Batch läuft jetzt vom Zuschnitt bis zum geprüften Ergebnis durch — ohne
Runzustand, der entsteht in P7.

**Batching und Kontext.** `COMMITTEE_MATCHER_BATCH_SIZE` ist der Wunsch, nicht
die Zusage: passt ein Batch nicht in das Eingabebudget
(`ENDPOINT_CONTEXT_TOKENS` minus Ausgabe- und Reasoningreserve), wird er 3→2→1
geteilt. Die Tokenschätzung rechnet über den neuen Wert
`COMMITTEE_MATCHER_CHARS_PER_TOKEN = 3.0` bewusst zu pessimistisch — eine
Unterschätzung würde Antworten abschneiden, eine Überschätzung kostet nur einen
zusätzlichen Batch. Passt eine einzelne Unit nicht, blockiert der Lauf mit
`MAP_UNIT_TOO_LARGE`; gekürzt wird weder Zuständigkeitstext noch Taxonomie.

**Ausgabeschema.** Der Vertrag 2.5 liegt als JSON-Schema vor und ist per Golden
Test eingefroren. Optionale Felder sind nullable statt abwesend, damit ein
fehlender Schlüssel von einem bewusst leeren unterscheidbar bleibt.
`industry_id` bleibt ein freier String: eine Enum über alle Snapshot-IDs wäre
strenger, würde das Schema aber um hunderte Werte aufblähen und den Lauf an
eine Schemafähigkeit binden, die nicht jeder Endpoint zuverlässig beherrscht.
Unbekannte IDs fängt die formale Prüfung ab.

**Formale Prüfung.** `validation.py` prüft Batchdeckung, Snapshotbindung,
Evidenzherkunft und die unittypabhängigen Obergrenzen. Erlaubte Belegquelle ist
der eigene Name beziehungsweise der eigene Zuständigkeitstext — der Text des
übergeordneten Committees ist Kontext und bekommt als häufigster Fehlerfall
eine eigene Meldung (`MAP_EVIDENCE_FROM_PARENT`). `committee_parent_core` und
`name_only` bleiben bei zwei Zuordnungen.

**Repair ohne Silent-Fix.** Eine beanstandete Antwort geht mit der vollständigen
Verstoßliste zurück an das Modell, höchstens
`COMMITTEE_MATCHER_MAX_REPAIR_ATTEMPTS` mal (Default 1). Bleibt ein Verstoß,
scheitert der Batch mit benannten Gründen — nichts wird gekappt, gelöscht oder
in der Confidence verschoben, und ein Batch wird nie teilweise übernommen.

**Boundary-Erkennung.** Dieselbe Industry in Geschwister-Units einer
Committee-Familie wird gemeldet, nicht entfernt: Überlappung kann legitim sein
und wird im Review entschieden.

Tests: 1542 grün, davon 67 neu und netzwerkfrei.

---

## 2026-08-22 — Committee Matcher V2, P5: Systemprompts und Provideradapter

Der Matcher kann jetzt mit einem Modell sprechen — tut es aber erst auf
ausdrückliche Freigabe.

**Prompts.** `mapping_system.md` und `repair_system.md` liegen als editierbare
Kontrollinputs unter `data/config/committee_matcher_prompts/`. `prompts.py`
prüft ihr Frontmatter und bindet die `schema_version` an den Ausgabevertrag des
Codestands: ein Prompt, der ein anderes Schema beschreibt als der Code
erzwingt, erzeugt systematisch ungültige Antworten und blockiert deshalb.
Der Inhaltshash deckt nur den Text ohne Frontmatter ab — eine reine
Versionsangabe bewegt ihn nicht, jede Textänderung schon. Vertauschte Dateien
fallen über `prompt_id` auf. Fremdtexte gehen in markierte Datenblöcke; eine
Zeile, die den Rahmen nachbildet, wird entschärft statt den Block zu schließen.

**Provideradapter.** `provider.py` hält zusätzlich das providerneutrale
Structured-Output-Interface, einen `FakeProvider` für netzwerkfreie P6/P7-Tests
und den `OpenRouterProvider` als einzigen Ort mit SDK-Kontakt. `create()`
prüft API-Key und ruft den Routing-Preflight vorgeschaltet auf — kein Call ohne
zulässigen Endpoint. Je Call genau ein Versuch: SDK-Retries bleiben aus, weil
ein wiederholter Call doppelt abrechnen kann, während der Workflow in P7 selbst
entscheidet, was wiederholt wird. Jeder Ausgang wird fachlich übersetzt (Auth,
Guthaben, abgewiesene oder zu große Anfrage, Refusal, Abschneiden am
Ausgabelimit, ungültiges JSON, leere Antwort); ein unbekannter SDK-Fehler gilt
fail closed als nicht wiederholbar. Fehlende Kosten bleiben `None`
(`cost_unknown`) statt stillschweigend 0.0 zu werden.

**Contract-Smoke.** `provider smoke` setzt einen einzelnen kostenpflichtigen
Minimalcall ab und prüft, was der kostenlose Preflight nicht sehen kann: Auth,
echte Structured-Output-Fähigkeit des gerouteten Endpoints, gemeldete Kosten
und ZDR-Verträglichkeit. Das Kommando ist implementiert, aber noch nicht live
ausgeführt — der erste kostenpflichtige Call ist ein Gate.

**API-Key.** `OPENROUTER_API_KEY` steht ausschließlich in `.env`, wird über
`os.getenv` in `config/settings.py` gelesen und erscheint in keinem Artefakt,
Manifest oder Terminalbericht.

60 neue netzwerkfreie Tests; Gesamtsuite 1403 grün.

---

## 2026-08-21 — Committee Matcher V2, Gate B: Providersettings + Routing-Preflight

Gate B ist beschlossen. Defaultmodell ist `deepseek/deepseek-v4-flash-0731`, den
Endpoint wählt OpenRouter selbst: kein `only`, kein `order`,
`allow_fallbacks = true`. Gefiltert wird ausschließlich über Eigenschaften —
`require_parameters`, Quantisierung (`fp8`/`bf16`/`fp16`), `data_collection =
"deny"`, `zdr = true` und ein Preisdeckel. Der neue Filter
`COMMITTEE_MATCHER_QUANTIZATIONS` ersetzt die frühere Endpointbindung
`COMMITTEE_MATCHER_PROVIDER_SLUG`: ohne ihn mischt ein Lauf 4-Bit- und
16-Bit-Endpoints, womit Evaluationsergebnisse nicht mehr auf den
Produktionslauf übertragbar wären. Die Kostenwache ist über
`COMMITTEE_MATCHER_COST_GUARD` abschaltbar, ohne Provider oder Modell zu binden.

Neu ist `committee_matcher/provider.py` mit dem Routing-Preflight und dem
Kommando `provider check`. Er fragt den öffentlichen Endpointkatalog
`models/<modell>/endpoints` ab — ohne Key, ohne Kosten, ohne Modellaufruf — und
bricht sofort mit Exit 4 ab, wenn zur Konfiguration kein Endpoint existiert.
Grund für die Vorschaltung: Eine leere Endpointmenge ist der einzige
Konfigurationsfehler, den `allow_fallbacks` nicht abfangen kann; ohne Preflight
scheitert stattdessen der erste kostenpflichtige Call mit einer generischen
SDK-Meldung. Jede Leerungsursache meldet sich getrennt
(`PROV_CATALOG_UNREACHABLE`, `PROV_MODEL_UNKNOWN`, `PROV_NO_STRUCTURED_OUTPUT`,
`PROV_NO_ENDPOINT` je für Quantisierungsfilter und Preisdeckel), und jeder
Befund nennt die beiden Stellschrauben, die die Workflowlogik nicht berühren:
Modellwechsel und `COMMITTEE_MATCHER_ZDR = False`. Fehlt einem Modell die
Structured-Output-Fähigkeit, fordert der Befund ausdrücklich einen Modellwechsel.

Was der Katalog nicht hergibt, ist ZDR: die Eigenschaft steht nicht je Endpoint
darin. Scheitert ein realer Call trotz grünem Preflight am Routing, listet
`routing_failure_finding` alle wirksamen Bedingungen und benennt `zdr=true` als
wahrscheinlichste Ursache.

Preise wertet der Preflight fail closed: ein fehlendes, leeres oder nicht
lesbares Preisfeld gilt als unendlich teuer statt als 0.0, sonst passierte ein
Endpoint mit unbekanntem Preis jeden Deckel und erschiene als der günstigste.
Ebenso belegt nur eine echte Liste in `supported_parameters` die
Structured-Output-Fähigkeit — bei einem String wäre `in` eine Teilstringsuche
und bejahte die Fähigkeit fälschlich.

Live geprüft am 2026-08-22: 7 von 30 Endpoints des Defaultmodells sind
zulässig, günstigster zu 0.080 USD pro Mtok Prompt — Katalogwerte, die sich
ohne Zutun des Projekts ändern. Tests: 25 netzwerkfreie Tests in
`tests/test_committee_matcher_provider_preflight.py`; Gesamtsuite grün.

---

## 2026-08-21 — Committee Matcher V2, P4: Taxonomie-Snapshot (yfinance)

Der Matcher holt seine Zielseite jetzt selbst. `taxonomy_source.py` ist das
einzige Modul des Pakets, das Netzwerk berührt; yfinance wird erst beim ersten
Abruf importiert, damit der providerfreie Kern importierbar bleibt. Der Adapter
zieht die elf Sector-Seeds aus den gepinnten yfinance-Konstanten, holt je Sector
die Industries und je Industry die Beschreibung — mit Pause zwischen den Abrufen
und endlichen Retries, weil Yahoo einen ungedrosselten Durchlauf über alle
Sektoren mit Rate-Limits beantwortet und ein halb abgerufener Snapshot wertlos
ist. Eine leere Sectorantwort gilt als Drosselung und ist retrybar.

Entscheidend ist, was **nicht** durchkommt: `INDUSTRY_FIELDS` ist eine
abschließende Allowlist aus `industry_id`, `industry_name`, `sector_id`,
`sector_name`, `description`. Market Cap, Market Weight, Firmen- und
Mitarbeiterzahlen sowie Symbole liefert yfinance mit, sie werden am Adapterrand
verworfen und erreichen kein Artefakt — eine Zuordnung soll fachliche Nähe von
Jurisdiktion und Industry belegen, nicht Marktgröße.

`taxonomy_snapshot.py` friert das Ergebnis ein: Inhaltshash `taxonomy-content-v1`
(SHA-256 über die sortierten Industries, ohne Abrufzeiten, damit zwei Läufe mit
gleichem Inhalt denselben Hash tragen), ein nach Sektoren gruppierter
Markdown-Katalog und ein run-lokaler Checkpoint. Bricht ein Lauf ab, persistiert
er nur den Checkpoint; `snapshot resume` setzt ohne erneuten Abruf des bereits
Geholten fort. Snapshot und Katalog werden all-or-nothing geschrieben.

Der Driftvergleich gegen `taxonomy/approved_baseline.json` trennt zwei Fälle:
Identitätsdrift (Industry neu, entfallen, umbenannt oder in einen anderen Sektor
verschoben) blockiert mit Exit 5 in `awaiting_taxonomy_approval`, weil sich die
Mappingzielmenge geändert hat und das eine menschliche Freigabe braucht; eine
reine Beschreibungsänderung warnt nur. Fehlt die Baseline ganz, stoppt jeder
Lauf ebenfalls mit Exit 5. `snapshot approve` bindet die Freigabe an den
Snapshotinhalt und weist einen nachträglich manipulierten Snapshot ab.

CLI: `python -m committee_matcher snapshot build|resume|approve --run-id ...`.
Livelauf gegen yfinance 1.3.0: 145 Industries in 11 Sektoren, Inhaltshash
`8c3da76572d9…`; ein absichtlich gedrosselter Zweitlauf blockierte mit
`TAX_SECTOR_EMPTY` und wurde per `resume` auf denselben Hash driftfrei
abgeschlossen. Versioniert ist nur die freigegebene Baseline; `runs/` und
`reports/` sind gitignoriert.

Review-Auflagen (gpt-5.6-terra, „Phase mit Auflagen abgenommen") direkt
nachgezogen: der Checkpoint ist jetzt an seine `run_id` gebunden und wird strikt
gegen den Inhaltsvertrag gelesen statt teilweise übernommen; `snapshot approve`
prüft zusätzlich Inhaltsvertrag, Feldmenge und die Bindung
`snapshot_id == run_id`, sodass ein in ein fremdes Runverzeichnis kopierter
Snapshot nicht freigabefähig ist; die Baseline wird vor jedem Driftvergleich
gegen ihren eigenen Hash nachgerechnet (`TAX_BASELINE_TAMPERED`), weil eine am
Hash vorbei editierte Baseline sonst genau die Drift verdecken könnte, die sie
melden soll. Beim Resume kommt nur noch die Beschreibung aus dem Checkpoint —
Name und Sektor holt auch der Fortsetzungslauf frisch, sonst entstünde ein
Mischsnapshot, in dem eine zwischenzeitlich umbenannte Industry unbemerkt
weiterliefe. Eine leere `Industry.overview` gilt wie ein leerer Sector als
Drosselung und fällt in die Retrylogik. In der Nachprüfung kam eine fehlende
Objektprüfung der JSON-Wurzel hinzu: eine Baseline, ein Snapshot oder ein
Checkpoint, dessen Wurzel eine Liste oder `null` ist, endet jetzt in einem
`TAX_*_MALFORMED`-Befund statt in einem `AttributeError`.

Tests: 45 netzwerkfreie P4-Tests gegen einen Fake-Client
(`tests/test_committee_matcher_taxonomy_p4.py`) inklusive Allowlist-,
Drift-, Checkpoint- und Manipulationsmatrix; Gesamtsuite 1318 grün.
Doku: `CLI_Bedienungsanleitung.md` 15c, `DATA_STRUCTURE.md` 3a.

---

## 2026-08-20 — Committee Matcher V2, P3: Input, Identität und Roster

Der providerfreie Kern liest den Corpus jetzt produktiv. `input_corpus.py` parst
jedes Mapping-Markdown strikt gegen den Inputvertrag: Manifestverifikation gegen
die Dateibytes, Größengrenzen, Frontmatter-Pflichtfelder, Kammer-Dateiname-
Konsistenz, `source_files`-Rollenprüfung, Bodyvertrag (eine H1, eindeutige H2 vor
dem Kommentarabschnitt) und ein Fremdanweisungsgate: `#userinput` blockiert mit
Datei und Zeile, sofern es nicht im reservierten Kommentarabschnitt steht.
Nichts wird geraten — jede Abweichung wird gemeldet.

`units.py` bildet daraus die Units und friert zwei Verträge ein: `unit-id-v1`
(Amtspräfix, NFKC, Typografie, Slug → `<kammer>--<committee>[--<sub>]`) und
`mapping-input-v1` (SHA-256 über den kanonischen Modellinput). Beide sind durch
Golden Tests gepinnt; die 225 IDs stehen als Sollmenge in
`data/config/committee_matcher_unit_ids_v1.yaml` und werden bei jedem Lauf
mengengleich verglichen. ID-Kollisionen blockieren statt umbenannt zu werden.

`roster_coverage.py` prüft die House-/Senate-Roster read-only gegen die Units.
Zugeordnet wird nur kanonisch oder per expliziter Aliasregel
(`data/config/committee_matcher_roster_rules.yaml`, `roster-rules-v1`, Pflichtfeld
`reason`); Fuzzy-Treffer sind reine Vorschläge. Fehlt ein Committee unter seiner
Rosterkammer, greift die Joint-Cross-Chamber-Regel. Stand: 227 von 230 Vorkommen
gemappt, 3 offen, 2 Units ohne Rostervorkommen — Entscheidung darüber liegt bei
Gate C, deshalb Warnung statt Blocker.

Neuer Parameter `COMMITTEE_MATCHER_MIN_EVIDENCE_CHARS` (Default 60): ein als
`jurisdiction_text` deklarierter Text darunter wird als `INP_EVIDENCE_TOO_SHORT`
gemeldet. Damit sind zwei abgeschnittene Appropriations-Abschnitte sichtbar,
statt still ins Mapping zu laufen.

Bedienung: `python -m committee_matcher input validate` und `roster validate`,
beide netzwerkfrei und ohne Schreibzugriff außerhalb der Artefaktwurzel.

Aus dem Phasenreview nachgezogen: Subcommittees tragen den Parent-Kontext als
eigenes Feld (im Inputhash, nie als Beleg), das Frontmatter wird auch auf
Provenienz geprüft, nicht dekodierbare Dateien und Größendrift melden Befunde
statt zu crashen, verwaiste Quelldateien blockieren, Units ohne Rostervorkommen
sind ein eigener Warnbefund und die eingefrorene Sollmenge wird inklusive
Kopffeldern validiert.

Test-Stand: 1276 Tests grün, davon 89 neu in
`tests/test_committee_matcher_input_units.py`; Corpusmanifest und
P0-Schutzbaseline nach dem Lauf unverändert verifiziert.

## 2026-08-20 — Committee Matcher V2, P2: providerfreies Gerüst

Das neue Paket `committee_matcher/` hält den providerfreien Kern. Die
Ownership-Karte über alle 17 Zielmodule steht im Docstring von `__init__.py`;
implementiert sind sechs davon: `status.py` (Workflow-, Unit- und
Coverage-Zustände, Exit-Code-Matrix mit bewusst unbelegtem Code 1), `errors.py`
(Befunde, strukturierte Persistenz, nach Schweregrad und Stage gruppierter
Terminalbericht), `paths.py` (Pfadwache), `settings_view.py` (typisierte
Settingssicht), `models.py` (Pydantic-Datenverträge) und `cli.py`
(`python -m committee_matcher`).

Die Pfadwache erlaubt Schreiben ausschließlich unter
`data/committee_matcher_v2/` und blockiert Traversal, Symlink-Ausbrüche, die
Inputbaseline sowie alle neun Pfade der P0-Schutzbaseline; fehlt oder bricht
die Schutzbaseline, blockiert sie (fail closed) statt ungeschützt zu schreiben.

`config/settings.py` enthält den Block `COMMITTEE_MATCHER_*` mit Typ, Bereich,
Default und Verbraucher je Setting. Offline-Settings müssen immer gültig sein;
die Provider-Settings bleiben bis Gate B `None` und blockieren jeden
Providerpfad mit Exit 4, fehlende Budgets mit Exit 6. Die
Sicherheitsinvarianten (`require_parameters=true`, `allow_fallbacks=false`,
genau ein `provider.only`-Eintrag, keine SDK-Retries, ein gleichzeitiger Call)
sind Codekonstanten und nicht konfigurierbar.

`models.py` pinnt die formalen Mappingregeln aus Plan 2.5 (0 Zuordnungen mit
Grund, 1–2 als Normalfall, 3–5 nur mit Broad-Scope-Ausnahme samt Confidence ≥ 3
und eigener Evidence/Rationale je Industry, > 5 immer ungültig) und weist über
`extra="forbid"` Market-Cap- und Weight-Felder auch am Modellrand zurück.
Kontextabhängige Regeln (Unittyp-Obergrenze, Snapshot- und Evidenceprüfung)
folgen bewusst erst in P6, die Artefaktschemata in P7/P8.

Der Paketimport zieht weder `openrouter` noch `yfinance` oder eine
HTTP-Bibliothek nach — durch Test gesichert. Neu:
`tests/test_committee_matcher_scaffold.py` (127 netzwerkfreie Tests; Gesamtsuite
1187). CLI, Familien, Exit-Codes und Settingstabelle:
`organisation/CLI_Bedienungsanleitung.md` Abschnitt 15.

## 2026-08-20 — Committee Matcher V2, P0: Inputbaseline migriert und eingefroren

Die 46 kanonischen Committee-Markdowns unter `data/committee_matcher_inputs/md/`
folgen jetzt dem Inputvertrag aus
`organisation/committee_jurisdiction_input_template_codex.md`. Das versionierte,
idempotente Migrationsskript `scripts/committee_matcher_migrate_inputs.py`
(`input-basis-v1`, Modi `--check`/`--apply`/`--report`) hat `unit_input_basis`
ergänzt, die zwölf alten `input_basis:`-Frontmatterfelder und die acht
HTML-Ersatzmarker entfernt und dabei Jurisdiktionstexte und Identitäten
unverändert gelassen.

`scripts/committee_matcher_baseline.py` erzeugt und prüft die beiden
Kontrollinputs: `data/committee_matcher_inputs/corpus_manifest.yaml`
(95 Dateien: 46 `mapping_input`, 2 `source_only`, 46 `raw_source`,
1 `administrative_excluded`, jeweils mit Rolle und SHA-256) und
`data/config/committee_matcher_protected_baseline.yaml` (SHA-256 der neun
matcherbezogenen Produktionspfade). Die Unit-Sollmenge ist mit 179
`subcommittee` + 31 `committee_parent_core` + 15 `committee_direct` = 225
eingefroren; kanonische Unit-IDs folgen erst in P3.

`PyYAML==6.0.3` ist in `requirements.txt` gepinnt. Die V1-Einträge des alten
Mapping-Agenten wurden aus `.gitignore` entfernt; gitignorierte Altartefakte mit
nicht reproduzierbarer Reviewarbeit existieren nicht mehr.

Nach dem Phasenreview nachgezogen: `--apply` ist corpusweit all-or-nothing
(Preflight vor jedem Schreiben, Rollback bei Schreibfehler), Mischzustände aus
migrierten und unmigrierten Dateien blockieren statt zu raten, und
`manifest`/`protect --verify` vergleichen feldweise gegen den neu berechneten
Sollpayload statt nur Hashes zu prüfen.

Test-Stand: `tests/test_committee_matcher_input_baseline.py` 90 Tests
(netzwerkfrei, schreibende Fälle nur auf synthetischem Corpus), Gesamtsuite
1060 Tests grün.

---

## 2026-07-11 — Suspicious-Trades-Workflow als Schwesterprojekt ausgelagert

Der zuvor im Congress Tracker enthaltene Workflow wurde vollständig nach
`suspicious-trades-workflow` migriert. Das Schwesterprojekt besitzt eine eigene
Projektkonfiguration (`pyproject.toml`, `requirements.txt`, Root-`.env`), ein
eigenes `.venv` sowie eigene Datenbereiche `data/inbox/` und `data/runtime/`.
Congress Tracker und Analyse-Workflow teilen weder Python-Code noch virtuelle
Umgebung oder fest gekoppelte Datenpfade.

Im Congress Tracker verbleibt ausschließlich der optionale neutrale
CSV-Producer `app/staging_export.py`. Er schreibt standardmäßig nach
`data/agent_exports/`, normalisiert die Transaktionsrichtung lokal und stellt
keine Imports zum ausgelagerten Projekt her. Taipy und die zugehörigen
Distributionen wurden aus dem Congress-Tracker-venv entfernt; die UI-Abhängigkeit
liegt nur noch im isolierten venv des Schwesterprojekts.

---

## 2026-07-11 — Suspicious-Trades B1 (historisch)

Vor der Auslagerung wurde die B1-Live-API-Schicht für reproduzierbare,
kostenkontrollierte Provider-Smokes gehärtet. Dazu gehörten sichere
Credential-Diagnosen, eindeutige Smoke-Status und Timeouts sowie korrigierte
Cachepreise. Die Implementierung und ihre weitere Entwicklung gehören seit der
anschließenden Migration vollständig zum eigenständigen Schwesterprojekt; im
Congress Tracker verbleibt davon kein Code.

---

## 2026-07-03 — Codebase-weites Debugging: Intervall 4 (Utils & Cross-Cutting)

**Abschluss des Debugging-Zyklus** (Commits `da88ed4`…`ace7e6c`, unabhängige Fable-Verifikation:
alle Punkte bestätigt, 1 Staging-Blocker gefunden + behoben). Teststand: **1037 passed, 0 failed** (+18 neue).

**utils/ (Fixes):**
- `ticker_sector`: permanenter yfinance-Fehler (`error_kind='other'`) landet nicht mehr im
  persistenten Null-Cache (Ticker galt sonst dauerhaft als delisted); toter Legacy-Block entfernt.
- `ticker_classifier`: T-Bill-Regex mit Ziffern-Pflicht (Wörter wie TRANSPORT → Manual-Review statt
  still Treasury/SKIP); Muni-Marker mit Wortgrenzen (AUTHENTIC-SPAC-Units nicht mehr als Muni-Bond).
- `ticker_normalizer`: SEARCH-Cache-Key enthält `ticker_type` (SPAC-Warrant/Unit erbten gegenseitig
  den FIGI); Cache-Spalte `classification_type` bekommt den echten Typ; HTTP-4xx wird nicht mehr
  negativ gecacht und löst keine Einzel-Search-Kaskade aus; unerreichbare 429/abort-Zweige entfernt;
  CUSIP-Misses werden negativ gecacht.
- `trade_id`/`migrate_add_trade_ids`: Migrationsskript hashte rohe CSV-Strings, die Pipeline hasht
  Timestamps → inkompatible IDs (Massen-Duplikat-Risiko). Fix: Datums-Konvertierung vor dem Hashen
  (empirisch: 113.675/113.675 IDs matchen den Master); Repräsentations-Abhängigkeit dokumentiert.
- `network_retry`: `ssl`-Pattern präzisiert; HTTPError ohne Status-Code fällt in den Text-Fallback.
- `issuer_ticker`/`issuer_group_mapper`: pd.NA/NaT-Lücke geschlossen (pandas 3.0/Arrow-Dtypes).
- `name_matcher`: merged Lookups memoisiert (war O(Zeilen×Mitglieder) Kopierarbeit pro match()).

**scripts/ (Fixes):** `analyse_event_study` lädt `post_return_{AR_HEADLINE_HORIZON_DAYS}d` dynamisch
(KeyError bei Headline 120 behoben — Voraussetzung für den geplanten Perzentil-Rerun);
`diagnose_scraper` set-Slicing-TypeError; `reset_pipeline` nutzt `settings.TRADES_MASTER_CSV` +
resettet `ar_outliers.csv`; `diagnose_master_quiver_delta` settings-Pfade + beidseitige
Drift-Anker an der normalize_dates-Logik-Kopie.

**Aufräumen (alle Löschungen grep-verifiziert, 0 Code-Referenzen):** `fetch_congress_votes.py`
(581 Z., Produzent ohne Konsument) + `vote_sector_mapping.py`; `analyse_quiver_changes.py`
(redundant + fehlerhaft); 8 tote Einmal-Skripte (`analyze_ticker_types`, `list_non_stock_tickers`,
`list_unknown_tickers`, `classify_tickers`, `convert_senate_data`, `convert_xlsx_to_csv`,
`inspect_excel`, `analyze_fehler_pdfs`, `extract_pdf_images`); Wurzel-Streuner `main.py`/`test.py`/
`print_columns.py`; leere Skelette `automation/`/`dashboard/`/`data_sources/` (`notifications/`
bleibt für Modul 10); `script.py` → `scripts/build_ticker_liste_index.py` (repo-root-verankert);
22 tote Settings entfernt (OpenFIGI-Legacy, Tesseract, VOTE_*, u. a.); `requirements.txt`
+`curl_cffi`/`openai`/`ddgs`/`jsonschema`, −`schedule`/`tenacity`/`pytesseract`.

---

## 2026-07-02/03 — Codebase-weites Debugging: Intervalle 1–3 (Import-Pipeline, Scoring/Signale, App/Streamlit)

**Motivation:** Systematische Fehlersuche über die produktive Pipeline, das Scoring-System und
das Streamlit-Dashboard (Branch `agent/new_scoring`); Intervall 4 (Utils/Scripts) läuft separat weiter.

**Intervall 1 — Import-Pipeline** (`scripts/import_trades.py`, `config/settings.py`):
- BUG-Fix: `Member_Committees` in `trades_master.csv` war immer leer (Master führt nur
  `Member_Committees_Full`) — neues `CURATED_COALESCE`-Mapping befüllt die Spalte per Coalesce.
- BUG-Fix (wichtigster): Im Delta-Modus sahen `sequence_signals`/`swarm_signals` nur die neue
  Delta-Charge — ein Trade, der erst mit einem historischen Master-Trade ein Paar/einen Schwarm
  bildet, blieb isoliert (`swarm_buy` ist Primärsignal!). Fix: beide Stages laden im Delta-Modus
  die Master-Historie als Gruppierungs-Kontext (Union, Pattern wie `asset_volume`); nur
  Delta-Trades erhalten Events/Aggregate. Neuer Helper `_load_signal_history`.
- Cleanup: `_apply_match_scores` entfernt — `MatchScore`/`MatchSource` werden nicht mehr erzeugt
  (kein Konsument, doppelter Fuzzy-Match-Durchlauf gespart). `_strip_master_internal`/
  `MASTER_INTERNAL_COLUMNS` entfernt (schützten vor nie existierender Spalte `api_strategy`).

**Intervall 2 — Scoring & Signale** (`scoring/trade_score.py`, `signals/`):
- BUG-Fix `swarm_paradox`: Events werden jetzt auch für die auslösenden Cluster-Trades emittiert
  (vorher nur Gegen-Trades) — Paradox-Dämpfer (`SCORING_PARADOX_DAMPER`) + `paradox_swarm`-Warning
  erreichen damit erstmals die swarm_buy-Trades.
- Hard-Gate: `paradox_swarm`-Warning bleibt jetzt auch bei Score 0 in `trade_warnings` sichtbar.
- `find_swarm_clusters`: Tumbling- → Rolling-Window (verkettete Schwärme über mehrere Sub-Fenster
  werden jetzt erkannt, vorher am ersten Trade verankert und verpasst). Wirkt auf alle 4
  Schwarm-Signale (buy/sell/conflict/paradox).
- `extreme_report` Fast-Trigger mit Untergrenze: `0 <= delay_days <= EXTREME_REPORT_FAST_DAYS`
  (negativer Delay = Datenfehler triggert nicht mehr).
- Toter Modul-5-Platzhalter `filter_by_ticker_sector` aus `tags/committee_tag.py` entfernt.

**Intervall 3 — App/Streamlit** (`app/*`, `config/settings.py`):
- Red-Flag-Ampel im Member-Detail-Popup emoji-frei, Schwellen konfigurierbar
  (`DETAIL_RED_FLAG_VOTE_TIMING_RATE`, `DETAIL_RED_FLAG_COMMITTEE_FULL_RATE`,
  `DETAIL_RED_FLAG_OUTPERFORMANCE_PP`).
- Neu `DETAIL_RELATED_RETURN_COLOR_PCT` (±Schwelle der Grün/Rot-Färbung der Related-Trades-Tabelle
  im Trade-Detail-Popup).
- Spaghetti-Highlight-Fix: verwaister session_state-Key (voriges Member-Popup) wird gegen die
  aktuellen Trajektorien validiert — Doppelklick hebt das Highlight jetzt wirklich auf.
- AR-Anzeige respektiert jetzt `AR_OUTLIER_MODE='percentile'` im Member-Popup (Quantile über die
  volle sichtbare Verteilung; Fallback auf absolute Schwellen bei < 2 Werten).
- Performance: `prefilt` (Trades-/Assets-/Members-Tabs) wird bei aktiven Score-Overrides aus dem
  einmal rekomputeten `corr_base` abgeleitet statt separat rekomputet (1 statt 2
  Voll-Recomputes pro Rerun, zeilenidentisches Ergebnis).
- `scoring_presets.json`-Korruptionsschutz: nicht parsebare Datei wird nach `.bak` gesichert +
  einmalige UI-Warnung (vorher drohte stiller Totalverlust beim nächsten Speichern).
- Signal-Ausschluss (Sidebar) neutralisiert Primärsignale jetzt auch im Hard-Gate — schließt der
  User beide Primärsignale aus, ist das Gate ganz aus (Hint-Scores kommen durch, für
  Was-wäre-wenn-Analysen).
- Glossar-Korrektur: `swarm_paradox`-Eintrag beschrieb fälschlich `swarm_buy`.

**Tests:** 1019 passed, 0 failed (Intervall 1: +4 Delta-Kontext-Tests; Intervall 2: +5, zwei
bestehende bewusst an neues Verhalten angepasst; Intervall 3: +26, zusätzlich unabhängig
verifiziert — 0 Blocker).
Doku: `Pipelines.md`, `DATA_STRUCTURE.md`, `Filter_Instanz_Parameter.md`,
`tool_specific_md/signal_info.md`, `AGENTS.md`.

## 2026-06-27 — Abnormal Return: Integration in Master + Detail-Popups (Phase 2)

**Motivation:** Das in der Discovery gebaute AR-Lineal (signierter, marktbereinigter BHAR
vs. SPY) wird produktiv — als **deskriptives** Maß zur Markteinordnung einzelner Trades/Member,
**nicht** als Scoring-Input (Diskriminator-Befund: AR korreliert ~0 mit `trade_score`).

- **Neue Pipeline-Stage `abnormal_returns`** (`scripts/import_trades.py`, Schritt [9b] im Full-Run,
  [10b] im Delta-Run, Single-Stage `--stage_abnormal_returns`): schreibt die kontinuierlichen
  Konstrukt-A-Spalten `signed_ar_{30,60,90,120,180}d` in `trades_master_complete.csv`. Läuft nach
  `post_trade_performance` (teilt den Preis-Cache), pro Trade deterministisch (delta-sicher).
  Outlier-Flag/Perzentil-Rang werden **nicht** persistiert (bei Anzeige abgeleitet); Konstrukt B
  bleibt dem Standalone-Report vorbehalten.
- **App-Anzeige in zwei Detail-Popups** (nur dort, keine Tabellen-Spalte): Trade-Detail
  (Headline AR 120d + zweiseitiger Perzentil-Rang gegen alle Trades + Tabelle roh vs. AR je Horizont);
  Member-Detail (Median-AR 120d + Ausreißer-Quote positiv/negativ). Pure Logik in
  `app/member_metrics.py`: `abnormal_return_summary`, `ar_percentile_rank`, `format_ar_rank`.
- **Default-Headline-Horizont 90 → 120 Tage** (`AR_HEADLINE_HORIZON_DAYS`; greift auch im
  Standalone-Report).
- **Tests:** +5 AR-Helper-Tests in `tests/test_member_metrics.py`; Gesamtsuite grün
  (2 vorbestehende, AR-fremde Fehlschläge: `owner`-Dedup).
- Doku: `DATA_STRUCTURE.md` 7e, `Pipelines.md` (Stage [8b] + Pipeline 7), `CLI_Bedienungsanleitung.md` (Abschnitt 3 + 5b).

## 2026-06-26 — Event-Study / Abnormal-Return-Modul (Insider-Aufdeckung, Discovery)

**Motivation:** Der rohe Kalender-Return (`post_return_{X}d`) ist kein scharfes
Lineal fur den Informationsvorsprung von Kongressmitgliedern — Marktbewegungen
(SPY) ueberlagern das Signal. Ein marktbereinigtes Lineal (signed BHAR) sollte
echter Alpha-Extraktion naherkommen.

**Gebaut:**
- **Neu** `scoring/abnormal_returns.py`: Berechnungs-Modul. Metrik pro Trade/Horizont H:
  `signed_ar = sell_sign × ( P_ticker(t0+H)/P_ticker(t0) − 1 − ( P_SPY(t0+H)/P_SPY(t0) − 1 ) )`.
  Zwei Konstrukte: A (Anker `transaction_date`, Spalten `signed_ar_{30,60,90,120,180}d`) und
  B (Anker `disclosure_date`, Spalten `signed_ar_disc_{30,90}d`). Reuse der Preis-Primitive aus
  `scoring/post_trade_returns.py`. Bootstrap-Helper `clustered_bootstrap_mean/diff` (member-geclustert
  uber `bio_guide_id`, ohne scipy).
- **Neu** `scripts/analyse_event_study.py`: eigenstandiges Analyse-Skript (keine Flags). Liest
  `trades_master_complete.csv`, berechnet AR in-memory, schreibt Report
  `organisation/analyse_event_study.md` + `data/processed/ar_outliers.csv`.
- **Geandert** `config/settings.py`: neuer `AR_*`-Block (Benchmark, Horizonte, Ausreißer-Schwellen,
  Bootstrap-Parameter).

**Architektur:** Keine Pipeline-Stage, kein Schreiben in den Master. AR-Spalten existieren nur
in-memory. Settings-Aenderung = Skript neu starten (nur Cache-Lookups).

**Kern-Befund (H=90, member-geclusterter Bootstrap):** Spearman(score, `signed_ar_90d`) = −0.0029
(roh: −0.0006) — ebenfalls ~0. Kein Signal hat einen statistisch belastbaren Edge (alle 95%-CIs
umschliessen 0). Die `committee_ex_officio`-Signifikanz aus dem Vorganger-Report (t−2.55) war ein
**Clustering-Artefakt** (Trade-Level-Bootstrap ohne Member-Clustering). Das +/−3%-Ausreißer-Band
flaggt 38% der Trades (kein scharfer Tail; heavy tails, p95 ≈ +/−17,5%). Scope dieses Reports:
nur 90d-Kalender-Lineal — Event-verankerte Lineale (Votes/Konstrukt C) aufgeschoben.

**Artefakte:** `organisation/analyse_event_study.md` (Report, Single-Source der Befunde),
`data/processed/ar_outliers.csv` (gitignored). Spalten + Metrik: `DATA_STRUCTURE.md` Abschnitt 7e.

---

## 2026-06-14 — Netz-Robustheit-Härtung (NetworkCanary)

**Motivation**: Ein leeres yfinance-Ergebnis (HTTP 200, keine Exception) ist
nicht von einem echten Delisting unterscheidbar. Bisher wurde aus diesem
mehrdeutigen Ergebnis zirkulär auf den Netz-Zustand geschlossen
(`_network_healthy`-Heuristik) — ein Netzwerkfehler konnte so fälschlich als
„delisted"/NaN persistiert werden.

**Lösung**: Out-of-Band-Gesundheitssignal über ein neues NetworkCanary-
Verdikt-Orakel.

- **Neu** `utils/network_canary.py`: probt garantiert-live Ticker
  (SPY/AAPL/MSFT) als unabhängiges Signal, GRÜN/ROT-Verdikt TTL-gecacht
  (60s), `verdict()`/`force_reprobe()`/`wait_then_reprobe()`.
- **Neu** `utils/network_retry.py`: zentraler `retry_call`-Helfer (R1-R6)
  mit typ-basierter transient-Klassifikation, Backoff, `NetworkRetryExhausted`.
- **`scoring/post_trade_returns.py`**: Variante-B-Fenster-State-Machine —
  ganzes Preis-Fenster vorläufig, ROT/Throttle (inkl. HTTP 429) → gesamtes
  Fenster verworfen + ab Fenster-Beginn im selben Lauf neu verarbeitet
  (rückwirkende Invalidierung). Fail-closed nach `CANARY_MAX_RED_ROUNDS` (30)
  ROT-Runden → `RuntimeError`, keine degradierten Daten geschrieben.
- **Marker-Schema-Wechsel** im Preis-Cache: Delisted-Tombstone war
  `(ticker, date=1900-01-01, close=NaN)`, jetzt
  `(ticker, last_checked_date=heute, close=NaN)`. Erkennung über NaN-close.
  Alte 1900-Marker migrieren automatisch. Neue `last_checked`-TTL
  (`POST_TRADE_DELISTED_RECHECK_DAYS = 30`). E5: Marker-Prune bei echten
  Kursen + Marker-Kollaps (nur jüngster NaN-Marker je Ticker).
- **`exit_status='delisted'`** wird jetzt aus dem Preis-Lookup abgeleitet
  (None ⇒ delisted) statt aus rohem 1900-Marker-Slice — heilt zuvor „stuck"
  gebliebene `delisted`-Werte selbst (Befund-2-Fix).
- **`utils/ticker_sector.py`**: Canary als Verdikt-Autorität (ersetzt
  `_network_healthy`, jetzt LEGACY); vor dem Schreiben von
  `delisted_trades_stage.csv` wird eine frische `force_reprobe()` erzwungen.
- **R1-R6**: `utils/ticker_normalizer.py` + `utils/issuer_group_mapper.py`
  auf zentrale transient-Klassifikation + `retry_call` umgestellt.

**Neue Settings** (`config/settings.py`): `CANARY_TICKERS`,
`CANARY_MIN_POSITIVE`, `CANARY_VERDICT_TTL_SECONDS`, `CANARY_PROBE_BACKOFFS`,
`CANARY_MAX_RED_ROUNDS`, `POST_TRADE_DELISTED_RECHECK_DAYS`.
`POST_TRADE_NETWORK_HEALTHY_TOLERANCE` als LEGACY markiert.

**Tests**: 948 passed (2 vorbestehende, fremde Scraper-Failures außerhalb
des Scopes). Details: `organisation/Briefing_stage_post_trade_performance.md`,
`organisation/Ticker_Architektur.md`, `organisation/Pipelines.md`,
`organisation/Preis_Aktualisierung.md`.

---

## 2026-06-12 — Committee-Quelldaten F2 + Ex-Officio-Signal v2

**Motivation:** Das alte `Member_Committees_Full`-Format war mehrdeutig (`;`
trennte Committees UND Subcommittees) — der Pipeline-Parser verlor
systematisch Subcommittee→Sektor-Zuordnungen (z. B. Walberg 4 statt 20
Sektoren). Lösung: Committee-Quelldaten von Grund auf neu erzeugt im
eindeutigen **F2-Format**, plus Überarbeitung des Ex-Officio-Bonus von
binär/member-global auf **rollen- und ebenen-differenziert**.

**F2-Format** (Spec: `scrapers/senate_pdf_parsing_tool/csv_zielstruktur.md`):
`;` trennt nur noch Committees, `:` leitet die Sub-Liste ein, `~` (NEU)
trennt Subcommittees, `|` trennt Name von Rolle. Rollen sind ebenen-abhängig
(`Chairman`/`Vice Chairman`/`Ranking Member`/`Ex Officio`/`Cochairman`/leer
auf Committee-Ebene; `Chair`/`Vice Chair`/`Ranking Member`/`Ex Officio`/leer
auf Sub-Ebene).

**Producer-Wechsel:**
- **Senate:** alter PDF-Parser (`scrapers/senate_pdf_parsing_tool/parse_us_senate_committee_pdf.py`)
  retiriert. Neu: `scrapers/senate_committee_xml/fetch_senate_xml.py`
  (dynamische Committee-Discovery aus senate.gov-Dropdown, curl_cffi
  Chrome-Impersonation gegen Akamai-403, 26 XML-Snapshots committet nach
  `scrapers/senate_committee_xml/raw/*.xml` + `_manifest.json` + Change-Report)
  → `scrapers/senate_committee_xml/parse_senate_xml.py` (XML→F2-CSV, 100
  Senatoren, Name „Last, First", Rollen-Mapping XML-`position`→F2
  ebenen-abhängig).
- **House:** `scrapers/house_pdf_parsing_tool/parse_house_committee_pdf.py`
  generalisiert auf alle 20 Standing + 2 Select (Intelligence mit 6 Subs,
  China) + 4 Joint Committees (House-Hälfte, Senatoren kommen aus dem
  Senate-XML); 436 Member, 26 Committees. Parser-Änderungen:
  `LEFT_COL_THRESHOLD` 330→318; Select-Sub-Header sz 8.0 (statt 7.5)
  ALL-CAPS zentriert; 2-zeilige Committee-Header werden gemergt
  (China-Select); dedizierter `parse_joint`-Pfad mit HOUSE/SENATE-Spalten-Label
  pro Committee (Flip real, kein Ranking-Member bei Joints); Ex-Officio-
  Erkennung fordert jetzt das Wort „officio" (False-Positive-Fix
  „may attend"/„authorized to sit").
- Neu: `scrapers/generate_committee_structure.py` erzeugt
  `data/processed/committees_structure.csv` (Chamber, Committee,
  Subcommittee) aus beiden Quellen.
- Producer-Outputs `data/mappings/{house,senate}_member&(sub)committees.csv`
  (437/101 Zeilen) bleiben namens- und spaltengleich, Inhalt jetzt F2.

**Reader/Tag/Signal/Scoring/UI v2:**
- Neuer zentraler F2-Reader `utils/committee_parser.py` — einziger
  kanonischer Parser für `Member_Committees_Full`. `app/member_metrics.py`
  re-exportiert von dort; `tags/committee_tag.py::CommitteeParser` nutzt ihn
  ebenfalls (alte verlustbehaftete Abflachung entfernt).
- `tags/committee_tag.py`: jeder Sektor-Eintrag in `Insider_Sectors`/
  `Relevant_Sectors` trägt jetzt `committee_role` UND `subcommittee_role`
  statt eines vermischten `role`.
- Neue Master-Spalte `ex_officio_role_keys` (`;`-getrennt, z. B.
  `committee_chairman;subcommittee_ranking_member`), erzeugt vom CommitteeTag
  in Stage `gates_tags`.
- `signals/committee_ex_officio_signal.py` v2: triggert nur noch, wenn ≥1
  relevanter Sektor-Treffer eine Führungsrolle trägt (statt global
  `Member_ExOfficio`).
- `scoring/trade_score.py` + `config/settings.py`: `SCORING_EX_OFFICIO_BONUS`
  (alt, einzelner Wert 10) ersetzt durch `SCORING_EX_OFFICIO_ROLE_BONUSES`
  (8 Role-Keys, Werte 8–15) + `SCORING_EX_OFFICIO_COMMITTEE_GLOBAL` (Toggle).
  Bonus = MAX über die Boni der vorhandenen Role-Keys (nur wenn ein
  Trigger-Signal getriggert hat). Breakdown-Token unverändert `ex_officio:+N`.
- Streamlit Sidebar-Form „Score-Gewichte": Signal-Gewichte jetzt pro Signal
  in `st.expander` gruppiert; eigener Expander „committee_ex_officio" mit
  8 `number_input` (ein Bonus pro Role-Key) + Committee-Global-Toggle.
  Presets/Recompute wachsen entsprechend.

**Debugging-Fixes:** `config/committee_mapping.py::_normalize` strippt jetzt
führendes `the ` nach „committee on "-Präfix (Befund: „Committee on the
Judiciary" → 17 statt 0 Sektoren) und kollabiert innere Mehrfach-Whitespaces
(Quell-XML enthält Doppel-Leerzeichen, z. B. SSBK-Sub); Producer härten
Whitespace ebenfalls.

**Stage-Sequenz-Korrektur:** Nach einer Roster-/Committee-Daten-Änderung ist
die korrekte Single-Stage-Re-Run-Sequenz `--stage_committees →
--stage_gates_tags → --stage_branch1_signals → --stage_sequence_signals →
--stage_swarm_signals → --stage_scoring (+ --regenerate_curated)` — ohne die
anschließenden sequence/swarm-Stages verschwinden Sequenz-/Schwarm-Signale
aus dem Master, da `--stage_branch1_signals` `signals_triggered` von Grund
auf neu baut. Details: `organisation/Pipelines.md`,
`organisation/CLI_Bedienungsanleitung.md`.

**Validierung:** Struktur-Orakel House 0 Abweichungen (26 Committees,
Joint-Headcounts handverifiziert), Senate 70/70 Sub-Paare; Regression nur
Real-World-Delta; Testsuite 774 passed (2 vorbestehende Scraper-Failures,
nicht aus diesem Modul). Pipeline-Probelauf (112.728 Trades):
committee/committee_full-Signale 10.571→**18.702 (+77 %)**;
committee_ex_officio 10.571 (binär)→9.594 (rollenbasiert); Score-Summe
1.553.123→1.668.977; Stichproben: Walberg 4→20 Insider-Sektoren, Gottheimer
Intelligence korrekt eigenes Committee.

**Tests:** `tests/test_senate_xml_parser.py`, `tests/test_committee_tag.py`,
`tests/test_committee_mapping.py` (erweitert), Anpassungen in
`test_member_metrics.py`, `test_branch1_signals.py`, `test_trade_score.py`,
`test_score_recompute.py`.

---

## 2026-06-07 — Member-Detail-Popup (Modul 9 Iteration)

**Neues Feature im Streamlit-Dashboard:** Klick auf eine Zeile im Members-Tab
öffnet ein `@st.dialog`-Popup, das einen einzelnen Kongress-Abgeordneten
umfassend auf Insider-Trading-Auffälligkeit auswertet. Alles wird **live pro
Member berechnet** — keine neuen Master-Spalten, kein Pipeline-Lauf.
Datenbasis: gefiltertes Trade-Subset (`apply_prefilter(...,
apply_horizon_filter=False)`), bei aktivem Signal-Ausschluss per
`recompute_subset` neu gescort.

- **Phase 0 — reine Metrik-Logik + Tests:** neues Modul `app/member_metrics.py`
  (~563 Zeilen, kein Streamlit-Code): Committee-Parsing
  (`parse_committees_full`, eigener verschachtelnder Parser, bewusst nicht der
  kanonische `CommitteeParser`), Horizont-Attribution (`nearest_horizon` +
  Mittelpunkts-Schwellen 45/75/105/150 Tage, vektorisiert via
  `_nearest_horizon_array`), Verlaufs-/Spaghetti-Aggregation
  (`compute_member_trajectories`, Cap bei 180 Tagen, Ende am Voll-Close —
  bewusste Divergenz zur Horizont-Attribution), Sektor-Konzentration,
  Chart-Aggregationen und Controls/Helfer. 29 Tests grün
  (`tests/test_member_metrics.py`).
- **Phase 1–4 — Popup-UI:** neues Modul `app/member_detail.py` (~498 Zeilen,
  Plotly + Streamlit), Einstieg `render_member_detail`. Header mit
  verschachteltem Gremien-Panel + Google-Namenssuche; Controls
  (Quartal-Range-Slider, „Nur ≤180d"-Toggle, Schwellwert-x-Slider); 4 Tabs
  Übersicht/Performance/Verläufe/Timing mit Diagrammen D1/D2/D5/D6/D7/Z3 sowie
  Spaghetti-Charts D3b/D4 mit bidirektionalem Highlight (Liste ↔ Plotly via
  `on_select`); Timeline mit Vote-Timing-Markern; Red-Flag-Ampel.
- **Phase 5 — Trades-Tab Ticker-Links:** Ticker-Spalte im Trades-Tab als
  Google-`LinkColumn` (neue Helfer `_ticker_google_url`,
  `_TICKER_LINK_REGEX`; `_trade_column_config(..., ticker_as_link=False)`).
  Recent-Trades-Tab unverändert (TextColumn).
- **Settings:** neuer Parameter `DETAIL_RETURN_THRESHOLD_PCT = 10.0` —
  reiner Anzeige-Schwellwert (Anteil Trades mit `paper_return_terminal > x`
  für D1/D7), live im Popup per Slider überschreibbar, kein Recompute.
- `apply_prefilter` um Parameter `apply_horizon_filter` erweitert.

**Details/Funktionsgruppen:** `organisation/DATA_STRUCTURE.md` (Abschnitt
„7c. App-Module: Member-Detail-Popup"); Beispiele/Verhalten:
`tests/test_member_metrics.py`.

---

## 2026-06-04 — Post-Trade Exit-Detection Entkopplung + Horizont-Sichtbarkeitsfilter + Partial-Close-Zerlegung

**Modul 5 Refactor:** Exit-Fenster separiert vom Return-Horizont. Settings.EXIT_DETECTION_WINDOW_DAYS (Default None=unbegrenzt) bestimmt, wie weit die Exit-Suche reicht; Settings.POST_TRADE_HORIZONS_DAYS (Default [30,60,90,120,180]) bleibt für Return-Snapshots. Folge: 58.572 closed_full (vorher 29.642), 32.722 held (vorher 60.927) bei 112.692 Trades — deutlich realistische Exit-Detektionen.

**Semantik-Änderungen (Modul 5):**
- `effective_return_pct` jetzt REALISIERT-ONLY: nur bei closed_* gesetzt (vorher held=längster Horizont-Snapshot). Bei held/delisted/unknown/not_applicable → NaN.
- `effective_return_horizon_days` jetzt CLOSED-ONLY: nur bei closed_* gesetzt. Bei held → NaN (vorher: Horizont-Tage des Snapshots).
- Neue Spalte `first_close_offset_days`: Tage bis erstem Gegen-Trade. Basis für Horizont-Sichtbarkeitsfilter. NaN bei held/not_applicable/delisted/unknown.
- 180-Tage-Horizont: `post_price_180d`, `post_return_180d` dynamisch mit POST_TRADE_HORIZONS_DAYS (Smoke-Run 27.05.2026 → 28.06.2026: neue Spalten).

**Modul 9 Feature:** Horizont-Sichtbarkeitsfilter im Dashboard. Sidebar-Toggle (Standard an) filtert global (alle 4 Tabs) auf `first_close_offset_days ≤ primary_horizon`. Beispiel: beim 30d-Horizont sieht man nur Trades, deren Exits in 30 Tagen lagen; held bleiben immer sichtbar.

**Display-Feature:** Post-Trade-Performance-Tab — neue Partial-Close-Zerlegungs-Tabelle (Build-Funktion `build_partial_close_rows` in `app/chart_helpers.py`). Zerlegt Öffnungs-Trade mit `closed_partial` in mehrere Display-Zeilen (eine pro Close-Event, trade_id `{parent}-pc{n}`, neue Spalte `parent_trade_id`). Rein UI-Ausgabe, NICHT in Master persistiert.

**Tests:** 55 Modul-Tests grün (`test_post_trade_returns.py`: held→NaN angepasst + neue Tests für entkoppeltes Fenster/Cap/first_close_offset_days; `test_chart_helpers.py`: 7 neue Tests für `build_partial_close_rows`).

**Beispiel-Verifikation:** Ankhor Spanberger/LHX (Kauf 2022-09-01): vorher held mit −8,01% (120d-Snapshot), jetzt closed_full mit −6,26% realisiert (first_close_offset 182 Tage).

---

## 2026-05-28 — Ticker-System Vereinfachung (Preferred-Auto + Routing-Konsolidierung + Slash-Fallback Removal)

- **Säule 1 — Preferred-Auto-Resolution:** `TickerClassification.parent_ticker` neu gesetzt auf Common-Stamm für 6 PREFERRED-Notationen (z.B. `BAC-PRK` → `parent_ticker='BAC'`). Klassifikator + Sektor-Stage setzen automatisch Fallback; Coverage 100% der 38 Preferreds im Master. Manuelle CSV `Vorzugsaktien_mapping.csv` → obsolet (Datei gelöscht).
- **Säule 2 — Routing-Konsolidierung (R2.c):** Neue zentrale Datei `utils/ticker_routing.py` mit `SECTOR_LOOKUP_TYPES` (5 Typen) + `POST_TRADE_PRICE_TYPES` (4 Typen, ohne NUMERIC_SUFFIX). Konstante `_YF_ELIGIBLE_TYPES` entfernt. `api_strategy` bleibt intern in `TickerClassification`, wird NICHT mehr persistent in `trades_master_complete.csv` geschrieben. Interne Spalte `MASTER_INTERNAL_COLUMNS` eingeführt; `_strip_master_internal()` entfernt `api_strategy` vor allen CSV-Saves (4 Stellen).
- **Säule 3 — Slash-Fallback Removal (R4):** Automatischer Retry `BRK-A` → `BRK/A` entfernt (Empirisch: 0 Cache-Treffer in 2.730 Einträgen). Code war messbar tot.
- Daten-Dateien gelöscht: `data/mappings/Vorzugsaktien_mapping.csv`, `data/processed/unmatched_preferred.csv` (Output nicht mehr generiert).
- 652 von 654 Tests grün (2 vorbestehende Failures, unabhängig).
- Doku-Updates: `organisation/DATA_STRUCTURE.md`, `organisation/Pipelines.md`, `organisation/manual_review_anleitung.md`, `organisation/Ticker_Architektur.md`.

---

## 2026-05-21 — Committee-Score Vereinheitlichung

- Spalte `Relevance_Score` aus `trades_master.csv` entfernt; vier Settings
  (`COMMITTEE_THRESHOLD`, `COMMITTEE_SECTOR_MATCH_POINTS`,
  `COMMITTEE_INDUSTRY_MATCH_POINTS`, `COMMITTEE_EX_OFFICIO_MULTIPLIER`)
  gestrichen.
- `Relevant_Sectors`-JSON pro Eintrag um `match_type` (`'full'`/`'sector'`) erweitert.
- Committee-Signal-Trigger: `len(Relevant_Sectors) > 0` (vorher Punkte-Schwelle).
- Neues Signal `committee_full` triggert nur bei mind. einem Eintrag mit `match_type='full'`.
- `SCORING_WEIGHTS`: `committee` 15→10, `committee_full` +5 (Voll-Treffer = 15 wie bisher).
- `suspicious_events.csv`: Spalte `relevance_score` → `match_count`; Migration via
  `_migrate_suspicious_events_schema` beim nächsten Pipeline-Lauf.
- 100 Branch1+Scoring-Tests grün. Pipeline-Re-Run notwendig.

---

## 2026-05-21 — Modul 9 Iteration: reaktive Aggregate + relevant_sectors-Spalte

Asset- und Member-Tab aggregieren jetzt on-the-fly aus dem gefilterten Trade-Subset
(`suspicious_assets.csv` / `suspicious_members.csv` werden nicht mehr geladen).
Alle Sidebar-Filter (Datum, Member-Suche, Ticker-Suche, Signal-Ausschluss) wirken
reaktiv; Min-Score greift auf `score_max` des Aggregats. KPI-Zeile (Anzahl,
Max-Score, Σ Volumen) erscheint in allen drei Tabs. Trade-Tab hat neue abgeleitete
Spalte `relevant_sectors` (eindeutige `sector`-Namen aus `Relevant_Sectors`-JSON).

**Neue Helper in `app/streamlit_app.py`:** `_parse_relevant_sectors`, `_unique_join`,
`_unique_signals`, `aggregate_assets`, `aggregate_members`.

**Smoke-Test:** 2000-Row-Subsample, Top-Asset BBG001S5N8V8 (trade_count=9,
score_max=683, sectors='Technology').

---

## 2026-05-20 — Modul 9 (Streamlit Dashboard MVP)

Drei neue Dateien: `app/__init__.py`, `app/score_recompute.py`, `app/streamlit_app.py`.

**Hauptfunktionen:**
- **Datumsbasis:** Radio `disclosure_date` / `transaction_date` (Default disclosure_date)
- **Zeitfenster:** Radio 7 / 30 / 365 Tage / Alles (Default 30 Tage)
- **Sortierung:** absteigend / aufsteigend nach `trade_score` (Default absteigend)
- **Signal-Ausschluss:** Multiselect über alle 9 Signale — löst Voll-Recompute via `scoring/trade_score.py` aus (Gewicht ausgeschlossener Signale = 0; `swarm_paradox` excluded → `SCORING_PARADOX_DAMPER=1.0`)
- **Member-Suche / Ticker-Suche:** Substring-Filter auf `name` bzw. `ticker`/`issuer_ticker`
- **Min-Score-Slider:** 0 .. max(50, max(trade_score))

**Drei Tabs:** Trades (KPIs + 16-Spalten-Tabelle) | Assets (suspicious_assets.csv) | Members (suspicious_members.csv)

**Start:** `venv/bin/streamlit run app/streamlit_app.py` → http://localhost:8501

**Smoke-Test:** 200 Test-Rows, Recompute + alle Filter fehlerfrei. Streamlit 1.57.0, Pandas 3.0.0.

---

## 2026-05-20 — Modul 6 (Vote-Timing) Status-Korrektur

Modul 6 war als „UNVOLLSTÄNDIG" markiert, ist aber seit Modul-7-Rebuild vollständig funktional. `VoteTimingTag` liest Roll-Calls direkt aus `bill_actions.json` (Floor Votes in `actions[*].roll_call[*]`) — kein separater Vote-Endpoint nötig (`fetch_congress_votes.py` ist nie wieder eingeführt worden).

**Smoke-Run-Verifikation (111.986 Trades):**
- 5.357 Trades mit `Vote_Timing_Hit=True` (4,8%)
- 0 Inkonsistenzen zwischen `Vote_Timing_Hit_Count ≥ 1` und `floor_action` in `signals_triggered`
- Score-Effekt: mean `trade_score` mit floor_action = 64,6 vs. 21,6 ohne

**Cleanup:**
- Leere Platzhalter-Files `data/congress_api/votes_house.json` und `votes_senate.json` entfernt
- Doku-Status in CLAUDE.md, README.md, Filter_Instanz_Parameter.md, settings.py-Kommentar aktualisiert

**Neue Tests:** `tests/test_vote_timing_tag.py` (~12 Tests).

---

## 2026-05-20 — Modul 8 Phase 8.6 (Voll-Pipeline-Smoke-Run)

Voll-Pipeline-Lauf produzierte `trades_master_complete.csv` mit allen 5 Modul-8-Spalten. Score-Verteilung über 111.986 Trades: mean=23.7, median=13, max=1705 (MSFT-Cluster Nov 2021), 80,5% Score > 0, 3.085 paradox-Warnings.

**Bug-Fix:** Stage-Reihenfolge: Suspicious-Export lief vor Save → `_load_trade_scores` las die noch nicht geschriebene `trades_master_complete.csv` → alle Score-Aggregate in `suspicious_assets.csv` / `suspicious_members.csv` fielen auf 0.

Lösung: In-Flight-Hand-off via `self._inflight_trade_scores`. `_load_trade_scores` priorisiert den DataFrame aus dem laufenden Run gegenüber dem File-Read. Im Delta-Mode wurde Save vor Suspicious-Export verschoben, damit `updated_master` als Override bereitsteht. Neuer Regression-Test `test_inflight_override_uebersteuert_file`. 16/16 Modul-8.4-Tests grün.

Nach Fix: Top-Member Michael McCaul mit `score_total=649.758` (44.289 Events, `score_max=1136`), Josh Gottheimer mit `score_max=1705`.

---

## 2026-05-19 — Modul 8 (Scoring-System), Phasen 8.1–8.5

| Phase | Artefakte | Tests |
|---|---|---|
| 8.1 Trade-Volume | `utils/amount_parser.py`, Stage `_stage_trade_volume`, Spalte `trade_volume_usd` | 21 grün |
| 8.2 Asset-Volume | `scoring/asset_volume.py`, Stage `_stage_asset_volume`, Spalte `asset_tradingvolume_usd` (rolling 90d über `issuer_group_key`, Option-A-Delta-Backfill) | 10 grün |
| 8.3 Trade-Score | `scoring/trade_score.py`, Stage `_stage_scoring`, Spalten `trade_score` / `trade_score_breakdown` / `trade_warnings`. Pipeline 16 Stages. | 23 grün |
| 8.4 Score-Aggregation | Helper `_load_trade_scores`, `_join_distinct_warnings`. `suspicious_assets.csv` +5 Spalten (`score_avg`/`max`/`total`, `total_volume_usd`, `warnings`). `suspicious_members.csv` +3 Spalten (`score_avg`/`max`/`total`). | 7 grün |
| 8.5 Doku-Migration | Sieben Doku-Dateien aktualisiert; Single-Source-Konvention etabliert. | — |

---

## 2026-05 — Modul 7 (Signal-Architektur), P1–P7

Gate/Tag/Signal-Konvention eingeführt (`gates/` / `tags/` / `signals/`).

| Phase | Inhalt |
|---|---|
| P1 | Architektur-Refactor: alte `filters/` → Drei-Klassen-Struktur |
| P2 | Branch-1 Per-Trade-Signale: `committee`, `committee_ex_officio`, `floor_action`, `extreme_report` |
| P3 | Sequenz-Signale: `contrarian`, `accumulative` |
| P4 | Suspicious-Export: `suspicious_events.csv` + Views `suspicious_assets.csv` / `suspicious_members.csv` |
| P5 | OpenFIGI-Coverage 82,7%; Spalte `share_class_figi` |
| P5.5 | Abgeleitete Spalte `issuer_ticker` (Share-Class-Level, Fallback-Kette `share_class_figi → ticker_base → ticker`) |
| P5.6 | Cross-Class Issuer Mapping via SEC EDGAR CIK: Stage `_stage_issuer_group`, Spalte `issuer_group_key` (Präfix `CIK:` / `TICKER:` / `SKIP:`). GOOGL+GOOG → `CIK:1652044`. |
| P6 | Schwarm-Signale (Branch 2): `swarm_buy`/`swarm_sell` (Greedy-Cluster), `swarm_conflict` (1 Gegen-Trade), `swarm_paradox` (≥2 Gegen-Trades, mutex zu conflict). Gruppiert nach `issuer_group_key`. 24.926 P6-Events. |
| P7 | Doku-Konsolidierung |

---

## 2026-04 / 2026-05 — Rebuild T1–T5 (Ticker-Architektur)

| Stufe | Inhalt |
|---|---|
| T1 | Foundation: `utils/trade_id.py`, `utils/ticker_classifier.py` (Offline-Regex-Klassifikator) |
| T2 | Network-Wrapper: `utils/ticker_normalizer.py` (OpenFIGI + SQLite-Cache), `utils/ticker_sector.py` (yfinance) |
| T3 | Integration in `scripts/import_trades.py` (Stage-Pipeline) |
| T4 | OFFEN — Migration alter Caches, `utils/openfigi_client.py` entfernen |
| T5 | P5 + P5.5 + P5.6 (siehe Modul 7) |

Architektur-Doku: `organisation/Ticker_Architektur.md`.

---

## Vor 2026-04 — Module 1–4

- Modul 1: Projektstruktur, venv
- Modul 2: House Trades Scraping (ZIP + XML + PDF). PDF Extractor v2 (OpenCV Grid + Pixel-Density + Vision API).
- Modul 3: Senate Trades Scraping (Playwright + DataTables API)
- Modul 4: Ausschuss-Sektor-Mapping (`config/committee_mapping.py`, 861-Row Mapping)

Module 5 (Yahoo-Kursdaten) und 6 (Roll-Call-Votes) übersprungen / unvollständig.
