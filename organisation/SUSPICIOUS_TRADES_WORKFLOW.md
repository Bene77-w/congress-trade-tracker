# Suspicious Trades – Workflow-Architektur und Implementierungsstand

Diese Datei ist die eigenständige technische Dokumentation des neuen AI-Workflows. Für Folgephasen sind zusätzlich die bindenden Pläne zu lesen:

- `~/.claude/plans/suspicious-trades-basisversion-klonstrategie.md` (v9): Gesamtarchitektur, Phasen B1–B5, Klonstrategie und gemeinsame Verträge.
- `~/.claude/plans/suspicious-trades-litellm-schicht.md` (v1): bindender LLM-Vertrag der in B1 implementierten Schicht.
- Erst nach dem jeweiligen Divergenzpunkt: `suspicious-trades-architektur-a.md`, `-b.md` oder `-c.md`. Für B2 sind diese Variantenpläne noch nicht relevant.

## Zielbild und Klonstrategie

Der Workflow untersucht US-Politiker-Trades anhand öffentlicher Ereignisse und Web-Evidenz. Drei Varianten teilen zunächst dieselbe Basis:

- A: Ereignisrecherche → blinde Marktprognose → mechanischer Trade-Match.
- B: Ereignisrecherche → trade-informierter Verdachts-Screener.
- C: Briefings → Frontier-Orchestrator mit Delegation → Devil's Advocate und Prediction Review.

Die gemeinsame Implementierung bleibt bis B5 im Package `suspicious_trades/`. Danach wird sie nach `suspicious_trades_c/` kopiert; das Original erhält den A/B-Zwischenstamm (Overview, Deep Research, Dossier-Gerüst) und wird erst danach in A und B getrennt. Deshalb gelten bereits jetzt zwingend:

- ausschließlich relative Intra-Package-Imports;
- Pfade relativ zu `PROJECT_ROOT`;
- `DATA_DIR = PROJECT_ROOT / "data" / __package__.split(".")[0]`, damit jeder Klon automatisch getrennte Daten hält;
- `data/agent_staging/` bleibt absichtlich variantenübergreifend;
- keine Variantenlogik in gemeinsamen Basisbausteinen.

## Phasen und gemeinsame Verträge

| Phase | Inhalt |
|---|---|
| B1, fertig | Package-Gerüst, Config/Overrides, LiteLLM, Tavily/Search, Smoke-CLI |
| B2, fertig | vier Ingestion-Adapter, Event-Registry mit Lebenszyklus/Outcome-Erkennung, CLI `ingest` |
| B3 | Trades-Zugriff, Empfehlungen/Tracking, Reporting-Verträge, Status/Stop und User-Hints |
| B4 | Taipy-Control-UI mit Run-Steuerung, Status, Konfiguration, Prompts, Kosten und Browsern |
| B5, fertig (Rest-Abnahme Control-UI-Sichtbarkeit offen bis B4) | Export markierter Recent Trades aus der bestehenden Streamlit-App ins gemeinsame Staging |
| danach | C-Divergenz; anschließend A/B-Zwischenstamm und A-/B-Divergenz |

Alle Varianten erzeugen pro Lauf `empfehlung.md` (kompaktes Ranking ohne numerischen Verdachtsscore) und `bericht.md` (Kausalitätsketten, Evidenz, Gegenargumente). Empfehlungen werden nach Rang, nicht nach Score, gespeichert und später gegen den bestehenden Preis-Cache hypothetisch verfolgt.

## B1: implementiertes Fundament

### Package und Konfiguration

