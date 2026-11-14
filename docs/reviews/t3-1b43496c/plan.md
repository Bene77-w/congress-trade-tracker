# Planquelle: Referenzdaten je Kongress und Anbindung an die Pipeline

Diese Datei fasst die Aufträge zusammen, nach denen der Branch `t3/1b43496c` umgesetzt wurde
(Review-Anker `b8f4988`). Die Aufträge stehen wörtlich und in zeitlicher Reihenfolge.
Spätere Aufträge gehen früheren vor.

Gültiger Umfang nach allen Änderungen: Phasen 1 bis 4. Die Phasen 5 und 6 hat der Nutzer
gestrichen (Auftrag 4). Die Grenze „rein additiv“ aus Auftrag 1 gilt seit Auftrag 3 nicht mehr
für `scripts/import_trades.py`; `app/` blieb unverändert zu lassen. Das Gate von Phase 4 wurde
mit Auftrag 4 auf die Kernzahlen gekürzt.

## Auftrag 1: Phasen 1 bis 3

Du baust im Projekt „Congress Trade Tracker“ (Python + Streamlit, analysiert Aktiengeschäfte von US-Kongressmitgliedern) einen neuen, kleinen Referenzdatenbestand: Abgeordnete, Ausschüsse, Mitgliedschaften und die Zuordnung Ausschuss → Industries, jeweils mit stabilen Schlüsseln und Kongress-Bezug. Arbeite selbst, starte keine Subagents. Du arbeitest in einem eigenen Worktree; Ausgangscommit (Review-Anker) ist `b8f4988`.

### Warum

Am 2027-01-03 beginnt der 120. Kongress. Heute hängen alle Zuordnungen stillschweigend am 119. Kongress und an Namen statt IDs:

- `data/mappings/house_member&(sub)committees.csv` und `senate_member&(sub)committees.csv`: Spalten `Name,Party,State,Committees,ExOfficio`, keine ID, kein Zeitbezug, Ausschüsse als geschachtelter String. Stand House Januar 2026, Senate Juni 2026.
- `data/committee_matcher_v2/reviews/map-fullrun-20261016/reviewed_assignments.csv` (522 Zeilen, 225 Units): Ausschuss → Industry, Schlüssel ist ein Namens-Slug (`unit_id` wie `house--agriculture`, dazu `unit_kind` mit `subcommittee`, `committee_parent_core`, `committee_direct`). `reviewed_unit_coverage.csv` daneben listet alle Units.
- Personen werden heute per Fuzzy-Namensabgleich verknüpft (`utils/name_matcher.py`), obwohl die Trade-Quelle eine `BioGuideID` mitliefert.

Sobald der 120. Kongress eingepflegt wird, würde er den Stand des 119. überschreiben. Deshalb wird jetzt der Stand des 119. gesichert und in eine Form gebracht, die mehrere Kongresse nebeneinander trägt. Die Anbindung an die bestehende Pipeline ist ein späterer Schritt und nicht Teil dieses Auftrags.

### Betriebsprofil

Der Bestand wird einmal je Kongresswechsel neu gebaut (alle zwei Jahre) und gelegentlich aufgefrischt, wenn sich Besetzungen ändern. Es schaut eine Person zu. Ein abgebrochener Lauf kostet nichts: Die Quellen sind frei, ein Neulauf dauert Sekunden. Daraus folgt: kein Resume, keine Checkpoints, kein CLI-Framework, keine Auditspur über ein Manifest hinaus. Dieses Projekt hat schon einmal ein Werkzeug für eine Zuordnungstabelle auf 19.000 Zeilen wachsen lassen; das soll sich hier nicht wiederholen.

### Entschiedenes

