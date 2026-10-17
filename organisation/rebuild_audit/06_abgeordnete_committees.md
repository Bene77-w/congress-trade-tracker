# Audit 06: Abgeordnete, Ausschuss-Mitgliedschaften, Kongress-Aktivitätsdaten

Stand: 2026-10-17, Branch `agent/committe_matcher`. Nur Lese-Analyse; Hilfsskript in `/tmp/audit_06/a.py`.

## 1. Features

| Feature | Fundort |
|---|---|
| House-PDF-Parser (Zwei-Spalten-Layout R/D per x-Schwelle, Alphabetenliste als Cross-Reference, Joint Committees) | `scrapers/house_pdf_parsing_tool/parse_house_committee_pdf.py:40-877` (`parse_all_committees:587`, `_cross_reference_alphabetical:718`, `parse_joint:765`) |
| House-Validierung gegen Oracle/Baseline | `scrapers/house_pdf_parsing_tool/validate_house.py:139` |
| Senate-PDF-Parser (Schriftstil-basiert) | `scrapers/senate_pdf_parsing_tool/parse_us_senate_committee_pdf.py:274` (`process_pdf`) |
| Senate-XML-Fetch (Discovery der Committee-Codes auf senate.gov, Änderungsbericht, Manifest) | `scrapers/senate_committee_xml/fetch_senate_xml.py:43,109` |
| Senate-XML-Parser (Rollen-Mapping, CSV im F2-Format) | `scrapers/senate_committee_xml/parse_senate_xml.py:25,76,144` |
| Struktur-Generator (Chamber/Committee/Subcommittee) | `scrapers/generate_committee_structure.py` (92 Z.) |
| F2-Grammatik-Parser (`;` Committee, `:` Sub-Liste, `~` Sub, `\|` Rolle) | `utils/committee_parser.py:35` |
| Kongress-Daten-Fetch (Bills, Details, Actions, Bill-Committees, Summaries, Subjects; Checkpoint, Lock, Update-Modus; hartkodiert `CONGRESS = 119`) | `scrapers/fetch_congress_data.py:53,72-1200` |
| Namensabgleich Trades zu Roster plus `former_members.csv` | `utils/name_matcher.py:138,163,202` |
| Mapping-Tabellen Subcommittee zu Sektor/Region, Legislative-Subject-Mapping | `data/mappings/subcommittee_region_mapping.csv`, `legislative_subject_mapping_complete.csv` (maschinell/manuell gepflegt, nicht auf Korrektheit geprüft) |

Zusätzlich im Verzeichnis `scrapers/` (nicht mein Kern, aber relevant): Trade-Scraper `house_scraper.py`, `senate_scraper.py`, `house_pdf_extractor(_v2).py` (v2 nutzt Claude-Modell, Kosten), `senate_pdf_extractor.py`, `quiver_scraper.py`; plus ein gespeicherter Browser-Dump `scrapers/Recherche-Aufgabe in Datei - Claude_files/` (Müll im Repo).

## 2. Funktionsprüfung

Entstehung der Rosters (belegt):
- House: aus dem offiziellen Clerk-PDF "List of Standing Committees ... 119th Congress, January 6, 2026" (`organisation/tool_specific_md/house_all_pdf_info.md`), per pdfplumber-Layoutparser. Kein XML-Weg.
- Senate: zwei redundante Wege, PDF-Parser und XML (senate.gov, `raw/_manifest.json` fetched_at 2026-06-08). Die aktuelle CSV nutzt das XML-Format (Namen "Nachname, Vorname", Committee-Namen "Committee on ..."). Der PDF-Parser ist damit überholt, aber nicht entfernt.
- Beide CSVs zuletzt committet 2026-06-12 (`8ea4697`). Sie sind ein **einzelner Snapshot** (House-Stand Jan 2026, Senate-Stand Juni 2026).