`suspicious_trades/config.py` lädt CWD-unabhängig ausschließlich die package-eigene `suspicious_trades/.env` und enthält sämtliche gemeinsamen, kommentierten Defaults: Pfade, Provider-/Modellparameter, autoritative `PRICING`-Werte, LLM- und Search-Caps, Ingestion-/Eval-Parameter sowie künftige UI-Werte. Bereits gesetzte Prozessvariablen haben bewusst Vorrang. Damit litellms eigener Root-`.env`-Autoload beim Import (DEV-Modus, CWD) die Package-Keys nicht als vermeintliche Prozessvariablen verdrängt, importiert `suspicious_trades/__init__.py` `config` als Erstes — die Package-`.env` ist dadurch bei jedem Einstiegspunkt vor dem ersten litellm-Import geladen. Der Loader akzeptiert nur dotenv-Zuweisungen und ignoriert ungültige Zeilen mit einer Warnung aus Pfad und Zeilennummer, ohne deren Inhalt oder Secrets auszugeben. Credential-Diagnosen unterscheiden redigiert `process-env`, `package-env`, `runtime-env` und `missing`; leere Werte und bekannte Platzhalter werden vor einem Live-Aufruf erkannt. `AGENT_SPECS` ist in der neutralen Basis bewusst leer; spätere Phasen definieren dort vollständige Rollen-Specs mit `model`, `effort`, `max_turns`, `cache`, `search`, `extract`, optional `crawl`, `max_output_tokens` und `prompt_template`.

`effective(reload=False)` introspektiert alle UPPERCASE-Konstanten und merged `data/<package>/config_overrides.json`:

- Dicts werden rekursiv gemerged, Listen vollständig ersetzt, Skalare ersetzt.
- Unbekannte Schlüssel, unbekannte Rollen und Typkonflikte werden gewarnt und ignoriert.
- Freie Modellstrings außerhalb `PRICING` bleiben erlaubt, erzeugen aber eine Warnung.
- Das Ergebnis ist pro Prozess memoisiert; `reload=True` dient Tests/Preview. Aufrufer erhalten tiefe Kopien.

Spätere Pipeline-Schritte müssen ausschließlich `effective()` lesen. Prompt-Overrides gehören nicht in dieses JSON; B3/B4 laden vollständige Dateien aus `data/<package>/prompt_overrides/`.

### LLM-Schicht

Öffentliche API aus `suspicious_trades/llm/`:

- `complete(role, messages, tools=None, spec_override=None)`
- `call_json(role, messages, schema, max_repair_attempts=2)`
- `call_with_tools(..., max_turns, run_ctx) -> ToolLoopResult`
- `UsageLog`, `RunContext` sowie typisierte Fehler für Key-, Validierungs-, Stop- und Kosten-Cap-Fälle.

`client.py` verwendet ausschließlich `litellm.completion()` (`litellm==1.85.1`), volle Providerpräfixe und die Env-Zuordnung `KEY_ENV_BY_PREFIX`. Es gibt keine Provider-Registry und keine direkten OpenAI-/Anthropic-SDK-Imports. `reasoning_effort="none"` wird weggelassen; andere Werte gehen mit `drop_params=True` an LiteLLM. Jeder Provideraufruf hat die konfigurierbare harte Obergrenze `LLM_TIMEOUT_SECONDS` (Default 60 Sekunden). Transiente Fehler können bei aktiviertem Schalter genau einmal auf ein gemapptes OpenRouter-Modell fallen; Request-/Authfehler nicht. Der Live-Smoke setzt LiteLLM-Retries explizit auf 0, damit Providerfehler ohne kostensteigernde Wiederholungen klassifiziert werden.

Prompt-Caching setzt providerneutral `cache_control={"type":"ephemeral"}` auf den letzten System-Block und einen wandernden letzten Verlaufsblock. Vor jedem Aufruf werden alte Verlaufspunkte entfernt; Eingaben werden nicht mutiert. Dynamische Run-Daten gehören daher in User-Messages, nie in statische System-Prompts.