- Personenschlüssel ist die `BioGuideID`.
- Für den 118. Kongress wird nichts rekonstruiert: Er übernimmt die Daten des 119. (Entscheidung des Nutzers). Das muss in den Daten als Übernahme erkennbar sein und darf nicht wie eine eigene Erhebung aussehen. Mein Vorschlag: eine Tabelle `congresses` (Nummer, Beginn, Ende, „Daten stammen aus Kongress …“) mit 118 → 119, statt alle Zeilen zu duplizieren. Wenn du nach dem ersten lauffähigen Durchstich eine bessere Form siehst, nimm sie und begründe sie in einem Satz.
- Zeiträume vor dem 118. Kongress bekommen keinen Eintrag.
- Quelle für Mitglieder, Ausschüsse und Mitgliedschaften ist das öffentliche Repo `unitedstates/congress-legislators` (gemeinfrei): `committee-membership-current.yaml`, `committees-current.yaml`, `legislators-current.yaml`, für ausgeschiedene Abgeordnete `legislators-historical.yaml`. Jüngster Commit der Mitgliedschaftsdatei war `97261713` (2026-11-04). Immer auf einen festen Commit pinnen und dessen SHA festhalten. Abruf über `gh api` oder die Raw-URL; sende dabei keine Projekt- oder Nutzerdaten mit.

### Phasen und Gates

**Phase 1: Stand des 119. Kongresses sichern**
- Die drei `…-current.yaml`-Dateien am gepinnten Commit roh ablegen, mit Manifest (Quelle, Commit-SHA, Abrufzeit, sha256). `legislators-historical.yaml` nicht roh einchecken (zu groß), nur den SHA festhalten.
- Im Manifest vermerken, dass die Jurisdiktions-Inputs (`data/committee_matcher_inputs/`, bereits mit sha256 in `corpus_manifest.yaml`) und die Mapping-Revision `map-fullrun-20261016` zum 119. Kongress gehören. Nichts davon kopieren.
- Als unabhängige Rohsicherung die amtlichen Quellen einmal frisch ziehen: Senate-XML über das vorhandene `scrapers/senate_committee_xml/fetch_senate_xml.py`, und das aktuelle House-Clerk-PDF der Ausschusslisten (siehe `organisation/tool_specific_md/house_all_pdf_info.md`). Nur ablegen, keine Parser anfassen.
- Gate: Manifest vollständig; Kopfzahlen plausibel (House rund 435 plus Delegierte, Senate 100).

**Phase 2: Normalisierte Tabellen**
- Als CSV, Vorschlag unter `data/reference/`: `congresses`, `members` (Schlüssel `bioguide_id`), `committees` (Schlüssel aus der Ausschuss-ID der Quelle, mit Kammer, Elternausschuss, Name, Kongress), `committee_memberships` (Person, Ausschuss, Kongress, Rolle). Rollen so übernehmen, wie die Quelle sie liefert; die heutige Spalte `ExOfficio` ist fachlich irreführend belegt und wird nicht nachgebaut.
- `members` muss alle Personen abdecken, die in der Trade-Quelle vorkommen. Die Quelldatei liegt nur im Haupt-Worktree und ist nicht getrackt: `/home/bene/Nextcloud/Projekte/bmb_trdprg/congress_tracker/data/raw/congress-trading-all.xlsx` (nur lesen; Spaltennamen siehe `scripts/import_trades.py`, ungefähr Zeile 384 bis 405).
- Gate: Schlüssel eindeutig, alle Fremdschlüssel lösen auf; Anteil der `BioGuideID`s aus der XLSX, die in `members` aufgehen, ausweisen und die fehlenden auflisten; Kopfzahlen gegen die bestehenden Roster-CSVs vergleichen und Abweichungen nennen. Ein einfacher Zahlenvergleich genügt, kein Abgleichswerkzeug.

**Phase 3: Ausschuss → Industries an die neuen Schlüssel hängen**
- Übersetzungstabelle `unit_id` (plus `unit_kind`, wo nötig) → Ausschuss-ID, daraus `committee_industries` mit Kongress-Bezug und Herkunft (`revision_id`, `unit_id`). Die fachlichen Inhalte der Revision bleiben unverändert.
- Gate: Alle 225 Units sind entweder aufgelöst oder mit Grund als nicht auflösbar gelistet; die 522 Zuordnungen kommen vollständig an. Rate bei unklaren Fällen nicht, sondern liste sie.

