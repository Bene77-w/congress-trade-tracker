# Dedup-Problem: Falsche Duplikaterkennung bei identischen Trades

## Problem

Ein Politiker kann am selben Tag dasselbe Wertpapier mehrfach handeln —
z.B. Apple-Aktien dreimal im Bereich $1.001–$15.000 verkaufen.

Alle drei Trades haben identische Feldwerte:
`name | ticker | asset_name | transaction_type | amount | transaction_date | owner`

Der bestehende `drop_duplicates`-Mechanismus entfernt 2 der 3 Trades,
obwohl sie legitime, separate Transaktionen sind.

---

## Zwei Quellen des Problems

### 1. Vision API (OCR) — behoben
**Dateien:** `house_pdf_extractor.py`, `senate_pdf_extractor.py`

Die Anthropic Vision API liest gescannte Papierformulare. Dabei gibt es zwei Fälle:

| Fall | Aussehen | Realität |
|------|----------|----------|
| OCR-Artefakt | 2× identisch | API hat dieselbe Zeile zweimal gelesen → soll entfernt werden |
| Legitimer Trade | 2× identisch | Politiker hat wirklich 2 Zeilen auf dem Formular → soll behalten werden |

Das Programm konnte diese Fälle nicht unterscheiden, da der einzige Unterschied
die **physische Zeilenposition auf dem Formular** ist.

### 2. pdfplumber / HTML-Scraping — offen
**Dateien:** `house_scraper.py`, `senate_scraper.py`

Kein OCR — jede Zeile wird deterministisch genau einmal gelesen.
Das "3 identische Apple-Sales"-Problem existiert theoretisch, tritt aber
selten auf. Aktuell nicht behoben (Risiko als überschaubar eingestuft).

---

## Lösung (umgesetzt für Vision API Extractors)

### Kernidee
Jeder Trade bekommt vom Vision API eine `row_index` (physische Zeilennummer
auf dem Formular). Zwei Trades sind nur dann ein echtes Duplikat, wenn sie
**inhaltlich identisch UND auf derselben Seite+Zeile** stehen.

### Änderungen

**1. API-Prompt** — neues Feld + Regel:
```
"row_index": 1-basierte Zeilennummer des Trades auf dem Formular.
Gleicher Inhalt auf VERSCHIEDENEN Zeilen → alle zurückgeben.
Gleicher Inhalt durch OCR-Artefakt (dieselbe Zeile 2×) → nur einmal zurückgeben.
```

**2. `run()` — Intra-Batch-Dedup**
```python
# Vorher:
df.drop_duplicates(subset=DEDUP_SUBSET)

# Nachher:
_intra_dedup = DEDUP_SUBSET + ["_page_num", "_row_index"]
df.drop_duplicates(subset=_intra_dedup)
# → OCR-Artefakt (gleiche Seite+Zeile) wird entfernt
# → legitime Mehrfachtrades (verschiedene Zeilen) bleiben erhalten
```

**3. `save()` — Anti-Join statt drop_duplicates auf combined**
```python
# Vorher:
combined = pd.concat([existing, df])
combined.drop_duplicates(subset=DEDUP_SUBSET, keep="last")
# Problem: 3 identische Apple-Trades in df → combined hat 6 Zeilen
#          → drop_duplicates kollabiert auf 1

# Nachher (Anti-Join):
existing_keys = set(existing[DEDUP_SUBSET].fillna("").apply(tuple, axis=1))
new_rows = df[~df[DEDUP_SUBSET].fillna("").apply(tuple, axis=1).isin(existing_keys)]
combined = pd.concat([existing, new_rows])
# → existing bleibt unverändert
# → nur wirklich neue Zeilen werden angehängt
```

### Warum Anti-Join in `save()` alleine nicht reicht
Das `drop_duplicates` in `run()` läuft **vor** `save()`. Wenn `run()` die 3 Apple-Trades
bereits auf 1 kollabiert, kommt bei `save()` nur 1 an — Anti-Join hilft dann nicht mehr.
Beide Fixes (run + save) müssen kombiniert werden.

---

## Status

| Datei | run() | save() | Zusatz |
|-------|-------|--------|--------|
| `house_pdf_extractor.py` | ✅ row_index-Dedup | ✅ Anti-Join | — |
| `senate_pdf_extractor.py` | ✅ row_index-Dedup | ✅ Anti-Join | + asset_name fehlte im alten Subset |
| `house_scraper.py` | ⏸️ offen | ⏸️ offen | pdfplumber: kein OCR, Risiko klein |
| `senate_scraper.py` | ⏸️ offen | ⏸️ offen | HTML: kein OCR, Risiko klein |

## Offene Lösung für Main-Scraper (falls nötig)

Analoges Vorgehen: In `_extract_transactions_from_text` (house) bzw.
`_extract_transactions` (senate) jeder Transaktion eine `_tx_index`
(Sequenznummer innerhalb des PDFs/der HTML-Tabelle) mitgeben.
Dann `drop_duplicates(DEDUP_SUBSET + ['_tx_index'])` in `run()`.
