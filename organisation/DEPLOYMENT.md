# Deployment: Plattformwechsel, USB-Portabilität & Docker

Referenz für das Aufsetzen des Projekts auf einem neuen System (Linux, Mac, Windows) — inkl. USB-Stick-Betrieb und Docker-Alternative. Nur relevant beim Einrichten/Umzug, nicht für den täglichen Lauf (dafür → `README.md` Quick-Start).

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

---

## 8. Betrieb im Docker-Container

Statt venv + System-Pakete manuell einzurichten, kann das Projekt komplett in
einem Docker-Container laufen. Das Image bringt Python, alle Pakete, Tesseract
und Chromium (Playwright) bereits mit. Code-Anpassungen sind **nicht** nötig —
`BASE_DIR = Path(__file__).resolve().parent.parent` macht den Pfad im Container
(`/app`) automatisch korrekt.

### Beteiligte Dateien (im Projektroot)

| Datei | Zweck |
|---|---|
| `Dockerfile` | Bau-Rezept: `python:3.12-slim` + Tesseract + `pip install -r requirements.txt` + `playwright install --with-deps chromium`. Startbefehl: Streamlit auf Port 8501. |
| `docker-compose.yml` | Start-Konfiguration: Port-Mapping `8501:8501`, `.env` via `env_file`, Bind-Mounts `./data` und `./logs`, `restart: unless-stopped`. |
| `.dockerignore` | Schliesst `venv/`, `archive/` (~20 GB), `data/`, `logs/`, `.env`, `__pycache__/` vom Build-Kontext aus. |

### Wichtige Design-Entscheidungen

- **Daten bleiben auf der Festplatte:** `data/` und `logs/` werden per Bind-Mount
  eingehaengt, nicht ins Image gebacken. Rebuilds verlieren also keine Daten,
  und die vorhandenen ~1,1 GB werden direkt weiterverwendet.
- **Secrets nie im Image:** `.env` steht in `.dockerignore` und wird zur Laufzeit
  ueber `env_file` injiziert.
- **`TESSERACT_PATH` im Container nicht setzen** — Default `/usr/bin/tesseract`
  greift. Steht in `.env` noch ein Windows-Pfad (`C:\Program Files\...`), Zeile
  loeschen oder auf `/usr/bin/tesseract` setzen.
- **Playwright/Chromium** laeuft nur bei aktivem Senate-Scraper — **kein**
  Ressourcenverbrauch im Leerlauf, nur groesseres Image (~700 MB) + laengerer Build.

### Voraussetzungen (einmalig, Linux Mint / Ubuntu)

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-v2
sudo usermod -aG docker $USER     # danach einmal ab- und wieder anmelden -> kein sudo noetig
docker compose version            # Pruefen, dass das Plugin da ist
```

### Bedienung

```bash
cd <projektroot>            # z.B. .../Projekte/bmb_trdprg/congress_tracker
docker compose build       # Image bauen (erster Lauf einige Minuten wg. Chromium)
docker compose up -d        # Container im Hintergrund starten -> http://localhost:8501

# Pipeline / Scraper im laufenden Container ausfuehren:
docker compose exec congress-tracker python scripts/import_trades.py
docker compose exec congress-tracker python scrapers/senate_scraper.py --dry-run