Danach ist Schluss. Keine Phase 4.

### Umfang und Grenzen

- Ein Build-Skript (Vorschlag `scripts/build_reference_data.py`), deterministisch, mit Kongressnummer und Quell-Commit als Eingabe, sodass der 120. Kongress später derselbe Aufruf mit anderen Werten ist. Richtwert höchstens 300 Zeilen Produktivcode und 200 Zeilen Tests. Wenn du darüber kommst, begründe es im Abschlussbericht.
- Tests mit echten Ausschnitten der Quelldateien als Fixtures, keine erfundenen Mocks für externe Formate.
- Pfade und Quell-Commit gehören in `config/settings.py`.
- Rein additiv: `scripts/import_trades.py`, `utils/name_matcher.py`, `tags/`, `signals/`, `committee_matcher/`, `config/committee_mapping.py`, `config/committee_revision.py` und `app/` nicht ändern. Die bestehenden Roster-CSVs und die Mapping-Revision bleiben, wie sie sind.
- Keine Pipeline starten, keine kostenpflichtigen API-Aufrufe, keine `.env`-Dateien öffnen (auch nicht `.deploy.env`), keine privaten Schlüssel ansehen.
- Python: falls der Worktree kein `venv` hat, `/home/bene/Nextcloud/Projekte/bmb_trdprg/congress_tracker/venv/bin/python` verwenden. Nicht die volle Testsuite laufen lassen, nur deine eigenen Tests.
- Doku knapp nachziehen: neue Datendateien in `organisation/DATA_STRUCTURE.md`, den Aufruf in `organisation/CLI_Bedienungsanleitung.md`, eine Zeile in `CHANGELOG.md`. Keine neuen Plan- oder Konzeptdokumente.
- Git: Commits auf dem Branch deines Worktrees sind erlaubt. Kein Merge nach `main`, kein Push, kein Stash. Commit-Nachrichten enden mit `Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>`.

### Abschlussbericht

Antworte auf Deutsch mit korrekten Umlauten, in dieser Reihenfolge:

1. Was entstanden ist (Dateien, Zeilenzahl Code und Tests), Branch und Commit-SHA.
2. Je Gate das Ergebnis mit Zahlen: Kopfzahlen, Anteil aufgelöster `BioGuideID`s, aufgelöste und nicht aufgelöste Units.
3. Abweichungen von diesem Auftrag und warum.
4. Was dir an den Daten aufgefallen ist und der Nutzer entscheiden muss.

Unterscheide dabei, was du nachgeprüft hast und was du nur annimmst.

## Auftrag 2: Ergänzung zu den Phasen 1 bis 3

Ergänzung aus einer parallel gelaufenen Prüfung des Audits. Vier Punkte betreffen deine Arbeit direkt. Falls die Phasen 1 bis 3 schon committet sind, ziehe die Punkte 1 und 2 als eigenen Korrektur-Commit nach, bevor du mit der Anbindung weitermachst.

1. **Mitgliedschaften aus allen Ständen eines Kongresses, nicht nur aus dem letzten.** `committee-membership-current.yaml` enthält nur, wer gerade im Amt ist. Der jüngste Stand deckt deshalb nur 97,4 % der Trades des 119. Kongresses ab; wer ausgeschieden ist, fehlt. Vereinige alle Commits der Datei innerhalb des Kongresses (für 119: ab 2025-01-03) und halte je Mitgliedschaft fest, in welchem Stand sie zuerst und zuletzt vorkam. Die Zuordnung bleibt vorerst auf Kongress-Ebene; die beiden Datumsangaben sind nur Information. Im Manifest stehen alle verwendeten Commit-SHAs, roh abgelegt wird weiterhin nur der jüngste Stand. Dafür darf das Build-Skript rund 100 Zeilen über den Richtwert gehen.

