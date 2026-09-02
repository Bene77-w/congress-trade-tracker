# Gold-Set-Creator — Bedienung

`committee_matcher/goldset_label.py` lässt die 22 Units der Entwicklungs-
Stichprobe von einem headless Agenten labeln, wahlweise über Claude Code oder
Codex. Ergebnis ist das Entwicklungs-Gold-Set, gegen das P9 den Matcher misst.

Der Produktivpfad `run plan|execute` bleibt ungenutzt — er ist der Prüfling.
Die Labels sind ein Vorschlag; das Gold-Set entsteht erst durch die Abnahme.

Begründungen und vollständige Fehlertabelle:
`organisation/committee_matcher_goldset_labeling_plan.md`.

## Ablauf

Jeder Kasten ist genau ein Terminalbefehl, in dieser Reihenfolge auszuführen.
Alle Aufrufe laufen aus dem Projektwurzelverzeichnis.

```
venv/bin/python committee_matcher/goldset_label.py prepare
  |   Werkzeug: liest Korpus, Units, Taxonomie-Snapshot und Modellvertrag,
  |   schreibt je Unit ein Aufgabenpaket samt Bindungshashes
  v
venv/bin/python committee_matcher/goldset_label.py preflight --engine claude
  |   Werkzeug + CLI, minimal bezahlt: Ein-Token-Lauf prüft Aufrufweg,
  |   Anmeldung, Modellname und Denktiefe
  v
venv/bin/python committee_matcher/goldset_label.py run --engine claude
  |   Agent, BEZAHLT: je Unit ein eigener headless Lauf, Rohantwort und Log
  |   je Unit. Abbruch jederzeit; erneuter Aufruf setzt fort
  v
venv/bin/python committee_matcher/goldset_label.py collect --engine claude
  |   Werkzeug: prüft jede Antwort gegen Vertrag, Taxonomie und Belegtext,
  |   schreibt labels_/notes_/info_claude.csv
  v
venv/bin/python committee_matcher/goldset_label.py preflight --engine codex
venv/bin/python committee_matcher/goldset_label.py run      --engine codex
venv/bin/python committee_matcher/goldset_label.py collect  --engine codex
  |   dieselben drei Schritte mit der zweiten Engine
  v
venv/bin/python committee_matcher/goldset_label.py merge --base claude --against codex
  |   Werkzeug: vergleicht beide Läufe, schreibt disagreements.md
  |   und die merged-Tabellen als Arbeitsstand
  v
venv/bin/python committee_matcher/goldset_label.py to-xlsx --source merged
  |   Werkzeug: baut goldset_review.xlsx mit den Blättern labels,
  |   jurisdiction, taxonomy, agent_notes, info; alle Zellen als Text
  v
xdg-open data/committee_matcher_v2/goldset/goldset_review.xlsx
  |   DU: jede Zeile ändern, löschen, ergänzen; Auswahlliste im Blatt
  |   labels; disagreements.md nennt die strittigen Units.
  |   Datei vor dem nächsten Schritt schließen
  v
venv/bin/python committee_matcher/goldset_label.py verify --source merged --fix-case
  |   Werkzeug: liest zurück, prüft erneut gegen den Vertrag, repariert
  |   Schreibweise und Randleerraum, nennt jede Abweichung mit Zelladresse.
  |   Wiederholen, bis keine Befunde mehr stehen
  v
venv/bin/python -m committee_matcher evaluate export --eval-id eval-2026-09-02
  |   Matcher: Evaluationsrevision eröffnen, Taxonomie, Stichprobe, Inputs
  |   und Prompts einfrieren; kein Modellaufruf
  v
venv/bin/python -m committee_matcher evaluate import --eval-id eval-2026-09-02
      Matcher: übernimmt die abgenommene Mappe validiert in die Revision.
      Ein Verstoß lehnt den gesamten Import ab (Exit 7)
```

