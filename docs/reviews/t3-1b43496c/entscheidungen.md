# Entscheidungen: Referenzdaten je Kongress (Branch `t3/1b43496c`)

## Implementierungsentscheidungen

| Entscheidung/Annahme | Begründung | Bezug (Spec/Ticket/Datei) |
|---|---|---|
| Der 118. Kongress steht nur als Zeile in `congresses.csv` (`data_from_congress = 119`); es werden keine Zeilen dupliziert. | Vorschlag des Auftrags übernommen; die Übernahme bleibt als solche erkennbar. | Auftrag 1, Entschiedenes; `data/reference/congresses.csv` |
| Mitgliedschaften sind die Vereinigung von 31 Ständen der Quelldatei seit 2025-01-03; roh abgelegt ist nur der jüngste Stand. | Der jüngste Stand allein deckt 97,40 % der Trades des 119. Kongresses ab, die Vereinigung 99,84 %. | Auftrag 2, Punkt 1; `data/reference/raw/119/manifest.json` |
| Zwei Units sind von Hand zugeordnet: `house--ways-and-means--tax-policy` → `HSWM05`, `senate--environment-and-public-works--fisheries-wildlife-and-water` → `SSEV15`. | Die Namen weichen zwischen Mapping-Revision und Quelle ab. Der Nutzer hat `HSWM05` am 2026-11-14 bestätigt. | `data/reference/unit_overrides.csv` |
| Fünf Ausschüsse bleiben ohne Unit und ohne Industries: `HSQJ`, `JCSE`, `SCNC`, `SSCM39`, `SSJU27`. | In der Mapping-Revision gibt es keine passende Unit; es wird nicht geraten. | Auftrag 1, Phase 3 |
| Das frische Senate-XML fehlt in der Rohsicherung. | senate.gov antwortete mit HTTP 403; ein weiterer Versuch wurde nicht unternommen. Offen beim Nutzer. | Auftrag 1, Phase 1; Auftrag 3, „Nicht Teil“ |
| `Member_Committees_Full` behält sein bisheriges Format und wird aus den Referenztabellen erzeugt. | Tags und Signale lesen das Format; der Auftrag verlangt den kleinsten Eingriff. | Auftrag 3, Phase 4; `utils/reference_data.py` |
| Neue Spalten `Member_Congress` und `Member_Committee_Status` mit den Werten `matched`, `no_membership`, `congress_not_covered`. | Der Auftrag verlangt drei unterscheidbare Fälle. | Auftrag 3, Phase 4 |
| `Member_ExOfficio` ist wahr, wenn die Quelle bei einer Mitgliedschaft den Titel „Ex Officio“ führt. | Rollen werden übernommen, wie die Quelle sie liefert; die alte Belegung der Spalte wird nicht nachgebaut. | Auftrag 1, Phase 2; `utils/reference_data.py` |
| `Member_Party` und `Member_State` stehen im Format der Quelle („Democratic“, „California“), nicht mehr als Kürzel. | Der Auftrag verbietet das Überschreiben der Quellfelder; gelesen werden sie nur in `app/`. | Auftrag 3, Phase 4; `scripts/import_trades.py` |
| Industries je Kongress liefert ein eigener `ReferenceMapper` mit der Schnittstelle des bisherigen `CommitteeMapper`; die Loader in `config/` bleiben unberührt. | Kleinster Weg ohne Eingriff in `config/committee_mapping.py` und `committee_matcher/`. | Auftrag 3, Phase 4; `utils/reference_data.py` |
| `utils/name_matcher.py`, die Roster-CSVs und `name_aliases.csv` bleiben im Repo, obwohl die Pipeline sie nicht mehr liest. | Der Auftrag verlangt, Module ohne Aufrufer zu listen und nicht zu löschen. | Auftrag 3, Phase 4 |
| `app/` löst Ausschussnamen weiter über den alten `CommitteeMapper` auf. | `app/` war nicht zu ändern. Gemessen: 202 von 204 Ausschüssen mit Industries liefern dasselbe, die zwei von Hand zugeordneten bleiben in der App leer. | Auftrag 2, Punkt 4; `app/member_metrics.py` |
| Die Phasen 5 und 6 wurden nicht umgesetzt. | Vom Nutzer gestrichen. | Auftrag 4 |

## Reviewbefunde

| Befund und Auswirkung | Schweregrad laut Prüfer | Prüfer, Review, Runde | Bezug (Spec/Ticket/`datei:zeile`) | Umgang | Begründung/Belege |
|---|---|---|---|---|---|
| Die Zuordnung gilt je Kongress: Ein Trade von Anfang 2025 erhält auch Ausschüsse, die die Person erst 2026 bekam. | NIEDRIG | Generalist, Final Review, Runde 1 | Auftrag 2, Punkt 1; Auftrag 3, Phase 4 | akzeptiertes Risiko | Auftrag 2 legt die Kongress-Ebene ausdrücklich fest; `first_seen` und `last_seen` liegen für eine spätere Verfeinerung vor. |
| `ReferenceMapper` liest `data._industries` und `data._committees` direkt. | NIEDRIG | Generalist und Architektur, Final Review, Runde 1 | `utils/reference_data.py:138` | akzeptiertes Risiko | Beide Klassen liegen im selben Modul mit 155 Zeilen; eine zusätzliche Methode brächte nur Umweg. |
| Der Build setzt `senate.MANIFEST` als Modulglobal um und nutzt `senate.requests`. | NIEDRIG | Architektur, Final Review, Runde 1 | `scripts/build_reference_data.py:119` | akzeptiertes Risiko | Die saubere Lösung wäre ein Parameter im Scraper; der lag außerhalb des Auftrags (Parser und Scraper nicht anfassen). Die Zeile ist kommentiert. |
| `ReferenceMapper.revision` schaltet in `CommitteeTag` die Zurechnung des Vollausschusses, ohne dass der Name das sagt. | NIEDRIG | Architektur, Final Review, Runde 1 | `utils/reference_data.py:137`, `tags/committee_tag.py:95` | akzeptiertes Risiko | `tags/` war nicht umzuschreiben; der Zusammenhang steht als Kommentar im Code und ist durch `test_committee_tag_reads_reference_mapper` abgedeckt. |
| In den Trades allein (`Member_Congress = 118`, Status `matched`) ist nicht sichtbar, dass die Ausschüsse aus dem 119. Kongress stammen. | NIEDRIG | Architektur, Final Review, Runde 1 | Auftrag 1, Entschiedenes | akzeptiertes Risiko | Die Übernahme steht in `congresses.csv` (`data_from_congress`), wie der Auftrag es vorsieht. |
| `REFERENCE_CONGRESS_DATA_FROM` (Build) und `congresses.csv` (Laufzeit) sind zwei Orte. | NIEDRIG | Architektur, Final Review, Runde 1 | Settings, `data/reference/congresses.csv` | akzeptiertes Risiko | Der Build erzeugt die Tabelle aus der Einstellung; gepflegt wird nur die Einstellung. |
| `scripts/reset_pipeline.py:66` räumt weiter `.enrichment_checkpoint.pkl` auf. | MITTEL (Teil des Doku-Befunds) | Generalist, Final Review, Runde 1 | `scripts/reset_pipeline.py:66` | akzeptiertes Risiko | Die Zeile entfernt Dateien aus alten Läufen, die im Haupt-Worktree noch liegen können; sie fällt mit dem Aufräumen in der nächsten Phase weg. |