2. **Schlüssel für Ausschüsse ist `(Kongress, Ausschuss-ID)`, nie die ID allein.** Die IDs der Quelle bezeichnen Plätze, keine feste Bedeutung: Zwischen dem 118. und 119. Kongress wurden 42 von 224 gemeinsamen IDs umbenannt, etwa `HSAG15` von „Forestry“ zu „Forestry and Horticulture“, wobei Horticulture von `HSAG03` herüberkam. Kein Join über Kongressgrenzen allein per ID. Namen kommen aus dem Stand des jeweiligen Kongresses.

3. **Zur Deutung deines Vergleichs mit dem Master:** Dort tragen 18 `BioGuideID`s zwei verschiedene Bundesstaaten (5.719 Zeilen), und die Spalte mischt Kürzel mit ausgeschriebenen Namen. Normalisiere das vor dem Zählen, sonst misst du Schreibweisen statt Fehler.

4. **`app/` setzt gleichbleibende Ausschüsse je Mitglied voraus** (`app/member_detail.py:216`, `app/member_metrics.py:348`). Nach der Anbindung hat ein Mitglied je nach Handelsdatum unterschiedliche oder keine Ausschüsse. Baue `app/` nicht um. Prüfe, was diese beiden Stellen dann anzeigen, und berichte es. Nur wenn eine Stelle dadurch abstürzen würde, ist die kleinste Korrektur erlaubt.

Zur Einordnung, ohne Handlungsbedarf: Die Loader in `config/committee_mapping.py` und `config/committee_revision.py` importieren sechs Module aus `committee_matcher/`, transitiv rund 3.500 Zeilen, und es gibt nur eine globale `COMMITTEE_MAPPING_REVIEW_ID`. Eine Teilrevision gilt dort als Probeartefakt (`config/committee_revision.py`, um Zeile 300). Lass diese Abhängigkeiten stehen; wenn die Wahl der Industries je Kongress daran hängt, nimm den kleinsten Weg und beschreibe im Bericht, was für den 120. Kongress daran noch fehlt.

## Auftrag 3: Folgeauftrag (Phasen 4 bis 6)

Danke, die Phasen 1 bis 3 sind abgenommen: Ich habe Commit `12a89ba` nachgeprüft (Schlüssel eindeutig, Fremdschlüssel lösen auf, 13 Tests grün). Hier der Folgeauftrag, der dich nicht erreicht hatte. Gleicher Worktree und Branch.

Der Nutzer hat entschieden, dass du auch die Anbindung umsetzt, soweit sie heute möglich ist. Damit entfällt die Grenze „rein additiv“ für die unten genannten Dateien. Alle übrigen Grenzen gelten weiter: keine `.env`-Dateien, keine kostenpflichtigen Aufrufe, kein Merge, kein Push, kein Stash, keine Subagents. Jede Phase bekommt einen eigenen Commit, damit sich jede einzeln prüfen und zurücknehmen lässt. Die ursprünglich vorgesehene Stoppbedingung ist erledigt: 342 von 342 `BioGuideID`s gehen auf, ein Namens-Fallback wird also nicht gebaut.

### Phase 4: Ausschüsse nach Handelsdatum zuordnen

Heute verknüpft die Pipeline Abgeordnete per Fuzzy-Namensabgleich mit dem aktuellen Roster (`utils/name_matcher.py`, in `scripts/import_trades.py` ungefähr Zeile 600 bis 620) und überschreibt dabei die korrekten Quellfelder Partei und Bundesstaat. Du hast die Folge selbst gemessen: 6.852 Zeilen mit einem Bundesstaat, der nicht zum Abgeordneten passt. Außerdem werden Trades aus 2017 mit Ausschüssen von heute bewertet.

