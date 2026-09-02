# Gold-Set-Creator

## Was das Werkzeug tut

Der Committee Matcher ordnet jedem Ausschuss die Branchen zu, die er
reguliert. Um zu wissen, wie gut er das tut, braucht es eine Referenz: 22
Ausschüsse, von Hand geprüft, mit der richtigen Antwort daneben. Das ist das
**Gold-Set**.

Dieses Werkzeug erstellt es. Es lässt einen starken KI-Agenten die 22
Ausschüsse bearbeiten, prüft dessen Antworten gegen feste Regeln und legt sie
dir als Excel-Tabelle zur Abnahme vor. Erst deine Abnahme macht daraus das
Gold-Set — der Agent liefert einen Vorschlag, kein Ergebnis.

Der Matcher selbst wird dabei nicht benutzt. Er ist der Prüfling; wenn er sein
eigenes Prüfmaß erstellte, wäre die Messung wertlos.

## Der ganze Ablauf

```
1.  venv/bin/python committee_matcher/goldset_label.py label

    Der Agent bearbeitet alle 22 Ausschüsse. Am Ende liegt
    data/committee_matcher_v2/goldset/goldset_review.xlsx bereit.
    KOSTET GELD. Dauer: etwa eine Stunde.

2.  xdg-open data/committee_matcher_v2/goldset/goldset_review.xlsx

    Du gehst die Tabelle durch: Zeilen ändern, löschen, ergänzen.
    Danach speichern und schließen.

3.  venv/bin/python committee_matcher/goldset_label.py finish --eval-id eval-2026-09-02

    Deine Fassung wird geprüft und als Gold-Set übernommen.
    Kostet nichts.
```

Mehr ist es nicht. Alles dazwischen erledigt das Werkzeug selbst.

## Schritt 1: `label`

Ein Aufruf, der intern fünf Dinge tut:

| | Was passiert | Warum |
|---|---|---|
| 1 | **Aufgaben bauen** | Jeder Ausschuss bekommt ein eigenes Paket: sein Zuständigkeitstext, die Liste aller 145 Branchen, die Regeln, das Antwortformat. |
| 2 | **Engine prüfen** | Ein Miniaufruf klärt, ob die KI erreichbar ist und den Modellnamen kennt. Schützt davor, dass 22 Läufe an einem Tippfehler scheitern. |
| 3 | **Labeln lassen** | Für jeden Ausschuss ein eigener KI-Lauf, mit Internetrecherche. Hier entstehen die Kosten. |
| 4 | **Antworten prüfen** | Jede Antwort wird gegen die Regeln gehalten: Steht das Zitat wirklich im Text? Sind es nicht zu viele Branchen? Fehlt eine Begründung? |
| 5 | **Tabelle schreiben** | Alles landet in einer Excel-Datei, die du bearbeiten kannst. |

**Der Lauf ist unterbrechbar.** Strg-C, Netzausfall, Kontingent leer — beim
nächsten `label` macht das Werkzeug dort weiter, wo es aufgehört hat. Fertige
Ausschüsse werden übersprungen, nicht neu bezahlt.

**Meldungen sind kein Abbruch.** Findet die Prüfung Regelverstöße, läuft der
Rest trotzdem durch und die Tabelle entsteht. Die Meldungen sind deine
Arbeitsliste für Schritt 2 — sie sagen dir, wo du hinschauen musst.

## Schritt 2: die Tabelle abnehmen

Die Excel-Datei hat fünf Blätter:

- **labels** — hier arbeitest du. Eine Zeile je zugeordnete Branche. Die
  Spalte `industry_id` hat eine Auswahlliste, damit du dich nicht vertippst.
- **jurisdiction** — der volle Zuständigkeitstext jedes Ausschusses. Dagegen
  prüfst du, ob ein Zitat stimmt.
- **taxonomy** — alle 145 Branchen mit Beschreibung.
- **agent_notes** — wo der Agent selbst unsicher war und warum. Lies das; er
  nennt oft genau die Zuordnung, über die du auch stolperst.
- **info** — Herkunft der Labels: Modell, Denktiefe, Zeitstempel, Hashes.
  Nicht anfassen.

Du darfst alles ändern: Zeilen löschen, neue eintragen, Begründungen
umschreiben. Regeln, die auch für deine Änderungen gelten:

- Jede Zuordnung braucht ein **wörtliches Zitat** aus dem Zuständigkeitstext
  (Spalte `evidence_excerpt`), eine **Sicherheit von 1 bis 4** und eine kurze
  **Begründung**.
- **Zwei Zuordnungen** je Ausschuss sind der Normalfall. Bis fünf gehen nur,
  wenn der Ausschuss wirklich mehrere getrennte Bereiche abdeckt — dann
  braucht es dafür eine eigene Begründung und Sicherheit mindestens 3.
- Ein Ausschuss hat **entweder** Zuordnungen **oder** einen Grund in
  `no_mapping_reason`. Nie beides.
- **Jeder** der 22 Ausschüsse braucht eine Zeile. Auch „keine Branche" ist
  eine Aussage und muss dastehen.

Schreib Branchennamen nie von Hand ab — nimm die Auswahlliste. Excel korrigiert
sonst still `packaged-foods` zu `Packaged-foods`, und das ist dann eine andere,
unbekannte Branche. (Das ist wirklich passiert.)

