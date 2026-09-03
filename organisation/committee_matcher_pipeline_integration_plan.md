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

**Zeilenbudget:** rund 400 Zeilen Produktivcode über alle Phasen, davon der
Löwenanteil Löschung. Was darüber hinausgeht, braucht einen Grund.

## Zielspezifikation

1. **Taxonomie je Lauf frisch.** Gold-Set und Fullrun holen Industries und
   Sektoren samt Beschreibung vor dem Lauf neu über denselben yfinance-Weg,
   den der API-Matcher benutzt. Kein Arbeiten gegen einen alten Snapshot.
2. **Der alte Mapper wird sichtbar abgetrennt.** Alle Legacy-Artefakte wandern
   in ein Archivverzeichnis, sind aus dem laufenden Programm nicht mehr
   erreichbar und lassen sich später in einem Zug löschen. Zusätzlich wird der
   jeweils abgelöste `approved_baseline.json` bei jedem Build unter seiner
   `snapshot_id` archiviert.
3. **Eine Mappingquelle.** Die Pipeline liest das Ergebnis des Matchers.
   `data/mappings/subcommittee_sector_industry_mapping.csv` wird nicht mehr
   gelesen.
4. **`Konfidenz` entfällt.** Die Rolle übernimmt `confidence` (1–4) aus dem
   Matcher. Auch `Sector_Rank` entfällt: seine einzige Aufgabe (Dublettenwahl)
   übernimmt `confidence`.
