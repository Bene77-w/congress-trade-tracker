# systemd-User-Timer: Quiver-Auto-Download + Import

Versionierte Vorlagen der systemd-Units, die den Quiver-Scraper
(`scrapers/quiver_scraper.py`) + die Import-Pipeline (`scripts/import_trades.py`)
taeglich um **03:00** als **User-Timer** ausfuehren.

`Persistent=true` holt einen verpassten Lauf nach, sobald der Laptop wieder
laeuft / die User-Session startet — ein reiner cron-Job wuerde bei nachts
ausgeschaltetem Rechner nie ausloesen.

## Warum User-Timer (nicht System-Timer)?
- Kein `sudo` noetig.
- Nutzt das Host-`venv` und die User-`.env` (Credentials) direkt.
- Laeuft im Kontext der User-Session; `Persistent=true` holt verpasste Laeufe
  beim naechsten Login nach.

## Installation
`__PROJECT_DIR__` durch den absoluten Projektpfad ersetzen und nach
`~/.config/systemd/user/` kopieren:

```bash
PROJECT_DIR=/home/bene/Nextcloud/Projekte/bmb_trdprg/congress_tracker
mkdir -p ~/.config/systemd/user
sed "s#__PROJECT_DIR__#$PROJECT_DIR#g" deploy/systemd/quiver-import.service > ~/.config/systemd/user/quiver-import.service
cp deploy/systemd/quiver-import.timer ~/.config/systemd/user/quiver-import.timer

systemctl --user daemon-reload
systemctl --user enable --now quiver-import.timer
```

## Optional: Lauf auch ohne aktives Login (Lingering)
Ohne Lingering laeuft der User-Timer nur waehrend einer aktiven Session
(`Persistent=true` holt beim Login nach — fuer einen Laptop meist ausreichend).
Soll der Timer auch ohne eingeloggten User feuern (benoetigt einmalig `sudo`):

```bash
sudo loginctl enable-linger $USER
```

## Bedienung / Diagnose
```bash
systemctl --user list-timers quiver-import.timer   # naechster/letzter Lauf
systemctl --user start quiver-import.service        # sofort manuell ausloesen
systemctl --user status quiver-import.service       # Ergebnis/Exit-Code
journalctl --user -u quiver-import.service -e       # Logs (zusaetzl. logs/quiver_cron.log)
```

## Fehlerklassen-Semantik
Die Service-Unit hat zwei `ExecStart`-Zeilen ohne `-`-Praefix. Faellt der
Scraper mit Non-Zero-Exit aus (2 = Login, 3 = Download/Abo), stoppt systemd
und fuehrt `import_trades.py` **nicht** aus — identisch zur cron-`&&`-Semantik.
