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

**Zugang zuerst.** Bei IONOS gibt es fuer einen bestehenden VPS kein Menue,
um nachtraeglich einen SSH-Schluessel zu hinterlegen, und keinen Knopf fuer
einen Passwort-Reset. Der praktikable Weg ist *Server > Aktionen > Image neu
installieren*: Dort setzt man ein Root-Passwort und traegt unter
*Weitere Einstellungen > Skripte > Benutzerdaten* (Skripttyp: Cloud Config)
den Schluessel ein:

```yaml
#cloud-config
disable_root: false
runcmd:
  - mkdir -p /root/.ssh
  - chmod 700 /root/.ssh
  - echo 'ssh-ed25519 AAAA... dein-kommentar' >> /root/.ssh/authorized_keys
  - chmod 600 /root/.ssh/authorized_keys
```

**Anbieter-Firewall.** IONOS weist dem Server eine eigene Firewall-Richtlinie
zu, die *vor* dem Betriebssystem filtert — die `ufw` auf dem Server allein
genuegt also nicht. Unter *Netzwerk > Firewall-Richtlinien* muessen TCP 80 und
443 zugelassen sein. Die von IONOS vorbelegten Ports 8443 und 8447 (Plesk)
kann man loeschen, wenn kein Plesk laeuft.

Dann als root auf dem Server:

```bash
apt update && apt upgrade -y
apt install -y ca-certificates curl rsync ufw fail2ban unattended-upgrades

# Docker aus dem offiziellen Repo (nicht aus den Ubuntu-Paketen)
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
  > /etc/apt/sources.list.d/docker.list
apt update && apt install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker

# Firewall: nur SSH und Web
ufw default deny incoming && ufw default allow outgoing
ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp
ufw --force enable
```

**SSH haerten.** Ubuntu 24.04 laedt `/etc/ssh/sshd_config.d/*.conf`, und der
*erste* gefundene Wert gewinnt. Die eigene Datei muss deshalb alphabetisch vor
`50-cloud-init.conf` sortieren:

```bash
cat > /etc/ssh/sshd_config.d/00-hardening.conf <<'CONF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
PubkeyAuthentication yes
MaxAuthTries 3
CONF
sshd -t && systemctl reload ssh
sshd -T | grep -E '^(passwordauthentication|permitrootlogin) '   # kontrollieren
```

Der Passwort-Login ist damit nur ueber SSH aus; die Remotekonsole des Anbieters
funktioniert weiter — das bleibt der Weg zurueck, falls der Schluessel abhanden
kommt.

## 3. Code und Datenstand auf den Server bringen

Bei oeffentlichem Repo genuegt ein Clone. Solange das Repo **privat** ist,
braeuchte der Server einen Deploy-Key — einfacher ist es, den Code direkt
hochzuladen:

```bash
# Auf dem Entwicklungsrechner, aus dem Projektroot:
rsync -az --delete \
  --exclude='.git/' --exclude='venv/' --exclude='archive/' \
  --exclude='__pycache__/' --exclude='data/' --exclude='logs/' \
  ./ root@<server-ip>:/srv/congress-tracker/

# Datenstand separat (~41 MB)
rsync -az --mkpath data/deploy/ root@<server-ip>:/srv/congress-tracker/data/deploy/
```

Der Ausschluss von `data/` ist wichtig: Sonst wandert der 645-MB-Master mit.

## 4. Domain eintragen und starten

Die Domain steht nicht im Caddyfile, sondern in einer `.env` — so bleiben alle
versionierten Dateien unveraendert und `git pull` laeuft spaeter konfliktfrei.
Auf dem Server `/srv/congress-tracker/deploy/.env` anlegen:

```bash
SITE_DOMAIN=deine-domain.de, www.deine-domain.de
# Passwortschutz (optional, s. Abschnitt 7)
APP_AUTH_ENABLED=0
APP_PASSWORD=
```

Mehrere Hostnamen komma-getrennt: Caddy holt fuer jeden ein eigenes
Zertifikat. Steht `www` nicht drin, laeuft der A-Record fuer `www` ins Leere.
Ein AAAA-Record beim Domain-Parking muss geloescht werden — sonst landet
sowohl der Browser als auch die Zertifikatspruefung ueber IPv6 auf der
Parkseite des Anbieters.

Dann starten:

```bash
cd /srv/congress-tracker
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env up -d --build
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
# Bei per rsync gepflegtem Server (privates Repo):
rsync -az --delete --exclude='.git/' --exclude='venv/' --exclude='archive/' \
  --exclude='__pycache__/' --exclude='data/' --exclude='logs/' \
  ./ root@<server-ip>:/srv/congress-tracker/
ssh root@<server-ip> "cd /srv/congress-tracker && \
  docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env up -d --build"
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
