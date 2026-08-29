# Committee Matcher V2 — P9 Entwicklungs-Gold-Set und Evaluation

Stand: 2026-08-28
Scope: Implementierung von P9 des Hauptplans
(`organisation/committee_matcher_v2_implementierungsplan.md`, Abschnitt P9)
Labelherkunft: `organisation/committee_matcher_goldset_labeling_plan.md`

## Ziel

`evaluate` misst den Prüfling (Produktivpfad `run plan|execute`) gegen das
Entwicklungs-Gold-Set. Primärmetrik ist Precision beziehungsweise FDR; das
Entwicklungsset qualifiziert keinen Full-Run.

## Aufteilung nach Labelabhängigkeit

Die Labels entstehen parallel (Bene, agentengestützt). Drei der vier Phasen
brauchen sie nicht und laufen vorab.

| Phase | Braucht Labels | Netzwerk | Kosten |
|---|---|---|---|
| P9a Evaluationsrevision + `evaluate export` | nein | yfinance, einmalig | keine |
| P9b Gold-Set-Import + Bindungsprüfung | nein (Fixtures) | nein | keine |
| P9c Metrikkern | nein (Fixtures) | nein | keine |
| P9d Evaluationslauf + Bericht | **ja** | OpenRouter | kostenpflichtig |

## Festlegungen

1. **CLI-Aufteilung.** Die registrierte Einzelaktion `evaluate goldset` wird zu
   `evaluate export|import|run`. Grund: P9 besteht aus drei getrennten
   Vorgängen mit eigenem Ausgang — der Hauptplan nennt `goldset-export`
   selbst als eigenen Schritt, und die Familien `run` und `review` sind bereits
   so geschnitten. `--eval-id` ist bei allen dreien Pflicht.
2. **Labelformat.** Long-Format aus dem Labelingplan: Blatt `labels` mit
   `unit_id, industry_id, confidence, evidence_excerpt, rationale_short,
   no_mapping_reason`. Leere `industry_id` plus gefüllter `no_mapping_reason`
   heißt `no_mapping`. Der Import liest XLSX und CSV mit denselben Spalten;
   CSV ist das maschinelle Format der Labelwerkzeuge.
3. **Metrikebene.** Gerechnet wird über Unit–Industry-Paare, nicht über Units:
   eine Unit mit zwei richtigen und einer falschen Zuordnung ist kein
   Alles-oder-nichts-Fall.
4. **Revisionen.** Evaluationsrevisionen liegen unter
   `data/committee_matcher_v2/evaluations/<eval_id>/`, neben den Run- und
   Reviewrevisionen. Keine Revision wird überschrieben.

## Phase P9a — Evaluationsrevision und `evaluate export`

Umfang: neues Modul `committee_matcher/evaluation.py` plus CLI-Aktion.

- Revisionsverzeichnis, Pfadwache, Nichtüberschreibbarkeit;
- genau eine frische yfinance-Abfrage als **Gegenprobe** zur freigegebenen
  Baseline. Eingefroren wird der freigegebene Stand, denn gegen ihn läuft auch
  der Prüflauf. Jede Abweichung des frischen Abrufs blockiert mit
  `awaiting_taxonomy_approval` (Exit 5) — auch die nur warnende
  Beschreibungsdrift, da der Beschreibungstext im Prompt steht;
- eingefroren werden: `snapshot_id` + `taxonomy_content_hash`, Stichprobe
  (Unit-IDs aus `data/committee_matcher_v2/goldset/sample_units.json`),
  `mapping_input_hash` je Unit, Prompt- und Schemahashes, `cache_key_version`,
  Variantenmatrix, Gesamtbudget;
- Ausgang `awaiting_development_goldset`, Exit 0 als erwartete Human-Übergabe.
  Der Zustand steht als Feld im Exportmanifest, nicht in einer zweiten Datei:
  die Revision entsteht so in einem einzigen atomaren Schreibvorgang. Den
  Wechsel nach `pending` belegt später die Existenz der Labeldatei.