`tool_loop.py` normalisiert OpenAI-Tool-Calls, prüft `run_ctx.check_stop()` vor jedem LLM-Call, reicht Toolfehler als Tool-Message zurück und erzwingt nach ausgeschöpften Tool-Turns einen Abschluss ohne Tools. Für deterministische Integrationsprüfungen können die ersten Tool-Turns mit `tool_choice="required"` erzwungen werden. Der minimale B1-`RunContext` kann bereits Stop-Datei, `UsageLog` und temporäre `spec_override`-Profile tragen; B3 ersetzt/erweitert ihn um die vollständige Statusschicht.

`usage.py` normalisiert Anthropic- und OpenAI-Cachefelder, berechnet deterministische Kosten aus `PRICING` und schreibt je Call `runs/<run_id>.usage.jsonl` sowie eine Aggregation in `runs/<run_id>.json`. Zeitlich begrenzte Tarife werden über `PRICING_PERIODS` gewählt (Sonnet-5-Einführungspreis bis einschließlich 2026-08-31), kontextabhängige Tarife über `PRICING_TOKEN_TIERS` (GPT-5.5 oberhalb 272.000 Input-Tokens). Die DeepSeek- und GPT-5.5-Cachepreise entsprechen den offiziellen Providerpreisen. Bei freien Modellen dient `litellm.completion_cost()` nur als gewarnte Schätzung. Vor jedem Call prüft der aktive `UsageLog` die laufweite Kostenobergrenze anhand der letzten Rollenkosten.

### Search-Schicht

`search_api/base.py` definiert das providerneutrale 5+1-Interface `search`, `extract`, `map_site`, `crawl`, `research`, `get_usage`. Nicht implementierte Providerfunktionen werfen explizit `NotImplementedError`.

`TavilySearchAPI` in `search_api/tavily.py` nutzt `tavily-python==0.7.26` für die fünf funktionalen Endpunkte. Da das SDK kein `get_usage()` anbietet, wird ausschließlich dieser Monitoring-Aufruf direkt per `httpx GET /usage` ausgeführt. Konstruktorparameter `project_id` und `session_id` setzen die Attribution: Projekt aus `TAVILY["project_ids"][package]`, Session gleich `run_id`.

Search, Extract, Map und Crawl erzwingen `include_usage=True`. `auto_parameters` wird überall entfernt; Map/Crawl entfernen zusätzlich `instructions`. Research ist standardmäßig deaktiviert und nie ein Agent-Tool. Das SDK dokumentiert für Research kein `include_usage`; dessen Credits werden deshalb ausdrücklich als unbekannt geloggt. Wegen eines SDK-Verlusts von HTTP-Statusdetails erhält der exakt gepinnte Standardclient einen engen Hook, der 429 samt `retry-after` sowie 432/433 bewahrt. 429 wird höchstens `SEARCH_MAX_RETRIES` wiederholt; 432/433 brechen sofort mit `SearchBudgetExhausted` ab.

Jeder Aufruf schreibt `runs/<run_id>.search_usage.jsonl` mit Rolle, Endpunkt, Tiefe, Credits und Erfolg. `check_budget()` wertet `/usage` gegen das kleinere von Providerlimit und `monthly_credit_budget` sowie `credit_reserve` aus. Es darf wegen des Tavily-Limits nur am Run-Start und nach jedem Agent-Step aufgerufen werden, nie pro Search-Call.

`SearchToolExecutor` stellt `web_search` immer, `web_extract` nur bei aktiviertem Extract-Block und `web_map`/`web_crawl` nur bei aktiviertem Crawl-Block bereit. Map und Crawl teilen `max_calls`; `max_depth`, `max_breadth` und `limit` werden auf die kleinste globale/Rollen/Call-Grenze gekappt. Standardrecherche ist Search → Scorefilter/URL-Deduplizierung → Extract. Map/Crawl sind nur für Vollständigkeit auf einer bereits bekannten Domain gedacht.

### CLI, Abhängigkeiten und Tests

