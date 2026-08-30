# Deployment des Dashboards auf einem eigenen Server

Diese Anleitung stellt das Streamlit-Dashboard oeffentlich unter einer eigenen
Domain bereit — ohne Login, mit HTTPS, auf einem eingefrorenen Datenstand.
Die Pipeline (Scraper, Import, Scoring) laeuft **nicht** auf dem Server; sie
bleibt auf dem Entwicklungsrechner.

**Rollenverteilung:**

| Ort | Aufgabe |
|---|---|
| Entwicklungsrechner | Pipeline laufen lassen, Snapshot bauen, hochladen |
| Server (VPS) | Nur das Dashboard anzeigen |

Grund fuer die Trennung: Der Server braucht dann weder API-Keys noch die
Quiver-Zugangsdaten, und ein Pipeline-Fehler kann die oeffentliche Seite nicht
umwerfen.

---

## 0. Was du brauchst

- **Einen VPS**, nicht Webhosting. Shared Webhosting fuehrt Python nur pro
  Seitenaufruf aus und kann keinen Dauerprozess mit WebSocket betreiben —
  Streamlit laeuft dort grundsaetzlich nicht. Passend sind z. B. IONOS
  „VPS Linux" oder Hetzner „CX22": 2 Kerne, 4 GB RAM, ca. 4–6 EUR/Monat.
  Das Dashboard selbst braucht davon nur rund 300 MB.
- **Eine Domain** mit einem A-Record auf die IP-Adresse des Servers.
  Domain und Server muessen nicht beim selben Anbieter liegen.

---

## 1. Snapshot bauen (auf dem Entwicklungsrechner)

```bash
source venv/bin/activate
python scripts/build_deploy_snapshot.py
```

Erzeugt aus dem Master-CSV (~645 MB):

- `data/deploy/trades_snapshot.parquet` (~15 MB)
- `data/deploy/snapshot_manifest.json` (Datenstand, Zeilenzahl — das Dashboard
  zeigt den Datenstand unter dem Titel an)

Beide Dateien sind bewusst **nicht** im Git-Repo (`.gitignore`): So bleibt die
Repo-Historie schlank, und die Trade-Daten liegen nicht dauerhaft oeffentlich
auf GitHub.

## 2. Server vorbereiten (einmalig)

Als root auf dem frischen Server:

```bash
# Paketstand aktualisieren + Docker installieren
apt update && apt upgrade -y
curl -fsSL https://get.docker.com | sh

# Automatische Sicherheitsupdates
apt install -y unattended-upgrades
dpkg-reconfigure -plow unattended-upgrades

# Firewall: nur SSH und Web
apt install -y ufw
ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw --force enable
```

Zusaetzlich empfohlen: SSH-Login nur per Schluessel
(`PasswordAuthentication no` in `/etc/ssh/sshd_config`, danach
`systemctl restart ssh`).

## 3. Code und Datenstand auf den Server bringen

```bash
# Auf dem Server: Code holen
git clone <repo-url> /srv/congress-tracker

# Auf dem Entwicklungsrechner: Snapshot hochladen (~15 MB)
rsync -avz data/deploy/ root@<server-ip>:/srv/congress-tracker/data/deploy/
```

## 4. Domain eintragen und starten

Auf dem Server in `deploy/Caddyfile` die erste Zeile auf die eigene Domain
aendern (statt `congress-tracker.example.de`). Dann:

```bash
cd /srv/congress-tracker
docker compose -f deploy/docker-compose.prod.yml up -d --build
```

Der erste Build dauert einige Minuten. Caddy holt das HTTPS-Zertifikat
automatisch, sobald die Domain auf den Server zeigt — es ist kein
Zertifikatsschritt von Hand noetig.

Danach ist die Seite unter `https://<deine-domain>` erreichbar.

Status pruefen:

```bash
docker compose -f deploy/docker-compose.prod.yml ps
docker compose -f deploy/docker-compose.prod.yml logs -f dashboard
```

## 5. Datenstand aktualisieren

Auf dem Entwicklungsrechner Pipeline laufen lassen, dann:

```bash
python scripts/build_deploy_snapshot.py
rsync -avz data/deploy/ root@<server-ip>:/srv/congress-tracker/data/deploy/
ssh root@<server-ip> "docker restart congress-dashboard"
```

Der Neustart ist noetig, weil Streamlit die Daten beim Start in den Cache
laedt. Er dauert wenige Sekunden.

## 6. Code-Aenderungen ausrollen

```bash
ssh root@<server-ip>
cd /srv/congress-tracker && git pull
docker compose -f deploy/docker-compose.prod.yml up -d --build
```

## 7. Passwortschutz (optional, standardmaessig aus)

Das Dashboard ist absichtlich ohne Login erreichbar — ein Passwort haelt
Betrachter ab, die sich die Arbeit nur ansehen sollen. Falls der Zugriff doch
eingeschraenkt werden muss:

1. In `config/settings.py`: `APP_AUTH_ENABLED = True`
2. Auf dem Server eine `.env` neben der Compose-Datei anlegen mit
   `APP_PASSWORD=<dein-passwort>`
3. `docker compose -f deploy/docker-compose.prod.yml up -d --build`

Das Passwort steht nie im Code, nur in der Umgebungsvariable.

## 8. Was auf dem Server bewusst fehlt

- **Keine API-Keys, keine Quiver-Zugangsdaten.** Das Dashboard braucht zur
  Laufzeit kein Netz und keine Schluessel; es liest nur den Snapshot.
- **Kein Scraper, kein Import, kein OCR.** Deshalb enthaelt
  `deploy/Dockerfile.app` weder Tesseract noch Chromium (~600 MB gespart).
- **Kein Schreibzugriff auf die Daten.** Der Snapshot ist als `:ro` gemountet.