Ziel: Jeder Trade bekommt die Ausschüsse, die am Transaktionsdatum galten, verknüpft über die `BioGuideID` und die Referenztabellen. Der Kongress des Trades ergibt sich aus dem Transaktionsdatum (bestehende Datums-Fallbacks der Pipeline beibehalten) und `congresses.csv`. Auch die Zuordnung Ausschuss → Industries wird über den Kongress gewählt (`committee_industries.csv`), damit der 120. Kongress später neben dem 119. stehen kann, statt ihn zu ersetzen.

Festlegungen:
- Partei und Bundesstaat kommen aus der Quelle und werden nicht mehr überschrieben.
- Drei Fälle müssen in den Daten unterscheidbar sein, etwa über eine Statusspalte: Ausschüsse gefunden; Kongress abgedeckt, aber keine Mitgliedschaft in den Daten (etwa die 13 Personen des 118. Kongresses sowie Pelosi und Clark); Transaktionsdatum in keinem abgedeckten Kongress (heute alles vor dem 118.).
- Welche Zeiträume abgedeckt sind, entscheidet allein `REFERENCE_CONGRESSES` bzw. `congresses.csv`. Im Code steht dazu keine Sonderregel, damit der Nutzer das später ohne Codeänderung erweitern kann.
- Wähle den kleinsten Eingriff, der die Daten richtig macht. Wenn die nachgelagerten Tags und Signale das bestehende Spaltenformat brauchen, erzeuge es aus den Referenztabellen, statt Tags und Signale umzuschreiben. Kein Umbau der Klasse in `import_trades.py` über die betroffene Stage hinaus.
- Der ersetzte Codepfad wird entfernt, nicht parallel behalten. Module, die dadurch keinen Aufrufer mehr haben, löschst du nicht selbst, sondern listest sie im Bericht.
- Die Loader in `config/committee_mapping.py` und `config/committee_revision.py` hängen an einer globalen `COMMITTEE_MAPPING_REVIEW_ID` und an rund 3.500 Zeilen aus `committee_matcher/`. Lass diese Abhängigkeiten stehen. Wenn die Wahl der Industries je Kongress daran hängt, nimm den kleinsten Weg und beschreibe, was für den 120. Kongress daran noch fehlt.
- `app/` nicht ändern; dein Befund dazu steht bereits.
- Neue Spalten in `organisation/DATA_STRUCTURE.md` nachtragen.

Gate (auf echten Daten, ohne Netz und ohne Vollauf): Lass nur die Zuordnung auf der Quiver-XLSX laufen und vergleiche mit dem bestehenden Master im Haupt-Worktree, nur lesend und mit `usecols`: `/home/bene/Nextcloud/Projekte/bmb_trdprg/congress_tracker/data/processed/trades_master_complete.csv` (rund 645 MB). Berichte:
- Anzahl der Trades, bei denen Bundesstaat oder Partei von der Quelle abweichen (erwartet 0; vorher 6.852),
- Anteil der Trades mit Ausschüssen je Jahr, alt gegen neu, und die Verteilung der drei Statuswerte je Kongress,
- den Fall Ro Khanna 2017 (erwartet: keine Ausschüsse, Status „kein abgedeckter Kongress“),
- drei bis fünf Personen, deren Ausschüsse sich ab 2023 gegenüber dem Master geändert haben, mit alt und neu,
- wie viele Trades ab 2023 dadurch ein Ausschuss-Industry-Paar gewinnen oder verlieren, soweit sich das ohne die Netz-Stages bestimmen lässt.
Passe die betroffenen Tests an und lass am Ende dieser Phase einmal die volle Testsuite laufen.

Richtwert: höchstens 250 Zeilen neuer Produktivcode netto. Wenn es mehr wird, begründe es.

### Phase 5: Kongressnummer bei den Kongressdaten

