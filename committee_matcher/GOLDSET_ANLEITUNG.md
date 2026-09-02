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

Dasselbe Werkzeug kann mit `--fullrun` auch die Arbeit selbst erledigen statt
nur den Maßstab dafür: dann bearbeitet der Agent **alle 225 Ausschüsse**, und
deine abgenommene Tabelle wird zum Mapping, mit dem das Programm rechnet. Siehe
[Der Fullrun](#der-fullrun-alle-225-ausschüsse).

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

## Der Fullrun: alle 225 Ausschüsse

Der normale Ablauf erzeugt den Maßstab — 22 Ausschüsse als Referenz. Mit
`--fullrun` läuft derselbe Ablauf über den **ganzen Korpus**, und was du
abnimmst, wird nicht zum Gold-Set, sondern zum Mapping, das die Pipeline
benutzt.

```
1.  venv/bin/python committee_matcher/goldset_label.py label --fullrun

    Der Agent bearbeitet alle 225 Ausschüsse. Am Ende liegt
    data/committee_matcher_v2/fullrun/fullrun_review.xlsx bereit.
    KOSTET GELD. Dauer: mehrere Stunden bis Tage.

2.  xdg-open data/committee_matcher_v2/fullrun/fullrun_review.xlsx

    Wie beim Gold-Set: durchgehen, ändern, speichern, schließen.

3.  venv/bin/python committee_matcher/goldset_label.py finish --fullrun \
        --review-id map-2026-09-02

    Deine Fassung wird geprüft und als Mapping übernommen. Ergebnis:
    data/committee_matcher_v2/reviews/map-2026-09-02/reviewed_assignments.csv
```

Drei Dinge sind anders als beim Gold-Set:

**Eigene Ablage.** Alles liegt unter `data/committee_matcher_v2/fullrun/`. Der
Fullrun fasst keine Datei des Gold-Sets an — sonst wäre die Referenz
überschrieben, an der die Pipeline gemessen wird.

**Kein Gesamtdeckel.** Der Zeitdeckel von zwölf Stunden für den ganzen Satz
entfällt; er würde einen Mehrtageslauf planmäßig abschneiden. Der Deckel je
Ausschuss bleibt — er ist die Notbremse gegen einen hängenden Prozess.

**Das Kontingent wird ausgewartet.** Meldet Claude oder Codex ein erreichtes
Ratenlimit, bricht der Lauf nicht ab. Er wartet zehn Minuten und fragt erneut,
so lange, bis wieder Antworten kommen. Am Ende sagt er dir, wie lange er
insgesamt gewartet hat:

```
    Ratenlimit erreicht (Versuch 3); neue Anfrage in 10 min. Bisher 20 min gewartet.
    ...
Fertig: 225 gelabelt, 0 uebersprungen, 0 fehlgeschlagen, 90 min auf das Abo gewartet
```

Willst du früher aufhören: Strg-C. Der nächste `label --fullrun` setzt bei den
offenen Ausschüssen fort und bezahlt die fertigen nicht erneut.

**Ziel des `finish`.** `--eval-id` und `--review-id` gehören zu je einer
Betriebsart und lassen sich nicht vertauschen: das Gold-Set kommt in eine
Evaluationsrevision, das Mapping in eine Reviewrevision. Der falsche Parameter
ist ein Bedienfehler und wird gemeldet, statt still das Falsche zu tun.

In der übernommenen Tabelle steht je Zuordnung, woher sie stammt: `model` für
eine Zeile, die der Agent so geliefert hat, `manual`, sobald du an Wert,
Zitat oder Begründung etwas geändert hast. Als Herkunft steht `claude-cli`
beziehungsweise `codex-cli` — die Abrechnung läuft über das Abo, deshalb gibt
es weder Anbieter noch Generation-ID.

## Optionen

| Option | Gilt für | Wirkung |
|---|---|---|
| `--engine claude` | `label`, `finish` | Nur Claude labelt. **Standard**, wenn du nichts angibst. |
| `--engine codex` | `label`, `finish` | Nur Codex labelt. |
| `--compared` | `label`, `finish` | Beide labeln unabhängig, das Werkzeug vergleicht sie und schreibt `disagreements.md` mit den Stellen, an denen sie sich uneinig sind. Kostet doppelt und dauert doppelt. |
| `--fullrun` | alle Kommandos | Alle 225 Ausschüsse statt der 22 der Stichprobe, eigene Ablage, kein Gesamtdeckel, Ratenlimit wird ausgewartet. |
| `--model NAME` | `label` | Welches Modell genau. Ohne Angabe der Wert aus `config/settings.py`. |
| `--effort STUFE` | `label` | Wie gründlich es denkt. Ohne Angabe der Wert aus `config/settings.py`. |
| `--force` | `label` | Alles neu erzeugen statt fortzusetzen. Bezahlt alle Läufe erneut. Die alte Tabelle wird vorher gesichert. |
| `--eval-id NAME` | `finish` | Name der Evaluationsrevision, in die das Gold-Set kommt. Ohne `--fullrun` Pflichtangabe. |
| `--review-id NAME` | `finish --fullrun` | Name der Reviewrevision, in die das Mapping kommt. Mit `--fullrun` Pflichtangabe. |

### Vollständiges Beispiel

Beide Engines, jeweils mit ausdrücklich gewähltem Modell und höchster
Denktiefe:

```bash
venv/bin/python committee_matcher/goldset_label.py label \
  --compared \
  --model claude-opus-5 \
  --effort xhigh

xdg-open data/committee_matcher_v2/goldset/goldset_review.xlsx

venv/bin/python committee_matcher/goldset_label.py finish \
  --compared \
  --eval-id eval-2026-09-02
```

Nur Codex, ein bestimmtes Modell, normale Denktiefe:

```bash
venv/bin/python committee_matcher/goldset_label.py label \
  --engine codex \
  --model gpt-5.6-sol \
  --effort high

venv/bin/python committee_matcher/goldset_label.py finish \
  --engine codex \
  --eval-id eval-2026-09-02
```

Der ganze Korpus, nur mit Claude, höchste Denktiefe:

```bash
venv/bin/python committee_matcher/goldset_label.py label \
  --fullrun \
  --engine claude \
  --model claude-opus-5 \
  --effort xhigh

xdg-open data/committee_matcher_v2/fullrun/fullrun_review.xlsx

venv/bin/python committee_matcher/goldset_label.py finish \
  --fullrun \
  --engine claude \
  --review-id map-2026-09-02
```

`--engine` beziehungsweise `--compared` müssen bei `label` und `finish`
**gleich** sein — `finish` muss wissen, welchen Stand es prüfen soll. Dasselbe
gilt für `--fullrun`: ohne das Flag suchte `finish` die Gold-Set-Tabelle.
`--model` und `--effort` braucht `finish` nicht; dort wird nicht mehr gelabelt.

### Welche Werte gibt es

**Claude.** Modell als Kurzname für die jeweils neueste Fassung — `opus`,
`sonnet`, `fable` — oder als vollständiger Name wie `claude-opus-5`. Denktiefe:
`low`, `medium`, `high`, `xhigh`, `max`. Lehnt die CLI eine Stufe ab, meldet
das Werkzeug es und nennt die gültigen.

**Codex.** Modell als vollständiger Name, etwa `gpt-5.6-sol`. Die Denktiefe
geht als `model_reasoning_effort` durch. Achtung: **Codex bestätigt die Stufe
nicht.** Es nimmt jeden Wert kommentarlos an, auch einen Tippfehler, und
rechnet dann mit seiner Standardstufe. Das Werkzeug meldet das als
`GS_EFFORT_UNCONFIRMED` und vermerkt die Stufe in der Herkunft als
„unbestaetigt".

Beide Namen werden unverändert an die jeweilige CLI weitergereicht. Es gibt
bewusst keine Liste erlaubter Werte im Werkzeug — sonst müsste jedes neue
Modell erst eingetragen werden. Der Preis dafür: ein Tippfehler fällt erst beim
Aufruf auf. Genau dagegen steht die Engineprüfung am Anfang von `label`, die
einen Fehler nach Sekunden meldet statt nach 22 Läufen.

Lohnt sich `--compared`? Wo beide Engines dasselbe sagen, kannst du schnell
durchgehen; wo sie sich unterscheiden, weißt du, wo genau hinzuschauen ist.
Einigkeit heißt aber nicht Richtigkeit — zwei Modelle können denselben Irrtum
teilen.

Zeitgrenzen und der Webzugang stehen als `GOLDSET_LABEL_*` in
`config/settings.py`.

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
| `GS_RATE_LIMITED` | Das Kontingent des Abos ist erschöpft | im Fullrun nichts — er wartet selbst; sonst später fortsetzen |

**Einzelne Schritte von Hand.** Für den Ausnahmefall gibt es die Bausteine
weiterhin einzeln — `prepare`, `preflight`, `run`, `collect`, `merge`,
`to-xlsx`, `verify` — jeweils mit eigenen Optionen. Ein einzelner Ausschuss
noch einmal:

```bash
venv/bin/python committee_matcher/goldset_label.py run --engine claude \
  --unit-id house--energy-and-commerce--health --force
```

Jeder Baustein nimmt `--fullrun` entgegen und arbeitet dann in der
Fullrun-Ablage.

`goldset_label.py <kommando> --help` zeigt zu jedem Baustein seine Optionen.

**Probelauf ohne Kosten.** `GOLDSET_LABEL_FAKE_DIR` ersetzt die KI durch
vorbereitete Antwortdateien:

```bash
GOLDSET_LABEL_FAKE_DIR=/pfad/zu/antworten \
  venv/bin/python committee_matcher/goldset_label.py label
```

Hintergrund, Begründungen und die vollständige Meldungstabelle:
`organisation/committee_matcher_goldset_labeling_plan.md`.
