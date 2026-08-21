# Committee Matcher V2 – Entscheidungsvorlage Gate B

Stand: 2026-08-21. Grundlage: `committee_matcher_v2_implementierungsplan.md`
(Abschnitte 2.6, Gate B) und `committee_matcher_planung_entscheidungen_codex.md`
(„OpenRouter- und Kostengrenze", „Calls und Batching", „Vor Gate B/C noch
verbindlich festzulegen"). Alle Preise und Endpointdaten stammen aus dem
öffentlichen OpenRouter-Katalog (`/api/v1/models`, ohne Key, kostenlos, kein
Modellaufruf); Token- und Zeichenmengen sind am realen Corpus und an der
freigegebenen Taxonomiebaseline gemessen.

Zweck: Bene bestätigt oder ändert zehn Werte; danach friert der Runvertrag sie
ein und ein einzelner kostenbegrenzter Contract-Smoke prüft sie live.

## Kurzfassung – zu bestätigende Werte

| # | Entscheidung | Empfehlung |
|---|---|---|
| 1 | Produktionsmodell | `anthropic/claude-opus-5` |
| 2 | Zweitkandidat der Evaluation | `openai/gpt-5.6-terra` |
| 3 | Provider-/Endpoint-Slug | `anthropic` bzw. `openai` (je Modell genau einer) |
| 4 | Datenschutz / ZDR | `data_collection = 'deny'`, ZDR verlangt |
| 5 | `max_price` | 6.00 / 30.00 USD pro Mtok (Prompt/Completion) |
| 6 | Kontext- und Tokengrenzen | Fenster 200 000; `max_output` 8 000; Reasoningreserve 24 000; Sicherheitsmarge 15 % |
| 7 | Timeout und Retries | 300 s; `WORKFLOW_RETRIES = 2`; SDK-Retries 0 |
| 8 | Budgets | Run 25 USD; Evaluation 40 USD; Qualifikation 15 USD |
| 9 | SDK-Pin | `openrouter==1.1.71` (installiert) |
| 10 | Confidence-Rubrik 1–4 | Vorschlag unter Punkt 10 |

Gesamtkosten aller drei Budgets zusammen: 80 USD. Die konservativ gerechneten
Ist-Kosten liegen bei rund einem Drittel davon.

## Messgrundlage

| Größe | Wert | Herkunft |
|---|---|---|
| Units | 225 | eingefrorene Sollmenge `unit-id-v1` |
| Unit-Texte gesamt | 394 053 Zeichen ≈ 98 500 Tokens | Corpus, Ø 1 751 Zeichen je Unit |
| Taxonomie-Katalog | 38 279 Zeichen ≈ 9 570 Tokens | freigegebene Baseline, 145 Industries |
| System-/Regelprompt | ≈ 900 Tokens | Schätzung nach Regelumfang des Codex-Dokuments |
| Prompt je Call (Batch 3) | Ø 11 800, max ≈ 13 900 Tokens | gemessen über alle 75 Batches |
| Calls | 75 (Batch 3) bzw. 225 (Batch 1) | + 20 % Repair-/Retryaufschlag |
| Output je Unit | 225 Tokens, mit Reasoning konservativ 675 | JSON-Ergebnis mit bis zu 2 Zuordnungen |

Umrechnung 4 Zeichen je Token (englischer Fachtext, konservativ). Der größte
Einzelcall bleibt mit ≈ 13 900 Tokens weit unter jedem Kandidatenfenster; der
Context-Split 3→2→1 wird rechnerisch nie gebraucht, bleibt aber als Schutz aktiv.

**Wichtigste Ableitung:** Der Taxonomie-Prefix macht 89 % jedes Prompts aus und
ist über alle Calls identisch. Batch 3 zahlt ihn 75-mal, Batch 1 dagegen
225-mal — daher kostet Batch 1 das 1,9-fache bei identischem Output. Das
bestätigt die Codex-Festlegung „Produktionsziel Batch 3" von der Kostenseite;
fachlich entscheidet weiterhin die Evaluation.

## Kostenmatrix (vollständiger Lauf über alle 225 Units)

Inklusive 20 % Repairaufschlag und dreifachem Reasoning-Output.

| Modell @ Endpoint | Preis in/out $/Mtok | Full-Run Batch 3 | Full-Run Batch 1 | Evaluationsmatrix |
|---|---|---|---|---|
| `anthropic/claude-opus-5` @ `anthropic` | 5,00 / 25,00 | **9,86** | 19,28 | 7,83 |
| `openai/gpt-5.6-terra` @ `openai` | 2,00 / 12,00 | **4,31** | 8,08 | 3,33 |
| `anthropic/claude-sonnet-5` @ `anthropic` | 2,00 / 10,00 | 3,94 | 7,71 | 3,13 |
| `google/gemini-3.7-flash` @ `google-vertex/global` | 0,375 / 1,875 | 0,74 | 1,45 | 0,59 |
| `openai/gpt-5.6-luna` @ `openai` | 0,20 / 1,20 | 0,43 | 0,81 | 0,33 |

Evaluationsmatrix = 20 Gold-Set-Units × 2 Batchvarianten × 3 Wiederholungen ohne
Cache-Reads (Codex-Vorgabe). Alle Werte in USD.

Die Spreizung zwischen billigstem und teuerstem Kandidaten beträgt über den
gesamten Full-Run 9,43 USD. Kosten sind damit kein tragfähiges Auswahlkriterium
— die Auswahl entscheidet sich an der Precision, weil jeder False Positive
manuelle Reviewzeit kostet und die Primärmetrik des Projekts verschlechtert.

## 1. Produktionsmodell

**Empfehlung: `anthropic/claude-opus-5`.**

Die Aufgabe ist kein Extraktions-, sondern ein Abgrenzungsproblem: Das Modell
muss „unmittelbar wesentliche Produkte, Tätigkeiten oder Regulierung" von
„Lieferkettennähe, allgemeiner Wirtschaftswirkung und thematischer
Nachbarschaft" trennen und im Zweifel `no_mapping` wählen — gegen den
Antwortdruck, aus 145 Kandidaten etwas zu liefern. Genau diese Zurückhaltung
unterscheidet Modellklassen stärker als jede Formatierungsfähigkeit, und sie
schlägt direkt auf die Primärmetrik durch.

PRO Opus 5:

- stärkste verfügbare Urteilsfähigkeit bei Negativentscheidungen — die
  häufigste und teuerste Fehlerklasse dieses Projekts
- Mehrkosten gegenüber dem günstigsten Kandidaten: 9,43 USD über den gesamten
  Full-Run, gegenüber geschätzt 3–6 h manueller Reviewzeit bei schlechterer
  Precision
- 1 M Kontextfenster, `structured_outputs` auf sechs Endpoints, `reasoning_effort`
  steuerbar, Uptime 99,95 % (24 h)
- Batch-Endpoint `anthropic/claude-opus-5:batch` zu halbem Preis bleibt als
  spätere Option offen, ohne den Modellslug zu wechseln

CONTRA Opus 5:

- 2,3-fache Kosten gegenüber `gpt-5.6-terra` bei einem Nutzen, der erst durch
  das Gold-Set belegt wird — vor der Evaluation ist die Precisionannahme eine
  Erwartung, keine Messung
- kein `seed`-Parameter auf den Anthropic-Endpoints; Lauf-zu-Lauf-Stabilität
  lässt sich nur über Wiederholungen messen, nicht über einen festen Seed
  reduzieren
- höchster Preis der Kandidatenliste; bei einer späteren Ausweitung auf
  häufigere Läufe skaliert der Nachteil linear

Einschränkung: Die Empfehlung gilt für V1 mit 225 Units und einem einmaligen
Full-Run. Bei periodischer Wiederholung ist der Kandidatenvergleich neu zu
führen.

## 2. Zweitkandidat der Evaluation

**Empfehlung: `openai/gpt-5.6-terra` als zweiter Kandidat in derselben
Variantenmatrix.**

Der Codex-Beschluss erlaubt ausdrücklich „gegebenenfalls vorab freigegebene
Modellkandidaten unter identischen Verträgen". Ein Zweitkandidat kostet in der
Evaluation 3,33 USD und beantwortet die Frage, die sonst offen bleibt: ob der
Preisunterschied zu Opus 5 überhaupt einen Precisionunterschied kauft.

PRO Zweitkandidat:

- belegt die Modellwahl empirisch statt per Annahme, bevor der Full-Run läuft
- `seed` auf allen OpenAI-Endpoints verfügbar; erlaubt eine sauberere Messung
  der Lauf-zu-Lauf-Stabilität als Anthropic
- halbiert das Risiko, dass ein Anbieterausfall den Zeitplan blockiert, da ein
  bereits evaluierter Ersatz vorliegt
- Zweitkandidat aus einer anderen Modellfamilie deckt familienspezifische
  Fehlermuster auf, die ein Vergleich innerhalb einer Familie verdeckt

CONTRA Zweitkandidat:

- verdoppelt Evaluationsdauer und -kosten (auf 11,16 USD zusammen) für eine
  Erkenntnis, die die Produktionsentscheidung möglicherweise nicht ändert
- ein Modellwechsel nach der Evaluation invalidiert laut Plan Evaluation und
  Qualifikation, sodass die Zweitmessung nur vor dem Holdout Wert hat
- das Gold-Set mit 15–20 Units ist klein; ein Precisionunterschied von wenigen
  Prozentpunkten bleibt statistisch nicht unterscheidbar

Einschränkung: Bei einem Unterschied unterhalb von zwei Fehlentscheidungen im
Gold-Set gilt das Ergebnis als unentschieden, und Punkt 1 bleibt gesetzt.

## 3. Provider- und Endpoint-Slug

**Empfehlung: `only = ["anthropic"]` für Opus 5, `only = ["openai"]` für
Terra.** Dazu `allow_fallbacks = false` und `require_parameters = true` gemäß
Plan 2.6.

Beide sind die Direktendpoints des jeweiligen Modellanbieters, führen den
niedrigsten Preis ihrer Endpointliste, unterstützen `structured_outputs` und
liegen bei 99,95 % beziehungsweise 99,88 % Uptime über 24 Stunden. Die
Alternativen (`amazon-bedrock/*`, `azure/*`, `google-vertex/*`) kosten
regionsabhängig 10 % mehr, und ein Teil der Vertex-Endpoints meldet
`structured_outputs = false` — sie scheiden damit aus.

Nicht empfohlen: `openai/flex` (halber Preis, aber verzögerte Verarbeitung ohne
Latenzgarantie) und `*/priority` (doppelter Preis ohne fachlichen Gewinn bei
einem seriellen Lauf ohne Zeitdruck).

## 4. Datenschutz und ZDR

**Empfehlung: `COMMITTEE_MATCHER_DATA_COLLECTION = 'deny'` und ZDR im
Providerblock verlangen.**

Die gesendeten Daten sind öffentliche Kongressdokumente und eine öffentliche
Industrietaxonomie — der Schutzbedarf ist gering. `deny` bleibt trotzdem
richtig, weil es reproduzierbares Routing erzwingt: Endpoints ohne
Datenschutzzusage fallen deterministisch heraus, statt je nach Auslastung
unterschiedlich zu routen.

Der öffentliche Katalog weist die tatsächliche ZDR-Eigenschaft je Endpoint nicht
aus. Der Contract-Smoke muss daher prüfen, welcher Endpoint real geroutet hat,
und das Ergebnis ins Runmanifest schreiben — das deckt sich mit der
Plananforderung „tatsächliches Routing" im Smoke.

## 5. `max_price`

**Empfehlung: `{'prompt': 6.00, 'completion': 30.00}` USD pro Mtok.**

Das sind 120 % des Opus-5-Listenpreises (5,00 / 25,00). Der Puffer fängt
Preisanpassungen und regionale Aufschläge ab, ohne einen unbeabsichtigt teureren
Endpoint zuzulassen: Der nächstteurere Opus-5-Endpoint
(`amazon-bedrock/us-east-1`, 5,50 / 27,50) bleibt zulässig; ein versehentlich
gesetzter Premiumslug wie `claude-opus-5-fast` (10,00 / 50,00) wird sicher
abgewiesen.

Bei Wahl von Terra als Produktionsmodell stattdessen 2,40 / 14,40.

`max_price` ersetzt laut Plan das lokale Runbudget nicht — es begrenzt nur den
zulässigen Endpointpreis.

## 6. Kontext-, Token- und Reservegrenzen

**Empfehlung:**

| Setting | Wert | Begründung |
|---|---|---|
| `ENDPOINT_CONTEXT_TOKENS` | 200 000 | bewusst unter den real verfügbaren 1 000 000 |
| `MAX_OUTPUT_TOKENS` | 8 000 | 3 Units × bis zu 5 Zuordnungen mit Evidenz + Rationale ≈ 3 400 Tokens; Faktor 2,3 Puffer |
| `REASONING_RESERVE_TOKENS` | 24 000 | dreifacher Outputwert für Reasoning-Traces |
| Sicherheitsmarge | 15 % | Aufschlag des Estimators vor der Fensterprüfung |

Das Fenster wird absichtlich auf 200 000 statt 1 000 000 gesetzt: Der größte
reale Call misst 13 900 Tokens, das Gate greift also nie regulär. Ein kleiner
gesetzter Wert macht das Context-Gate zu einem scharfen Wächter gegen
Promptfehler — ein versehentlich verzehnfachter Prompt blockiert sichtbar,
statt still 1 M Tokens zu senden und abgerechnet zu werden.

Token-Estimator: 4 Zeichen je Token mit 15 % Aufschlag. Ein modellspezifischer
Tokenizer ist nicht installiert und für die Fensterfrage bei einem Faktor 14
Abstand auch nicht nötig; die Abweichung der Heuristik liegt weit innerhalb der
Marge.

## 7. Timeout und Retries

**Empfehlung: `REQUEST_TIMEOUT_S = 300.0`, `WORKFLOW_RETRIES = 2` (Default
bleibt), SDK-Retries 0 (bereits als Invariante gesetzt).**

300 Sekunden sind großzügig für einen Call mit ≈ 12 000 Prompt-Tokens und bis zu
8 000 Outputtokens plus Reasoning. Großzügigkeit ist hier die günstigere Seite:
Ein Timeout erzeugt laut Codex-Beschluss `outcome_unknown`, blockiert weitere
Calls über die reservierten Worst-Case-Kosten und braucht für einen erneuten
Versuch deine ausdrückliche Bestätigung mit Doppelabrechnungswarnung. Ein zu
knapper Timeout kostet also deutlich mehr Zeit als ein zu großzügiger.

## 8. Budgets

**Empfehlung:**

| Setting | Wert | Deckung gegenüber der konservativen Schätzung |
|---|---|---|
| `MAX_RUN_COST_USD` | 25,00 | 2,5-fach über dem Opus-5-Full-Run (9,86) |
| `MAX_EVALUATION_COST_USD` | 40,00 | 3,6-fach über der Zweikandidatenmatrix (11,16) |
| `MAX_QUALIFICATION_COST_USD` | 15,00 | Holdout ist kleiner als die Entwicklungsmatrix |

Alle drei sind harte Obergrenzen, keine Prognosen: Ein Call, der nicht
vollständig ins Restbudget passt, wird nicht gestartet. Der Faktor 2,5 bis 3,6
deckt Repair-Ketten, Wiederholungen bei uneindeutiger Evaluation (3→5 Läufe) und
reservierte Kosten ungeklärter Outcomes ab. Das Evaluationsbudget liegt höher
als das Runbudget, weil die Matrix mehrfach über dieselben Units läuft.

## 9. SDK-Pin

**Empfehlung: `openrouter==1.1.71` bestätigen.**

Installiert und in `requirements.txt` gepinnt. Das Paket ist das offizielle SDK
(`OpenRouterTeam/python-sdk`), zieht `httpx>=0.28.1`, `httpcore>=1.0.9` und
`pydantic>=2.11.2,<2.13` — alle drei erfüllt deine vorhandenen Pins ohne
Upgrade. Der Contract-Smoke bestätigt oder verwirft diesen Pin laut Codex-
Beschluss endgültig.

Zu beachten: Das SDK liefert ein bis zwei Releases pro Tag. Der Pin bleibt
unverändert, bis ein Befund einen Wechsel erzwingt; ein Versionswechsel gilt als
Vertragsänderung und erfordert einen erneuten Smoke.

## 10. Confidence-Rubrik 1–4

Der Codex-Beschluss verlangt „exakte Confidence-Definitionen" vor Gate B, ohne
sie festzulegen. **Vorschlag zur Bestätigung:**

| Stufe | Bedeutung | Prüffrage |
|---|---|---|
| 4 | Jurisdiktionstext benennt die Industry oder ihre Kernprodukte ausdrücklich | Steht die Zuständigkeit wörtlich im Text? |
| 3 | Text benennt eine Tätigkeit oder Regulierung, die die Industry unmittelbar und wesentlich erfasst | Wäre die Zuordnung ohne Fachwissen aus dem Zitat ableitbar? |
| 2 | Zuordnung fachlich vertretbar, Evidenz deckt aber nur einen Teilbereich der Industry | Trägt das Zitat die ganze Industry oder nur ein Segment? |
| 1 | Zuordnung plausibel, Evidenz belegt die Unmittelbarkeit nicht zweifelsfrei | Bleibt ein Lieferketten- oder Nachbarschaftsverdacht? |

Anschlussregeln, beide bereits durch Codex-Beschlüsse gedeckt:

- Broad-Scope-Ausnahme (3–5 Zuordnungen) verlangt bei jeder Zuordnung
  mindestens Stufe 3
- Confidence löscht vor abgeschlossener Kalibrierung niemals automatisch
  Mappingzeilen; sie wird gespeichert und im Gold-Set gegen die beobachtete
  Precision je Stufe kalibriert

Offen und bewusst nicht vorgeschlagen: die spätere Nutzungsregel (ab welcher
Stufe eine Zuordnung ohne manuellen Blick übernommen wird). Sie braucht die
Kalibrierungsdaten und gehört fachlich hinter das Gold-Set, nicht vor den ersten
Call.

## Nicht empfohlen: Prompt-Caching in V1

89 % jedes Prompts sind cachefähig, und Caching senkt den Full-Run von 9,86 auf
5,62 USD. Trotzdem: **erst nach dem Full-Run erwägen.**

Der Gewinn beträgt 4,24 USD einmalig. Dem steht gegenüber, dass Cache-Writes
eigene Preise haben (6,25 USD/Mtok bei Anthropic, das 1,25-fache des
Normalpreises), dass die Endpoints implizites Caching laut Katalog nicht
durchgängig melden, und dass jede Cachemechanik die Frage aufwirft, ob der
Kostenvergleich zwischen Evaluationsvarianten noch sauber ist. Vier Dollar
rechtfertigen dieses Risiko in einem einmaligen Lauf nicht.

## Offene Umsetzungspunkte (keine Entscheidung, gehört in P5)

- `jsonpath-python==1.1.6` fehlt in `requirements.txt`; es kam als transitive
  Abhängigkeit des SDK mit
- `OPENROUTER_API_KEY` steht in `.env`, wird aber von `config/settings.py` nicht
  gelesen; nötig sind `OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY', '')`
  und eine leere Vorlagenzeile in `.env.example`
- der Key gehört nicht in den `COMMITTEE_MATCHER_*`-Block, da dessen Werte per
  UI überschreibbar sind, und in kein Auditartefakt

## Nach der Bestätigung

1. Werte in `config/settings.py` eintragen und Runvertrag einfrieren
2. P5 implementieren: Prompts, providerneutrales Interface, OpenRouter-Adapter,
   Fake-Provider
3. Contract-Smoke: ein einzelner kostenbegrenzter Call prüft Auth, Structured
   Output, tatsächliches Routing, `usage.cost`, Provider-IDs und
   Generation-Lookup; er bestätigt oder verwirft den SDK-Pin