docker compose logs -f      # Live-Logs (Strg+C zum Verlassen)
docker compose down         # Container stoppen
```

### Docker-Daemon

Der Docker-Dienst startet per Default beim Booten (`systemctl is-enabled docker`)
und belegt im Leerlauf ~70 MB RAM. Autostart bei Bedarf abschalten:

```bash
sudo systemctl disable --now docker.service docker.socket   # Autostart AUS
sudo systemctl enable  --now docker.service docker.socket   # Autostart AN
```

## 9. Oeffentliches Dashboard-Deployment (VPS)

Anders als Abschnitt 8 (Docker-Container fuer den vollen Pipeline-Betrieb)
geht es hier um eine **oeffentlich erreichbare** Streamlit-Instanz auf einem
eigenen Server. Leitidee: Auf dem Server laeuft nur das Dashboard, nicht die
Pipeline. Der Server bekommt weder API-Keys noch Quiver-Zugangsdaten noch den
Scraper — er liest einen eingefrorenen Datenstand, den der
Entwicklungsrechner erzeugt und per rsync hochlaedt.

| Ort | Aufgabe |
|---|---|
| Entwicklungsrechner | Pipeline laufen lassen, Snapshot bauen, hochladen |
| Server (VPS) | Nur das Dashboard anzeigen |

**Schritt-fuer-Schritt-Anleitung (Server einrichten, Domain, Caddy, Upload,
Betrieb):** `deploy/README_deployment.md`.

### Beteiligte Artefakte

| Datei | Zweck |
|---|---|
| `scripts/build_deploy_snapshot.py` | Eigenstaendiges Skript (keine Pipeline-Stage), erzeugt den Snapshot in `data/deploy/` — Details: `organisation/CLI_Bedienungsanleitung.md` Abschnitt 14, Datenformat: `organisation/DATA_STRUCTURE.md` Abschnitt 7g. |
| `deploy/Dockerfile.app` | Schlankes Dashboard-Image ohne Tesseract/Chromium (~600 MB kleiner als `Dockerfile`, weil auf dem Server kein Scraper laeuft). |
| `deploy/docker-compose.prod.yml` | Produktiv-Compose: Dashboard-Container + Caddy als HTTPS-Reverse-Proxy, Speicherdeckel, Snapshot read-only gemountet. |
| `deploy/Caddyfile` | Reverse-Proxy-Konfiguration (Domain, automatisches HTTPS via Let's Encrypt). |
| `.streamlit/config.toml` | Servereinstellungen: `headless = true`, `maxUploadSize = 5`, `gatherUsageStats = false`, `showErrorDetails = "none"`. |

### Neue ENV-Variablen (`.env.example`)

| Variable | Default | Zweck |
|---|---|---|
| `APP_AUTH_ENABLED` | aus (`0`) | Passwort-Gate vor dem Dashboard. Bewusst deaktiviert — ein Login schreckt Betrachter ab. |
| `APP_PASSWORD` | leer | Passwort, wenn `APP_AUTH_ENABLED=1`. Max. 5 Fehlversuche je Browser-Sitzung (`APP_AUTH_MAX_ATTEMPTS`, `config/settings.py`). |
| `APP_PUBLIC_MODE` | aus (`0`) | Scoring-Presets liegen pro Browser-Session im Arbeitsspeicher statt in `data/config/scoring_presets.json` — verhindert, dass sich Besucher einer geteilten Instanz gegenseitig die Presets ueberschreiben. |

### Abhaengigkeit

`pyarrow==18.1.0` ist seit dem Snapshot-Format eine direkte Abhaengigkeit in
`requirements.txt` (vorher nur transitiv ueber Streamlit) — noetig zum
Lesen/Schreiben der Parquet-Dateien in `data/deploy/`.

### Stand

Seit 2026-08-30 live unter `https://congress-trading-tracker.com` (auch `www`).

| Merkmal | Wert |
|---|---|
| Server | Privater VPS; Details nur in der lokalen `.deploy.env` |
| Projektpfad | Lokal über `DEPLOY_PATH` in `.deploy.env` konfiguriert |
| Container | `congress-dashboard` (healthy, Deckel 2 GB) + `congress-caddy`, beide `restart: unless-stopped` |
| Datenstand | 114.347 Trades, Stand 2026-07-21, 80 MB im Speicher |
| HTTPS | Let's Encrypt via Caddy, automatische Erneuerung |
| Zugang | SSH nur per Schluessel, `ufw` auf 22/80/443, fail2ban, unattended-upgrades |
| Passwortschutz | eingebaut, bewusst deaktiviert (`APP_AUTH_ENABLED=0`) |

Neustartfest geprueft: nach `reboot` war die Seite ohne Eingriff in rund
40 Sekunden wieder erreichbar.

Offen: Git-Historie bereinigen (Altdaten entfernen, braucht Nutzerfreigabe),
danach Repo oeffentlich schalten. Details: `CHANGELOG.md`.
