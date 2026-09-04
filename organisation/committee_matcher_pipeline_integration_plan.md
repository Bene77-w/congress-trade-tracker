# Committee Matcher V2 in die Pipeline integrieren — Vorgehensplan

Stand 2026-09-03. Ersetzt die Legacy-Ausschusszuordnung der Pipeline durch das
Ergebnis des Matchers und entfernt die alte Integration.

## Betriebsprofil

- **Wie oft:** Das Mapping entsteht 1–4× im Jahr (Fullrun oder API-Matcher).
  Die Pipeline läuft dagegen laufend gegen das Ergebnis.
- **Wer schaut zu:** Bene, live am Terminal; jede Phase endet an einem Gate.
- **Was kostet ein Fehler:** Ein falsches Mapping verfälscht Filter 3 und damit
  `trade_score` in allen Trades — deshalb bleibt der alte Stand bis zur
  bestandenen Funktionsprüfung im Archiv abrufbar.

## Zielspezifikation

1. **Taxonomie je Lauf frisch.** Submapper und Fullrun holen Industries und
   Sektoren samt Beschreibung vor dem Lauf neu über denselben yfinance-Weg,
   den der API-Matcher benutzt. Kein Arbeiten gegen einen alten Snapshot. Die
   bestehende Baseline bleibt bis zur Freigabe des neuen Snapshots gültig; sie
   ist die Vergleichsgrundlage des Driftvergleichs, nicht sein Opfer.
2. **Der alte Mapper wird sichtbar abgetrennt.** Alle Legacy-Artefakte wandern
   in ein Archivverzeichnis, sind aus dem laufenden Programm nicht mehr
   erreichbar und lassen sich später in einem Zug löschen. Zusätzlich wird der
   jeweils abgelöste `approved_baseline.json` **bei jeder Freigabe** unter
   seiner `snapshot_id` archiviert — nicht vor dem Build.
3. **Eine Mappingquelle.** Die Pipeline liest das Ergebnis des Matchers.
   `data/mappings/subcommittee_sector_industry_mapping.csv` wird nicht mehr
   gelesen.
4. **`Konfidenz` entfällt.** Die Rolle übernimmt `confidence` (1–4) aus dem
   Matcher. Auch `Sector_Rank` entfällt: seine einzige Aufgabe (Dublettenwahl)
   übernimmt `confidence`.
5. **`confidence` wirkt auf den Score.** Der Committee-Beitrag lautet
   `Basisbetrag × Faktor(confidence)`, je Stufe in `config/settings.py`
   einstellbar. Bei mehreren **Voll-Treffern** zählt die höchste Konfidenz
   (MAX wie beim Ex-Officio-Bonus); Halb-Treffer bleiben außen vor, weil sie
   `committee_full` nicht auslösen.
6. **Altintegration entfernt**, danach Funktionsprüfung an echten Daten.

## Codeanalyse: der Ist-Zustand

### Kette heute (Legacy)

| Ort | Aufgabe |
| --- | --- |
| `data/mappings/subcommittee_sector_industry_mapping.csv` | 861 Zeilen; `Chamber, Committee, Subcommittee, Sector, Industry, Sector_Rank, Konfidenz` |
| `config/committee_mapping.py:73` `_load_mapping` | prüft die 7 Pflichtspalten, baut zwei Lookups über **normalisierte Namen** |
| `config/committee_mapping.py:200-209` | Dedup je `(sector, industry)`; **einziger Leser von `rank`** |
| `tags/committee_tag.py:253` | derselbe Dedup eine Ebene höher; schneidet gegen `sector_yf`/`industry_yf`, setzt `match_type` (`full`/`sector`) und die Rollen-Keys |
| `scripts/import_trades.py:1038-1076` `_apply_committee_relevance` | ruft `CommitteeTag()`, schreibt `Insider_Sectors`, `Relevant_Sectors`, `ex_officio_role_keys` |
| `signals/committee_signal.py`, `committee_full_signal.py`, `committee_ex_officio_signal.py` | feuern aus `Relevant_Sectors` |
| `scoring/trade_score.py:150,198` | `SCORING_WEIGHTS['committee_full'] = 5` |
| `config/settings.py:434` | `COMMITTEE_MAPPING_FILE` — Dateiname, umschaltbar |
| `scripts/reset_pipeline.py:120` | schützt die Mapping-CSV vor dem Reset |