**Datei schließen, bevor du Schritt 3 startest.** Ein offenes Excel würde die
Reparaturen des Werkzeugs beim nächsten Speichern überschreiben.

## Schritt 3: `finish`

Ein Aufruf, der drei Dinge tut:

| | Was passiert | Warum |
|---|---|---|
| 1 | **Zurücklesen und prüfen** | Dieselben Regeln wie in Schritt 1, jetzt auf deine Fassung. Kleinigkeiten wie Groß-/Kleinschreibung und Leerzeichen am Rand repariert es selbst. |
| 2 | **Revision eröffnen** | Ein Ablageort für diese Evaluation wird angelegt und der Stand von Taxonomie und Texten eingefroren. Passiert nur beim ersten Mal. |
| 3 | **Übernehmen** | Deine Tabelle wird zum Gold-Set. |

**Bleibt ein Verstoß stehen, bricht `finish` ab** und übernimmt nichts. Das ist
Absicht: Zitatpflicht, Anzahlgrenze und die Regel für breite Ausschüsse werden
danach nie wieder geprüft. Was hier durchrutscht, verfälscht später jede Zahl.

Jede Meldung nennt die genaue Stelle, meist als Zelladresse:

```
[GS_INDUSTRY_CASE]     labels!B3       'Healthcare-plans' statt 'healthcare-plans'
[GS_SCHEMA_INVALID]    labels!C4       confidence='7' ist keine ganze Zahl zwischen 1 und 4
[GS_DECISION_CONFLICT] labels!Zeile 7  traegt biotechnology und zugleich eine no_mapping-Begruendung
```

Korrigieren, speichern, schließen, `finish` erneut aufrufen. So oft, bis nichts
mehr kommt.

## Optionen

Es gibt drei, mehr braucht der Normalbetrieb nicht.

| Option | Wirkung |
|---|---|
| `--engine claude` | Nur Claude labelt. **Standard**, wenn du nichts angibst. |
| `--engine codex` | Nur Codex labelt. |
| `--compared` | Beide labeln unabhängig, das Werkzeug vergleicht sie und schreibt `disagreements.md` mit den Stellen, an denen sie sich uneinig sind. Kostet doppelt und dauert doppelt. |
| `--force` | Nur bei `label`: alles neu erzeugen statt fortzusetzen. Bezahlt die 22 Läufe erneut. Die alte Tabelle wird vorher gesichert. |

`--engine` und `--compared` müssen bei `label` und `finish` **gleich** sein —
`finish` muss wissen, welchen Stand es prüfen soll:

```bash
venv/bin/python committee_matcher/goldset_label.py label  --compared
venv/bin/python committee_matcher/goldset_label.py finish --compared --eval-id eval-2026-09-02
```

Lohnt sich `--compared`? Wo beide Engines dasselbe sagen, kannst du schnell
durchgehen; wo sie sich unterscheiden, weißt du, wo genau hinzuschauen ist.
Einigkeit heißt aber nicht Richtigkeit — zwei Modelle können denselben Irrtum
teilen.

Modell, Denktiefe und Zeitgrenzen stehen als `GOLDSET_LABEL_*` in
`config/settings.py` und werden nicht über die Kommandozeile gesetzt.

## Wenn etwas schiefgeht

Rückmeldung des Werkzeugs: `0` alles sauber · `1` Abbruch, es fehlt eine
Voraussetzung · `2` durchgelaufen, aber es gibt Meldungen. Jede Meldung trägt
einen `GS_`-Code und sagt, was los ist, warum es zählt und was zu tun ist.

| Meldung | Bedeutet | Zu tun |
|---|---|---|
| `GS_INDUSTRY_CASE` | Excel hat einen Branchennamen umgeschrieben | nichts — `finish` repariert das selbst |
| `GS_EVIDENCE_NOT_VERBATIM` | Das Zitat steht so nicht im Zuständigkeitstext | Zitat aus dem Blatt `jurisdiction` übernehmen |
| `GS_UNIT_MISSING` | Ein Ausschuss hat keine Zeile | Zeile ergänzen: Branche eintragen oder `no_mapping_reason` füllen |
| `GS_WEBSEARCH_OFF` | Der Agent hat keine Quelle genannt | meist harmlos; er hielt den vorliegenden Text für ausreichend |
| `GS_RESUME_MISMATCH` | Eine alte Antwort passt nicht zum aktuellen Lauf | mit `--force` neu erzeugen oder die alte Antwort prüfen |

**Einzelne Schritte von Hand.** Für den Ausnahmefall gibt es die Bausteine
weiterhin einzeln — `prepare`, `preflight`, `run`, `collect`, `merge`,
`to-xlsx`, `verify` — jeweils mit eigenen Optionen. Ein einzelner Ausschuss
noch einmal:

```bash
venv/bin/python committee_matcher/goldset_label.py run --engine claude \
  --unit-id house--energy-and-commerce--health --force
```

`goldset_label.py <kommando> --help` zeigt zu jedem Baustein seine Optionen.

**Probelauf ohne Kosten.** `GOLDSET_LABEL_FAKE_DIR` ersetzt die KI durch
vorbereitete Antwortdateien:

```bash
GOLDSET_LABEL_FAKE_DIR=/pfad/zu/antworten \
  venv/bin/python committee_matcher/goldset_label.py label
```

Hintergrund, Begründungen und die vollständige Meldungstabelle:
`organisation/committee_matcher_goldset_labeling_plan.md`.