5. **`confidence` wirkt auf den Score.** Der Committee-Beitrag lautet
   `Basisbetrag × Faktor(confidence)`, je Stufe in `config/settings.py`
   einstellbar. Bei mehreren relevanten Treffern zählt die höchste Konfidenz —
   dasselbe MAX-Prinzip wie beim Ex-Officio-Bonus.
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
→ Mapping → `reviews/<id>/reviewed_assignments.csv` (32 Spalten, u. a.
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

### P1 — Taxonomie je Lauf frisch, mit Archiv (~80 Zeilen)

`goldset_label.py` erhält vor `prepare` einen Snapshotschritt über den
vorhandenen Weg (`snapshot build` + `approve`), kein zweiter Abrufcode. Der
Abruf läuft bei **jedem** Build, ohne Altersschwelle. Der abgelöste
`approved_baseline.json` wandert vorher nach
`data/committee_matcher_v2/taxonomy/archiv/approved_baseline_<snapshot_id>.json`.

Gate A: Fullrun-Trockenlauf zeigt einen frischen Snapshot, das Archiv trägt den
Vorgänger, kein Lauf arbeitet gegen eine veraltete Taxonomie.

### P2 — Mappingquelle umstellen (~120 Zeilen)

`config/committee_mapping.py` liest `reviewed_assignments.csv` statt der
Legacy-CSV: Schlüssel aus `chamber`/`committee_name`/`unit_name`, Wert
`{sector, industry, confidence}`. Nur Zeilen mit `decision == "mapped"`.
`CommitteeMapper` behält seine öffentliche Schnittstelle, damit `CommitteeTag`,
die Signale und das Dashboard unverändert bleiben.

Gate B: Gegenüberstellung alt/neu — Zahl der Units, Sektoren je Unit, und die
Liste der Ausschüsse, die nur eine der beiden Quellen kennt.

### P3 — Alten Mapper abtrennen (~60 Zeilen, überwiegend Löschung)

`Konfidenz` und `Sector_Rank` fallen weg; Dedup entscheidet nach `confidence`.
Die Legacy-CSV wandert nach `data/mappings/archiv/`, der Legacy-Ladeweg in
`committee_mapping.py` wird entfernt statt umgeschaltet — danach führt kein
Codepfad mehr zur alten Quelle. `COMMITTEE_MAPPING_FILE` und der Schutzeintrag
in `reset_pipeline.py:120` folgen der neuen Quelle. Das Archiv ist so
geschnitten, dass ein späteres `rm -r` es vollständig entfernt.

Gate C: Pipelinelauf auf echten Trades; `Relevant_Sectors` und
`ex_officio_role_keys` entstehen wie zuvor, Trefferzahl gegen den alten Stand.
Die fünf Ausschüsse ohne Namensentsprechung sind namentlich abgehakt.

### P4 — `confidence` in den Score (~110 Zeilen)

Zielformel, wie besprochen:

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
SCORING_COMMITTEE_CONFIDENCE_DEFAULT = 1.0   # Faktor ohne bekannte Konfidenz
```

Der Basisbetrag bleibt `SCORING_WEIGHTS['committee_full']` und wird von 5 auf
**25** angehoben; damit ergibt Konfidenz 4 genau 25 Punkte.

Die Trägerkette ist die eigentliche Arbeit, nicht die Formel:

| Ort | Änderung |
| --- | --- |
| `committee_mapping.py` | `confidence` je Eintrag statt `rank`/`Konfidenz` |
| `committee_tag.py` | reicht `confidence` je Treffer durch; Dedup nach MAX |
| `import_trades.py` | neue Masterspalte `Committee_Confidence_Max` |
| `scoring/trade_score.py:198` | `w = weights[sig] × faktor` für die Committee-Signale |
| `compute_trade_scores` | liest die neue Spalte, gibt sie durch |
| `app/score_recompute.py`, `app/scoring_presets.py` | UI-Neuberechnung kennt den Faktor, sonst rechnet das Dashboard anders als die Pipeline |

`compute_trade_score` bekommt den Wert als fünften Parameter mit Default, damit
die vorhandenen Tests unverändert bleiben. Der Breakdown weist den Faktor aus
(`committee_full(c3 x0.75):19`), sonst wäre der Score im Detail-Popup nicht mehr
nachvollziehbar.

Gate D: Scoreverteilung vor/nach an denselben Trades. Zu prüfen ist die
Balance — heute steht `swarm_buy` auf 20 und `committee_full` auf 5; nach der
Anhebung dominiert der Committee-Filter das Primärsignal-Paar. Das ist eine
Kalibrierentscheidung, keine technische Frage.

### P5 — Funktionsprüfung

Vollständiger Pipelinelauf, Dashboard-Sichtprüfung, Testsuite. Erst danach gilt
die Integration als erfolgreich und das Archiv als Rückfallweg, nicht als
Zwischenstand.

## Vor der Implementierung zu entscheiden

1. **Reihenfolge.** Es existiert noch kein `reviewed_assignments.csv`: der
   Fullrun über 225 Units ist nie gelaufen. P2 bis P5 haben ohne dieses
   Artefakt keine Datengrundlage. Entweder erst der Fullrun (Stunden bis Tage
   Abo-Zeit), oder die Integration entsteht gegen ein Probeartefakt und wird
   danach gegen das echte gemessen.
2. **Joint Committees — entschieden 2026-09-03.** Die Trade-Seite liefert
   `Member_Chamber` als `house`/`senate`; der Matcher führt 4 Joint-Units unter
   `joint`. Regel: `joint` trifft beide Kammern.
3. **Rückwirkendes Rescoring — entschieden 2026-09-03.** `trade_score` steht in
   der Master-CSV; nach P4 ändern sich die Scores aller historischen Trades.
   Die rückwirkende Änderung ist gewollt; die Neuberechnung läuft über
   `app/score_recompute.py`.

## Nicht-Ziele

- Keine zweite Abruflogik für yfinance — der Matcherweg ist der einzige.
- Kein Umbau von `import_trades.py` über die geänderte Aufrufstelle hinaus.
- Keine Änderung an Signalen, Gates oder Dashboard, solange die Schnittstelle
  von `CommitteeMapper` gehalten wird.
- Keine Migration historischer Master-Zeilen; der nächste Lauf schreibt neu.

## Entschieden (2026-09-03)

- Archiv trennt den **alten Mapper** sichtbar ab und bleibt als Ganzes
  löschbar; der abgelöste Taxonomie-Snapshot wird mitarchiviert.
- `snapshot build` läuft bei jedem Build, ohne Altersschwelle.
- Keine Mindestkonfidenz. Statt eine Stufe auszuschließen, entscheidet der
  Faktor je Stufe über den Beitrag; ein Faktor `0` schließt eine Stufe aus,
  ohne dass es dafür einen zweiten Schalter braucht.