`import_trades.py` ist **nicht** die Schemastelle: es ruft nur `CommitteeTag()`.
Das Schema sitzt in `committee_mapping.py`, die Auswertung in `committee_tag.py`.

### Kette des Matchers

`committee_matcher/taxonomy_source.py` (einziger Netzwerkort, yfinance-Allowlist
`industry_id, industry_name, sector_id, sector_name, description`)
→ `snapshot build` → `snapshot approve`
→ `data/committee_matcher_v2/taxonomy/approved_baseline.json` (145 Industries,
11 Sektoren)
→ Mapping → `reviews/<id>/reviewed_assignments.csv` (33 Spalten: 32 aus
`artifacts.ASSIGNMENT_COLUMNS` plus `review_id`, u. a.
`chamber`, `committee_name`, `unit_name`, `unit_kind`, `industry_id`,
`industry_name`, `sector_id`, `sector_name`, `confidence` 1–4, `decision`).

`approved_baseline.json` ist der **freigegebene yfinance-Snapshot des Matchers**,
nicht ein Artefakt des alten Trackers. Der alte Tracker hat kein Taxonomie-
Artefakt; sein Pendant ist die Mapping-CSV.

### Befund zur Passung

- **Namen deckungsgleich.** Alle 11 Sektoren und 141 genutzten Industry-Namen
  der Legacy-CSV stehen wortgleich im Snapshot. Keine Übersetzungstabelle nötig.
- **Schlüssel rekonstruierbar.** `chamber`/`committee_name`/`unit_name` decken
  `Chamber`/`Committee`/`Subcommittee`; Committee-Ebene = leeres Subcommittee
  (heute 27 Zeilen) = `unit_kind` `committee_direct|committee_parent_core`.
- **`Konfidenz` ist tot.** Downstream liest sie niemand (geprüft in `tags/`,
  `signals/`, `scoring/`, `app/`).
- **`Sector_Rank` hat genau einen Zweck**, Dublettenwahl an zwei Stellen.
- **Committee-Ebene wird beidseitig anders dargestellt.** Die Legacy-CSV lässt
  `Subcommittee` leer; der Matcher setzt `unit_name = committee_name`. Der
  Lookup-Bau in P2 muss das umsetzen, sonst findet keine Unit der
  Ausschussebene ihren Schlüssel.
- **Abdeckung gemessen** (mit `CommitteeMapper._normalize` auf beiden Seiten,
  Committee-Ebene wie oben abgebildet): Legacy kennt 147 distinkte
  `(Chamber, Committee, Subcommittee)`, der Matcher 225 Units. Nur **3
  Legacy-Ausschüsse** haben keine Entsprechung:

  | Fehlender Schlüssel | Ursache |
  | --- | --- |
  | `joint committee on taxation`, `joint economic committee` | Legacy führt sie unter `Chamber=House`, der Matcher unter `chamber=joint`; Namen sind identisch |
  | `natural resources / water, wildlife and fisheries` | Subcommittee umbenannt; löst sich mit dem neueren Korpus |

  Der Matcher deckt mehr ab, nicht weniger. Die Ausschussnamen selbst passen
  auf beiden Seiten wortgleich, auch die Select- und Permanent-Select-Gremien.

## Phasen

Der Schnitt folgt dem Review: kein Schritt entfernt eine Referenz, bevor der
Vergleich gegen sie gelaufen ist.

### P1 — Taxonomie-Refresh atomar und resume-sicher

`submapper.py` erhält vor `prepare` einen Snapshotschritt über den vorhandenen
Weg (`snapshot build` → Driftvergleich → `approve`), kein zweiter Abrufcode.
Der Abruf läuft bei **jedem** Lauf, ohne Altersschwelle.

Reihenfolge, verbindlich:

