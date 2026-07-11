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
| B2 | vier Ingestion-Adapter und Event-Registry einschließlich Outcome-Erkennung |
| B3 | Trades-Zugriff, Empfehlungen/Tracking, Reporting-Verträge, Status/Stop und User-Hints |
| B4 | Taipy-Control-UI mit Run-Steuerung, Status, Konfiguration, Prompts, Kosten und Browsern |
| B5 | Export markierter Recent Trades aus der bestehenden Streamlit-App ins gemeinsame Staging |
| danach | C-Divergenz; anschließend A/B-Zwischenstamm und A-/B-Divergenz |

Alle Varianten erzeugen pro Lauf `empfehlung.md` (kompaktes Ranking ohne numerischen Verdachtsscore) und `bericht.md` (Kausalitätsketten, Evidenz, Gegenargumente). Empfehlungen werden nach Rang, nicht nach Score, gespeichert und später gegen den bestehenden Preis-Cache hypothetisch verfolgt.

## B1: implementiertes Fundament

### Package und Konfiguration

`suspicious_trades/config.py` lädt ausschließlich die package-eigene `suspicious_trades/.env` und enthält sämtliche gemeinsamen, kommentierten Defaults: Pfade, Provider-/Modellparameter, autoritative `PRICING`-Werte, LLM- und Search-Caps, Ingestion-/Eval-Parameter sowie künftige UI-Werte. `AGENT_SPECS` ist in der neutralen Basis bewusst leer; spätere Phasen definieren dort vollständige Rollen-Specs mit `model`, `effort`, `max_turns`, `cache`, `search`, `extract`, optional `crawl`, `max_output_tokens` und `prompt_template`.

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

`client.py` verwendet ausschließlich `litellm.completion()` (`litellm==1.85.1`), volle Providerpräfixe und die Env-Zuordnung `KEY_ENV_BY_PREFIX`. Es gibt keine Provider-Registry und keine direkten OpenAI-/Anthropic-SDK-Imports. `reasoning_effort="none"` wird weggelassen; andere Werte gehen mit `drop_params=True` an LiteLLM. Transiente Fehler können bei aktiviertem Schalter genau einmal auf ein gemapptes OpenRouter-Modell fallen; Request-/Authfehler nicht.

Prompt-Caching setzt providerneutral `cache_control={"type":"ephemeral"}` auf den letzten System-Block und einen wandernden letzten Verlaufsblock. Vor jedem Aufruf werden alte Verlaufspunkte entfernt; Eingaben werden nicht mutiert. Dynamische Run-Daten gehören daher in User-Messages, nie in statische System-Prompts.

`tool_loop.py` normalisiert OpenAI-Tool-Calls, prüft `run_ctx.check_stop()` vor jedem LLM-Call, reicht Toolfehler als Tool-Message zurück und erzwingt nach ausgeschöpften Tool-Turns einen Abschluss ohne Tools. Der minimale B1-`RunContext` kann bereits Stop-Datei, `UsageLog` und temporäre `spec_override`-Profile tragen; B3 ersetzt/erweitert ihn um die vollständige Statusschicht.

`usage.py` normalisiert Anthropic- und OpenAI-Cachefelder, berechnet deterministische Kosten aus `PRICING` und schreibt je Call `runs/<run_id>.usage.jsonl` sowie eine Aggregation in `runs/<run_id>.json`. Bei freien Modellen dient `litellm.completion_cost()` nur als gewarnte Schätzung. Vor jedem Call prüft der aktive `UsageLog` die laufweite Kostenobergrenze anhand der letzten Rollenkosten.

### Search-Schicht

`search_api/base.py` definiert das providerneutrale 5+1-Interface `search`, `extract`, `map_site`, `crawl`, `research`, `get_usage`. Nicht implementierte Providerfunktionen werfen explizit `NotImplementedError`.

`TavilySearchAPI` in `search_api/tavily.py` nutzt `tavily-python==0.7.26` für die fünf funktionalen Endpunkte. Da das SDK kein `get_usage()` anbietet, wird ausschließlich dieser Monitoring-Aufruf direkt per `httpx GET /usage` ausgeführt. Konstruktorparameter `project_id` und `session_id` setzen die Attribution: Projekt aus `TAVILY["project_ids"][package]`, Session gleich `run_id`.

Search, Extract, Map und Crawl erzwingen `include_usage=True`. `auto_parameters` wird überall entfernt; Map/Crawl entfernen zusätzlich `instructions`. Research ist standardmäßig deaktiviert und nie ein Agent-Tool. Das SDK dokumentiert für Research kein `include_usage`; dessen Credits werden deshalb ausdrücklich als unbekannt geloggt. Wegen eines SDK-Verlusts von HTTP-Statusdetails erhält der exakt gepinnte Standardclient einen engen Hook, der 429 samt `retry-after` sowie 432/433 bewahrt. 429 wird höchstens `SEARCH_MAX_RETRIES` wiederholt; 432/433 brechen sofort mit `SearchBudgetExhausted` ab.

Jeder Aufruf schreibt `runs/<run_id>.search_usage.jsonl` mit Rolle, Endpunkt, Tiefe, Credits und Erfolg. `check_budget()` wertet `/usage` gegen das kleinere von Providerlimit und `monthly_credit_budget` sowie `credit_reserve` aus. Es darf wegen des Tavily-Limits nur am Run-Start und nach jedem Agent-Step aufgerufen werden, nie pro Search-Call.

