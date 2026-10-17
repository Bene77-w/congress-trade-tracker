# Implementierungsplan: kompakte Terminalstatusmeldungen im Committee Matcher V2

Stand: 2026-08-20  
Scope: Live-Status für fachlich relevante Workflowvorgänge; bestehender
strukturierter Abschlussbericht bleibt erhalten  
Grundlage: `organisation/committee_matcher_v2_implementierungsplan.md`

## Ziel und Ausgabeformat

Jeder nutzerrelevante Vorgang meldet Start, Ergebnis, Wartezustand oder Fehler
kompakt im Terminal. Interne Funktionsaufrufe und erfolgreiche einzelne
yfinance-Requests erzeugen keine eigene Zeile; Netzwerkfehler, Retries und
Checkpointfortschritt bleiben sichtbar.

```text
[START] mapping | committee=House Agriculture | units=6
[OK] mapping | unit=Livestock... | industries=2
[INFO] mapping | unit=Nutrition... | no_mapping | reason=keine direkte Zuständigkeit
[OK] mapping | committee=House Agriculture | mapped=5 | no_mapping=1 | industries=8
[WARN] taxonomy | industry=uranium | retry=2/3 | network_timeout
[ERROR] provider | unit=house-agriculture-livestock | code=API_TIMEOUT
[WAIT] taxonomy | run=run-2026-08-20 | awaiting_taxonomy_approval
```

`no_mapping` ist eine gültige fachliche Entscheidung und erscheint als
`INFO`, nicht als Fehler.

## Phase S1 – Statusvertrag

Prio: hoch; Umfang: sechs Statusarten und ein Zeilenformat.

- Statusarten `START | OK | INFO | WARN | ERROR | WAIT`;
- Pflichtangaben Stage, Vorgang, Objekt und Ergebnis;
- Unit-Ergebnis mit Anzahl zugeordneter Industries;
- Committee-Abschluss mit `mapped`, `no_mapping`, `failed` und Anzahl
  eindeutiger Industries;
- Fehler mit Code, betroffenem Objekt und nächstem Handlungsschritt;
- keine Prompttexte, API-Schlüssel oder vollständigen Providerresponses.

## Phase S2 – Zentraler Terminalreporter

Prio: hoch; Umfang: ein bestehendes Modul plus Reporter-Tests.

- `committee_matcher/errors.py` um einen gemeinsamen Live-Renderer erweitern;
- Status auf `stdout`, Warnungen und Fehler auf `stderr`;
- vorhandene Fehlerpersistenz und aggregierten Abschlussbericht beibehalten;
- keine verteilten freien `print()`-Aufrufe in Fachmodulen.

## Phase S3 – Integration in P0–P4

Prio: hoch; Umfang: fünf implementierte CLI-Aktionen.

- `input validate`: Start, Datei-/Unitanzahl und Ergebnis;
- `roster validate`: Coveragezahlen und Lücken;
- `snapshot build`: Sektorfortschritt, Checkpoints, Retries und Ergebnis;
- `snapshot resume`: Wiederaufnahmepunkt und Restumfang;
- `snapshot approve`: Freigabe oder Ablehnung;
- genau ein Abschlussbericht je Lauf, auch bei Abbruch und Exceptions.

Kontrollpunkt: S1–S3 durch Tests bestanden und durch einen separaten
Read-only-Phasenreview abgenommen.

## Phase S4 – Integration in P6/P7

Prio: hoch; Abhängigkeit: implementierter Mappingkern und Workflow.

- Start und Abschluss jedes Batches;
- Ergebnis jeder Mapping-Unit;
- Zusammenfassung jedes Committees;
- Repair-Versuche und endgültige Fehlschläge;
- Kostenstand nach jedem Modellcall;
- Provider-/Netzwerkfehler sofort als `WARN` oder `ERROR`;
- Human-Gates als `WAIT`.

S4 wird mit P6/P7 implementiert; vorab entsteht kein Mapping-Simulationscode.

## Phase S5 – Abnahme

Prio: hoch; Umfang: mindestens zwölf deterministische Terminaltests.

- Mapping mit 1, 2 und 5 Industries;
- `no_mapping` und Committee-Gesamtsumme;
- Netzwerk-Retry und finaler API-Fehler;
- Taxonomie-Freigabegate und Benutzerabbruch;
- Geheimnisfreiheit der Ausgabe;
- Streamtrennung `stdout`/`stderr`;
- genau ein Abschlussbericht bei Erfolg und Fehler.

Abschluss: Statusausgabe für alle implementierten Vorgänge vollständig;
spätere Phasen übernehmen denselben Reporter als verbindlichen Vertrag.