1. frisch bauen (`snapshot build` schreibt nur unter `runs/<run_id>/`);
2. gegen die **noch gültige** Baseline vergleichen — `load_baseline`
   (`taxonomy_snapshot.py:576`) braucht sie dafür;
3. Freigabe: driftfrei → automatisch; Drift → Halt am Gate, Bene entscheidet;
4. erst bei Freigabe die abgelöste Baseline nach
   `data/committee_matcher_v2/taxonomy/archiv/approved_baseline_<snapshot_id>.json`
   kopieren;
5. neue Baseline atomar einsetzen (`write_text_atomic`).

Damit endet ein Abruffehler mit unveränderter Baseline statt ohne. Ein
laufender Mehrtages-Fullrun bleibt an dem Snapshot, mit dem er begonnen hat:
`resume` liest die `snapshot_id` aus dem Laufzustand, nicht die aktuelle
Baseline. Ein neuer Snapshot wirkt erst auf den nächsten Lauf.

Gate A: Trockenlauf zeigt frischen Snapshot, unveränderte Baseline bei
simuliertem Abruffehler, Archiveintrag genau bei Freigabe, `resume` bleibt auf
seiner `snapshot_id`.

### P2 — Produktionsrevision erzeugen und binden

**Stand 2026-09-04: Bindung implementiert, Gate B erfüllt.**
`config/committee_revision.py` löst `COMMITTEE_MAPPING_REVIEW_ID` auf und
weist jede unvollständige Variante mit eigenem Code ab (`MAP_REVISION_UNSET`,
`MAP_REVISION_MISSING`, `MAP_MANIFEST_MISSING`, `MAP_MANIFEST_INVALID`,
`MAP_NOT_REVIEWED`, `MAP_COUNT_MISMATCH`, `MAP_INDUSTRY_UNKNOWN`,
`MAP_ARTIFACT_CONTRACT`, `MAP_CONFIDENCE_INVALID`); Tests in
`tests/test_committee_revision_p2.py`. Die Produktionsrevision über 225 Units
steht weiterhin aus — gebunden ist bis dahin die Testlaufrevision
`map-test-20260904` (5 Units, 7 Zuordnungen, `scope: test-fullrun`).

Heute existiert nur ein Probeartefakt: `reviews/review-p8-run2/` mit 7 Units
und 9 Zuordnungen (`import_manifest.json:8`). Das Produktionsmapping über 225
Units muss der Fullrun erst liefern; er läuft parallel zu P3, weil der Adapter
gegen das Probeartefakt entstehen kann, der Cutover aber nicht.

`reviews/<id>/` ist kein Pfad, sondern eine Menge. Nötig ist deshalb:

- **aktive Revision konfigurierbar:** `COMMITTEE_MAPPING_FILE` adressiert nur
  einen Dateinamen unter `data/mappings` (`committee_mapping.py:28`) und
  taugt nicht. Neu: `COMMITTEE_MAPPING_REVIEW_ID` plus abgeleiteter Pfad unter
  `data/committee_matcher_v2/reviews/<id>/`.
- **drei Prüfungen beim Laden**, mehr nicht: `import_manifest.json` vorhanden
  (der Abschlussmarker — fehlt er, ist die Revision unvollständig),
  `review_status == "reviewed"`, Zeilenzahl gegen `assignment_count`.
- **Industry-IDs gegen die aktive Baseline**, nicht den Taxonomiehash. Der
  Hash ändert sich mit jedem Refresh aus P1 und würde die eben freigegebene
  Revision beim nächsten Lauf abweisen — die Schutzmaßnahme legte Filter 3
  lahm. Die Frage, die zählt, ist: kennt die Baseline noch jede `industry_id`
  der Revision? `snapshot.industry_ids()` beantwortet sie, benutzt wie in
  `fullrun_import.py`.
- **Unitzahl beim Laden ausweisen.** Keine Hartsperre gegen das Probeartefakt;
  ein Lauf, der „7 Units" meldet, ist am Terminal sofort als solcher zu
  erkennen.

Gate B: Loader nimmt die vollständige Revision an und weist jede unvollständige
Variante (Manifest fehlt, Status `open`, Zeilenzahl abweichend, unbekannte
`industry_id`) mit eindeutigem Fehlercode ab.