`SearchToolExecutor` stellt `web_search` immer, `web_extract` nur bei aktiviertem Extract-Block und `web_map`/`web_crawl` nur bei aktiviertem Crawl-Block bereit. Map und Crawl teilen `max_calls`; `max_depth`, `max_breadth` und `limit` werden auf die kleinste globale/Rollen/Call-Grenze gekappt. Standardrecherche ist Search → Scorefilter/URL-Deduplizierung → Extract. Map/Crawl sind nur für Vollständigkeit auf einer bereits bekannten Domain gedacht.

### CLI, Abhängigkeiten und Tests

`python -m suspicious_trades.run smoke` prüft jedes eindeutige Modell aus `SMOKE_MODELS` und künftigen `AGENT_SPECS` sowie Tavily. Fehlende Keys sind `SKIP`; die Zusammenfassung verweist dafür ausdrücklich auf `suspicious_trades/.env`. Providerfehler sind `FAIL`. `--cache-check` ergänzt zwei identische große Anthropic-Calls und einen dreiturnigen Mini-Tool-Loop; beide Pfade müssen Cache-Reads melden. Smoke-Artefakte landen unter `data/suspicious_trades/runs/`, das wie spätere Klonverzeichnisse gitignoriert ist.

Das Subprojekt verwaltet seine Installation und Credentials unabhängig von der Hauptanwendung: `pip install -r suspicious_trades/requirements.txt` installiert die exakt gepinnten Pakete `litellm==1.85.1` und `tavily-python==0.7.26`. `suspicious_trades/.env` (gitignored) enthält Platzhalter für `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`, `TAVILY_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `XAI_API_KEY` und `OPENROUTER_API_KEY`. Die projektweite Root-`.env` wird von diesem Package nicht geladen. B2 ergänzt seine benötigten Ingestion-Keys später in derselben Subprojekt-Datei.

Offline-Abnahme: `venv/bin/python -m pytest -q suspicious_trades/tests` – aktuell 46 Tests ohne Netzwerk. Sie decken Config-Merge, Cachemarker, Effort, Usage/Kosten, Cap, JSON-Repair, Fallback, Tool-Loop/Stop, alle Search-Endpunkte, Attribution, Credit-Log, Retry/Budget, Tool-Gating/Caps und CLI ab.

## Anschlussvertrag für B2

B2 ergänzt ausschließlich `ingestion/`, `registry/` und den CLI-Subcommand `ingest`; B1-Schichten werden wiederverwendet, nicht dupliziert. Gemeinsames Eingabeschema:

`EventCandidate(source, source_id, event_class, title, event_date, update_date, expected_outcome_date, url, raw_payload)`, mit `event_id = "<source>:<source_id>"`. `event_date` ist das fachliche Ereignisdatum, `update_date` das Delta-/Änderungsdatum. Re-Ingest merged denselben Schlüssel statt zu duplizieren.

Adapter: Congress.gov liest ausschließlich den bestehenden Cache; Federal Register nutzt RULE/PRORULE; war.gov-RSS erzeugt je Contract-Absatz oberhalb des konfigurierten Mindestvolumens sofort `outcome_known`; SAM.gov verknüpft Award Notices über `solicitationNumber`. Die Registry implementiert `open -> outcome_known -> closed` sowie `expired`. Outcome-Regeln: Congress Floor-Roll-Call/Public Law, PRORULE→RULE bzw. RULE-effective date, war.gov sofort, SAM Award bzw. Timeout. Deaktivierte `INGEST_SOURCES_ENABLED`-Quellen werden übersprungen, vorhandene Registry-Ereignisse aber nie gelöscht.

B2-Tests bleiben offline und frieren kleine Rohantworten je Quelle unter `suspicious_trades/tests/fixtures/` ein. Live-Smokes je Quelle sind separat und keyabhängig. Nach jedem Agent-Step künftiger Phasen muss der Aufrufer `TavilySearchAPI.check_budget()` ausführen; vor LLM-Calls übernimmt der gemeinsame Tool-Loop Stop- und Kostenprüfung.

## Relevante spätere Erweiterungspunkte

- B3 liest den Trades-Master strikt read-only, validiert Staging-Pflichtspalten und dedupliziert nach `trade_id`; Recommendations nutzen den bestehenden Preis-Cache ohne eigenen Fetch.
- B3 schreibt Status atomar in `runs/<run_id>.status.json`, führt Stop-Dateien und ergänzt User-Hints/Reporting. Die vorhandenen Usage-Dateien sind Eingaben der Status-/Kostenaggregation.
- B4 schreibt nur Abweichungen in `config_overrides.json`, vollständige Prompt-Overrides in eigene Markdown-Dateien und startet CLI-Schritte als Subprozesse.
- B5 exportiert die gefilterte/manuell markierte Recent-Trades-Auswahl nach `data/agent_staging/`.
- Erst Variantenphasen befüllen `AGENT_SPECS` und Prompt-Templates. Crawl-Tools dürfen nur Rollen mit Crawl-Block sehen; Dossier-/Formatter-Rollen arbeiten ohne Search-Tools.