Der kostenpflichtige Live-Smoke startet nur mit dem Pflichtflag `--confirm-live-costs`; ohne dieses Flag endet er vor jedem Provideraufruf mit Exit-Code 2. Er prüft jedes eindeutige Modell aus `SMOKE_MODELS` und künftigen `AGENT_SPECS` sowie Tavily. Seine Statussemantik ist:

| Status | Bedeutung | Exit-Code 1 |
|---|---|---|
| `PASS` | Providercheck erfolgreich | nein |
| `SKIP` | Credential fehlt; Provider wird nicht aufgerufen | nein |
| `AUTH_FAIL` | Platzhalter oder ungültiges Credential | ja |
| `MODEL_CONFIG_FAIL` | nicht unterstützter/falscher Modellstring | ja |
| `ACCESS_BLOCKED` | externe Quote-, Billing-, Regions- oder Budgetblockade | nein |
| `FAIL` | Netzwerk-, Timeout- oder sonstiger Providerfehler | ja |

`--cache-check` ergänzt zwei identische Anthropic-Calls und verlangt beim zweiten Call einen Cache-Read. Darauf folgen zwei erzwungene serielle Tool-Calls (Schritte 1 und 2) und der toolfreie Finalcall `CACHE_OK`, zusammen genau drei Loop-Turns; auch der Loop muss einen Cache-Read melden. Der Systemprompt wird lokal tokengezählt und über `CACHE_CHECK_PROVIDER_TO_LOCAL_TOKEN_RATIO` (Default 2,0) knapp über die Provider-Mindestgröße gebracht; Tool-Calls haben ein separates Limit von 128 Output-Tokens. Smoke-Artefakte landen unter `data/suspicious_trades/runs/`, das wie spätere Klonverzeichnisse gitignoriert ist.

Kostenpflichtige LLM-/Search-Smokes dürfen nach normalen Implementierungen niemals automatisch ausgeführt werden. Sie erfordern einen ausdrücklichen Userauftrag plus das Pflichtflag; Standard-Abnahme ist ausschließlich die Offline-/Mock-Suite. Die verbindliche Agentenregel steht in `AGENTS.md` Regel 11.

