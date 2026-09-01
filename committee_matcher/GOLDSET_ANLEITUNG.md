# Gold-Set-Creator — Bedienungsanleitung

`committee_matcher/goldset_label.py` lässt die 22 Units der Entwicklungs-
Stichprobe von einem headless Agenten labeln, wahlweise über Claude Code oder
Codex. Ergebnis ist das Entwicklungs-Gold-Set, gegen das P9 den Matcher misst.

Der Produktivpfad `run plan|execute` bleibt dabei ungenutzt — er ist der
Prüfling. Die Labels sind ein Vorschlag; das Gold-Set entsteht erst durch die
Abnahme in der Reviewmappe.

Hintergrund und Begründungen: `organisation/committee_matcher_goldset_labeling_plan.md`.

## Ablauf

```bash
P="venv/bin/python committee_matcher/goldset_label.py"

$P prepare                                        # Aufgabenpakete bauen
$P preflight --engine claude                      # Engine und Modell prüfen
$P run      --engine claude                       # KOSTENPFLICHTIG
$P collect  --engine claude                       # prüfen und sammeln
# dieselben drei Schritte mit --engine codex
$P merge    --base claude --against codex         # vergleichen
$P to-xlsx  --source merged                       # Reviewmappe erzeugen
# Mappe in Excel/LibreOffice abnehmen
$P verify   --source merged --fix-case            # zurücklesen und prüfen
```

## Kommandos

| Kommando | Wirkung | Bezahlt |
|---|---|---|
| `prepare` | Aufgabenpaket je Unit nach `data/committee_matcher_v2/goldset/labeling/tasks/` | nein |
| `preflight` | Ein-Token-Lauf; prüft Aufrufweg, Anmeldung, Modellname, Denktiefe | minimal |
| `run` | je Unit ein headless Lauf; Rohantwort und Log nach `labeling/runs/<engine>/` | **ja** |
| `collect` | Rohantworten gegen Vertrag, Taxonomie und Belegtext prüfen, als CSV sammeln | nein |
| `merge` | zwei Engineläufe vergleichen, `disagreements.md` und die `merged`-Tabellen schreiben | nein |
| `to-xlsx` | neue Reviewmappe aus den Zwischentabellen erzeugen | nein |
| `verify` | bearbeitete Reviewmappe zurücklesen und erneut prüfen | nein |

## Optionen

**Engine, Modell, Denktiefe** (`preflight`, `run`)

`--engine claude|codex` · `--model <name>` · `--effort <stufe>`. Modell und
Denktiefe gehen roh an die CLI durch, damit künftige Modelle ohne Codeänderung
laufen; ein Tippfehler fällt deshalb erst beim Aufruf auf — dagegen steht
`preflight`. Defaults als `GOLDSET_LABEL_*` in `config/settings.py`.

**Webzugang** (`preflight`, `run`)

WebSearch und WebFetch laufen ohne Zutun mit, weil Recherche zum Auftrag
gehört. `--web-access-off` schaltet sie ab. Der Zustand gehört zur Provenienz:
er steht in der Antwortdatei und in der Spalte `web_access` der `info`-Tabelle.

**Zeit** (`run`)

`--timeout` deckelt die einzelne Unit (Default 1800 s), `--total-timeout` den
ganzen Satz (Default 43200 s). Ist der Gesamtdeckel erreicht, endet der Lauf
geordnet; ein erneuter Aufruf setzt fort.

**Auswahl und Wiederholung** (`run`)

`--unit-id <id>` mehrfach für einzelne Units. Ohne Angabe die ganze Stichprobe.
Vorhandene Antworten werden übersprungen — aber nur, wenn Modell, Denktiefe,
Webzugang und Bindungshashes passen; sonst `GS_RESUME_MISMATCH`. `--force`
erzeugt neu.

**Quelle** (`to-xlsx`, `verify`)

`--source merged` (Default) oder ein Enginename.

**Mappe** (`to-xlsx`, `verify`)

`to-xlsx` blockt auf eine vorhandene Zieldatei, da sie deine Abnahmearbeit
trägt; `--force` sichert sie vorher als `<name>.<zeitstempel>.bak.xlsx`.
`verify --fix-case` repariert Schreibweise und Randleerraum, sichert ebenfalls
vorher und weigert sich, solange der Sperrvermerk `.~lock.<name>#` daneben
liegt — das geöffnete Programm verwürfe die Änderung sonst beim Speichern.
`--file <pfad>` prüft eine Mappe an anderer Stelle.

## Was `merge` vergleicht

Je Unit die Entscheidung (`mapped` gegen `no_mapping`) und, wenn beide
`mapped` sagen, die Menge der Industry-IDs. `disagreements.md` nennt beide
Mengen und getrennt, welche ID nur die eine und welche nur die andere Engine
vergeben hat. Begründungstexte und `confidence` werden **nicht** verglichen —
sie sind nie wörtlich gleich, ein Textabgleich meldete Dissens, wo keiner ist.
Beidseitig unbeantwortete Units zählen zu keiner Seite.

Übereinstimmung heißt nicht Richtigkeit: zwei Modelle können denselben Irrtum
teilen. Die Richtigkeitsprüfung sind der Vertrag (`collect`, `verify`) und
deine Abnahme.

## Exit-Codes

`0` erfolgreich · `1` blockiert (Bedienfehler, fehlende Voraussetzung) ·
`2` durchgelaufen, aber mit Befunden zu einzelnen Units.

`preflight` gibt 2, wenn die CLI die Denktiefe nicht bestätigt (Codex tut das
nie). `merge` gibt 2, wenn eine Unit in bloß einem oder in keinem der beiden
Läufe steht; Dissens allein ist das Ergebnis des Vergleichs und kein Befund.

Jeder Befund trägt einen `GS_`-Code und nennt Was, Warum, Abhilfe und die
auslösende Codestelle. Die vollständige Codetabelle steht in
`organisation/committee_matcher_goldset_labeling_plan.md`.

## Häufige Befunde

| Code | Bedeutung | Abhilfe |
|---|---|---|
| `GS_WEBSEARCH_OFF` | Antwort ohne Quelle, obwohl der Zugang offen stand | Log der Unit auf `web_search` prüfen; fehlt es dort auch, greift der Schalter nicht und der Satz ist zu wiederholen |
| `GS_INDUSTRY_CASE` | `industry_id` weicht nur in der Schreibweise ab | `verify --fix-case`; Ursache ist meist die Excel-Autokorrektur |
| `GS_RESUME_MISMATCH` | vorhandene Antwort entstand unter anderen Parametern | Antwort prüfen, mit `run --unit-id … --force` neu erzeugen |
| `GS_UNIT_MISSING` | eine Unit der Stichprobe fehlt | fehlende Unit nachlaufen lassen oder Zeile in der Mappe ergänzen |
| `GS_XLSX_OVERWRITE` | Zieldatei vorhanden | `--force`, das sichert die alte Mappe |

## Test ohne Kosten

`GOLDSET_LABEL_FAKE_DIR=<verzeichnis>` ersetzt beide CLIs durch vorbereitete
Antwortdateien `<unit_id>.json` — kein Netz, kein Kontingent.