### P3 — V2-Adapter bauen und im Schatten messen

`config/committee_mapping.py` liest `reviewed_assignments.csv` statt der
Legacy-CSV, **ohne** den Legacy-Weg schon zu entfernen. Wert je Eintrag
`{sector, industry, confidence}`, nur Zeilen mit `decision == "mapped"`.
`CommitteeMapper` behält seine öffentliche Schnittstelle, damit `CommitteeTag`,
die Signale und das Dashboard unverändert bleiben.

Drei Festlegungen, die den Adapter von der heutigen Semantik unterscheiden:

- **Kanonische Namen wiederverwenden.** `_normalize` wird nicht um Präfixe
  erweitert; der Matcher besitzt mit `units.match_key` (`units.py:118`) die
  robustere Kanonisierung. Der Adapter benutzt sie auf beiden Seiten des
  Vergleichs.
- **Joint-Fallback wiederverwenden.** Die Regel „`joint` trifft beide Kammern"
  existiert bereits als `_lookup_committee` (`roster_coverage.py:231`) und wird
  übernommen statt nachgebaut.
- **Parent-Core nicht verwässern.** Den Committee-Lookup speisen allein Units
  mit `unit_kind` `committee_direct` oder `committee_parent_core`. Die heutige
  Aggregation aller Subcommittee-Zeilen in den Committee-Lookup
  (`committee_mapping.py:121-124`) entfällt — sie würde den bewusst erzeugten
  Parent-Core mit Subcommittee-Sektoren auffüllen.

**Der Schattenvergleich braucht keinen zweiten Ladeweg in Produktivcode.**
`CommitteeTag` nimmt den Mapper als Parameter (`committee_tag.py:76`) und
`_apply_committee_relevance` (`import_trades.py:1038`) ist eine reine Funktion
über einen DataFrame. Ein Wegwerfskript baut beide Mapper nebeneinander, lässt
`calculate_relevance` zweimal über den Master laufen und stellt gegenüber:

- Trefferzahl je Signal (`committee`, `committee_full`, Ex-Officio-Keys);
- Trades, die nur eine der beiden Quellen als relevant erkennt, mit Ausschuss;
- die Committees, die durch die Parent-Core-Abgrenzung ihren Committee-Lookup
  verlieren — namentlich, nicht als Zahl;
- die drei bekannten ungeklärten Roster-Einträge (`errors.json:23`):
  Select Subcommittee January 6 (house), Commission on Security and
  Cooperation in Europe (senate), Senate Caucus on International Narcotics
  Control — je Eintrag entweder Alias in
  `committee_matcher_roster_rules.yaml` oder bewusster Ausschluss;
- die drei Legacy-Ausschüsse ohne Entsprechung aus der Abdeckungstabelle oben;
- **Scoreprojektion** nach der P4-Formel, siehe Wirkungsbericht unten.

Gate C (Freigabegate): Unittests für kanonische Namen, Joint-Fallback,
Parent-Core-Abgrenzung und Dedup nach `confidence` sind grün, die
Gegenüberstellung liegt vor, Bene gibt die gemessene Verschiebung ausdrücklich
frei. Ohne diese Freigabe findet kein Cutover statt.

### P4 — Atomarer Cutover mit Confidence-Scoring

Erst hier fällt der Legacy-Weg. Zielformel, wie besprochen:

```
Committee-Beitrag = SCORING_WEIGHTS['committee_full'] × CONFIDENCE_FAKTOR
```

Neu in `config/settings.py`:

```python
SCORING_COMMITTEE_CONFIDENCE_FACTORS = {
    4: 1.00,   # confidence_4_faktor
    3: 0.75,
    2: 0.50,
    1: 0.25,
}
SCORING_COMMITTEE_CONFIDENCE_DEFAULT = 0.0   # ohne bekannte Konfidenz kein Beitrag
```

Der Basisbetrag bleibt `SCORING_WEIGHTS['committee_full']` und wird von 5 auf
**25** angehoben; damit ergibt Konfidenz 4 genau 25 Punkte.