Das Subprojekt verwaltet seine Installation und Credentials unabhängig von der Hauptanwendung: `pip install -r suspicious_trades/requirements.txt` installiert die exakt gepinnten Pakete `litellm==1.85.1` und `tavily-python==0.7.26`. `suspicious_trades/.env` (gitignored) enthält Platzhalter für `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`, `TAVILY_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `XAI_API_KEY` und `OPENROUTER_API_KEY`. Die projektweite Root-`.env` wird von diesem Package nicht geladen. B2 ergänzt seine benötigten Ingestion-Keys später in derselben Subprojekt-Datei.

Offline-Abnahme: `venv/bin/python -m pytest -q suspicious_trades/tests` – aktuell 66 Tests ohne Netzwerk. Sie decken zusätzlich Credential-Provenienz/Platzhalter, Dotenv-Robustheit und -Priorität, die config-vor-litellm-Importinvariante, Timeout, zeit-/tokenabhängige Preise, Smoke-Klassifikation/Exitcodes (inkl. `key_source` im Fehlerdetail), Pflichtflag und den deterministischen Cache-Tool-Loop ab. Gesamtabnahme am 2026-07-11: 1104 Projekt-Tests bestanden. Der einmalig ausdrücklich autorisierte Live-Smoke bestand für OpenAI, Anthropic Opus/Sonnet/Fable, xAI Grok (via VPN), Tavily und den Anthropic-Cachecheck; Gemini war wegen Free-Tier-Quote 0 `ACCESS_BLOCKED`. Der anfängliche DeepSeek-`AUTH_FAIL` war kein ungültiger Package-Key, sondern litellms Root-`.env`-Autoload (s. „Package und Konfiguration"); nach dem Fix bestand ein erneut autorisierter Live-Smoke für DeepSeek Flash/Pro mit `key_source=package-env`.

## B2: Ingestion und Event-Registry (implementiert)

B2 ergänzt ausschließlich `suspicious_trades/ingestion/`, `suspicious_trades/registry/` und den CLI-Subcommand `ingest`; B1-Schichten werden wiederverwendet, nicht dupliziert. Alle Importe sind relativ, Pfade paketabgeleitet, sämtliche Schwellen/Lookbacks/Quellenschalter/Timeouts liegen in `config.py` und werden über `config.effective()` gelesen.

### Gemeinsamer Vertrag

`EventCandidate(source, source_id, event_class, title, event_date, update_date, expected_outcome_date, url, raw_payload)` in `ingestion/base.py`, mit `event_id = "<source>:<source_id>"`. `event_date` ist das fachliche Ereignisdatum, `update_date` das Delta-/Änderungsdatum — beide werden nie vermischt. `to_iso_date`/`parse_iso_date` normalisieren beliebige API-Datumsformate defensiv auf `YYYY-MM-DD`. Jeder Adapter erbt `IngestionAdapter` und trennt `fetch()` (quellenspezifischer Roh-Abruf, ggf. Netz) von `parse(raw, *, today)` (reine, offline testbare Normalisierung). `run_ingestion(settings, *, today, adapters=None)` führt alle über `INGEST_SOURCES_ENABLED` aktivierten Quellen aus; deaktivierte werden protokolliert übersprungen, eine fehlschlagende Quelle bricht den Gesamtlauf nicht ab (redigierter Fehler-Log, übrige Quellen laufen weiter).

### Adapter (`ingestion/`)

- **`congress_gov.py`** — liest ausschließlich den bestehenden Cache `data/congress_api/` (`CONGRESS_CACHE_DIR`, kein eigener Download). Ein Event je Bill; Filter über `updateDate` gegen `INGEST_LOOKBACK_DAYS`. `event_date = introduced_date`, `update_date = updateDate`, `source_id = bill_id`; Payload trägt policy_area, sponsors, committees, legislative_subjects, latest_action und die Actions-Liste (Basis der Floor-/Public-Law-Outcome-Erkennung). URL wird aus congress/typ/nummer konstruiert.
- **`federal_register.py`** — öffentliche Dokument-API (kein Key), mappt API-Typ „Rule"/„Proposed Rule" auf die kanonischen Codes `RULE`/`PRORULE` und filtert gegen `FEDREG_DOC_TYPES` (Notices raus). Payload: type, abstract, agencies, rin (`regulation_id_numbers`), docket_ids, effective_on, comments_close_on. `expected_outcome_date = effective_on || comments_close_on`.
- **`war_gov_rss.py`** — defense.gov-Contracts-RSS (stdlib `ElementTree` + Regex, keine Zusatz-Abhängigkeit). Je Tagesartikel Teilstreitkraft-Header + Contract-Absätze; ein Event je Absatz ≥ `WAR_GOV_MIN_CONTRACT_USD` (Betrags-Parser deckt „$X", „$X million/billion" ab). `source_id = <guid>#<laufende Contract-Nr über alle Absätze>` (stabil auch bei geänderter Schwelle). Veröffentlichung IST das Outcome (Registry setzt sofort `outcome_known`).
- **`sam_gov.py`** — Get-Opportunities-API v2 (`SAM_GOV_API_KEY`). Solicitation/Presolicitation = offenes Event; Award-Notices erkennt `is_award_notice()`. Payload: type, solicitationNumber, naicsCode, classificationCode, agency, award{date,amount,awardee}, responseDeadLine.

### Registry (`registry/event_registry.py`)

