# Aufgegebener Planteil des Committee Matchers V2

Stand 2026-09-02. Entscheidung von Bene: das produktive Mapping aller 225 Units
läuft über die CLI-Abos (`submapper.py --fullrun` → `fullrun_import.py`).
Der API-Matcher bleibt als eigenständiger Mappingweg erhalten, wird aber nicht
mehr um die Auswertungs- und Qualifikationsstufen ergänzt.

Bezug: `organisation/committee_matcher_v2_implementierungsplan.md` (Phasen P9d,
P10, P11) und `organisation/committee_matcher_fullrun_plan.md` (der Weg, der an
ihre Stelle tritt).

## Was gebaut bleibt

Der vollständige Mappingweg, ohne Lücke:

```
input validate → roster validate → snapshot build → snapshot approve
→ provider check [→ provider smoke] → run plan → run execute
[→ run reconcile] → review export → review import
```

`evaluate export|import` bleiben ebenfalls: sie erzeugen die Gold-Set-Labeldatei
und nehmen sie validiert zurück. Beides braucht der CLI-Weg weiterhin.

## Was aufgegeben ist

| Aufgegeben | Plan | Was es getan hätte | Warum es entfällt |
| --- | --- | --- | --- |
| `evaluate run` | P9d | Variantenmatrix (2 Batchgrößen × 3 Wiederholungen) kostenpflichtig gegen das Gold-Set laufen lassen und Präzision, Recall, Stabilität und Kosten je Variante ausweisen | misst die Güte eines API-Modells, das im Produktivbetrieb nicht mehr mappt |
| `qualify holdout` | P10 | unabhängige zweite Stichprobe als Freigabegate vor dem Full-Run | Gate vor einem Lauf, den es in dieser Form nicht mehr gibt |
| `full-run` | P11 | eigene Kommandofamilie für den qualifizierten, filterlosen Lauf über die Sollmenge | ersetzt durch `run plan|execute --all-units`; die Qualifikationsbedingung entfällt mit P10 |
| `report` | P7 | aufbereiteter Fehler-, Kosten- und Coverage-Bericht einer Revision | Auswertung, kein Mapping. Die Rohdaten liegen ohnehin je Revision: `errors.json`, `cost_report.json`, `unit_coverage.csv` |

Mit gestrichen: `WorkflowState.AWAITING_HOLDOUT` in `status.py`.

## Was an ihre Stelle tritt

**Statt `full-run`:** `--all-units` an `run plan` und `run execute`. Das Flag
schließt jeden Filter aus (`CLI_RUN_TARGET_CONFLICT`) und steht in der
`selection` des Runmanifests, damit `run execute` denselben Umfang ausführt, den
`run plan` bepreist hat. Ohne Zielangabe blockiert der Lauf weiterhin
(`CLI_RUN_TARGET_REQUIRED`) — der teuerste Bedienfehler des Werkzeugs muss
ausdrücklich verlangt werden.

**Statt `evaluate run` und `qualify holdout`:** keine automatisierte Messung.
Die Güte belegt der manuelle Reviewdurchgang über `review export|import`
beziehungsweise die Fullrun-Mappe.

**Statt `report`:** die Artefakte der Revision unter
`data/committee_matcher_v2/runs/<run_id>/` direkt lesen.

## Was liegen bleibt, ohne im Produktivweg zu stehen

`committee_matcher/metrics.py` (370 Zeilen, getestet) rechnet Präzision, Recall,
Entscheidungs- und Stabilitätsmaße über Unit-Industry-Paare. Es hat keinen
Aufrufer mehr, ist aber reine Funktionsbibliothek ohne Datei- und Netzzugriff.
Wer eine Gold-Set-Messung nachrüsten will — auch gegen den CLI-Fullrun —
beginnt dort.

Ungenutzt bleiben ferner die Settings
`COMMITTEE_MATCHER_EVALUATION_BATCH_SIZES`, `_REPETITIONS`,
`_MAX_REPETITIONS` und `COMMITTEE_MATCHER_MAX_EVALUATION_COST_USD`. Sie werden
weiterhin validiert und in die Evaluationsrevision eingefroren, steuern aber
keinen Lauf mehr.

## Wenn es doch gebraucht wird

Die Fundamente stehen: `provider.py`, `mapping.py`, `workflow.py` (Runzustand,
Resume, Kostenwache), `evaluation.py` (Revision, Bindung, Gold-Set-Import) und
`metrics.py`. Ein Nachbau von `evaluate run` ist damit ein Handler, der je
Variante eine Runrevision fährt und `metrics.variant_metrics` darauf anwendet —
kein Neuanfang.