Datenqualität (belegt, empirisch mit `utils.committee_parser`):
- House: 436 Zeilen, 221 R / 215 D, keine doppelten Namen, 56 Staaten/Territorien; 6 Delegierte vorhanden (DC, GU, MP, PR, AS, VI). Das ergibt 430 stimmberechtigte Mitglieder statt 435. Vermutlich Vakanzen oder fehlende Zeilen, nicht geprüft.
- Senate: 100 Zeilen, 53 R / 47 D, 50 Staaten, keine Duplikate. Vollständig.
- House: alle Committee- und Subcommittee-Namen stimmen mit `us_house_all_committees.csv` überein (0 unbekannt, alle 26 Master-Committees genutzt). Gut.
- Senate: **Namensinkonsistenz**. Roster nutzt "Committee on Finance", Master `us_senate_all_committees.csv` nutzt "Finance"; alle Committees und 70 Subcommittee-Paare matchen nicht per Exaktvergleich ("Subcommittee on ..." wird im Master offenbar ohne Präfix oder anders geführt). Jeder Konsument braucht Normalisierung; Normalisierungslogik wurde hier nicht geprüft (vermutet: im CommitteeMapper).
- Namensformat uneinheitlich: House "Vorname Nachname", Senate "Nachname, Vorname" (macht Name-Matcher komplexer).
- Parsing-Artefakte im House-Roster: `Pablo JoséHernández` (fehlendes Leerzeichen, Zeile 318) und `Radewagen` (nur Nachname, Zeile 328). Konkrete Fehlzuordnungsgefahr beim Namensabgleich (Delegierte, wenig Trades, geringe Wirkung).
- 5 House-Mitglieder ohne Committees (Jeffries, Clark, Johnson, Pelosi, Scalise): plausibel (Führung), kein Fehler.
- Rollen: House 24 Chairman, 22 Ranking Member; Senate 24 Chairman, 18 Ranking auf Committee-Ebene (bei 24 Chairs erwartbar mehr Ranking; `Ranking` fehlt evtl. bei Select/Joint, ungeprüft). Zwei Chairs beim Narcotics Caucus sind legitim (Co-Chairs). `ExOfficio`-Spalte ist laut `data/raw/member&(sub)committees_structure.md` fachlich irreführend definiert (true = Chair/Ranking eines Hauptcommittees, nicht Ex-officio) und kollidiert mit der Rolle "Ex Officio" (Senate Sub: 114 Einträge). Doppelbelegung des Begriffs.
- **Historisierung: nicht vorhanden (belegt)**. Es gibt keine Zeitachse, nur den aktuellen Stand; `tags/committee_tag.py` nutzt `Member_Committees_Full` ohne Datum. Trades aus 2023-2024 (118th Congress) werden mit Ausschüssen des 119th Congress bewertet. Das ist ein inhaltlicher Look-ahead-/Fehlzuordnungs-Bias. `fetch_congress_data.py` ist ebenfalls nur auf Kongress 119 hartkodiert. Mitglieder, die ausgeschieden sind, stehen nur in `former_members.csv` (110 Zeilen, nur name/party/state/chamber, **ohne** Committees).
- Redundanz: Senate PDF vs. XML; `house_pdf_extractor` v1/v2; `generate_committee_structure.py` erzeugt `data/processed/committees_structure.csv`, das Verzeichnis `data/processed` enthält aber nur `trades_master_complete.csv` (Output fehlt, daher vermutlich ungenutzt, ungeprüft).
- Tests: `pytest tests/test_house_pdf_extractor.py test_house_scraper.py test_senate_pdf_extractor.py test_senate_scraper.py test_senate_xml_parser.py test_quiver_scraper.py` = **alle grün (267 passed, ~Sekunden)**. Diese testen aber überwiegend Trade-Scraper; für den House-Committee-Parser existiert kein Pytest, nur das Skript `validate_house.py` (manuell, uncommitted Änderung). Parser für `committee_parser.py` getestet über `tests/test_committee_tag.py` (nicht einzeln gelaufen).
- Nicht ausgeführt (Regeln): `fetch_congress_data.py` (API). Korrektheit der Bill/Committee-Zuordnung daher nur vermutet.

## 3. Code-Qualität

- Positiv: `utils/committee_parser.py` (66 Z.) ist sauber, rein, gut dokumentiert. Der XML-Weg ist kompakt (130+157 Z.) und robust, inklusive Discovery und Änderungsbericht.
- Negativ: `fetch_congress_data.py` 1322 Z. mit 7 Fetch-Stufen, Lock, Checkpoint, Merge in einer Klasse; globale Mutation `CONGRESS` in `main` (Zeile 1242). House-Parser 927 Z. mit mehreren Sonderfällen (z. B. `parse_agriculture`, separater Spezialfall) und an die Layout-Eigenheiten eines PDF-Datums gebunden (Parser-Update 2026-06-12 für Schwelle 318 statt 330). Fragil bei jedem neuen PDF-Release.
- Alt-Codes parallel: Senate-PDF-Parser (520 Z.) neben XML; drei Roster-Quellformate (F2-Grammatik mit Separatoren statt Tabelle). Eine geschachtelte String-Kodierung in einer CSV-Spalte ist unnötig und führt zu Parser-Zwang in allen Consumern.
- Dateileichen: Browser-Dump, `scsoal.pdf`, PDF im Repo, `__pycache__`.