Drei Semantikpunkte, ohne die die Formel falsch rechnet:

- **Nur Voll-Treffer bestimmen die Konfidenz.** Die Masterspalte heißt
  `Committee_Full_Confidence_Max` und entsteht ausschließlich aus Einträgen mit
  `match_type == "full"`. Im heutigen Modus `sector_and_industry`
  (`settings.py:50`) enthält `Relevant_Sectors` ohnehin nur Voll-Treffer; unter
  `sector_or_industry` oder `hybrid` würde ein hochkonfidenter Halb-Treffer
  sonst den Beitrag eines schwächeren Voll-Treffers anheben, obwohl er
  `committee_full` gar nicht auslöst.
- **Faktor 0 schließt wirklich aus.** Das Hard-Gate prüft heute nur, *ob*
  `committee_full` getriggert hat (`trade_score.py:177`); mit Faktor 0 bliebe
  das Gate offen und Hint-Gewichte könnten weiter Punkte liefern. Künftig gilt
  `committee_full` mit effektivem Gewicht 0 als nicht wirksames Primärsignal —
  dieselbe Logik, mit der `score_recompute.py:89` ausgeschlossene Signale aus
  `SCORING_PRIMARY_SIGNALS` entfernt. Der Eingriff bleibt auf
  `committee_full` beschränkt.
- **Fehlende Konfidenz ist fail-closed.** Default `0.0` statt `1.0`: ein
  fehlender Wert darf nicht wie Konfidenz 4 zählen. Der Import zählt die
  betroffenen Zeilen und meldet sie, damit der Ausschluss sichtbar bleibt und
  nicht still wirkt.

Trägerkette:

| Ort | Änderung |
| --- | --- |
| `committee_mapping.py` | Legacy-Ladeweg entfernt; `confidence` je Eintrag statt `rank`/`Konfidenz` |
| `committee_tag.py` | reicht `confidence` je Treffer durch; Dedup nach MAX |
| `import_trades.py` | neue Masterspalte `Committee_Full_Confidence_Max` |
| `scoring/trade_score.py:177,198` | Gate-Prüfung auf effektives Gewicht; `w = weights[sig] × faktor` |
| `compute_trade_scores` | liest die neue Spalte, gibt sie durch |
| `app/score_recompute.py` | `SCORING_ATTRS` um die beiden neuen Attribute erweitert, sonst rechnet das Dashboard anders als die Pipeline |
| `scripts/reset_pipeline.py:120` | Eintrag `DATA_MAPPINGS / settings.COMMITTEE_MAPPING_FILE` **entfernen** — er greift auf ein dann gelöschtes Setting zu und würde den Import sprengen |

`compute_trade_score` bekommt den Wert als fünften Parameter mit Default, damit
die vorhandenen Tests unverändert bleiben. Der Breakdown weist den Faktor aus
(`committee_full(c3 x0.75):19`), sonst wäre der Score im Detail-Popup nicht mehr
nachvollziehbar.

Danach, in dieser Reihenfolge:

1. Legacy-CSV nach `data/mappings/archiv/` verschieben; das Archiv ist so
   geschnitten, dass ein späteres `rm -r` es vollständig entfernt;
2. **einmaliger Full-Rebuild** des Masters (kein `--delta`);
3. Dashboard-Sichtprüfung, vollständige Testsuite;
4. kanonische Doku nachziehen (`DATA_STRUCTURE.md` für die neue Spalte,
   `CLI_Bedienungsanleitung.md` für den geänderten Ablauf).

Gate D: Pipelinelauf grün, Testsuite grün, Scoreverteilung entspricht der in
P3 freigegebenen Projektion.

## Wirkungsbericht statt Kalibrierentscheidung

Der Basisbetrag 25 ist entschieden; Gate C täuscht darum keine offene Wahl
vor, sondern misst die Folge. Gemessen im aktuellen Master (114.245 Trades,
2026-09-03):

| Gruppe | Trades |
| --- | --- |
| `committee_full` | 19.066 |
| `swarm_buy` | 11.518 |
| nur `committee_full` | 16.493 |
| nur `swarm_buy` | 8.945 |
| beide | 2.573 |

