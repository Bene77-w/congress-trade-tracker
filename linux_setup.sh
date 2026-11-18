#!/bin/bash
# ============================================================
# Linux Mint Cinnamon — Setup: Congress Trade Tracker
# Ausführung NACH Migration von Windows auf Linux
#
# Voraussetzung: Projekt von Nextcloud hierher kopiert
# Ausführung:    bash linux_setup.sh
# ============================================================

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo -e "${BLUE}============================================${NC}"
echo -e "${BLUE}  Linux Mint Setup — Congress Trade Tracker${NC}"
echo -e "${BLUE}============================================${NC}"
echo ""

# -------------------------------------------------------
# 1. System-Abhängigkeiten
# -------------------------------------------------------
echo -e "${YELLOW}[1/4] System-Pakete installieren...${NC}"
sudo apt update -qq
sudo apt install -y \
    python3 \
    python3-pip \
    python3-venv \
    chromium-browser \
    chromium-chromedriver

echo -e "${GREEN}  ✓ System-Pakete installiert${NC}"
echo ""

# -------------------------------------------------------
# 2. Python Virtual Environment
# -------------------------------------------------------
echo -e "${YELLOW}[2/4] Python venv erstellen...${NC}"
cd "$SCRIPT_DIR"

if [ -d "venv" ]; then
    echo -e "${YELLOW}  venv existiert bereits — wird neu erstellt${NC}"
    rm -rf venv
fi

python3 -m venv venv
source venv/bin/activate
echo -e "${GREEN}  ✓ venv erstellt und aktiviert${NC}"
echo ""

# -------------------------------------------------------
# 3. Python-Pakete installieren
# -------------------------------------------------------
echo -e "${YELLOW}[3/4] Python-Pakete aus requirements-dev.txt installieren...${NC}"
pip install --upgrade pip --quiet
pip install -r requirements-dev.txt
echo -e "${GREEN}  ✓ Pakete installiert${NC}"
echo ""

# -------------------------------------------------------
# 4. .env Datei prüfen / erstellen
# -------------------------------------------------------
echo -e "${YELLOW}[4/4] .env Konfiguration prüfen...${NC}"

if [ ! -f ".env" ]; then
    cat > .env << EOF
# Congress Trade Tracker — Umgebungsvariablen
# Erstellt von linux_setup.sh am $(date '+%d.%m.%Y')

# Congress.gov API Key (für Filter 4: Voting)
CONGRESS_API_KEY=

# Email-Benachrichtigungen (optional)
SMTP_SERVER=
SMTP_PORT=587
EMAIL_USER=
EMAIL_PASSWORD=
EMAIL_RECIPIENT=
EOF
    echo -e "${GREEN}  ✓ .env erstellt${NC}"
else
    echo -e "${GREEN}  ✓ .env bereits vorhanden${NC}"
fi

echo ""

# -------------------------------------------------------
# Playwright (für Quiver-Scraper)
# -------------------------------------------------------
echo -e "${YELLOW}[+] Playwright Browser installieren...${NC}"
python -m playwright install chromium 2>/dev/null && \
    echo -e "${GREEN}  ✓ Playwright Chromium installiert${NC}" || \
    echo -e "${YELLOW}  ⚠ Playwright übersprungen (kein Playwright in requirements.txt)${NC}"

echo ""

# -------------------------------------------------------
# Abschluss
# -------------------------------------------------------
echo -e "${BLUE}============================================${NC}"
echo -e "${GREEN}  Setup abgeschlossen!${NC}"
echo -e "${BLUE}============================================${NC}"
echo ""
echo "Aktivierung (ab jetzt immer zuerst ausführen):"
echo -e "${YELLOW}  cd $SCRIPT_DIR${NC}"
echo -e "${YELLOW}  source venv/bin/activate${NC}"
