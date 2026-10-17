# Implementierungsplan: Mapping-Prompt v3 / Recherche-Sentinel

Stand: 2026-09-05  
Status: umgesetzt und unabhaengig reviewt

## Ziel und Abgrenzung

Der Committee-Matcher akzeptiert `Recherche` als besonderen Wert von
`evidence_excerpt`, setzt dafuer die Regeln aus `mapping-system-v3` und
`repair-system-v2` in API-Mapper und Submapper durch und weist solche
Zuordnungen in Review und Evaluation getrennt aus.

Das persistierte Schema `mapping-output-v1` bleibt unveraendert: Der Sentinel
ist ein zulaessiger Wert des vorhandenen Textfelds, keine neue Spalte. Eine
Schemaversionsaenderung ist daher nicht vorgesehen.

## Festgelegte technische Entscheidungen

| Thema | Entscheidung | Begruendung |
|---|---|---|
| Sentinel-Vergleich | `comparable_text(value) == comparable_text(RESEARCH_EVIDENCE)` | Akzeptiert nur den vollstaendigen Sentinel, aber robust gegen Rand-Whitespace und Grossschreibung. |
| Confidence | Sentinel-Zuordnungen duerfen hoechstens `RESEARCH_MAX_CONFIDENCE = 3` tragen. | Entspricht dem Promptvertrag; kein stilles Herabsetzen. |
| Broad Scope | Sobald eine Unit mehr als zwei Zuordnungen hat, darf keine davon den Sentinel tragen. | Rein recherchegestuetzte Zuordnungen nehmen laut Prompt nie an der Ausnahme teil. |
| Evidence-Mindestspanne | Echte Evidence muss mindestens zwei Woerter enthalten; Sentinel ist ausgenommen. | Setzt „ein einzelnes Wort genuegt nie“ um, ohne willkuerliche Drei-Wort-Grenze. |
| Repair-Sperre | Eine im vorherigen Stand textbelegte Zuordnung darf im Repair nicht zu `Recherche` werden; der Uebergang wird als eigener Repair-Verstoss gemeldet. | Die Sperre ist eine Uebergangsregel und kann nicht aus dem reparierten Payload allein validiert werden. |
| Submapper-Repair | Kein neuer Repair-Call. | Der Submapper besitzt derzeit keinen Repair-Pfad; seine Antworten werden gesammelt und im Workbook manuell korrigiert. |
| Review/Artefakte | Bestehendes Feld beibehalten, Recherche-Zeilen im Workbook sichtbar markieren und beim Rueckimport nach denselben Regeln pruefen. | Kennzeichnung ohne Bruch des CSV-/JSON-Schemas. |
| Metriken | Recherche-Anzahl und -Praezision separat ausweisen; bestehende Gesamtmetriken bleiben unveraendert. | Vergleichbarkeit alter Kennzahlen bleibt erhalten. |

## Phase 1 – Gemeinsamer Vertrag und API-Validator

1. In `committee_matcher/models.py` `RESEARCH_EVIDENCE` und
   `RESEARCH_MAX_CONFIDENCE` definieren sowie eine gemeinsame Sentinel-Hilfe
   bereitstellen.
2. Broad-Scope-Modellregel um das Verbot von Recherche-Evidence erweitern.
3. In `committee_matcher/validation.py`:
   - Sentinel in `evidence_supported()` ohne Quellen-Substring akzeptieren,
   - eigene Verletzung fuer Confidence ueber 3 erzeugen,
   - einwoertige echte Evidence mit eigener Verletzung ablehnen.
4. In `committee_matcher/mapping.py` die Schema-Beschreibung aktualisieren.

Gate 1:

- Bestehende Mapping-P6-Tests bleiben gruen.
- Neue Tests decken exakten/normalisierten Sentinel, Confidence 3/4,
  Einwort-Evidence, normale Evidence und Broad Scope ab.

## Phase 2 – Repair-Invariante

1. Vorherigen und reparierten Unitstand je Unit/Industry vergleichen.
2. Den Uebergang von echter Evidence zu `Recherche` als
   `MAP_REPAIR_EVIDENCE_TO_RESEARCH` ablehnen.
3. Die Verletzung in weitere Repair-Versuche einspeisen; kein stilles
   Zurueckkopieren oder Loeschen.