`EventRegistry(path, settings)` persistiert `events.jsonl` (atomar via tmp+rename) unter `DATA_DIR`. `update(candidates, *, today)` läuft in vier Schritten: (1) Upsert (Re-Ingest desselben `event_id` = Update, `first_seen` bleibt erhalten, `raw_payload` wird gemergt), (2) Award-Zuordnung (Award-Notices erzeugen KEIN eigenes Event, sondern schließen die offene Solicitation gleicher `solicitationNumber`), (3) Outcome-Detektor, (4) Close-/Expire-Sweep. Rückgabe `RegistryStats(new_events, updated, outcomes_detected, closed, expired)` (counts-Schema §5e).

Lebenszyklus `open → outcome_known → closed` plus `expired`. Vier bindende Outcome-Regeln:

| event_class | Outcome |
|---|---|
| `legislation` | Floor-Action mit `roll_call` ODER `latest_action` „Became Public Law" (Datum = Action-/Law-Datum) |
| `regulation` | RULE: `effective_on ≤ today`; PRORULE: späteres RULE gleicher RIN/Docket ingested (Datum = RULE-`publication_date`) |
| `defense_contract` | sofort bei Ingest (`actual_outcome_date = event_date`) |
| `procurement` | Award-Notice gleicher `solicitationNumber`; ersatzweise `responseDeadLine + PROCUREMENT_AWARD_TIMEOUT_DAYS` überschritten → `expired` |

Abschluss aller Klassen: `outcome_known → closed`, sobald `actual_outcome_date` älter als `EVENT_CLOSE_AFTER_DAYS`. Deaktivierte Quellen werden übersprungen, bestehende Registry-Ereignisse nie gelöscht.

### CLI und Config

`python -m suspicious_trades.run ingest [--today YYYY-MM-DD]` ruft `run_ingestion` + `EventRegistry.update` und druckt Kandidaten je Quelle, übersprungene/fehlgeschlagene Quellen und die Registry-Kennzahlen. `--today` ankert Lookback-/Outcome-Fenster deterministisch (Default heute). Neue Config-Keys: `CONGRESS_CACHE_DIR`, `EVENTS_FILENAME`, `INGEST_TIMEOUT_SECONDS`, `FEDERAL_REGISTER_API_URL`/`_PER_PAGE`, `WAR_GOV_RSS_URL`, `SAM_GOV_API_URL`/`SAM_GOV_LIMIT` (bestehende `INGEST_*`/`FEDREG_*`/`WAR_GOV_*`/`EVENT_CLOSE_AFTER_DAYS`/`PROCUREMENT_AWARD_TIMEOUT_DAYS` wiederverwendet). `.env` ergänzt Platzhalter `SAM_GOV_API_KEY` und optional `CONGRESS_API_KEY`.

### Tests

Offline-Abnahme: `venv/bin/python -m pytest -q suspicious_trades/tests` – 93 Tests ohne Netzwerk (66 B1 + 27 B2). Eingefrorene Rohantworten je Quelle unter `suspicious_trades/tests/fixtures/{congress_gov,federal_register,war_gov,sam_gov}/`. Abgedeckt: Feld-Mapping und Lookback-/Typ-/Volumen-Filter je Adapter, `is_award_notice`, Re-Ingest-Update statt Duplikat, alle vier Outcome-Regeln inkl. RULE-nicht-effektiv und Floor-ohne-Roll-Call, expired- und closed-Sweep, Konfigurierbarkeit der Schwellen, Quellen-Schalter (skip), fehlertoleranter Quellen-Fehler, CLI-Ende-zu-Ende (inkl. Award schließt Solicitation im Folgelauf). Live-Smokes je Quelle bleiben separat und keyabhängig. Nach jedem Agent-Step künftiger Phasen muss der Aufrufer `TavilySearchAPI.check_budget()` ausführen; vor LLM-Calls übernimmt der gemeinsame Tool-Loop Stop- und Kostenprüfung.

## B5: implementierter Export

B5 erweitert ausschliesslich die bestehende Streamlit-Haupt-App (Recent-Trades-Tab in `app/streamlit_app.py`) um einen Export in das variantenuebergreifende Zwischenlager; die Agent-Packages (`suspicious_trades*/`) werden nicht beruehrt.

