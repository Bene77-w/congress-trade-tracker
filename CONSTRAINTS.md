# Constraints

Zuletzt geprüft: 2026-11-20 mit Bene (Interview zur Sanierung, Audit `docs/sanierung/audit.md`).
Gesamtprüfung am Ticketende: `tools/check.sh` (Basis: Tag `sanierung-anker`).

## Grundregeln (immer, blockieren)

- Keine neuen Unterdrückungen: `# noqa`, `# type: ignore`, `pragma: no cover`, `# nosec`, `pylint: disable`
- Keine unfertige Arbeit: `raise NotImplementedError`, `except …: pass`, `TODO`/`FIXME` im Code
- Keine übersprungenen Tests: `pytest.mark.skip`/`xfail`, `pytest.skip(`, `importorskip`
- Tests nur mit Begründung entfernen: gelöschte Testdatei oder entferntes Assert braucht im Commit eine Zeile `Tests-entfernt: <pfad-oder-glob> <Grund>`. Grund: In der Sanierung fallen Tests mit ihrem Produktivcode weg, das soll sichtbar bleiben.
- Keine Secrets im Code
- Diese Datei wird nicht abgeschwächt, damit eine Änderung durchkommt

Geprüft von `tools/floor_guard.py` (Diff gegen die Basis) und `detect-secrets scan` (geänderte Dateien).

## Mit Zahlen erzwungen

Zeilenbudget und Architekturgrenzen **warnen bis 2026-12-04 und blockieren danach** (Interview: neue Dimensionen zunächst nur Warnung).

| Dimension | Regel | Geprüft von | Läuft |
|---|---|---|---|
| Grundregeln | 0 Verstöße | `python tools/floor_guard.py` | Ticketende, vor Merge |
| Secrets | 0 Funde | `detect-secrets scan <geänderte Dateien>` | Ticketende, vor Merge |
| Tests | volle Suite grün | `python -m pytest -q` | jedes Ticket (Interview: volle Suite je Ticket) |
| Zeilenbudget | Obergrenzen der Tabelle unten | `python tools/check_lines.py` | Ticketende |
| Architektur: App-Grenze | `app` importiert nichts aus `signals`, `tags`, `gates`, `scripts`, `scrapers`, `committee_matcher` | `lint-imports --contract app-grenze` | Ticketende |
| Architektur: keine App-Importe | Nichts außerhalb von `app` importiert `app` | `lint-imports --contract keine-app-importe` | Ticketende |

Die Verträge stehen in `.importlinter`.

### Zeilenbudget (Ratchet)

| Messgröße | Obergrenze |
|---|---|
| Produktivcode | ≤ 42089 |
| Tests | ≤ 26697 |

Gezählt werden versionierte `*.py`. Produktivcode umfasst alles außer `tests/` und `tools/`. Startwert ist der Stand am 2026-11-20. Sinkt ein Wert, wird die Obergrenze im selben Commit auf den neuen Stand gesenkt. Sie wird nie angehoben, außer über eine Ausnahme unten.
Ziel der Sanierung: Produktivcode ≤ 15000, Tests ≤ 10000 (Audit Abschnitt 3). Begründung: Das Audit belegt ≈ −24.100 Zeilen als sicher löschbar und −65 bis −70 % als realistisch. Ohne Ratchet wuchsen in Stufe 1 ≈ 830 Zeilen über Review-Korrekturen nach.

## Gemessen, noch nicht erzwungen

| Messgröße | Heute | Richtung | Geprüft von |
|---|---|---|---|
| Signalmodule unabhängig | 13 Querimporte | nicht steigen, Ziel 0 | `lint-imports --contract signale-unabhaengig` |
| Netz-Clients nur in Adaptern | 4 Pfade (`app`, `scoring.abnormal_returns` über `post_trade_returns`) | nicht steigen, Ziel 0 | `lint-imports --contract netz-nur-in-adaptern` |
| Testlaufzeit | 214 s | nicht steigen, Ziel < 20 s | `pytest -q` |

## Ausnahmen

| ID | Regel | Pfad | Grund | Verantwortlich | Läuft ab |
|---|---|---|---|---|---|
| W1 | app-grenze | `app.member_metrics`, `app.streamlit_app` → `signals.committee_signal`; `scoring.post_trade_returns` → `signals.committee_signal`; `scoring.trade_score` → `signals.trade_volume_signal` | Altbestand; entfällt mit E2 (ein Committee-Modul), dem Read-Model `app/data.py` und O5 (Score-Editor weg) | Bene | 2027-02-18 |
| W2 | keine-app-importe | `scripts.analyse_event_study` → `app.score_correlation` | Altbestand; entfällt mit `analysis/event_study` (O7) | Bene | 2027-02-18 |