Bindungsvertrag: `evaluation.BOUND_FIELDS` nennt die Felder, in denen ein
Prüflauf mit der Revision übereinstimmen muss. `batching` wird ohne
`batch_size` verglichen, da die Batchgröße die Variante ist; `selection` und
`unit_count` stehen nicht darin, da der Prüflauf die Unitmenge in Teilläufen
abarbeiten darf.

Abnahme: Export zweimal unter derselben `eval_id` überschreibt nichts;
geänderte Stichprobe oder Taxonomie verlangt eine neue Revision; das Manifest
trägt weder Prompttext noch Zugangsdaten; beide Batchgrößen der Matrix binden
konfliktfrei gegen dieselbe Revision.

## Phase P9b — Gold-Set-Import und Bindungsprüfung

- Labels lesen (XLSX/CSV), Zeilen zu Units verdichten;
- strikt abgelehnt wird nur, was das Ergebnis unbrauchbar macht: unbekannte
  Unit, fehlende Unit der Stichprobe, unbekannte Industry, doppelte Industry je
  Unit, unlesbare Confidence, Formelzelle, Unit mit Zuordnung **und**
  `no_mapping_reason`, `no_mapping` ohne Grund, stale Bindung gegen das
  Exportmanifest. Exit 7, es wird nichts geschrieben;
- angenommen entsteht `goldset.json` in der Revision samt Labelprovenienz
  (Quellpfad, SHA-256 der Labeldatei, Zeilenzahl);
- der Zustand wechselt von `awaiting_development_goldset` zu `pending`. Er
  wird nicht gespeichert, sondern aus den Dateien der Revision abgeleitet
  (`evaluation.revision_state`): Exportmanifest eröffnet, `goldset.json` führt
  weiter.

Die Labeldatei liest der Import als XLSX (Blatt `labels`) oder CSV; Blatt und
Spalten stehen als Literale in `evaluation.py` und werden bewusst nicht aus
`review.py` importiert — die Übereinstimmung mit dem Reviewvertrag ist
gewollt, die Kopplung an den Prüfling nicht.

Abnahme: handgebaute Fixtures je Ablehnungsgrund; ein abgelehnter Import
hinterlässt keine Datei; ein zweiter Import unter derselben Revision wird
abgelehnt.

## Phase P9c — Metrikkern

Reine Funktionen über (Goldlabels, Laufergebnisse); keine Datei, kein Netz.

| Metrik | Definition |
|---|---|
| Precision / FDR | über Unit–Industry-Paare; FDR = 1 − Precision |
| Recall / F1 | dieselbe Paarmenge |
| Decision-Accuracy | Anteil Units mit gleicher Entscheidung `mapped`/`no_mapping`, beide Fehlrichtungen getrennt ausgewiesen |
| Stabilität | über die Wiederholungen einer Variante: Anteil Units mit identischer Industrymenge, dazu mittlerer Jaccard |
| Confidence | Verteilung je Stufe und Precision je Stufe |
| Broad-Scope-Fehler | mehr als zwei Zuordnungen ohne gültige Ausnahme, oder Ausnahme mit Confidence unter 3 |
| Kosten / Laufzeit | aus Kostenwache und Laufzustand |

Abnahme: handgerechnete Fixtures für jede Metrik, ein Fall mit vollständigem
`no_mapping`, ein Fall mit leerem Lauf; Division durch null liefert `None`,
nicht 0.0.

## Gate — vor P9d

Freigabe durch Bene. Anlass: erster kostenpflichtiger Mehrfachlauf
(2 Varianten × 3 Wiederholungen × 22 Units). Vorgelegt werden Worst Case gegen
`COMMITTEE_MATCHER_MAX_EVALUATION_COST_USD` (aktuell 40,00 USD) und die
eingefrorene Variantenmatrix.

## Phase P9d — Evaluationslauf und Bericht

Nicht Teil des aktuellen Auftrags. Varianten Batchgröße 1 gegen 3, je drei
Läufe ohne Cache-Reads, bei uneindeutiger Stabilität höchstens fünf nach
erneuter Budgetprüfung; Bericht mit allen Metriken je Variante.
