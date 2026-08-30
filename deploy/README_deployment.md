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
  Das Dashboard braucht im Betrieb rund 800 MB Arbeitsspeicher pro aktiver
  Sitzung; die Compose-Datei deckelt es bei 2 GB.
- **Eine Domain** mit einem A-Record auf die IP-Adresse des Servers.
  Domain und Server muessen nicht beim selben Anbieter liegen.

---

## 1. Snapshot bauen (auf dem Entwicklungsrechner)

```bash
source venv/bin/activate
python scripts/build_deploy_snapshot.py
```

Erzeugt in `data/deploy/` alles, was der Server braucht:

| Datei | Groesse | Zweck |
|---|---|---|
| `trades_snapshot.parquet` | ~15 MB | die Trades (aus dem 645-MB-Master) |
| `price_history.parquet` | ~26 MB | Kursdaten fuer die Verlaufs-Charts |
| `snapshot_manifest.json` | wenige Zeilen | Datenstand, den die App unter dem Titel anzeigt |

Die App sucht den Kurs-Cache zuerst am Normalort (`data/cache/`, den es auf
dem Server nicht gibt) und faellt dann auf `data/deploy/` zurueck — ein
zusaetzlicher Mount ist nicht noetig. Fehlt die Datei ganz, laufen alle
Detail-Popups weiter, zeigen statt der Kursverlaeufe aber nur den Hinweis,
dass Kursdaten fehlen.

Der Ordner ist bewusst **nicht** im Git-Repo (`.gitignore`): So bleibt die
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

# Auf dem Entwicklungsrechner: Datenstand hochladen (~41 MB)
rsync -avz data/deploy/ root@<server-ip>:/srv/congress-tracker/data/deploy/
```

## 4. Domain eintragen und starten

Die Domain steht nicht im Caddyfile, sondern in einer `.env` — so bleiben alle
versionierten Dateien unveraendert und `git pull` laeuft spaeter konfliktfrei.
Auf dem Server `/srv/congress-tracker/deploy/.env` anlegen:

```bash
SITE_DOMAIN=deine-domain.de
# Passwortschutz (optional, s. Abschnitt 7)
APP_AUTH_ENABLED=0
APP_PASSWORD=
```

Dann starten:

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

Der Snapshot enthaelt den Kurs-Cache; ein separater Upload ist nicht noetig.

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

In `/srv/congress-tracker/deploy/.env` setzen:

```bash
APP_AUTH_ENABLED=1
APP_PASSWORD=<dein-passwort>
```

Dann `docker compose -f deploy/docker-compose.prod.yml up -d`.

Das Passwort steht nie im Code und in keiner versionierten Datei. Nach fuenf
Fehlversuchen bleibt das Formular fuer diese Sitzung gesperrt
(`APP_AUTH_MAX_ATTEMPTS` in `config/settings.py`).

## 8. Was auf dem Server bewusst fehlt

- **Keine API-Keys, keine Quiver-Zugangsdaten.** Das Dashboard braucht zur
  Laufzeit kein Netz und keine Schluessel; es liest nur den Snapshot.
- **Kein Scraper, kein Import, kein OCR.** Deshalb enthaelt
  `deploy/Dockerfile.app` weder Tesseract noch Chromium (~600 MB gespart).
- **Kein Schreibzugriff auf die Daten.** Snapshot und Kurs-Cache sind `:ro`
  gemountet, der Container laeuft als unprivilegierter Nutzer, und der
  CSV-Export des Recent-Tabs ist im oeffentlichen Modus ausgeblendet — sonst
  koennte jeder Besucher Dateien auf der Serverplatte anlegen.