Die `--eval-id` ist frei wählbar, muss aber in `export` und `import` dieselbe
sein. Ohne `--labels` liest der Import die Arbeitsdatei des Werkzeugs,
`data/committee_matcher_v2/goldset/goldset_review.xlsx`.

## Wie ein Lauf funktioniert

Ein Lauf ist **eine Unit, ein Prozess, ein Agent**. Kein Batch, keine
gemeinsame Sitzung, kein Gedächtnis zwischen Units — jede Unit bekommt
dieselbe Ausgangslage, damit keine Unit von der Antwort einer anderen lernt.

```
Aufgabenpaket (aus prepare)
  Systemprompt = Modellvertrag + Rechercheauftrag
  Nutzerprompt = Unit, Zuständigkeitstext, Taxonomiekatalog
  Ausgabeschema = erzwungenes JSON
        |
        v
headless CLI im temporären Verzeichnis
  ohne Projektkontext, ohne CLAUDE.md/AGENTS.md, ohne Skills
  nur WebSearch und WebFetch als Werkzeuge, keine Datei- oder Bashrechte
        |
        v
Antwort: decision, assignments[], sources[], Unsicherheitsnotiz
        |
        v
abgelegt als runs/<engine>/<unit_id>.json samt Provenienz
  Engine, Modell, Denktiefe, Webzugang, Bindungshashes, Dauer, Turns
```

Der Vertrag, gegen den `collect` und `verify` prüfen: jede Zuordnung braucht
ein wörtliches Zitat aus dem Zuständigkeitstext, Confidence 1 bis 4 und eine
Begründung. Regulär sind zwei Zuordnungen je Unit; bis fünf nur mit
ausdrücklicher Broad-Scope-Ausnahme, eigener Begründung, Confidence ab 3 und
je Zuordnung eigenem Zitat. Enge Unittypen — Ausschusskern und Units ohne
Zuständigkeitstext — bleiben immer bei zwei, dort ist die Ausnahme unzulässig. Entweder Zuordnungen **oder** `no_mapping_reason`,
nie beides.

`merge` vergleicht die Entscheidung (`mapped` gegen `no_mapping`) und, wenn
beide `mapped` sagen, die Menge der Industry-IDs. Begründungstexte und
Confidence werden nicht verglichen — sie sind nie wörtlich gleich.
Übereinstimmung heißt nicht Richtigkeit: zwei Modelle können denselben Irrtum
teilen. Die Richtigkeitsprüfung sind der Vertrag und deine Abnahme.

## Kommandos

| Kommando | Wirkung | Bezahlt |
|---|---|---|
| `prepare` | Aufgabenpaket je Unit nach `labeling/tasks/` | nein |
| `preflight` | Ein-Token-Lauf; prüft Engine, Anmeldung, Modell, Denktiefe | minimal |
| `run` | je Unit ein headless Lauf; Antwort und Log nach `labeling/runs/<engine>/` | **ja** |
| `collect` | Antworten prüfen und als CSV sammeln | nein |
| `merge` | zwei Engineläufe vergleichen, `disagreements.md` schreiben | nein |
| `to-xlsx` | Reviewmappe aus den Zwischentabellen bauen | nein |
| `verify` | bearbeitete Reviewmappe zurücklesen und prüfen | nein |

## Flags