## 4. Wiederverwendbares Wissen

- Rosters als Referenz: `data/mappings/house_member&(sub)committees.csv` (436) und `senate_member&(sub)committees.csv` (100), `us_house_all_committees.csv` (115 Subcommittee-Zeilen) und `us_senate_all_committees.csv`. Die House-Namensliste ist sauber, nur wenige Artefakte.
- Senate-XML-Quelle: `https://www.senate.gov/committees/membership_assignments.htm` plus Committee-XMLs je Code (`scrapers/senate_committee_xml/raw/*.xml`, Codes im `_manifest.json`). Kleine, stabile Quelle, direkt wiederverwendbar.
- Parsing-Wissen House-PDF: `organisation/tool_specific_md/house_all_pdf_info.md` (Seitenstruktur 5-59 Committees, 60-80 Alphabetenliste, Spalten x0-Schwelle, Roman=R / Italic=D) und `scrapers/house_pdf_parsing_tool/extraction_plan.md`; Senate: `scrapers/senate_pdf_parsing_tool/senate_pdf_info.md`, `csv_zielstruktur.md`.
- `data/raw/former_members.csv` (110 ehemalige Mitglieder) als Namensliste für den Matcher; `data/mappings/name_aliases.csv`.
- Mapping-Wissen Subcommittee zu Sektor/Region und Legislative-Subject zu Sektor (`subcommittee_*mapping*.csv`, `legislative_subject_mapping_complete.csv`): menschlicher/LLM-Aufwand, aufbewahren (Qualität nicht geprüft).
- Bessere offene Quelle (Vermutung, nicht recherchiert): `unitedstates/congress-legislators` (YAML/CSV: `legislators-current/historical`, `committees-current`, `committee-membership-current`, jeweils mit Bioguide-ID, Term-Daten, FEC/Thomas-IDs). Das würde für Namens-IDs und zeitliche Gültigkeit der Mitgliedschaft helfen; historische Committee-Mitgliedschaften (118th und früher) sind dort meines Wissens nicht vollständig vorhanden (Vermutung), eventuell ergänzend aus Congress.gov API `/committee` oder GovInfo-Rosters. Bioguide-ID als Schlüssel würde Namens-Matching großteils ersetzen.

## 5. Rebuild-Empfehlung

- Rebuild dieses Bereichs ist leicht und sinnvoll: Quelle `congress-legislators` (oder Senate-XML + Clerk-Daten) holen, in eine normalisierte Tabelle `member`, `committee`, `membership(member_id, committee_id, role, valid_from, valid_to)` überführen. Das löst Namens-Inkonsistenz, String-Kodierung, `ExOfficio`-Doppelbelegung und fehlende Historisierung in einem Schritt.
- Wegwerfen: House-PDF-Parser (927 Z.) und Senate-PDF-Parser (520 Z.), da fragil und redundant, falls die externe Quelle ausreicht. Behalten als Fallback nur, wenn historische Stände (PDF je Kongress) gebraucht werden.
- Behalten: `committee_parser`-Idee nicht nötig (entfällt mit Tabelle), Senate-XML-Fetch/Parser (klein), `former_members.csv`, Mapping-CSVs (Sektor/Region), Parsing-Wissen als Doku.
- `fetch_congress_data.py` neu schreiben und aufspalten (Bills / Votes / Committee-Referral), Kongress als Parameter, Ergebnis in DB/Parquet statt JSON-Dateien mit Checkpoint-Logik; Umfang vorher mit den Konsumenten abstimmen (`tags/vote_timing_tag.py`, `scripts/import_trades.py:1120`).
- Kritischster inhaltlicher Fix unabhängig vom Rebuild: zeitgerechte Ausschusszuordnung für Trades vor dem 119th Congress.