Die Export-Logik liegt streamlit-frei und offline testbar in `app/staging_export.py`. `export_selection(df, staging_dir=..., timestamp=..., export_ts=...)` schreibt `data/agent_staging/trade_selection_<timestamp>.csv` mit exakt dem festen §5b-Spaltenschema (`STAGING_COLUMNS`, 22 Spalten in bindender Reihenfolge; Details/Herkunft je Spalte: Plan §5b und `organisation/DATA_STRUCTURE.md`). `direction` wird — falls in der Zeile leer/fehlend — via `signals.committee_signal._direction_from_transaction_type` abgeleitet (Rueckgabe 'buy'/'sell'/'other', identisch zur Nachberechnung des kuenftigen Staging-Readers; die "+1/-1"-Annotation der Plan-Tabelle meint die Semantik, nicht das Rueckgabeformat). `export_ts` traegt den ISO-Export-Zeitpunkt. Fehlt eine Quellspalte im DataFrame, wird sie schema-stabil als leere Spalte geschrieben und eine Warnung geloggt. Das Zielverzeichnis (`config/settings.py: AGENT_STAGING_DIR`, relativ zu `BASE_DIR`, bewusst ausserhalb der Klon-DATA_DIR) wird beim Export automatisch angelegt und ist gitignoriert.

UI-Bedienung im Recent-Trades-Tab (beide §5b-Wege, keine Emojis):

- Normalweg „Alle gefilterten Zeilen exportieren" — exportiert den kompletten gefilterten `recent_pool` (nicht nur die per „mehr anzeigen" sichtbare Teilmenge).
- Feintuning „Auswahl exportieren" — Mehrfachauswahl ueber die Checkbox-Spalte (`st.dataframe`, `selection_mode='multi-row'`, Shift-Klick-Bereichsauswahl) der sichtbaren Tabelle. Der Recent-Trades-Tab hatte bisher keine Zeilen-Selektion/kein Detail-Popup (anders als Trades-/Members-Tab), daher kollidiert multi-row mit keinem bestehenden Klick-Verhalten.

Nach dem Export erscheint eine Erfolgsmeldung mit Dateipfad und Zeilenzahl. Die Control-UI-Sichtbarkeit (Uebernahme der Export-Datei in einen Lauf) folgt erst mit B4.

Offline-Abnahme: `venv/bin/python -m pytest -q tests/test_staging_export.py` (10 Tests: exaktes Schema/Reihenfolge, direction-Ableitung + Erhalt vorhandener Werte, fehlende-Spalten-Pfad, `export_ts`, Dateinamens-Timestamp, Verzeichnis-Anlage, Settings-Default-Dir, CSV-Round-Trip). Die Tests liegen bei den uebrigen Haupt-App-Tests unter `tests/`.

## Relevante spätere Erweiterungspunkte

- B3 liest den Trades-Master strikt read-only, validiert Staging-Pflichtspalten und dedupliziert nach `trade_id`; Recommendations nutzen den bestehenden Preis-Cache ohne eigenen Fetch.
- B3 schreibt Status atomar in `runs/<run_id>.status.json`, führt Stop-Dateien und ergänzt User-Hints/Reporting. Die vorhandenen Usage-Dateien sind Eingaben der Status-/Kostenaggregation.
- B4 schreibt nur Abweichungen in `config_overrides.json`, vollständige Prompt-Overrides in eigene Markdown-Dateien und startet CLI-Schritte als Subprozesse.
- B5 exportiert die gefilterte/manuell markierte Recent-Trades-Auswahl nach `data/agent_staging/`.
- Erst Variantenphasen befüllen `AGENT_SPECS` und Prompt-Templates. Crawl-Tools dürfen nur Rollen mit Crawl-Block sehen; Dossier-/Formatter-Rollen arbeiten ohne Search-Tools.