| Flag | Kommando | Wirkung |
|---|---|---|
| `--engine claude\|codex` | `preflight`, `run`, (`collect`) | welche CLI labelt |
| `--model <name>` | `preflight`, `run` | roh an die CLI; keine Auswahlliste, damit künftige Modelle ohne Codeänderung laufen |
| `--effort <stufe>` | `preflight`, `run` | Denktiefe, ebenfalls roh durchgereicht. Codex bestätigt sie nie — die Provenienz weist sie dann als `unbestaetigt` aus |
| `--web-access-off` | `preflight`, `run` | schaltet WebSearch und WebFetch ab. Standard ist offen, weil Recherche zum Auftrag gehört |
| `--timeout <s>` | `run` | Deckel je Unit, Default 1800 |
| `--total-timeout <s>` | `run` | Deckel für den ganzen Satz, Default 43200. Erreicht endet der Lauf geordnet, ein erneuter Aufruf setzt fort |
| `--unit-id <id>` | `run` | einzelne Unit, mehrfach zulässig. Ohne Angabe die ganze Stichprobe |
| `--force` | `run` | vorhandene Antworten neu erzeugen statt überspringen |
| `--source merged\|<engine>` | `to-xlsx`, `verify` | welche Zwischentabelle gilt; Default `merged` |
| `--force` | `to-xlsx` | vorhandene Zieldatei vorher als `<name>.<zeitstempel>.bak.xlsx` sichern |
| `--file <pfad>` | `verify` | Mappe an anderer Stelle prüfen |
| `--fix-case` | `verify` | Schreibweise und Randleerraum reparieren; sichert vorher |
| `--show` | `prepare` | Auszug des ersten Aufgabenpakets ausgeben |

Defaults für Engine, Modell, Denktiefe und Deckel stehen als `GOLDSET_LABEL_*`
in `config/settings.py`.

**Wiederaufnahme:** `run` überspringt vorhandene Antworten nur, wenn Modell,
Denktiefe, Webzugang und Bindungshashes passen; sonst `GS_RESUME_MISMATCH`.
Ein Satz aus zwei Modellen oder halber Recherche entsteht so nicht versehentlich.

**Schreibschutz:** `to-xlsx` blockt auf eine vorhandene Zieldatei, weil sie
deine Abnahmearbeit trägt. `verify --fix-case` weigert sich, solange der
Sperrvermerk `.~lock.<name>#` daneben liegt — das geöffnete Tabellenprogramm
verwürfe die Änderung sonst beim nächsten Speichern.

## Exit-Codes

`0` erfolgreich · `1` blockiert (Bedienfehler, fehlende Voraussetzung) ·
`2` durchgelaufen, aber mit Befunden zu einzelnen Units.

`preflight` gibt 2, wenn die CLI die Denktiefe nicht bestätigt. `merge` gibt 2,
wenn eine Unit in bloß einem oder in keinem der beiden Läufe steht; Dissens
allein ist das Ergebnis des Vergleichs und kein Befund.

Jeder Befund trägt einen `GS_`-Code und nennt Was, Warum, Abhilfe und die
auslösende Codestelle.

| Code | Bedeutung | Abhilfe |
|---|---|---|
| `GS_WEBSEARCH_OFF` | Antwort ohne Quelle, obwohl der Zugang offen stand | Log der Unit auf `web_search` prüfen; fehlt es dort auch, greift der Schalter nicht und der Satz ist zu wiederholen |
| `GS_INDUSTRY_CASE` | `industry_id` weicht nur in der Schreibweise ab | `verify --fix-case`; Ursache ist meist die Excel-Autokorrektur |
| `GS_RESUME_MISMATCH` | vorhandene Antwort entstand unter anderen Parametern | Antwort prüfen, mit `run --unit-id … --force` neu erzeugen |
| `GS_UNIT_MISSING` | eine Unit der Stichprobe fehlt | Unit nachlaufen lassen oder Zeile in der Mappe ergänzen |
| `GS_XLSX_OVERWRITE` | Zieldatei vorhanden | `--force`, das sichert die alte Mappe |

## Test ohne Kosten

`GOLDSET_LABEL_FAKE_DIR` ersetzt beide CLIs durch vorbereitete Antwortdateien
`<unit_id>.json` — kein Netz, kein Kontingent:

```bash
GOLDSET_LABEL_FAKE_DIR=/pfad/zu/antworten \
  venv/bin/python committee_matcher/goldset_label.py run --engine claude
```
