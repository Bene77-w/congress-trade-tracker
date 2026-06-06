# Doku-Index — kanonische Quellen + Update-Trigger

**Konvention (Single-Source-of-Truth):** Pro Thema EINE kanonische Datei.
Andere Dateien verlinken nur darauf, kein paralleles Duplizieren.

Schneller Themen-Einstieg (kanonische Quelle pro Thema):
- **Spalten** (CSV/JSON/XLSX) → `DATA_STRUCTURE.md` · **Gate/Tag/Signal + Score-Formel** → `Filter_Instanz_Parameter.md` · **Pipeline-Stages** → `Pipelines.md` · **Signal-Schema** → `tool_specific_md/signal_info.md` · **Ticker/Issuer** → `Ticker_Architektur.md` · **CLI** → `CLI_Bedienungsanleitung.md`
- **Phasen-Historie (fertige Module)** → `CHANGELOG.md` · **Aktiver Modul-Stand (WIP)** → `AGENTS.md` · **Offene Pläne** → `~/.claude/plans/*.md`

Bei Code-Änderungen die folgenden Tabellen prüfen — die „Aktualisieren bei"-Spalte
nennt konkrete Trigger, dadurch muss nicht jede Datei einzeln gelesen werden.
Dieser Index ist die **Routing-Quelle** für den Skill `/doku-update` und den `doku-agent`.

## Top-Level

| Datei | Zweck (1 Satz) | Aktualisieren bei |
|---|---|---|
| `AGENTS.md` (via `CLAUDE.md` geladen) | Projektstatus, Regeln/Konventionen, aktiver WIP-Modul, Orientierungs-Workflow, Doku-Map. | Neue Regel/Konvention, neuer WIP-Modul-Stand, neues Util/Skript, Phase abgeschlossen. |
| `CHANGELOG.md` | Phasen-Historie aller abgeschlossenen Module + Iterationen (datiert). | Modul/Phase abgeschlossen, größere Iteration fertig. |

## `organisation/`

| Datei | Zweck (1 Satz) | Aktualisieren bei |
|---|---|---|
| `README.md` | Kurz-Überblick mit Datenfluss-Diagramm, Modul-Status, Quick-Start, Scoring-Tabelle. | Neuer Modul-Status, Datenfluss, Scoring-Formel/-Gewichte geändert. |
| `DATA_STRUCTURE.md` | Vollständige Spalten-Definition **aller** CSV/JSON/XLSX. | Neue/geänderte Spalte (CSV, Cache, JSON), neue Datendatei, Suffix-Konvention geändert. |
| `Pipelines.md` | Ausführungsreihenfolge aller 9 Pipelines + Import-Stages (aktuell **17**). | Neue/entfernte Stage, Stage-Abhängigkeiten geändert, neue Problem-Output-Datei, Dedup-Logik. |
| `CLI_Bedienungsanleitung.md` | Vollständige CLI-Referenz aller Scripts inkl. Flag-Matrix. | Neues CLI-Flag, neues Script, geänderter Default-Workflow. |
| `Filter_Instanz_Parameter.md` | Konkrete Gate/Tag/Signal-Instanzen + Parameter + Scoring-Formel. | Neuer Filter/Signal, Schwelle/Gewicht geändert, neue Scoring-Komponente. |
| `Ticker_Architektur.md` | Design-Doku der Ticker-/Issuer-Spalten (3 Ebenen, Fallback-Ketten). | Neuer Ticker-Spalten-Typ, Fallback-Kette geändert, neue Cross-Class-Logik. |
| `migration.md` | Plattformwechsel-Anleitung (Linux↔Windows, USB-Betrieb). | Neue Python-Abhängigkeit, neue ENV-Variable, neue plattformspezifische Permission. |
| `manual_review_anleitung.md` | Anleitung für 4 manuelle Review-Prozesse (Namen, Ticker, Preferred, Duplicate). | Neuer Manual-Review-Typ, Review-Datei-Struktur geändert, neue Mapping-CSV. |

## `organisation/tool_specific_md/`

| Datei | Zweck (1 Satz) | Aktualisieren bei |
|---|---|---|
| `signal_info.md` | **Vollständige Signal-System-Doku**: Architektur, 10 Signal-Typen, Snapshot-Konzept, Scope-Key-Konvention, 21-Spalten-Schema von `suspicious_events.csv`. | Neuer Signal-Typ, Trigger-Logik/Spalten geändert, Scope-Key-Format, Y-Tage-Persistence, Signal-Event-ID-Hash. |
| `HAND_DELIVERED_PDF_ANALYSE.md` | Analyse der 59 gescannten Hand-Delivered House PTR PDFs (Typ A–D). | Neuer PDF-Formulartyp, Layout-Wechsel, neue OCR-Schwierigkeit, neuer Member mit Hand-Delivered-Form. |
| `house_all_pdf_info.md` | House-Committee-PDF Layout + Parsing-Parameter (80 Seiten, Font-Größen, Schwellwerte). | PDF-Layout geändert, neue Rollen, neue Committee-Struktur, neuer Parsing-Schwellwert. |
| `modul2_walkthrough.md` | Walkthrough House Scraper (ZIP→XML→PDF→Block-Parser). | Neuer Filing-Type, Block-Parser-Logik, neue Skip-Pattern, Output-Spalte hinzu/entfernt. |
| `modul3_walkthrough.md` | Walkthrough Senate Scraper (Playwright + DataTables API). | API-Parameter geändert, Agreement-Flow, Report-Type-Filter, neue Test-Fälle. |
| `Scraper_problems.md` | Bekannte Scraper-Probleme + Status (aktiv/gelöst/irrelevant). | Neues Scraper-Problem, Status geändert, neue Gegenmaßnahme, neue Fehlerrate. |
| `Senate_website_structure.md` | efdsearch.senate.gov Selektoren + HTML-Layout. | Website-Layout/Selektoren geändert, Bond-Metadaten-Format, neue Asset-Type. |
| `vote_timing_audit.md` | Stichproben-Audit der Vote-Timing-Hits. | Neuer Smoke-Run mit relevant abweichender Hit-Anzahl, neue Audit-Punkte hinzu, Mapping-Änderungen. |

**Konvention**: Bei jeder Code-Änderung mindestens eine Doku-Datei anpassen (s. `AGENTS.md` Regel 4).
Phase-Abschluss → `CHANGELOG.md` + alle in der „Aktualisieren bei"-Spalte genannten Dateien;
aktiver WIP-Modul-Stand → `AGENTS.md`.
