# Entscheidungen — Ticket P1
Startcommit: 6c589768f1b4aa11824624db7006b6ea5a831723

## Implementierungsentscheidungen
| Entscheidung/Annahme | Begründung | Bezug (Spec/Ticket/Datei) |
|---|---|---|
| `scripts/report_ticker_types.py` und `scripts/build_ticker_liste_index.py` gelöscht (Kandidaten mit Zweckprüfung). | Kein Aufrufer in Code, Shell, Deploy oder Dockerfile (grep). Das erste ist ein reiner Verteilungsreport über den Master und den Normalizer-Cache, das zweite laut eigenem Docstring eine Recherchehilfe „keine Pipeline-Funktion“. Doku nennt nur das Artefakt `ticker_liste_index.csv`, nicht das Skript. | P1, Plan |
| `csv_zielstruktur.md` und `senate_pdf_info.md` per `git mv` nach `organisation/tool_specific_md/`; Verweis in `utils/committee_parser.py:7` angepasst. | Plan P1 | P1 |
| Fehlermeldungen in `import_trades.py` verweisen statt auf `migrate_add_trade_ids.py` auf „Full Rebuild: python scripts/import_trades.py“. | Das Skript ist gelöscht; der Hinweis wäre sonst irreführend. Nur der Text, keine Logik; CRLF-Zeilenenden der Datei erhalten. | P1, `scripts/import_trades.py` |
| Kommentar „Logik-Kopie in diagnose_master_quiver_delta.py“ in `import_trades.py` entfernt; Verweise in `utils/trade_id.py`, `utils/ticker_normalizer.py`, `quiver_scraper.py` bereinigt. | Verweise auf gelöschten Code. | P1 |
| `requirements.txt`: entfernt `anthropic`, `opencv-python-headless`, `pytest-mock` sowie die nur von `anthropic` abhängigen `distro`, `docstring_parser`, `jiter`. | AST-/grep-Nachweis: kein `import anthropic/cv2/PIL-Zweig/pytest_mock/distro/jiter/docstring_parser` im Restcode; `pip show`: Required-by nur `anthropic` (bzw. leer). `committee_matcher/submapper.py:1961` nennt `ANTHROPIC_*` nur als Umgebungsvariablen-Namen. | P1 |
| `beautifulsoup4` bleibt, obwohl Plan-Kandidat. | `pip show beautifulsoup4`: Required-by `yfinance` (Laufzeitabhängigkeit der Kursdaten); `bs4` wird zwar nirgends direkt importiert, die Pin-Zeile sichert die Version. | P1, `requirements.txt` |
| `pytest`, `iniconfig`, `pluggy` nach neuer `requirements-dev.txt` (`-r requirements.txt` + Test-Pakete); `linux_setup.sh` installiert `requirements-dev.txt`. | Dockerfile und `deploy/Dockerfile.app` installieren nur `requirements.txt` und führen kein pytest aus; Deploy-Skripte nennen pytest nicht. Damit ohne weitere Änderung zulässig. | P1 |
| Root-`Dockerfile`: apt-Block (Tesseract, libglib für OpenCV) entfernt; `linux_setup.sh`: Tesseract-/libgl1-/libglib-Pakete, `TESSERACT_PATH`, `ANTHROPIC_API_KEY`-Eintrag in der generierten `.env` und Tesseract-Version entfernt; `.env.example`: `ANTHROPIC_API_KEY` entfernt; Kommentar in `deploy/Dockerfile.app` angepasst. | Einziger Nutzer von Tesseract/OpenCV/Anthropic-SDK war der gelöschte Extraktor-Code (grep: kein `pytesseract`, `cv2`, `anthropic`-Import mehr). Plan nennt „Tesseract-Installation“ in `linux_setup.sh`; Dockerfile-Prüfung ist Gate. | P1 |
| Testanzahl: 2371 − 244 = 2127 (34 + 126 + 20 + 64 `def test_` in den vier gelöschten Dateien, keine Parametrisierung). | Gate „Baseline minus gelöschte Tests“. | `baseline.md` |

## Reviewbefunde
| Befund und Auswirkung | Schweregrad laut Prüfer | Prüfer, Review, Runde | Bezug (Spec/Ticket/`datei:zeile`) | Umgang | Begründung/Belege |
|---|---|---|---|---|---|
| Wissen zu handschriftlichen House-PTRs Typ D fehlte nach dem Löschen des Extractors (abweichende Betragszuordnung G/H/I). | MITTEL | gpt-6.2-sol high, Interim Review, Runde 1 | P1; `6c58976:scrapers/house_pdf_extractor.py:69` | behoben | Zuordnung in `organisation/tool_specific_md/Trade_scraper_status.md` ergänzt. |
| Doku-Installationsbefehl nutzt `requirements.txt`, danach fehlt `pytest`. | NIEDRIG | gpt-6.2-sol high, Interim Review, Runde 1 | `organisation/DEPLOYMENT.md:115,120` | behoben | Auf `requirements-dev.txt` umgestellt; Tesseract aus der Dockerfile-Beschreibung (`:222`) entfernt. Übrige Doku folgt in P8. |
| Typ-D-Betragsklassen gerundet und dadurch missverständlich. | NIEDRIG | gpt-6.2-sol high, Interim Review, Runde 2 | `organisation/tool_specific_md/Trade_scraper_status.md:40` | behoben | Exakte Grenzen aus dem Altcode eingetragen; Verdikt Runde 2: abgenommen. |
