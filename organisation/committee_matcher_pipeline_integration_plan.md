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
2. **Archiv.** Der abgelöste `approved_baseline.json` wird bei jedem neuen
   Snapshot in ein Archivverzeichnis innerhalb des Taxonomieverzeichnisses
   kopiert, benannt nach seiner `snapshot_id`. Gleiches gilt einmalig für die
   Legacy-Mapping-CSV.
3. **Eine Mappingquelle.** Die Pipeline liest das Ergebnis des Matchers.
   `data/mappings/subcommittee_sector_industry_mapping.csv` wird nicht mehr
   gelesen.
4. **`Konfidenz` entfällt.** Die Rolle übernimmt `confidence` (1–4) aus dem
   Matcher. Auch `Sector_Rank` entfällt: seine einzige Aufgabe (Dublettenwahl)
   übernimmt `confidence`.
5. **`confidence` wirkt auf den Score.** Über `config/settings.py` einstellbar,
   mit tatsächlicher Wirkung auf den Committee-Anteil des `trade_score` —
   nicht als reines Beifeld.
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
- **Offen:** `unit_name` des Matchers und `Subcommittee` der CSV stammen aus
  verschiedenen Quellen; die Namensnormalisierung muss gegen echte Trades
  gemessen werden (Phase 3, Gate C).

## Phasen

### P1 — Taxonomie je Lauf frisch, mit Archiv (~80 Zeilen)

`goldset_label.py` erhält vor `prepare` einen Snapshotschritt über den
vorhandenen Weg (`snapshot build` + `approve`), kein zweiter Abrufcode. Der
abgelöste `approved_baseline.json` wandert vorher nach
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

### P3 — Altintegration entfernen (~60 Zeilen, überwiegend Löschung)

`Konfidenz` und `Sector_Rank` fallen weg; Dedup entscheidet nach `confidence`.
Legacy-CSV nach `data/mappings/archiv/` verschoben, `COMMITTEE_MAPPING_FILE`
und der Eintrag in `reset_pipeline.py:120` folgen der neuen Quelle.

Gate C: Pipelinelauf auf echten Trades; `Relevant_Sectors` und
`ex_officio_role_keys` entstehen wie zuvor, Trefferzahl gegen den alten Stand.

### P4 — `confidence` in den Score (~90 Zeilen)

Neu in `config/settings.py`:

```python
COMMITTEE_CONFIDENCE_MIN = 2          # darunter zählt eine Zuordnung nicht
COMMITTEE_CONFIDENCE_FACTORS = {4: 1.0, 3: 0.8, 2: 0.55, 1: 0.3}
```

`CommitteeTag` reicht `confidence` je Eintrag durch und schreibt die höchste
relevante Konfidenz in eine Masterspalte; `scoring/trade_score.py` multipliziert
den `committee_full`-Beitrag mit dem Faktor. Ein Faktor `1.0` für alle Stufen
reproduziert das heutige Verhalten — die Umstellung ist damit messbar.

Gate D: Scoreverteilung vor/nach, an denselben Trades.

### P5 — Funktionsprüfung

Vollständiger Pipelinelauf, Dashboard-Sichtprüfung, Testsuite. Erst danach gilt
die Integration als erfolgreich und das Archiv als Rückfallweg, nicht als
Zwischenstand.

## Nicht-Ziele

- Keine zweite Abruflogik für yfinance — der Matcherweg ist der einzige.
- Kein Umbau von `import_trades.py` über die geänderte Aufrufstelle hinaus.
- Keine Änderung an Signalen, Gates oder Dashboard, solange die Schnittstelle
  von `CommitteeMapper` gehalten wird.
- Keine Migration historischer Master-Zeilen; der nächste Lauf schreibt neu.

## Zu klären vor P1

1. **Archivlesart.** Ich lese „Baseline des alten Matchers" als den jeweils
   abgelösten `approved_baseline.json` — es gibt kein Taxonomie-Artefakt des
   alten Trackers. Trifft das zu?
2. **Snapshot je Lauf und Kosten.** `snapshot build` ist ein Netzwerkabruf über
   145 Industries mit Pause und Retry; er verlängert jeden Fullrun-Start.
   Soll er unbedingt laufen oder ab einem Alter (z. B. 24 h)?
3. **`COMMITTEE_CONFIDENCE_MIN`.** Vorschlag 2: Konfidenz 1 ist im Matcher die
   schwächste Stufe und trüge sonst denselben Filter wie ein Volltreffer.