`scrapers/fetch_congress_data.py` kennt bereits `--congress` (Default 119, Zeile 53 und ungefähr 1212), schreibt aber in feste Dateien unter `data/congress_api/`. Ein Abruf für den 120. Kongress würde die Daten des 119. überschreiben, und die Leser (`tags/vote_timing_tag.py`, `scripts/import_trades.py` um Zeile 1120) kennen nur einen Stand.

Ziel, bewusst klein gehalten: Die Ausgabedateien tragen die Kongressnummer, der Default kommt aus `config/settings.py`, und die Leser verarbeiten alle vorhandenen Kongresse. Nichts abrufen (der Abruf braucht einen API-Schlüssel). Die vorhandenen Dateien des 119. Kongresses liegen nur im Haupt-Worktree und sind nicht getrackt; nenne im Bericht den einen Umbenennungsschritt, der nach einem Merge dort nötig ist. Keine Neustrukturierung der Datei: Ob das Vote-Timing-Signal überhaupt bleibt, ist noch nicht entschieden.

Gate: Tests der betroffenen Leser grün; ein Trockenlauf oder `--help` mit `--congress 120` zeigt die neuen Pfade, ohne etwas zu überschreiben.

Richtwert: höchstens 80 Zeilen netto.

### Phase 6: Ablauf für den 120. Kongress festhalten

Die Besetzungen des 120. Kongresses stehen erst im Januar/Februar 2027 fest, hier ist also nichts zu bauen. Erweitere deinen Abschnitt in `organisation/CLI_Bedienungsanleitung.md` zu „Neuen Kongress einpflegen“: die Aufrufe in der richtigen Reihenfolge und was von Hand zu tun ist (Eintrag in `REFERENCE_CONGRESSES`, Quell-Commit pinnen, Build laufen lassen, Gates ansehen, Overrides prüfen, Mapping-Revision für geänderte Ausschüsse, amtliche Rohsicherung). Höchstens 40 Zeilen, keine eigene Plandatei.

### Nicht Teil dieses Auftrags

Kein Vollauf der Pipeline und kein Neuaufbau des Masters. Das Mapping Ausschuss → Industries inhaltlich nicht ändern. Das Senate-XML nicht erneut versuchen; das kläre ich mit dem Nutzer.

### Abschlussbericht

Wie zuvor, je Phase: Commit-SHA, Gate-Ergebnis mit Zahlen, Zeilenbilanz (hinzugefügt und entfernt), die Liste der Module ohne Aufrufer und alles, was nach einem Merge im Haupt-Worktree von Hand zu tun ist. Wenn das Netz wieder ausfällt, committe vorher den jeweils fertigen Stand.

## Auftrag 4: Kürzung durch den Nutzer

Änderung vom Nutzer: Nach Phase 4 ist Schluss. Die Phasen 5 (Kongressnummer bei den Kongressdaten) und 6 (Abschnitt „Neuen Kongress einpflegen“) entfallen; fang sie nicht an. Falls du mit einer davon schon begonnen hast, verwirf die uncommitteten Änderungen daran.

Der Grund ist der Tokenverbrauch: Die Aufgabe ist einfacher, als der bisherige Aufwand vermuten lässt. Bring Phase 4 deshalb auf dem kürzesten Weg zu Ende:
- Keine weitere Erkundung über die betroffene Stage und ihre direkten Leser hinaus.
- Das Gate auf die Kernzahlen beschränken: Abweichungen bei Bundesstaat und Partei, Verteilung der drei Statuswerte je Kongress, der Fall Khanna 2017. Die Beispiele einzelner Personen und die Zählung gewonnener oder verlorener Ausschuss-Industry-Paare entfallen.
- Die volle Testsuite genau einmal am Ende; Ausgabe nur zusammengefasst lesen.
- Committen, dann ein kurzer Abschlussbericht: Commit-SHA, die Gate-Zahlen, Zeilenbilanz, Module ohne Aufrufer, was nach einem Merge von Hand zu tun ist. Höchstens etwa 250 Wörter.
