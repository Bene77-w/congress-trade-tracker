# Migrations-Anleitung: Plattformwechsel & USB-Portabilität

Diese Anleitung beschreibt alle Schritte um das Projekt auf einem neuen System (Linux, Mac, Windows) einzurichten — auch beim Betrieb von einem USB-Stick.

---

## USB-Stick Betrieb

### Verzeichnisname umbenennen
Der Projektordner (`bmb_trdprg`, `bmb_trdprg_1`, o.ä.) kann **beliebig umbenannt** werden — dank `BASE_DIR = Path(__file__).resolve().parent.parent` in `config/settings.py` wird der Pfad immer dynamisch berechnet.

### Was auf dem USB-Stick liegt
```
USB-Stick (exFAT):
  bmb_trdprg/
    congress_tracker/    ← Code + Daten (portabel)
    .env                 ← API-Keys (wandert mit)
```

### Was NICHT auf dem USB-Stick liegt — das venv
Das venv ist **nicht portabel** zwischen Plattformen und auch nicht auf exFAT lauffähig:
- Enthält hardcodierte absolute Pfade zur Python-Installation des Ausgangssystems
- Windows-venv (`.exe`) läuft nicht auf Linux und umgekehrt
- Linux-venv benötigt Symlinks → funktioniert nicht auf exFAT/FAT32

**Lösung:** venv lokal auf dem jeweiligen Rechner erstellen (einmalig pro System):

```bash
# Linux:
python3 -m venv --copies ~/venv_congress
source ~/venv_congress/bin/activate
pip install -r /media/.../congress_tracker/requirements.txt

# Windows:
python -m venv C:\venvs\congress
C:\venvs\congress\Scripts\activate
pip install -r D:\bmb_trdprg\congress_tracker\requirements.txt
```

**Danach starten (jedes Mal):**
```bash
# Linux:
source ~/venv_congress/bin/activate
cd /media/bene/.../congress_tracker

# Windows:
C:\venvs\congress\Scripts\activate
cd D:\bmb_trdprg\congress_tracker
```

### Voraussetzungen auf jedem Rechner

| Voraussetzung | Linux | Windows |
|---|---|---|
| Python 3.10+ | `sudo apt install python3` | python.org |
| Tesseract OCR | `sudo apt install tesseract-ocr` | Installer (UB-Mannheim) |
| Playwright-Browser | `playwright install chromium` | identisch |
| venv (einmalig) | `python3 -m venv --copies ~/venv_congress` | `python -m venv C:\venvs\congress` |

---

## 1. Voraussetzungen installieren

### Python
- Version: **3.10+** empfohlen
- Linux/Mac: `python3 --version`
- Windows: Installer von python.org, „Add to PATH" aktivieren

### Tesseract OCR
Wird für House PDF Extractor (`scrapers/house_pdf_extractor_v2.py`) benötigt.

| Plattform | Installation | Standard-Pfad |
|-----------|-------------|---------------|
| Linux (Ubuntu/Debian) | `sudo apt install tesseract-ocr` | `/usr/bin/tesseract` |
| Mac (Homebrew) | `brew install tesseract` | `/opt/homebrew/bin/tesseract` |
| Windows | Installer: https://github.com/UB-Mannheim/tesseract/wiki | `C:\Program Files\Tesseract-OCR\tesseract.exe` |

**Wenn Tesseract nicht am Standard-Pfad liegt:** Umgebungsvariable setzen:
```bash
# Linux/Mac (.env oder Shell):
export TESSERACT_PATH=/usr/local/bin/tesseract

# Windows (.env):
TESSERACT_PATH=C:\Pfad\zu\tesseract.exe
```

### Playwright (für Senate Scraper)
```bash
pip install playwright
playwright install chromium
```

---

## 2. Projekt klonen / kopieren

Das Projekt verwendet `BASE_DIR = Path(__file__).resolve().parent.parent` in `config/settings.py` — der Projektpfad wird **automatisch** erkannt, egal wo es liegt.

```bash
# Projekt in beliebiges Verzeichnis kopieren/klonen, z.B.:
# Linux/Mac:
cd /home/<user>/projekte/congress_tracker
# Windows:
cd C:\Users\<user>\Projekte\congress_tracker
```

---

## 3. Virtual Environment einrichten

```bash
# Linux/Mac:
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Windows:
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

---

## 4. Umgebungsvariablen (.env)

`.env` Datei im Projektroot anlegen (Vorlage: vorhandene `.env` kopieren):

```env
# API-Keys
CONGRESS_API_KEY=...
ANTHROPIC_API_KEY=...

# SMTP (optional, für Email-Benachrichtigungen)
SMTP_SERVER=...
SMTP_PORT=587
EMAIL_USER=...
EMAIL_PASSWORD=...
EMAIL_RECIPIENT=...

# Tesseract (nur wenn nicht am Standard-Pfad)
# TESSERACT_PATH=/pfad/zu/tesseract
```

---

## 5. `.claude/settings.local.json` anpassen

Die Datei enthält Berechtigungsregeln für Claude Code. Sie ist **plattformspezifisch** und muss nach einem Plattformwechsel aktualisiert werden.

**Linux/Mac** (aktueller Stand, bereits korrekt):
```json
{
  "permissions": {
    "allow": [
      "Bash(python:*)",
      "Bash(./venv/bin/python:*)",
      "Bash(source:*)"
    ]
  }
}
```

**Windows** (anpassen auf):
```json
{
  "permissions": {
    "allow": [
      "Bash(python:*)",
      "Bash(.\\venv\\Scripts\\python.exe:*)",
      "Bash(.\\venv\\Scripts\\activate:*)"
    ]
  }
}
```

---

## 6. Bekannte plattformspezifische Unterschiede

| Thema | Linux/Mac | Windows |
|-------|-----------|---------|
| venv aktivieren | `source venv/bin/activate` | `venv\Scripts\activate` |
| Python-Executable | `python3` / `./venv/bin/python` | `python` / `venv\Scripts\python.exe` |
| Tesseract-Pfad | `/usr/bin/tesseract` | `C:\Program Files\Tesseract-OCR\tesseract.exe` |
| Pfad-Trenner | `/` | `\` (in Python: `Path` verwenden, dann automatisch) |
| Playwright-Browser | `playwright install chromium` | identisch |
| OpenCV (`cv2`) | `pip install opencv-python` | identisch |

---

## 7. Verifizierung nach Migration

```bash
# 1. BASE_DIR prüfen
python -c "from config.settings import BASE_DIR; print(BASE_DIR)"

# 2. Imports testen
python -c "from config.settings import BASE_DIR, PDF_EXTRACTOR_TESSERACT_PATH; print(PDF_EXTRACTOR_TESSERACT_PATH)"

# 3. Tests ausführen
python -m pytest tests/ -v --tb=short

# 4. Import-Pipeline testen (ohne Scraper)
python scripts/import_trades.py --dry-run  # falls --dry-run unterstützt
```