4. Teilrepair und anschliessendes Merge weiterhin vollstaendig validieren.

Gate 2:

- Tests fuer Voll- und Teilrepair beweisen, dass echte Evidence erhalten
  bleibt, bestehende Recherche-Evidence unveraendert bleiben darf und andere
  gueltige Reparaturen weiter funktionieren.

## Phase 3 – Submapper-Paritaet

1. Lokale Submapper-Konstanten durch den gemeinsamen Sentinel-Vertrag
   ergaenzen beziehungsweise daran ausrichten.
2. Schema-Description des Submappers aktualisieren.
3. `evidence_supported()`, Antwortpruefung, Workbook-Rueckpruefung und
   Broad-Scope-Pruefung um Sentinel-, Confidence- und Mindestspanne-Regeln
   erweitern.
4. Verifizieren, dass Webzugriff weiterhin standardmaessig aktiv und mit
   `--web-access-off` abschaltbar ist; keine CLI-Aenderung.

Gate 3:

- Neue Submapper-Tests decken Agentantwort und manuell bearbeitetes Workbook
  ab.
- Bestehende `tests/test_submapper.py` bleiben gruen.

## Phase 4 – Review und Metriken

1. Recherche-Zeilen im Review-Workbook per abgeleiteter Markierung sichtbar
   machen, ohne importrelevante Spalten hinzuzufuegen.
2. Review-Rueckimport weist Sentinel mit Confidence ueber 3 und Sentinel in
   Broad Scope zurueck beziehungsweise meldet den bestehenden bewussten
   Review-Ausnahmefall klar.
3. `UnitOutcome` uebernimmt die Evidence-Herkunft intern; eine neue
   `ResearchMetrics`-Ausgabe zaehlt Recherche-Zuordnungen und misst deren
   Precision getrennt.
4. `VariantMetrics` und Evaluationsausgabe um diesen abgeleiteten Block
   erweitern.

Gate 4:

- Review-P7- und Metrics-P9-Tests pruefen Kennzeichnung, Importregeln,
  Recherche-Anzahl und Recherche-Praezision.
- Alte Gesamtmetriken liefern unveraenderte Werte.

## Phase 5 – Dokumentation und Gesamtpruefung

1. `committee_matcher/SUBMAPPER_ANLEITUNG.md` um Recherche-Regeln,
   Confidence-Deckel und Workbook-Kennzeichnung ergaenzen.
2. Die betroffenen kanonischen Vertrags-/Ablaufabschnitte gemaess
   `organisation/DOKU_INDEX.md` aktualisieren; mindestens
   `organisation/DATA_STRUCTURE.md` pruefen und nur bei geaenderter
   Artefaktsemantik anpassen.
3. Abschluss in `CHANGELOG.md` dokumentieren.
4. Fokussierte Tests, danach den relevanten Committee-Matcher-Testsatz
   ausfuehren.
5. `git diff --check`, `git diff --stat` und `git status --short` pruefen;
   vorhandene fremde Worktree-Aenderungen nicht veraendern.

Gate 5:

- Alle fokussierten Tests gruen.
- Keine unbeabsichtigte Schema- oder Artefaktspaltenaenderung.
- Doku beschreibt API-Mapper und Submapper konsistent.

## Phase 6 – Unabhaengiger Agent-Review

Nach abgeschlossener Implementierung wird auf ausdruecklichen Nutzerwunsch
ein Subagent fuer einen unabhaengigen Code-Review gestartet. Er prueft Diff,
Regelinvarianten, Tests, Rueckwaertskompatibilitaet und Doku, nimmt aber keine
eigenstaendigen Aenderungen vor. Belastbare Befunde werden anschliessend im
Hauptagenten behoben und erneut getestet.

Gate 6:

- Keine offenen Review-Befunde hoher oder mittlerer Schwere.
- Abschlussbericht nennt Tests, Restgrenzen und betroffene Dateien.

## Implementierungsfreigabe

Am 2026-09-05 freigegeben und umgesetzt. Abschlusspruefung: 1283 Tests der
Committee-Matcher-/Submapper-Suite bestanden; unabhaengiger Agent-Review ohne
offene Befunde hoher oder mittlerer Schwere.