Heutige Scoreverteilung: Median 0, 90 % ≤ 20, 99 % ≤ 25.

Konfidenz 4 wird mit 25 Punkten damit nicht nur schwerer als `swarm_buy` (20),
sondern trifft auch die größere Gruppe; Konfidenz 3 ergibt gerundet 19 und
liegt knapp darunter. Der Bericht vor dem Cutover enthält deshalb:
Konfidenzverteilung der Voll-Treffer, Score-Perzentile vorher/nachher,
Committee-only/Swarm-only/Overlap und die Zusammensetzung der Top-N. Freigabe
ist eine ausdrückliche Zustimmung zu dieser Verschiebung.

Der Bericht ist eine einmalige Abfrage über den Master, kein eingechecktes
Skript. Er beantwortet eine Freigabefrage und hat danach keinen Leser.

## Nicht-Ziele

- Keine zweite Abruflogik für yfinance — der Matcherweg ist der einzige.
- Kein Umbau von `import_trades.py` über die geänderte Aufrufstelle und die
  neue Konfidenzspalte hinaus.
- Keine Änderung an Signalen oder Dashboard, solange die Schnittstelle von
  `CommitteeMapper` gehalten wird. Das Hard-Gate ist die eine Ausnahme.
- **Kein zweiter Ladeweg für den Schattenvergleich.** Ein Schalter, der eine
  Phase später wieder entfernt wird, ist teurer als das Wegwerfskript, das
  denselben Vergleich über den injizierbaren Mapper zieht.
- Keine Hartsperre gegen das Probeartefakt und keine Hashbindung der Revision
  an die Baseline. Beides schützt vor einem Fehler, den nur Bene selbst
  auslösen könnte, und die Hashbindung stünde zusätzlich gegen den
  Taxonomie-Refresh aus P1.
- **Kein separates Migrationsskript**, aber ein einmaliger Full-Rebuild: ein
  Delta-Lauf merged alte Masterzeilen unverändert durch
  (`import_trades.py:2496`), sie behielten sonst ihre Legacy-Zuordnung.
  `score_recompute.py` hilft dabei nicht — es rechnet den Score aus
  vorhandenen Signalspalten neu, erzeugt `Relevant_Sectors` aber nicht neu.
- Keine zusätzliche Select-Präfix-Logik: die Matcher-Kanonisierung deckt die
  Select- und Permanent-Select-Gremien bereits ab.
- `app/scoring_presets.py` bleibt unangetastet. Presets speichern die im
  Dashboard **editierbaren** Parameter; die Konfidenzfaktoren stehen
  ausschließlich in `config/settings.py`. Sollen sie später im UI editierbar
  werden, ist das ein eigener Scope mit eigenem Widget.

## Entschieden (2026-09-03)

- Archiv trennt den **alten Mapper** sichtbar ab und bleibt als Ganzes
  löschbar; der abgelöste Taxonomie-Snapshot wird bei jeder Freigabe
  mitarchiviert.
- `snapshot build` läuft bei jedem Lauf, ohne Altersschwelle; die Freigabe
  bleibt bei Drift ein Gate.
- Keine Mindestkonfidenz. Statt eine Stufe auszuschließen, entscheidet der
  Faktor je Stufe über den Beitrag; Faktor `0` schließt eine Stufe vollständig
  aus, inklusive Hard-Gate.
- **Joint Committees.** Die Trade-Seite liefert `Member_Chamber` als
  `house`/`senate`; der Matcher führt 4 Joint-Units unter `joint`. Regel:
  `joint` trifft beide Kammern (Wiederverwendung von `roster_coverage.py:231`).
- **Rückwirkendes Rescoring.** `trade_score` steht in der Master-CSV; nach P4
  ändern sich die Scores aller historischen Trades. Die rückwirkende Änderung
  ist gewollt und entsteht durch den Full-Rebuild.
- **Reihenfolge.** Der Fullrun über 225 Units läuft parallel zu P3; P1–P3
  entstehen gegen das Probeartefakt, der Cutover in P4 verlangt das
  vollständige Artefakt.
